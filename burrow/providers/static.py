"""Static hosts: plain IPs or names to fingerprint (a NAS, a Pi, a router)."""
from __future__ import annotations

from ..config import Config
from ..model import Host, Inventory
from . import Provider


class StaticProvider(Provider):
    name = "static"
    interval = 3600

    def __init__(self, hosts: list[str]):
        self.hosts = hosts

    @classmethod
    def from_config(cls, cfg: Config):
        hosts = cfg.scan.get("hosts") or []
        return [cls(hosts)] if hosts else None

    def discover(self) -> Inventory:
        return Inventory(hosts=[Host(id=f"static:{h}", name=h, kind="host", address=h, source=self.name)
                                for h in self.hosts])

    def describe(self) -> str:
        return f"{len(self.hosts)} hosts"
