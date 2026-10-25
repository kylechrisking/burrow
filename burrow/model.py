"""Plain data types shared by providers, apps and the engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Container:
    name: str
    image: str
    state: str          # running / exited / ...
    status: str = ""    # "Up 3 hours (healthy)"


@dataclass
class Storage:
    name: str
    used: int
    total: int
    type: str = ""


@dataclass
class Host:
    id: str                      # provider-unique, e.g. "pve:lxc/131", "docker:ai"
    name: str
    kind: str                    # node | lxc | qemu | docker | host
    status: str = "unknown"      # running / stopped / online / offline / unknown
    address: str | None = None   # LAN IP used for probing and links
    parent: str | None = None    # host id of the node a guest runs on
    source: str = ""
    vmid: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)  # cpu (0-1), mem, maxmem, disk, maxdisk, uptime, cores
    storage: list[Storage] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)       # other names this host is known by (Docker /info Name)
    tags: list[str] = field(default_factory=list)          # Proxmox tags; "burrow-hide" hides the host

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Endpoint:
    """A port worth fingerprinting."""
    host_id: str
    port: int
    hint: str = ""                # image name, container name — anything that names the app
    container: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    source: str = ""


@dataclass
class Inventory:
    hosts: list[Host] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)


@dataclass
class Stat:
    label: str
    value: str
    tone: str = ""   # "" | ok | warn | bad — lets a card highlight a number


@dataclass
class Service:
    id: str
    app: str
    name: str
    category: str
    icon: str
    host_id: str | None
    address: str
    port: int
    scheme: str = "http"
    url: str | None = None        # link shown on the card
    container: str | None = None
    status: str = "unknown"       # up / down / unknown
    latency_ms: int | None = None
    version: str | None = None
    stats: list[Stat] = field(default_factory=list)
    note: str | None = None       # e.g. "add an API key for stats"
    error: str | None = None
    checked_at: float | None = None
    missing_since: float | None = None
    manual: bool = False
    description: str | None = None
    check: str | None = None          # manual cards: URL that decides up/down
    fields: list | None = None        # [[card]]: [[spec, label], ...]
    refresh: float = 0                # [[card]]: seconds between fetches (0 = every check)
    matched: bool = True              # fingerprint matched (False: trusted on the image name alone)

    @property
    def base(self) -> str:
        return f"{self.scheme}://{self.address}:{self.port}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["host"] = None
        return d
