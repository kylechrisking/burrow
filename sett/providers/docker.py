"""Docker: containers and their published ports, read-only.

Endpoints are "unix:///var/run/docker.sock" (auto-detected when mounted) or
"tcp://host:2375" — for remote engines put tecnativa/docker-socket-proxy in
front with CONTAINERS=1 INFO=1, so nothing can write.

Labels on a container override discovery:
    sett.name, sett.app, sett.url, sett.description, sett.category,
    sett.icon, sett.hide=true
Homepage, Traefik and caddy-docker-proxy labels are read too (see with_borrowed_labels).
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
from urllib.parse import urlparse

from .. import net
from ..config import Config
from ..model import Container, Endpoint, Host, Inventory
from . import Provider

log = logging.getLogger(__name__)

DEFAULT_SOCKET = "/var/run/docker.sock"


class DockerProvider(Provider):
    interval = 30

    def __init__(self, endpoint: str, address: str | None = None):
        self.endpoint = endpoint
        u = urlparse(endpoint)
        self.socket = u.path if u.scheme == "unix" else None
        self.base = None if self.socket else f"http://{u.hostname}:{u.port or 2375}"
        # Address the published ports are reached on. For tcp:// it is that host;
        # for a local socket the engine tries to match the engine to a Proxmox
        # guest by name, and falls back to our default gateway.
        self.address = address or (u.hostname if not self.socket else None)
        self.name = f"docker:{u.hostname or 'local'}"
        self._summary = ""

    @classmethod
    def from_config(cls, cfg: Config):
        eps = cfg.docker.get("endpoints")
        addresses = cfg.docker.get("addresses", {})
        if eps is None:
            eps = [f"unix://{DEFAULT_SOCKET}"] if os.path.exists(DEFAULT_SOCKET) else []
        return [cls(e, addresses.get(e)) for e in eps]

    def _get(self, path: str):
        if self.socket:
            conn = net.UnixHTTPConnection(self.socket)
            try:
                conn.request("GET", path, headers={"Host": "docker"})
                r = conn.getresponse()
                body = r.read()
                if r.status >= 400:
                    raise RuntimeError(f"docker {path}: HTTP {r.status}")
                return json.loads(body)
            finally:
                conn.close()
        return net.get_json(self.base + path, timeout=5)

    def discover(self) -> Inventory:
        info = self._get("/info")
        engine = info.get("Name") or self.name
        hid = f"docker:{engine}"
        host = Host(
            id=hid, name=engine, kind="docker", status="running", source=self.name,
            address=self.address, aliases=[engine],
            metrics={"maxcpu": info.get("NCPU"), "maxmem": info.get("MemTotal"),
                     "docker_version": info.get("ServerVersion")},
        )
        endpoints: list[Endpoint] = []
        running = 0
        me = socket.gethostname() if self.socket else None   # our own container id prefix
        for c in self._get("/containers/json?all=1"):
            if me and c.get("Id", "").startswith(me):
                continue
            name = (c.get("Names") or ["?"])[0].lstrip("/")
            labels = with_borrowed_labels(c.get("Labels") or {})
            if labels.get("sett.hide", "").lower() == "true":
                continue
            state = c.get("State", "")
            running += state == "running"
            image = c.get("Image", "")
            host.containers.append(Container(name, image, state, c.get("Status", "")))
            seen: set[int] = set()
            for p in c.get("Ports") or []:
                pub = p.get("PublicPort")
                if not pub or p.get("Type") != "tcp" or pub in seen:
                    continue
                seen.add(pub)
                endpoints.append(Endpoint(hid, pub, hint=f"{image} {name}", container=name,
                                          labels=labels, source=self.name))
            if not seen and state == "running" and (labels.get("sett.app") or c.get("HostConfig", {}).get("NetworkMode") == "host"):
                # host-networked: no published ports, let the hint pick the app's default port
                endpoints.append(Endpoint(hid, 0, hint=f"{image} {name}", container=name,
                                          labels=labels, source=self.name))
        self._summary = f"{engine}: {running} running"
        return Inventory(hosts=[host], endpoints=endpoints)

    def describe(self) -> str:
        return self._summary


_HOST_RULE = re.compile(r"Host\(\s*`([^`]+)`")


def with_borrowed_labels(labels: dict[str, str]) -> dict[str, str]:
    """Fill sett.* labels from metadata people already have on their containers,
    so migrating costs nothing. Explicit sett.* labels always win.

    - Homepage:  homepage.name / .description / .icon / .group / .href
    - Traefik:   traefik.http.routers.<r>.rule = Host(`app.example.com`)  -> link
    - caddy-docker-proxy:  caddy = app.example.com                        -> link
    """
    out = dict(labels)

    def put(key: str, value: str | None):
        if value and not out.get(key):
            out[key] = value

    put("sett.name", labels.get("homepage.name"))
    put("sett.description", labels.get("homepage.description"))
    put("sett.category", labels.get("homepage.group"))
    put("sett.url", labels.get("homepage.href"))
    icon = labels.get("homepage.icon", "")
    if icon and "/" not in icon:   # "sonarr.png" -> "sonarr"; URLs and mdi- icons are left alone
        put("sett.icon", re.sub(r"\.(png|svg|webp)$", "", icon))

    # Traefik: prefer a TLS router, else the first one with a Host() rule.
    routers: dict[str, dict] = {}
    for k, v in labels.items():
        m = re.match(r"traefik\.https?\.routers\.([^.]+)\.(rule|tls|entrypoints)$", k)
        if m:
            routers.setdefault(m.group(1), {})[m.group(2)] = v
    best = None
    for r in routers.values():
        host = _HOST_RULE.search(r.get("rule", ""))
        if not host:
            continue
        tls = r.get("tls", "").lower() == "true" or "secure" in r.get("entrypoints", "") or "443" in r.get("entrypoints", "")
        if best is None or (tls and not best[1]):
            best = (host.group(1), tls)
    if best:
        put("sett.url", f"{'https' if best[1] else 'http'}://{best[0]}")

    caddy = labels.get("caddy") or labels.get("caddy_0")
    if caddy:
        first = caddy.split(",")[0].split()[0].strip()
        put("sett.url", first if first.startswith("http") else f"https://{first}")
    return out
