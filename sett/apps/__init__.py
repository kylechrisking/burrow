"""App catalog and the probing logic shared by identify and check."""
from __future__ import annotations

import re
import urllib.error

from .. import net
from ..model import Stat
from . import arr, dashboards, home, infra, media, monitoring, platform, productivity  # noqa: F401  (registers apps)
from . import minecraft, mqtt
from .base import REGISTRY, App

# Apps that answer on many hosts (a reverse proxy) are tried after specific ones.
GENERIC = {"caddy"}


def all_ports() -> set[int]:
    return {p for a in REGISTRY.values() for p in a.ports}


def candidates(port: int, hint: str = "", forced: str | None = None) -> list[App]:
    if forced and forced in REGISTRY:
        return [REGISTRY[forced]]
    hinted = [a for a in REGISTRY.values() if hint and a.images and a.hinted_by(hint)]
    by_port = [a for a in REGISTRY.values() if port in a.ports and a not in hinted]
    by_port.sort(key=lambda a: a.id in GENERIC)
    return hinted + by_port


class Probe:
    """Result of asking an address:port for an app's probe path."""

    def __init__(self, app: App, scheme: str, resp: net.Response | None = None,
                 raw: dict | None = None, latency_ms: int | None = None):
        # raw: the decoded answer of a non-HTTP protocol (Minecraft status, MQTT CONNACK)
        self.app, self.scheme, self.resp, self.raw = app, scheme, resp, raw
        self.latency_ms = latency_ms if latency_ms is not None else (resp.elapsed_ms if resp else None)

    @property
    def matched(self) -> bool:
        if self.raw is not None:
            return True
        return self.resp is not None and self.app.match is not None and self.app.match(self.resp)

    @property
    def up(self) -> bool:
        return self.raw is not None or (self.resp is not None and self.resp.status < 500)

    @property
    def version(self) -> str | None:
        if self.raw is not None:
            return (self.raw.get("version") or {}).get("name") if self.app.kind == "minecraft" else None
        if self.resp is not None and self.app.version:
            try:
                return self.app.version(self.resp)
            except Exception:
                return None
        return None


_HTTPS_HINT = re.compile(rb"plain http request was sent to https|client sent an http request to an https server", re.I)


def fetch(address: str, port: int, path: str, scheme: str, cache: dict | None = None,
          timeout: float = 4.0) -> tuple[str, net.Response] | None:
    """GET path, trying the preferred scheme and then the other one.
    Returns (scheme_used, response) or None if nothing answered over HTTP."""
    order = [scheme, "https" if scheme == "http" else "http"]
    for sch in order:
        key = (sch, address, port, path)
        if cache is not None and key in cache:
            r = cache[key]
        else:
            try:
                r = net.request(f"{sch}://{address}:{port}{path}", timeout=timeout)
            except (urllib.error.URLError, OSError, ValueError):
                r = None
            if cache is not None:
                cache[key] = r
        if r is None:
            continue
        if r.status == 400 and _HTTPS_HINT.search(r.body):
            continue
        return sch, r
    return None


def probe(app: App, address: str, port: int, cache: dict | None = None, scheme: str | None = None) -> Probe | None:
    try:
        if app.kind == "minecraft":
            status, ms = minecraft.ping(address, port)
            return Probe(app, "tcp", raw=status, latency_ms=ms)
        if app.kind == "mqtt":
            rc, ms = mqtt.connack(address, port)
            return Probe(app, "tcp", raw={"rc": rc}, latency_ms=ms)
    except (OSError, ValueError):
        return None
    got = fetch(address, port, app.probe, scheme or app.scheme, cache)
    if not got:
        return None
    return Probe(app, got[0], got[1])


def raw_stats(kind: str, raw: dict) -> list[Stat]:
    """Stats for the non-HTTP protocols, straight from their handshake."""
    if kind == "minecraft":
        p = raw.get("players", {})
        online = p.get("online", 0)
        out = [Stat("Players", f"{online}/{p.get('max', '?')}", "ok" if online else "")]
        names = [x.get("name") for x in p.get("sample", []) or [] if x.get("name")]
        if names:
            out.append(Stat("Online", ", ".join(names[:4])))
        m = minecraft.motd(raw.get("description"))
        if m:
            out.append(Stat("MOTD", m[:60]))
        return out
    if kind == "mqtt":
        rc = raw.get("rc", -1)
        return [Stat("Access", mqtt.describe(rc), "warn" if rc == 0 else "")]
    return []
