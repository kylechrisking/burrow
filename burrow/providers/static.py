"""Static hosts: plain IPs, names, or whole subnets to fingerprint.

    [scan]
    hosts = ["192.168.1.123", "nas.lan", "192.168.1.0/24"]

A subnet is swept for any port an app is known to use, and only addresses
that answer become hosts, named by reverse DNS where it resolves. That is
the zero-config path for a lab without Proxmox or a reachable Docker API.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor

from .. import net
from ..config import Config
from ..model import Host, Inventory
from . import Provider

log = logging.getLogger(__name__)

MAX_SWEEP = 1024   # addresses per run; a /22


class StaticProvider(Provider):
    name = "static"
    interval = 1800

    def __init__(self, hosts: list[str]):
        self.plain = [h for h in hosts if "/" not in h]
        self.networks = []
        for h in hosts:
            if "/" in h:
                try:
                    self.networks.append(ipaddress.ip_network(h, strict=False))
                except ValueError:
                    log.warning("scan: %r is not a network", h)
        self._summary = ""

    @classmethod
    def from_config(cls, cfg: Config):
        hosts = cfg.scan.get("hosts") or []
        return [cls(hosts)] if hosts else None

    def discover(self) -> Inventory:
        hosts = [Host(id=f"static:{h}", name=h, kind="host", address=h, source=self.name) for h in self.plain]
        found = self._sweep()
        known = set(self.plain)
        for ip, name in found:
            if ip not in known:
                hosts.append(Host(id=f"static:{ip}", name=name or ip, kind="host", address=ip, source=self.name))
        swept = sum(min(n.num_addresses, MAX_SWEEP) for n in self.networks)
        self._summary = f"{len(hosts)} hosts" + (f" ({len(found)} live of {swept} swept)" if self.networks else "")
        return Inventory(hosts=hosts)

    def _sweep(self) -> list[tuple[str, str | None]]:
        if not self.networks:
            return []
        from ..apps import all_ports   # late import: apps import providers' config indirectly
        ports = sorted(all_ports() | {22, 80, 443})
        addrs = []
        for n in self.networks:
            hosts = list(n.hosts()) if n.num_addresses > 2 else list(n)
            if len(hosts) > MAX_SWEEP:
                log.warning("scan: %s has %d addresses; sweeping the first %d", n, len(hosts), MAX_SWEEP)
            addrs += [str(a) for a in hosts[:MAX_SWEEP]]

        def alive(ip: str) -> bool:
            return any(net.tcp_open(ip, p, timeout=0.3) for p in ports)

        with ThreadPoolExecutor(max_workers=128, thread_name_prefix="sweep") as pool:
            live = [ip for ip, ok in zip(addrs, pool.map(alive, addrs)) if ok]
            names = list(pool.map(_rdns, live))
        return list(zip(live, names))

    def describe(self) -> str:
        return self._summary


def _rdns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0]
    except OSError:
        return None
