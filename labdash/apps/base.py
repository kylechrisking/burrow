"""App fingerprints: how to recognise an app on a port, and what to show for it."""
from __future__ import annotations

import base64
import threading
from dataclasses import dataclass, field
from typing import Callable

from .. import net
from ..model import Stat

Matcher = Callable[[net.Response], bool]


@dataclass
class Ctx:
    """What a stats function gets: the service base URL, its secrets, helpers."""
    base: str
    secret: dict
    cache: dict          # per-service scratch space that survives between checks (session cookies)
    version: str | None = None   # a stats function may set this when it learns the version

    def get(self, path: str, **kw) -> net.Response:
        return net.request(self.base + path, **kw)

    def json(self, path: str, **kw):
        return net.get_json(self.base + path, **kw)


@dataclass
class App:
    id: str
    name: str
    category: str
    ports: list[int]
    probe: str = "/"
    match: Matcher | None = None
    images: list[str] = field(default_factory=list)
    icon: str = ""
    scheme: str = "http"
    kind: str = "http"                       # http | minecraft
    version: Callable[[net.Response], str | None] | None = None
    stats: Callable[[Ctx], list[Stat]] | None = None
    needs: str | None = None                 # hint shown when stats need a secret, e.g. "api_key"
    link_path: str = ""

    def __post_init__(self):
        self.icon = self.icon or self.id

    def hinted_by(self, hint: str) -> bool:
        h = hint.lower()
        return any(i in h for i in self.images)


# ---- matchers ---------------------------------------------------------------

def title(*words: str) -> Matcher:
    ws = [w.lower() for w in words]
    return lambda r: r.status < 500 and any(w in r.title.lower() for w in ws)


def body(*needles: str) -> Matcher:
    ns = [n.lower() for n in needles]
    return lambda r: r.status < 500 and any(n in r.text.lower() for n in ns)


def json_has(key: str, value: str | None = None) -> Matcher:
    def m(r: net.Response) -> bool:
        j = r.json()
        if not isinstance(j, dict) or key not in j:
            return False
        return value is None or value.lower() in str(j[key]).lower()
    return m


def header(name: str, contains: str) -> Matcher:
    return lambda r: contains.lower() in r.header(name).lower()


def any_of(*ms: Matcher) -> Matcher:
    return lambda r: any(m(r) for m in ms)


def json_field(*path: str):
    def v(r: net.Response):
        j = r.json()
        for p in path:
            if not isinstance(j, dict):
                return None
            j = j.get(p)
        return str(j) if j not in (None, "") else None
    return v


def basic_auth(user: str, password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


# ---- formatting --------------------------------------------------------------

def rate(bps: float) -> str:
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1000 or unit == "GB/s":
            return f"{bps:.0f} {unit}" if unit in ("B/s", "KB/s") else f"{bps:.1f} {unit}"
        bps /= 1000
    return ""


def count(n) -> str:
    n = int(n)
    return f"{n/1000:.1f}k" if n >= 10000 else f"{n:,}"


_lock = threading.Lock()
REGISTRY: dict[str, App] = {}


def register(*apps: App) -> None:
    with _lock:
        for a in apps:
            REGISTRY[a.id] = a
