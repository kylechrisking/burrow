"""HTTP server: the static UI, the theme list and /api/state."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import icons
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
        server_version = "sett"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", csp: str | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if csp:
                self.send_header("Content-Security-Policy", csp)
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
            if path == "/api/history":
                return self._json(engine.history.snapshot())
            if path.startswith("/icons/"):
                q = parse_qs(urlparse(self.path).query)
                name = q["u"][0] if path == "/icons/url" and "u" in q else unquote(path[len("/icons/"):])
                got = icons.resolve(name, engine.cfg.config_dir)
                if not got:
                    return self._send(404, b"", "text/plain", "max-age=3600")
                # third-party SVG on our origin: forbid scripts if someone opens it directly
                return self._send(200, got[0], got[1], "max-age=86400", csp="default-src 'none'; style-src 'unsafe-inline'")
            if path in ("/custom.css", "/custom.js"):
                # Your own additions, mounted next to sett.toml. Empty when absent.
                f = engine.cfg.config_dir / path.lstrip("/")
                ctype = "text/css" if path.endswith(".css") else "text/javascript"
                return self._send(200, f.read_bytes() if f.is_file() else b"", ctype + "; charset=utf-8", "no-cache")
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
