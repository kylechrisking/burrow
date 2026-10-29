"""The engine: run providers, merge what they see, identify apps, keep them checked."""
from __future__ import annotations

import copy
import logging
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from . import apps, net
from .apps.base import REGISTRY, Ctx
from .brock import Brock, badge
from .config import Config, data_dir
from .history import History, apply_rules
from .model import Endpoint, Host, Service, Stat
from .providers import load_providers

log = logging.getLogger(__name__)

MISSING_GRACE = 3600   # keep a service that vanished from discovery, marked down, for an hour
HISTORY_SAVE_EVERY = 300


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.providers = load_providers(cfg)
        self.inventories: dict = {}
        self.pstatus: dict[str, dict] = {p.name: {"name": p.name, "ok": None, "error": None, "at": None, "detail": ""}
                                         for p in self.providers}
        self.services: dict[str, Service] = {}
        self.svc_cache: dict[str, dict] = defaultdict(dict)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="probe")
        self.changed = threading.Event()
        # Where a local Docker socket's published ports are reached when the engine
        # is not a known guest: our own LAN IP when running on the Docker host itself
        # (linkable), or the bridge gateway when running inside a container (not).
        if net.in_container():
            self.local_addr, self.local_linkable = net.default_gateway(), False
        else:
            self.local_addr, self.local_linkable = net.primary_ip(), True
        self.identified_at: float | None = None
        self.weather: dict | None = None
        self.started = time.time()
        self.gen = 0                    # bumped on reload; provider/weather threads of an old gen exit
        self.force_identify = False
        self.config_error: str | None = None
        self.history = History()
        self.brock: Brock | None = None
        self.data = data_dir()
        self.history_file = self.data / "history.json" if self.data else None
        if self.history_file:
            n = self.history.load(self.history_file)
            if n:
                log.info("history: %d series restored from %s", n, self.history_file)
        # Bumped whenever something the page shows may have changed; /api/events waits on it.
        self.version = 0
        self.updated = threading.Condition()

    def notify(self) -> None:
        with self.updated:
            self.version += 1
            self.updated.notify_all()

    def wait_for_change(self, seen: int, timeout: float) -> int:
        """Block until the version moves past `seen` (or the timeout); returns the version."""
        with self.updated:
            self.updated.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version

    def save_history(self) -> None:
        if not self.history_file:
            return
        try:
            self.history.save(self.history_file)
        except OSError as e:
            log.warning("history: could not save to %s: %s", self.history_file, e)

    # ---- loops -------------------------------------------------------------

    def start(self) -> None:
        self._start_sources()
        threading.Thread(target=self._identify_loop, daemon=True, name="identify").start()
        threading.Thread(target=self._check_loop, daemon=True, name="check").start()
        threading.Thread(target=self._watch_loop, daemon=True, name="watch").start()
        threading.Thread(target=self._history_loop, daemon=True, name="history").start()

    def _history_loop(self) -> None:
        while True:
            time.sleep(HISTORY_SAVE_EVERY)
            self.save_history()

    def _start_sources(self) -> None:
        log.info("providers: %s", ", ".join(p.name for p in self.providers) or "none (add [proxmox]/[docker]/[scan] config)")
        for p in self.providers:
            threading.Thread(target=self._provider_loop, args=(p, self.gen), daemon=True, name=p.name).start()
        if self.cfg.weather.get("latitude") is not None:
            threading.Thread(target=self._weather_loop, args=(self.gen,), daemon=True, name="weather").start()
        self._start_brock()

    def _start_brock(self) -> None:
        self.brock = Brock(self.cfg.brock) if self.cfg.brock.get("url") else None
        if self.brock:
            threading.Thread(target=self._brock_loop, args=(self.brock,), daemon=True, name="brock").start()

    def _brock_loop(self, feed: Brock) -> None:
        while feed is self.brock:
            feed.poll()
            feed.at = time.time()
            self.notify()
            time.sleep(feed.every)

    def _watch_loop(self) -> None:
        """Reload sett.toml and the secrets file when they change: display settings
        apply on the next page refresh, provider settings restart discovery."""
        from . import config
        while True:
            time.sleep(3)
            if not config.changed(self.cfg):
                continue
            try:
                new = config.load()
            except Exception as e:   # a half-saved or broken file: keep running on the last good one
                self.config_error = f"config not reloaded: {e}"[:300]
                log.warning(self.config_error)
                self.cfg.files = {f: config._mtime(Path(f)) for f in self.cfg.files}
                continue
            self.config_error = None
            if self.cfg.weather != new.weather:
                self.weather = None   # removed or moved: never show the old place's forecast
            restart = config.providers_changed(self.cfg, new) or self.cfg.weather != new.weather
            new.port = self.cfg.port   # the listening socket cannot move
            brock_moved = {k: new.brock.get(k) for k in ("url", "token", "every")} != \
                {k: self.cfg.brock.get(k) for k in ("url", "token", "every")}
            self.cfg = new
            if brock_moved and not restart:
                self._start_brock()   # a full restart below starts it anyway
            if restart:
                with self.lock:
                    self.gen += 1
                    self.providers = load_providers(new)
                    self.inventories = {}
                    self.pstatus = {p.name: {"name": p.name, "ok": None, "error": None, "at": None, "detail": ""}
                                    for p in self.providers}
                self._start_sources()
            self.force_identify = True
            self.changed.set()
            self.notify()
            log.info("config reloaded%s", " (discovery restarted)" if restart else "")

    def _provider_loop(self, p, gen: int) -> None:
        while gen == self.gen:
            st = self.pstatus.get(p.name)
            if st is None:
                return
            try:
                inv = p.discover()
                with self.lock:
                    if gen != self.gen:
                        return
                    self.inventories[p.name] = inv
                st.update(ok=True, error=None, detail=p.describe())
                for h in inv.hosts:
                    m = h.metrics
                    if m.get("cpu") is not None:
                        self.history.record(f"{h.id}|cpu", m["cpu"] * 100)
                    if m.get("maxmem") and m.get("mem") is not None:
                        self.history.record(f"{h.id}|mem", m["mem"] / m["maxmem"] * 100)
            except Exception as e:  # keep the last good inventory
                log.warning("%s: %s", p.name, e)
                st.update(ok=False, error=str(e)[:200])
            st["at"] = time.time()
            self.changed.set()
            self.notify()
            time.sleep(p.interval)

    def _identify_loop(self) -> None:
        last_sig = None
        last_run = 0.0
        while True:
            self.changed.wait(timeout=10)
            self.changed.clear()
            hosts, endpoints = self.merged()
            sig = (frozenset((h.id, h.address, h.status) for h in hosts.values()),
                   frozenset((e.host_id, e.port, e.hint) for e in endpoints))
            if self.force_identify or sig != last_sig or time.monotonic() - last_run > self.cfg.identify_interval:
                if not self.providers or self.inventories:
                    self.force_identify = False
                    t0 = time.monotonic()
                    try:
                        self.identify(hosts, endpoints)
                    except Exception:
                        log.exception("identify failed")
                    log.info("identify: %d services in %.1fs", len(self.services), time.monotonic() - t0)
                    self.notify()
                    last_sig, last_run = sig, time.monotonic()

    def _check_loop(self) -> None:
        while True:
            time.sleep(self.cfg.check_interval)
            with self.lock:
                svcs = list(self.services.values())
            list(self.pool.map(self._check, svcs))

    # ---- merging -------------------------------------------------------------

    def merged(self) -> tuple[dict[str, Host], list[Endpoint]]:
        """Combine every provider's hosts into one set. A Docker engine or a
        static host that is also a Proxmox guest (same IP, or the engine's
        hostname is the guest's name) is folded into that guest."""
        with self.lock:
            invs = list(self.inventories.values())
        hosts: dict[str, Host] = {}
        for inv in invs:
            for h in inv.hosts:
                hosts[h.id] = copy.deepcopy(h)
        guests = [h for h in hosts.values() if h.kind in ("lxc", "qemu", "node")]
        by_addr = {h.address: h for h in guests if h.address}
        by_name = {h.name.lower(): h for h in guests}
        remap: dict[str, str] = {}
        for h in list(hosts.values()):
            if h.kind not in ("docker", "host"):
                continue
            target = by_addr.get(h.address) if h.address else None
            if target is None and h.kind == "docker":
                target = by_name.get(h.name.lower())
            if target is not None:
                target.containers += h.containers
                target.aliases += [a for a in h.aliases if a != target.name]
                if h.metrics.get("docker_version"):
                    target.metrics["docker_version"] = h.metrics["docker_version"]
                remap[h.id] = target.id
                del hosts[h.id]
        endpoints = [replace(e, host_id=remap.get(e.host_id, e.host_id)) for inv in invs for e in inv.endpoints]

        # Hidden hosts vanish entirely: no card, no probing, no services.
        hide_names = {n.lower() for n in self.cfg.hide.get("hosts", [])}
        hidden = {h.id for h in hosts.values()
                  if h.name.lower() in hide_names or (h.address or "") in hide_names
                  or any(t.lower() in ("sett-hide", "sett.hide") for t in h.tags)}
        hidden |= {h.id for h in hosts.values() if h.parent in hidden}
        for hid in hidden:
            del hosts[hid]
        endpoints = [e for e in endpoints if e.host_id not in hidden]
        return hosts, endpoints

    # ---- identification ------------------------------------------------------

    def identify(self, hosts: dict[str, Host], endpoints: list[Endpoint]) -> None:
        targets: dict[tuple[str, int], dict] = {}

        # 1. Published Docker ports: we know exactly what is there.
        for e in endpoints:
            h = hosts.get(e.host_id)
            known = bool(h and h.address)
            addr = h.address if known else self.local_addr
            if not addr:
                continue
            ports = [e.port]
            if e.port == 0:  # host-networked container: try its app's default port
                ports = [a.ports[0] for a in apps.candidates(0, e.hint, e.labels.get("sett.app"))[:1] if a.ports]
            for port in ports:
                targets[(addr, port)] = {"host": h.id if h else None, "ep": e, "linkable": known or self.local_linkable}

        # 2. Well-known ports on every host with an address (guests whose Docker we cannot see, a NAS...).
        scan = [(h, p) for h in hosts.values()
                if h.address and h.status in ("running", "online", "unknown")
                for p in apps.all_ports() if (h.address, p) not in targets]
        for (h, p), ok in zip(scan, self.pool.map(lambda hp: net.tcp_open(hp[0].address, hp[1]), scan)):
            if ok:
                targets[(h.address, p)] = {"host": h.id, "ep": None, "linkable": True}

        # 3. Fingerprint every open target.
        cache: dict = {}
        found = [s for s in self.pool.map(lambda kv: self._fingerprint(kv[0], kv[1], cache), targets.items()) if s]
        found = _one_per_host(found)

        now = time.time()
        hidden = set(self.cfg.hide.get("apps", []))
        hidden_names = {n.lower() for n in self.cfg.hide.get("names", [])}
        new = {s.id: s for s in found if s.app not in hidden and s.name.lower() not in hidden_names}
        for s in self._manual_services():
            new[s.id] = s
        with self.lock:
            for sid, s in new.items():
                old = self.services.get(sid)
                if old and old.app == s.app:  # keep the last stats so cards do not flicker
                    s.stats, s.note = old.stats, old.note
                    s.version = s.version or old.version
                    s.down_since = old.down_since
            for sid, old in self.services.items():
                if sid in new or old.manual or old.host_id not in hosts:
                    continue
                since = old.missing_since or now
                if now - since < MISSING_GRACE:
                    old.status, old.missing_since = "down", since
                    old.down_since = old.down_since or since
                    new[sid] = old
            self.services = new
            self.identified_at = now
        # Fill stats straight away rather than waiting for the first check tick.
        list(self.pool.map(self._check, [s for s in new.values() if s.checked_at is None or not s.stats]))

    def _fingerprint(self, key: tuple[str, int], t: dict, cache: dict) -> Service | None:
        addr, port = key
        e: Endpoint | None = t["ep"]
        hint = e.hint if e else ""
        forced = e.labels.get("sett.app") if e else None
        named_by_image = None
        for app in apps.candidates(port, hint, forced):
            trusted = e is not None and (forced or app.hinted_by(hint))
            if trusted and named_by_image is None:
                named_by_image = app
            pr = apps.probe(app, addr, port, cache)
            if pr is None:
                continue
            if pr.matched or trusted:
                return self._service(app, addr, port, t, pr)
        if named_by_image is not None and named_by_image.kind == "http":
            # The image says what it is but it did not answer in time (busy, starting up):
            # keep the identity and let the regular checks fill in status and stats.
            return self._service(named_by_image, addr, port, t, None)
        if e is not None and e.container:
            # A container we cannot name: still show it, so nothing published is invisible.
            got = apps.fetch(addr, port, "/", "http", cache)
            image = e.hint.split()[0].split("/")[-1].split(":")[0]
            return Service(
                id=f"{addr}:{port}", key=_key("container", t["host"], e.container, f"{addr}:{port}"),
                app="container", name=e.labels.get("sett.name") or e.container,
                category=e.labels.get("sett.category") or "Containers", icon=e.labels.get("sett.icon") or image,
                description=e.labels.get("sett.description"), host_id=t["host"], address=addr, port=port,
                scheme=got[0] if got else "tcp", container=e.container,
                url=e.labels.get("sett.url") or (f"{got[0]}://{addr}:{port}" if got and t["linkable"] else None),
                status="up", latency_ms=got[1].elapsed_ms if got else None,
            )
        return None

    def _service(self, app, addr, port, t, pr) -> Service:
        e: Endpoint | None = t["ep"]
        labels = e.labels if e else {}
        url = labels.get("sett.url")
        scheme = pr.scheme if pr else app.scheme
        if not url and t["linkable"] and app.kind == "http":
            url = f"{scheme}://{addr}:{port}{app.link_path}"
        return Service(
            id=f"{addr}:{port}", key=_key(app.id, t["host"], str(port) if app.multi else "", f"{addr}:{port}"),
            app=app.id, name=labels.get("sett.name") or app.display_name(e.hint if e else ""),
            category=labels.get("sett.category") or app.category, icon=labels.get("sett.icon") or app.icon,
            description=labels.get("sett.description"), host_id=t["host"], address=addr, port=port,
            scheme=scheme, url=url, container=e.container if e else None,
            status="up" if pr else "unknown", latency_ms=pr.latency_ms if pr else None,
            version=pr.version if pr else None, matched=bool(pr and pr.matched),
        )

    def _manual_services(self) -> list[Service]:
        """[[service]] link cards and [[card]] JSON-fed cards from the config.
        `check` is the URL whose answer decides up/down (defaults to `url`;
        false means none); a card's `data` URL doubles as its check."""
        out = []
        for kind, items in (("manual", self.cfg.services), ("card", self.cfg.cards)):
            for d in items:
                if not d.get("name"):
                    continue
                check = d.get("data") if kind == "card" else d.get("check", d.get("url"))
                u = urlparse(check or d.get("url") or "")
                out.append(Service(
                    id=f"{kind}:{d['name']}", app=kind, name=d["name"], category=d.get("category", "Links"),
                    icon=d.get("icon", d["name"].lower()), host_id=None, address=u.hostname or "",
                    port=u.port or (443 if u.scheme == "https" else 80), scheme=u.scheme or "http",
                    url=d.get("url"), description=d.get("description"), check=check or None,
                    fields=[list(f) for f in d.get("fields", [])], refresh=float(d.get("refresh", 0)),
                    manual=True,
                ))
        return out

    # ---- checks -------------------------------------------------------------

    def _check(self, s: Service) -> None:
        try:
            if s.manual:
                if s.refresh and s.checked_at and time.time() - s.checked_at < s.refresh:
                    return
                if not s.check:
                    s.status = "unknown"   # a plain link: nothing to check
                elif s.app == "card":
                    r = net.request(s.check, timeout=8, headers={"Accept": "application/json"})
                    data = r.json() if r.status < 400 else None
                    s.status, s.latency_ms = ("up" if isinstance(data, dict) else "down"), r.elapsed_ms
                    s.error = None if data is not None else f"HTTP {r.status}" if r.status >= 400 else "not JSON"
                    if isinstance(data, dict):
                        s.stats = _card_stats(s.fields or [[k, k] for k in list(data)[:4]], data)
                else:
                    r = net.request(s.check, timeout=5)
                    s.status, s.latency_ms = ("up" if r.status < 500 else "down"), r.elapsed_ms
            elif s.app == "container":
                t0 = time.monotonic()
                up = net.tcp_open(s.address, s.port, timeout=2)
                s.status = "up" if up else "down"
                s.latency_ms = int((time.monotonic() - t0) * 1000) if up else None
            else:
                self._check_app(s)
            s.error = None if s.status != "down" else (s.error or "no response")
        except Exception as e:
            s.status, s.error = "down", _reason(e)
        s.checked_at = time.time()
        if s.status == "down":
            s.down_since = s.down_since or s.checked_at
        else:
            s.down_since = None
        if s.status == "up":
            for st in s.stats:
                self.history.record(f"{s.key}|{st.label}", st.value)
            if s.latency_ms is not None:
                self.history.record(f"{s.key}|latency", s.latency_ms)
        self.notify()

    def _check_app(self, s: Service) -> None:
        app = REGISTRY[s.app]
        pr = apps.probe(app, s.address, s.port, scheme=s.scheme if s.scheme != "tcp" else None)
        if pr is None:
            s.status, s.latency_ms, s.error = "down", None, "no response"
            return
        up = pr.up
        s.status = "up" if up else "down"
        s.error = None if up else f"HTTP {pr.resp.status}"
        s.latency_ms = pr.latency_ms
        s.version = pr.version or s.version
        s.missing_since = None
        if not up:
            return
        if pr.raw is not None:
            s.stats = apps.raw_stats(app.kind, pr.raw)
            return
        secret = self.cfg.secret(app.id)
        if app.stats:
            ctx = Ctx(s.base, secret, self.svc_cache[s.id])
            try:
                s.stats = app.stats(ctx)
                s.version = ctx.version or s.version
                s.note = None
            except Exception as e:
                s.note = f"stats unavailable: {e}"[:160]
        if not s.stats and app.needs and not secret:
            s.note = f"add {app.needs} to secrets for stats"

    # ---- state for the UI ----------------------------------------------------

    def state(self) -> dict:
        hosts, _ = self.merged()
        with self.lock:
            services = list(self.services.values())

        by_container = {(h.id, c.name): c for h in hosts.values() for c in h.containers}
        dupes = Counter(s.name for s in services)
        same_host = defaultdict(set)
        for s in services:
            same_host[s.name].add(s.host_id)
        svc_out = []
        for s in services:
            d = s.to_dict()
            for k in ("check", "fields", "refresh", "missing_since", "matched"):
                d.pop(k, None)
            h = hosts.get(s.host_id)
            d["host"] = h.name if h else None
            c = by_container.get((s.host_id, s.container)) if s.container else None
            d["health"] = c.health if c else ""
            if dupes[s.name] > 1 and not s.manual:
                d["name"] = f"{s.name} · {d['host']}" if len(same_host[s.name]) > 1 and d["host"] else f"{s.name} :{s.port}"
            if not s.manual and f"{s.address}:{s.port}" in self.cfg.links:
                d["url"] = self.cfg.links[f"{s.address}:{s.port}"]
            o = self._override(s, d)
            if o.get("hide"):
                continue
            d.update({k: o[k] for k in ("name", "description", "url", "category", "icon") if k in o})
            d["stats"] = _shape_stats(s, o)
            row = self.brock.find(s.key, d["name"], s.name) if self.brock else None
            d["uptime"] = badge(row, self.cfg.brock, o, self.brock.url + "/") if row else None
            svc_out.append(d)

        nodes = sorted((h for h in hosts.values() if h.kind == "node"), key=lambda h: h.name)
        guests = defaultdict(list)
        others = []
        for h in hosts.values():
            if h.kind == "node":
                continue
            (guests[h.parent] if h.parent in hosts else others).append(h)
        node_out = []
        for n in nodes:
            nd = n.to_dict()
            nd["guests"] = [g.to_dict() for g in sorted(guests[n.id], key=lambda g: g.vmid or 0)]
            node_out.append(nd)

        guest_list = [g for gs in guests.values() for g in gs]
        containers = [c for h in hosts.values() for c in h.containers]
        return {
            "title": self.cfg.title,
            "theme": self.cfg.theme,
            "generated_at": time.time(),
            "identified_at": self.identified_at,
            "summary": {
                "nodes": len(nodes),
                "nodes_online": sum(n.status == "online" for n in nodes),
                "guests": len(guest_list),
                "guests_running": sum(g.status == "running" for g in guest_list),
                "containers": len(containers),
                "containers_running": sum(c.state == "running" for c in containers),
                "services": sum(d["status"] in ("up", "down") for d in svc_out),
                "services_up": sum(d["status"] == "up" for d in svc_out),
            },
            "nodes": node_out,
            "hosts": [h.to_dict() for h in sorted(others, key=lambda h: h.name)],
            "services": sorted(svc_out, key=lambda d: (d["category"], d["name"].lower())),
            "categories": self.cfg.categories,
            "bookmarks": self.cfg.bookmarks,
            "weather": self.weather,
            "search": self.cfg.search,
            "views": self.cfg.views,
            "ui": self.cfg.ui,
            "config_error": self.config_error,
            "providers": list(self.pstatus.values()) + ([{
                "name": "brock", "ok": self.brock.ok, "error": self.brock.error, "at": self.brock.at,
                "detail": self.brock.describe() if self.brock.ok else ""}] if self.brock else []),
        }

    def icon_urls(self) -> set[str]:
        """Icon URLs something on the page can ask for: a card's icon (labels,
        [[service]], [[card]]) or an override's. /icons/url fetches only these,
        so it cannot be used to make Sett download arbitrary addresses."""
        with self.lock:
            names = {s.icon for s in self.services.values()}
        names |= {o.get("icon") for o in self.cfg.overrides.values() if isinstance(o, dict)}
        names |= {d.get("icon") for d in self.cfg.services + self.cfg.cards + self.cfg.bookmarks}
        return {n for n in names if isinstance(n, str) and n.startswith(("http://", "https://"))}

    def _override(self, s: Service, d: dict) -> dict:
        """[override."<key>"] from the config, most specific key first:
        "app@host", the card's display name, then the app id."""
        ov = self.cfg.overrides
        for key in (f"{s.app}@{d['host']}", d["name"], s.name, s.app):
            if key in ov:
                return ov[key]
        return {}

    # ---- weather -------------------------------------------------------------

    def _weather_loop(self, gen: int) -> None:
        w = self.cfg.weather
        imperial = w.get("units", "metric") == "imperial"
        q = (f"latitude={w['latitude']}&longitude={w['longitude']}&timezone=auto&forecast_days=1"
             "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min"
             + ("&temperature_unit=fahrenheit" if imperial else ""))
        while gen == self.gen:
            try:
                j = net.get_json(f"https://api.open-meteo.com/v1/forecast?{q}", timeout=10)
                cur, day = j["current"], j["daily"]
                self.weather = {
                    "label": w.get("label", ""), "unit": "°F" if imperial else "°C",
                    "temp": round(cur["temperature_2m"]), "text": _wmo(cur["weather_code"]),
                    "high": round(day["temperature_2m_max"][0]), "low": round(day["temperature_2m_min"][0]),
                }
                self.notify()
            except Exception as e:
                log.info("weather: %s", e)
            time.sleep(900)


