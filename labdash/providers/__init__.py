"""Discovery providers. Each module is self-contained; see docs/design.md."""
from __future__ import annotations

from ..config import Config
from ..model import Inventory


class Provider:
    name: str = "provider"
    interval: float = 30

    @classmethod
    def from_config(cls, cfg: Config) -> "Provider | None":
        raise NotImplementedError

    def discover(self) -> Inventory:
        raise NotImplementedError

    def describe(self) -> str:
        """One line for the footer, e.g. '3 nodes, 9 guests'."""
        return ""


def load_providers(cfg: Config) -> list[Provider]:
    from .docker import DockerProvider
    from .proxmox import ProxmoxProvider
    from .static import StaticProvider

    out: list[Provider] = []
    for cls in (ProxmoxProvider, DockerProvider, StaticProvider):
        out.extend(cls.from_config(cfg) or [])
    return out
