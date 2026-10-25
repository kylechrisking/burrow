"""Tiny HTTP/TCP helpers on top of the standard library."""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "burrow/0.1 (+https://github.com/kylechrisking/burrow)"

_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    elapsed_ms: int = 0

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    @property
    def title(self) -> str:
        m = re.search(rb"<title[^>]*>(.*?)</title>", self.body, re.I | re.S)
        return m.group(1).decode("utf-8", "replace").strip() if m else ""

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # surface 3xx to the caller
        return None


_opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_insecure))
_opener_follow = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_insecure))


def request(url: str, *, method: str = "GET", headers: dict | None = None, data: bytes | None = None,
            timeout: float = 4.0, follow: bool = True, max_bytes: int = 512 * 1024) -> Response:
    """HTTP request that never raises on status codes. TLS is not verified —
    lab services almost all use self-signed certificates."""
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.monotonic()
    opener = _opener_follow if follow else _opener
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read(max_bytes)
            status, hdrs = r.status, r.headers
    except urllib.error.HTTPError as e:
        body = e.read(max_bytes) if e.fp else b""
        status, hdrs = e.code, e.headers
    return Response(url, status, {k.lower(): v for k, v in (hdrs or {}).items()}, body,
                    int((time.monotonic() - t0) * 1000))


def get_json(url: str, **kw):
    kw.setdefault("headers", {})
    kw["headers"].setdefault("Accept", "application/json")
    r = request(url, **kw)
    if r.status >= 400:
        raise RuntimeError(f"HTTP {r.status} from {url.split('?')[0]}")
    j = r.json()
    if j is None:
        raise RuntimeError(f"non-JSON reply from {url.split('?')[0]}")
    return j


def tcp_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class UnixHTTPConnection(http.client.HTTPConnection):
    """http.client over a unix socket, for the Docker API."""

    def __init__(self, path: str, timeout: float = 5.0):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def default_gateway() -> str | None:
    """IPv4 default gateway from /proc (Linux). Inside a bridged container this
    is the Docker host, which is how a local socket's published ports are reached."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000":
                    return socket.inet_ntoa(bytes.fromhex(parts[2])[::-1])
    except OSError:
        pass
    return None
