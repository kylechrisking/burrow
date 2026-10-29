"""Optional sign-in: per-user passwords with optional TOTP two-factor codes,
a trusted reverse-proxy header (Authelia, Authentik, oauth2-proxy...), and
API tokens for scripts. All off until [auth] is configured.

    [auth]
    session_days = 30
    proxy_header = "Remote-User"            # trust this header...
    trusted_proxies = ["10.0.0.2/32"]       # ...only from these addresses

    [auth.users.kyle]
    password_hash = "pbkdf2_sha256$600000$..."   # python -m sett add-user kyle
    totp_secret = "JBSWY3DPEHPK3PXP"             # optional: asks for a 6-digit code too

The same [auth] table may live in the secrets file instead (it wins there),
which keeps TOTP secrets and API tokens out of a config people share.
Standard library only: PBKDF2 for passwords, HMAC-signed session cookies,
RFC 6238 for codes.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import struct
import threading
import time
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

COOKIE = "sett_session"
PBKDF2_ROUNDS = 600_000
TOTP_STEP = 30
MAX_FAILURES = 8          # per client address...
FAILURE_WINDOW = 900      # ...in this many seconds, then logins are refused until it passes


# ---- passwords -----------------------------------------------------------------

def hash_password(password: str, rounds: int = PBKDF2_ROUNDS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64(salt)}${_b64(dk)}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, _unb64(want))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---- TOTP (RFC 6238) ---------------------------------------------------------------

def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper().replace(" ", "") + "=" * (-len(secret.replace(" ", "")) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return str(code).zfill(digits)


def totp_counter(code: str, secret: str, now: float | None = None, window: int = 1) -> int | None:
    """The time step a code is valid for (allowing one step of clock drift either
    way), or None. The caller refuses a step it has already accepted (replay)."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return None
    now_step = int((now if now is not None else time.time()) // TOTP_STEP)
    try:
        for step in range(now_step - window, now_step + window + 1):
            if hmac.compare_digest(totp(secret, step), code):
                return step
    except (ValueError, TypeError):   # a malformed secret in the config
        log.warning("auth: a totp_secret is not valid base32")
    return None


def otpauth_uri(user: str, secret: str, issuer: str = "Sett") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(user)}?secret={secret}&issuer={quote(issuer)}&digits=6&period={TOTP_STEP}"


# ---- the gate ------------------------------------------------------------------------

