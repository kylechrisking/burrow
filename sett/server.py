"""HTTP server: the static UI, the theme list, /api/state, the live event
stream, and (when [auth] is configured) sign-in."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import icons
from .auth import COOKIE, Auth, _ip_in
from .engine import Engine

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"
THEMES = WEB / "themes"
NAME_RE = re.compile(r"/\*\s*name:\s*(.+?)\s*\*/")
EVENT_PING = 20          # seconds between keep-alive comments on /api/events
EVENT_COALESCE = 0.5     # a burst of checks finishing becomes one message

# Reachable without signing in: the sign-in page and what it needs to look right.
PUBLIC = {"/login", "/login.html", "/login.js", "/app.css", "/favicon.svg", "/custom.css",
          "/healthz", "/api/login", "/api/logout"}


def list_themes() -> list[dict]:
    out = []
    for f in sorted(THEMES.rglob("*.css")):
        m = NAME_RE.search(f.read_text(encoding="utf-8")[:200])
        tid = f.relative_to(THEMES).with_suffix("").as_posix()
        out.append({"id": tid, "name": m.group(1) if m else tid.title()})
    return out


def can_edit(engine: Engine, auth: Auth, user: str | None) -> bool:
    """Card edits: signed in when sign-in is on; otherwise allowed unless [ui] edit = false."""
    if engine.cfg.ui.get("edit") is False:
        return False
    return bool(user) if auth.enabled else True


def full_state(engine: Engine, user: str | None, auth: Auth) -> dict:
    state = engine.state()
    state["themes"] = list_themes()
    state["user"] = user
    state["auth"] = {"login": auth.login_enabled}
    state["can_edit"] = can_edit(engine, auth, user) and engine.edits_file is not None
    return state


def status(engine: Engine) -> dict:
    """A compact up/down view for scripts and monitoring agents."""
    st = engine.state()
    return {
        "generated_at": st["generated_at"],
        "summary": st["summary"],
        "services": [{k: s.get(k) for k in ("key", "name", "app", "category", "host", "url", "status",
                                            "latency_ms", "down_since", "error", "version")}
                     for s in st["services"]],
        "nodes": [{"name": n["name"], "status": n["status"], "health": n.get("health", [])} for n in st["nodes"]],
        "providers": st["providers"],
    }


def make_handler(engine: Engine, auth: Auth | None = None):
    auth = auth or Auth(engine)

    class Handler(BaseHTTPRequestHandler):
        server_version = "sett"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # ---- plumbing ----------------------------------------------------------------

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", csp: str | None = None,
                  headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if csp:
                self.send_header("Content-Security-Policy", csp)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200, headers: dict | None = None):
            self._send(code, json.dumps(obj, separators=(",", ":")).encode(), "application/json", headers=headers)

        def client_ip(self) -> str:
            """The browser's address. X-Forwarded-For is only believed from a
            trusted proxy, and only its last hop (the one that proxy added)."""
            peer = self.client_address[0]
            fwd = self.headers.get("X-Forwarded-For")
            if fwd and _ip_in(peer, auth.conf.get("trusted_proxies") or []):
                return fwd.split(",")[-1].strip()
            return peer

        def https(self) -> bool:
            return (self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                    and _ip_in(self.client_address[0], auth.conf.get("trusted_proxies") or []))

        def _cookie(self, value: str, max_age: int) -> str:
            secure = "; Secure" if self.https() else ""
            return f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}"

        def _body(self) -> dict:
            n = min(int(self.headers.get("Content-Length") or 0), 16384)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}
            return d if isinstance(d, dict) else {}

        def _gate(self, path: str) -> tuple[bool, str | None]:
            """(allowed, user). Sends the refusal itself when not allowed."""
            if not auth.enabled:
                return True, None
            user = auth.identify(self.headers, self.client_address[0])
            if user or path in PUBLIC or path.startswith("/themes/"):
                return True, user
            if path in ("/", "/index.html") and auth.login_enabled:
                self._send(302, b"", "text/plain", headers={"Location": "login"})
            elif path in ("/", "/index.html"):
                # No passwords to sign in with: only the SSO proxy (or a token) gets in.
                self._send(401, b"Sign in through your single sign-on proxy to see this dashboard.\n",
                           "text/plain; charset=utf-8")
            else:
                self._json({"error": "sign in required"}, 401)
            return False, None

        # ---- routes ------------------------------------------------------------------

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/api/edit":
                user = auth.identify(self.headers, self.client_address[0]) if auth.enabled else None
                if not can_edit(engine, auth, user):
                    if auth.enabled and not user and engine.cfg.ui.get("edit") is not False:
                        return self._json({"error": "sign in to edit cards"}, 401)
                    return self._json({"error": "editing cards isn't allowed here"}, 403)
                origin = self.headers.get("Origin")
                if (self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"
                        or (origin and urlparse(origin).netloc != self.headers.get("Host", ""))):
                    return self._json({"error": "send JSON from the dashboard"}, 403)
                d = self._body()
                try:
                    engine.set_edit(str(d.get("key", ""))[:200], d.get("fields"))
                except (ValueError, OSError) as e:
                    return self._json({"error": str(e)}, 422)
                return self._json({"ok": True})
            if path == "/api/login":
                if not auth.login_enabled:
                    return self._json({"ok": False, "error": "sign-in is not configured"}, 404)
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    return self._json({"ok": False, "error": "expected JSON"}, 415)   # no cross-site form posts
                d = self._body()
                user, pw, code = str(d.get("user", ""))[:64], str(d.get("password", ""))[:256], str(d.get("code", ""))[:12]
                ok, msg = auth.login(user, pw, code, self.client_ip())
                if ok:
                    value, max_age = auth.make_session(user)
                    return self._json({"ok": True}, headers={"Set-Cookie": self._cookie(value, max_age)})
                if msg == "code required":
                    return self._json({"ok": False, "code": True}, 401)
                return self._json({"ok": False, "error": msg}, 429 if "Too many" in msg else 401)
            if path == "/api/logout":
                return self._json({"ok": True}, headers={"Set-Cookie": self._cookie("", 0)})
            self._json({"error": "not found"}, 404)

        def do_GET(self):
            path = self.path.split("?")[0]
            allowed, user = self._gate(path)
            if not allowed:
                return
            if path == "/api/state":
                return self._json(full_state(engine, user, auth))
            if path == "/api/events":
                return self._events(user)
            if path == "/api/status":
                return self._json(status(engine))
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path == "/api/history":
                return self._json(engine.history.snapshot())
            if path == "/login":
                path = "/login.html"
            if path == "/login.html" and (user or not auth.login_enabled):
                return self._send(302, b"", "text/plain", headers={"Location": "./"})
            if path.startswith("/icons/"):
                q = parse_qs(urlparse(self.path).query)
                name = q["u"][0] if path == "/icons/url" and "u" in q else unquote(path[len("/icons/"):])
                if name.startswith(("http://", "https://")) and name not in engine.icon_urls():
                    return self._send(404, b"", "text/plain", "max-age=60")
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

        def _events(self, user: str | None):
            """Server-sent events: the full state whenever it changes, a comment
            every EVENT_PING seconds so proxies keep the connection open."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")   # nginx: do not buffer the stream
            self.end_headers()
            last, seen = "", -1
            try:
                while True:
                    seen = engine.wait_for_change(seen, EVENT_PING)
                    if auth.enabled and not auth.identify(self.headers, self.client_address[0]):
                        self.wfile.write(b"event: signed-out\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    time.sleep(EVENT_COALESCE)
                    seen = engine.version
                    st = full_state(engine, user, auth)
                    sig = json.dumps({**st, "generated_at": 0}, separators=(",", ":"))
                    if sig == last:
                        self.wfile.write(b": ping\n\n")
                    else:
                        last = sig
                        self.wfile.write(b"data: " + json.dumps(st, separators=(",", ":")).encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

    return Handler


def serve(engine: Engine, port: int) -> None:
    auth = Auth(engine)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(engine, auth))
    httpd.daemon_threads = True
    log.info("listening on :%d%s", port, " (sign-in required)" if auth.enabled else "")
    httpd.serve_forever()
