"""Docker: containers and their published ports, read-only.

Endpoints are "unix:///var/run/docker.sock" (auto-detected when mounted) or
"tcp://host:2375" — for remote engines put tecnativa/docker-socket-proxy in
front with CONTAINERS=1 INFO=1, so nothing can write.

Labels on a container override discovery:
    labdash.name, labdash.app, labdash.url, labdash.hide=true
"""
from __future__ import annotations

import json
import logging
import os
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
        for c in self._get("/containers/json?all=1"):
            name = (c.get("Names") or ["?"])[0].lstrip("/")
            labels = c.get("Labels") or {}
            if labels.get("labdash.hide", "").lower() == "true":
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
            if not seen and state == "running" and (labels.get("labdash.app") or c.get("HostConfig", {}).get("NetworkMode") == "host"):
                # host-networked: no published ports, let the hint pick the app's default port
                endpoints.append(Endpoint(hid, 0, hint=f"{image} {name}", container=name,
                                          labels=labels, source=self.name))
        self._summary = f"{engine}: {running} running"
        return Inventory(hosts=[host], endpoints=endpoints)

    def describe(self) -> str:
        return self._summary