def _wmo(code: int) -> str:
    """WMO weather code -> words (open-meteo's `weather_code`)."""
    for codes, text in (((0,), "Clear"), ((1,), "Mostly clear"), ((2,), "Partly cloudy"), ((3,), "Cloudy"),
                        ((45, 48), "Fog"), ((51, 53, 55, 56, 57), "Drizzle"), ((61, 63, 65, 66, 67), "Rain"),
                        ((71, 73, 75, 77), "Snow"), ((80, 81, 82), "Showers"), ((85, 86), "Snow showers"),
                        ((95, 96, 99), "Thunderstorms")):
        if code in codes:
            return text
    return ""


class _Blank(dict):
    def __missing__(self, key):
        return ""


def _shape_stats(s: Service, o: dict) -> list[dict]:
    """Apply an override's stat settings, in this order:
        tones  = { Missing = { warn_above = 0 } }   rule tables, by the stat's own label
        rename = { Queue = "Downloading" }
        stats  = ["Downloading", "Missing"]       which to show, in this order (new or old names)
    Each stat keeps `series`, its history key, so a rename never breaks a sparkline."""
    tones, rename, pick = o.get("tones", {}), o.get("rename", {}), o.get("stats")
    out = []
    for st in s.stats:
        tone = apply_rules(st, tones[st.label]) if st.label in tones else st.tone
        out.append({"label": rename.get(st.label, st.label), "orig": st.label, "value": st.value,
                    "tone": tone, "series": f"{s.key}|{st.label}"})
    if pick is not None:
        by = {**{d["orig"]: d for d in out}, **{d["label"]: d for d in out}}
        out = [by[n] for n in pick if n in by]
    for d in out:
        del d["orig"]
    return out


