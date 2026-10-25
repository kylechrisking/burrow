"""HTTP server: the static UI, the theme list and /api/state."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import Engine

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"
THEMES = WEB / "themes"
NAME_RE = re.compile(r"/\*\s*name:\s*(.+?)\s*\*/")


def list_themes() -> list[dict]:
    out = []
    for f in sorted(THEMES.rglob("*.css")):
        m = NAME_RE.search(f.read_text(encoding="utf-8")[:200])
        tid = f.relative_to(THEMES).with_suffix("").as_posix()
        out.append({"id": tid, "name": m.group(1) if m else tid.title()})
    return out


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "burrow"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/state":
                state = engine.state()
                state["themes"] = list_themes()
                return self._json(state)
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path == "/":
                path = "/index.html"
            f = (WEB / path.lstrip("/")).resolve()
            if WEB.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype, "no-cache")

    return Handler


def serve(engine: Engine, port: int) -> None:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(engine))
    httpd.daemon_threads = True
    log.info("listening on :%d", port)
    httpd.serve_forever()
