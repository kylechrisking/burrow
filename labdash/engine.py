"""The engine: run providers, merge what they see, identify apps, keep them checked."""
from __future__ import annotations

import copy
import logging
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import urlparse

from . import apps, net
from .apps import minecraft
from .apps.base import REGISTRY, Ctx
from .config import Config
from .model import Endpoint, Host, Service, Stat
from .providers import load_providers

log = logging.getLogger(__name__)

MISSING_GRACE = 3600   # keep a service that vanished from discovery, marked down, for an hour


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
        self.gateway = net.default_gateway()
        self.identified_at: float | None = None
        self.started = time.time()

    # ---- loops -------------------------------------------------------------

    def start(self) -> None:
        log.info("providers: %s", ", ".join(p.name for p in self.providers) or "none (add [proxmox]/[docker]/[scan] config)")
        for p in self.providers:
            threading.Thread(target=self._provider_loop, args=(p,), daemon=True, name=p.name).start()
        threading.Thread(target=self._identify_loop, daemon=True, name="identify").start()
        threading.Thread(target=self._check_loop, daemon=True, name="check").start()

    def _provider_loop(self, p) -> None:
        while True:
            st = self.pstatus[p.name]
            try:
                inv = p.discover()
                with self.lock:
                    self.inventories[p.name] = inv
                st.update(ok=True, error=None, detail=p.describe())
            except Exception as e:  # keep the last good inventory
                log.warning("%s: %s", p.name, e)
                st.update(ok=False, error=str(e)[:200])
            st["at"] = time.time()
            self.changed.set()
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
            if sig != last_sig or time.monotonic() - last_run > self.cfg.identify_interval:
                if not self.providers or self.inventories:
                    t0 = time.monotonic()
                    try:
                        self.identify(hosts, endpoints)
                    except Exception:
                        log.exception("identify failed")
                    log.info("identify: %d services in %.1fs", len(self.services), time.monotonic() - t0)
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
        return hosts, endpoints

    # ---- identification ------------------------------------------------------

    def identify(self, hosts: dict[str, Host], endpoints: list[Endpoint]) -> None:
        targets: dict[tuple[str, int], dict] = {}

        # 1. Published Docker ports: we know exactly what is there.
        for e in endpoints:
            h = hosts.get(e.host_id)
            addr = h.address if h and h.address else self.gateway
            if not addr:
                continue
            ports = [e.port]
            if e.port == 0:  # host-networked container: try its app's default port
                ports = [a.ports[0] for a in apps.candidates(0, e.hint, e.labels.get("labdash.app"))[:1] if a.ports]
            for port in ports:
                targets[(addr, port)] = {"host": h.id if h else None, "ep": e, "linkable": bool(h and h.address)}

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
            for sid, old in self.services.items():
                if sid in new or old.manual or old.host_id not in hosts:
                    continue
                since = old.missing_since or now
                if now - since < MISSING_GRACE:
                    old.status, old.missing_since = "down", since
                    new[sid] = old
            self.services = new
            self.identified_at = now
        # Fill stats straight away rather than waiting for the first check tick.
        list(self.pool.map(self._check, [s for s in new.values() if s.checked_at is None or not s.stats]))

    def _fingerprint(self, key: tuple[str, int], t: dict, cache: dict) -> Service | None:
        addr, port = key
        e: Endpoint | None = t["ep"]
        hint = e.hint if e else ""
        forced = e.labels.get("labdash.app") if e else None
        for app in apps.candidates(port, hint, forced):
            pr = apps.probe(app, addr, port, cache)
            if pr is None:
                continue
            trusted = e is not None and (forced or app.hinted_by(hint))
            if pr.matched or trusted:
                return self._service(app, addr, port, t, pr)
        if e is not None and e.container:
            # A container we cannot name: still show it, so nothing published is invisible.
            got = apps.fetch(addr, port, "/", "http", cache)
            image = e.hint.split()[0].split("/")[-1].split(":")[0]
            return Service(
                id=f"{addr}:{port}", app="container", name=e.labels.get("labdash.name") or e.container,
                category="Containers", icon=image, host_id=t["host"], address=addr, port=port,
                scheme=got[0] if got else "tcp", container=e.container,
                url=e.labels.get("labdash.url") or (f"{got[0]}://{addr}:{port}" if got and t["linkable"] else None),
                status="up", latency_ms=got[1].elapsed_ms if got else None,
            )
        return None

    def _service(self, app, addr, port, t, pr) -> Service:
        e: Endpoint | None = t["ep"]
        labels = e.labels if e else {}
        url = labels.get("labdash.url")
        if not url and t["linkable"] and app.kind == "http":
            url = f"{pr.scheme}://{addr}:{port}{app.link_path}"
        return Service(
            id=f"{addr}:{port}", app=app.id, name=labels.get("labdash.name") or app.name,
            category=app.category, icon=app.icon, host_id=t["host"], address=addr, port=port,
            scheme=pr.scheme, url=url, container=e.container if e else None,
            status="up", latency_ms=pr.latency_ms, version=pr.version,
        )

    def _manual_services(self) -> list[Service]:
        out = []
        for d in self.cfg.services:
            if not d.get("name") or not d.get("url"):
                continue
            u = urlparse(d["url"])
            out.append(Service(
                id=f"manual:{d['name']}", app="manual", name=d["name"], category=d.get("category", "Links"),
                icon=d.get("icon", d["name"].lower()), host_id=None, address=u.hostname or "",
                port=u.port or (443 if u.scheme == "https" else 80), scheme=u.scheme, url=d["url"], manual=True,
            ))
        return out

    # ---- checks -------------------------------------------------------------

    def _check(self, s: Service) -> None:
        try:
            if s.manual:
                r = net.request(s.url, timeout=5)
                s.status, s.latency_ms = ("up" if r.status < 500 else "down"), r.elapsed_ms
            elif s.app == "container":
                t0 = time.monotonic()
                up = net.tcp_open(s.address, s.port, timeout=2)
                s.status = "up" if up else "down"
                s.latency_ms = int((time.monotonic() - t0) * 1000) if up else None
            else:
                self._check_app(s)
            s.error = None if s.status == "up" else (s.error or "no response")
        except Exception as e:
            s.status, s.error = "down", str(e)[:160]
        s.checked_at = time.time()

    def _check_app(self, s: Service) -> None:
        app = REGISTRY[s.app]
        pr = apps.probe(app, s.address, s.port, scheme=s.scheme if s.scheme != "tcp" else None)
        if pr is None:
            s.status, s.latency_ms, s.error = "down", None, "no response"
            return
        up = pr.mc is not None or pr.resp.status < 500
        s.status = "up" if up else "down"
        s.error = None if up else f"HTTP {pr.resp.status}"
        s.latency_ms = pr.latency_ms
        s.version = pr.version or s.version
        s.missing_since = None
        if not up:
            return
        if pr.mc is not None:
            s.stats = _minecraft_stats(pr.mc)
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

        dupes = Counter(s.name for s in services)
        same_host = defaultdict(set)
        for s in services:
            same_host[s.name].add(s.host_id)
        svc_out = []
        for s in services:
            d = s.to_dict()
            h = hosts.get(s.host_id)
            d["host"] = h.name if h else None
            if dupes[s.name] > 1:
                d["name"] = f"{s.name} · {d['host']}" if len(same_host[s.name]) > 1 and d["host"] else f"{s.name} :{s.port}"
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
                "services": len(services),
                "services_up": sum(s.status == "up" for s in services),
            },
            "nodes": node_out,
            "hosts": [h.to_dict() for h in sorted(others, key=lambda h: h.name)],
            "services": sorted(svc_out, key=lambda d: (d["category"], d["name"].lower())),
            "providers": list(self.pstatus.values()),
        }


def _minecraft_stats(mc: dict) -> list[Stat]:
    p = mc.get("players", {})
    online = p.get("online", 0)
    out = [Stat("Players", f"{online}/{p.get('max', '?')}", "ok" if online else "")]
    names = [x.get("name") for x in p.get("sample", []) or [] if x.get("name")]
    if names:
        out.append(Stat("Online", ", ".join(names[:4])))
    m = minecraft.motd(mc.get("description"))
    if m:
        out.append(Stat("MOTD", m[:60]))
    return out