def _card_stats(fields: list, data: dict) -> list[Stat]:
    """fields: [[spec, label], ...]. A spec is a JSON key, or a template such
    as "{media_free_gb} GB"; labels may be templates too ("{a_label}")."""
    flat = _Blank({k: ("" if v is None else v) for k, v in data.items()})
    out = []
    for f in fields:
        spec, label = (f + [f[0]])[:2]
        value = spec.format_map(flat) if "{" in spec else flat[spec]
        if isinstance(value, float):
            value = f"{value:,.1f}"
        elif isinstance(value, int) and not isinstance(value, bool):
            value = f"{value:,}"
        st = Stat(label.format_map(flat) if "{" in label else label, str(value))
        if len(f) > 2 and isinstance(f[2], dict):
            st.tone = apply_rules(st, f[2])
        out.append(st)
    return out


def _reason(e: Exception) -> str:
    """Words for a failed check: "Connection refused", not "<urlopen error [Errno 111] ...>"."""
    r = getattr(e, "reason", None) or e
    text = getattr(r, "strerror", None) or str(r)
    return (text if isinstance(text, str) else str(r))[:160] or type(e).__name__


def _key(app: str, host_id: str | None, part: str, fallback: str) -> str:
    """A service's stable identity: the app (or container) on a host, which an
    IP change from DHCP or a remapped port does not alter. Without a host the
    address is all there is."""
    if not host_id:
        return fallback
    return f"{app}@{host_id}" + (f"/{part}" if part else "")


def _rank(s: Service, app) -> tuple:
    """Lower is better: a port that answered as the app, then Docker-sourced, then the app's preferred port."""
    return (not s.matched, s.container is None, app.ports.index(s.port) if s.port in app.ports else 99)


def _one_per_host(found: list[Service]) -> list[Service]:
    """An app answering on two ports of one host (DSM on 5000 and 5001) is one
    service: keep the Docker-sourced one, else the app's preferred port."""
    best: dict[tuple, Service] = {}
    out = []
    # A container that already has a named app (Caddy on :80, qBittorrent inside gluetun)
    # needs no extra "unknown port" cards; an unnamed container gets one card, not one per port.
    named = {(s.host_id, s.container) for s in found if s.app != "container" and s.container}
    for s in sorted(found, key=lambda s: s.port):
        if s.app == "container":
            if (s.host_id, s.container) not in named and ("c", s.host_id, s.container) not in best:
                best[("c", s.host_id, s.container)] = s
            continue
        app = REGISTRY.get(s.app)
        if app is None or app.multi:
            out.append(s)
            continue
        key = (s.host_id or s.address, s.app)
        cur = best.get(key)
        if cur is None or _rank(s, app) < _rank(cur, app):
            best[key] = s
    return out + list(best.values())