class Auth:
    """Decides who a request is. One per server; reads [auth] from the live
    config on every call, so edits to sett.toml or the secrets file apply
    without a restart."""

    def __init__(self, engine):
        self.engine = engine
        self.key = _session_key(engine.data)
        self._failures: dict[str, list[float]] = {}
        self._used_steps: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def conf(self) -> dict:
        cfg = self.engine.cfg
        base = dict(getattr(cfg, "auth", {}) or {})
        extra = cfg.secrets.get("auth") if isinstance(cfg.secrets.get("auth"), dict) else {}
        users = {**(base.get("users") or {}), **(extra.get("users") or {})}
        base.update(extra)
        base["users"] = users
        return base

    @property
    def enabled(self) -> bool:
        c = self.conf
        return bool(c["users"] or c.get("proxy_header") or c.get("api_tokens"))

    @property
    def login_enabled(self) -> bool:
        return bool(self.conf["users"])

    # who is this?
    def identify(self, headers, client_ip: str) -> str | None:
        """The signed-in user's name, or None. Checks a trusted proxy header,
        then a bearer API token, then the session cookie."""
        c = self.conf
        hdr = c.get("proxy_header")
        if hdr and headers.get(hdr) and _ip_in(client_ip, c.get("trusted_proxies") or []):
            return headers.get(hdr).strip()[:64]
        bearer = headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            token = bearer[7:].strip()
            for name, t in (c.get("api_tokens") or {}).items():
                if isinstance(t, str) and t and hmac.compare_digest(t.encode(), token.encode()):
                    return f"token:{name}"
            return None
        return self.check_session(_cookie(headers.get("Cookie", ""), COOKIE))

    # sessions: "user|expiry|fingerprint|sig". The fingerprint ties a session to the
    # user's current password and TOTP secret, so changing either signs everyone out.
    def _fingerprint(self, user: str) -> str:
        u = self.conf["users"].get(user) or {}
        material = f"{u.get('password_hash', '')}|{u.get('totp_secret', '')}".encode()
        return hashlib.sha256(material).hexdigest()[:16]

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self.key, payload.encode(), hashlib.sha256).digest())

    def make_session(self, user: str) -> tuple[str, int]:
        days = float(self.conf.get("session_days", 30))
        max_age = int(days * 86400)
        payload = f"{_b64(user.encode())}|{int(time.time()) + max_age}|{self._fingerprint(user)}"
        return f"{payload}|{self._sign(payload)}", max_age

    def check_session(self, value: str | None) -> str | None:
        if not value or value.count("|") != 3:
            return None
        payload, sig = value.rsplit("|", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return None
        try:
            u, expiry, fp = payload.split("|")
            user = _unb64(u).decode()
        except (ValueError, UnicodeDecodeError):
            return None
        if int(expiry) < time.time() or user not in self.conf["users"]:
            return None
        return user if hmac.compare_digest(fp, self._fingerprint(user)) else None

    # signing in
    def login(self, user: str, password: str, code: str, client_ip: str) -> tuple[bool, str]:
        """(ok, message). A wrong user, password or code all give the same answer."""
        if self.locked_out(client_ip):
            return False, "Too many attempts. Try again in a few minutes."
        u = self.conf["users"].get(user)
        stored = u.get("password_hash", "") if isinstance(u, dict) else ""
        # Always run one hash, so timing does not reveal which user names exist.
        ok = check_password(password, stored or _dummy_hash()) and bool(stored)
        if ok and u.get("totp_secret"):
            if not code:
                return False, "code required"
            step = totp_counter(code, u["totp_secret"])
            with self._lock:
                if step is None or step <= self._used_steps.get(user, -1):
                    ok = False
                else:
                    self._used_steps[user] = step
        if not ok:
            self._fail(client_ip)
            return False, "Wrong user name, password or code."
        with self._lock:
            self._failures.pop(client_ip, None)
        return True, ""

    def needs_code(self, user: str) -> bool:
        u = self.conf["users"].get(user)
        return isinstance(u, dict) and bool(u.get("totp_secret"))

    def _fail(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._failures.get(ip, []) if now - t < FAILURE_WINDOW]
            hits.append(now)
            self._failures[ip] = hits
            if len(self._failures) > 10000:   # bound memory under a spray of addresses
                self._failures = {k: v for k, v in self._failures.items() if now - v[-1] < FAILURE_WINDOW}
        log.warning("auth: failed sign-in from %s", ip)

    def locked_out(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            return len([t for t in self._failures.get(ip, []) if now - t < FAILURE_WINDOW]) >= MAX_FAILURES


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("not-a-real-password")


def _session_key(data: Path | None) -> bytes:
    """SETT_SESSION_KEY, else a random key kept in the data folder (so sessions
    survive a restart), else a random key for this run only."""
    env = os.environ.get("SETT_SESSION_KEY")
    if env:
        return env.encode()
    if data:
        f = data / "session.key"
        try:
            if f.is_file():
                return f.read_bytes()
            key = secrets.token_bytes(32)
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
            return key
        except OSError as e:
            log.warning("auth: could not keep a session key in %s (%s); sign-ins last until restart", data, e)
    return secrets.token_bytes(32)


def _cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def _ip_in(ip: str, nets: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in nets:
        try:
            if addr in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            log.warning("auth: trusted_proxies entry %r is not a network", n)
    return False


# ---- CLI: python -m sett add-user NAME [--totp] ------------------------------------------

def add_user_cli(args: list[str]) -> int:
    import getpass
    names = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--svg")]
    if not names:
        print("usage: python -m sett add-user NAME [--totp [--svg FILE]]")
        return 2
    name = names[0]
    pw = getpass.getpass(f"Password for {name}: ")
    if len(pw) < 8:
        print("Use at least 8 characters.")
        return 1
    if getpass.getpass("Again: ") != pw:
        print("Those didn't match.")
        return 1
    lines = [f'[auth.users.{_toml_key(name)}]', f'password_hash = "{hash_password(pw)}"']
    if "--totp" in args:
        secret = new_totp_secret()
        lines.append(f'totp_secret = "{secret}"')
        from . import qr
        uri = otpauth_uri(name, secret)
        print("\nScan this with your authenticator app:\n")
        print(qr.terminal(qr.encode(uri)))
        print("\nOr enter the key by hand, or paste the link:")
        print(f"  key: {secret}")
        print(f"  uri: {uri}")
        svg_at = next((args[i + 1] for i, a in enumerate(args) if a == "--svg" and i + 1 < len(args)), None)
        if svg_at:
            Path(svg_at).write_text(qr.svg(qr.encode(uri)), encoding="utf-8")
            os.chmod(svg_at, 0o600)
            print(f"  QR code also saved to {svg_at} (it holds the secret: delete it once scanned)")
    print("\nPut this in the secrets file (or sett.toml):\n")
    print("\n".join(lines))
    return 0


def _toml_key(name: str) -> str:
    return name if name.replace("-", "").replace("_", "").isalnum() else '"' + name.replace('"', '\\"') + '"'
