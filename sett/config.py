"""Config: an optional TOML file, plus a few environment overrides.

Nothing is required. With no file at all the dashboard uses the Docker socket
if one is mounted, and shows whatever that finds.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Config:
    title: str = "HomeLab"
    theme: str = "midnight"
    port: int = 8099
    proxmox: dict = field(default_factory=dict)
    docker: dict = field(default_factory=dict)
    scan: dict = field(default_factory=dict)
    hide: dict = field(default_factory=dict)
    services: list[dict] = field(default_factory=list)      # [[service]] manual cards
    cards: list[dict] = field(default_factory=list)         # [[card]] JSON-fed cards
    bookmarks: list[dict] = field(default_factory=list)     # [[bookmark]]
    overrides: dict[str, dict] = field(default_factory=dict)  # [override."app" | "app@host" | "Name"]
    links: dict[str, str] = field(default_factory=dict)     # "ip:port" -> URL shown on the card
    caddy_links: dict[str, str] = field(default_factory=dict)  # "ip:port" -> site URL, from [caddy] file
    categories: list[str] = field(default_factory=list)     # display order
    weather: dict = field(default_factory=dict)
    search: str = ""                                        # "google" | "duckduckgo" | "" (filter only)
    config_dir: Path = Path("/config")
    files: dict[str, float] = field(default_factory=dict)   # path -> mtime, watched for reload
    views: list[dict] = field(default_factory=list)         # [[view]]
    ui: dict = field(default_factory=dict)                  # [ui] accent, wallpaper, density, clock
    secrets: dict[str, dict] = field(default_factory=dict)
    auth: dict = field(default_factory=dict)                # [auth] sign-in, see auth.py
    vitals: dict = field(default_factory=dict)               # [vitals] uptime on cards, see vitals.py
    check_interval: float = 15
    identify_interval: float = 300

    def secret(self, app_id: str) -> dict:
        return self.secrets.get(app_id, {})


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load() -> Config:
    path = Path(os.environ.get("SETT_CONFIG", "/config/sett.toml"))
    raw: dict = {}
    files = {str(path): _mtime(path)}
    if path.is_file():
        raw = _read_toml(path)
        log.info("config: %s", path)
    else:
        log.info("config: none at %s, running on auto-detection only", path)

    cfg = Config(
        title=raw.get("title", "HomeLab"),
        theme=raw.get("theme", "midnight"),
        port=int(raw.get("port", 8099)),
        proxmox=raw.get("proxmox", {}),
        docker=raw.get("docker", {}),
        scan=raw.get("scan", {}),
        hide=raw.get("hide", {}),
        services=raw.get("service", []),
        cards=raw.get("card", []),
        bookmarks=raw.get("bookmark", []),
        overrides=raw.get("override", {}),
        links=raw.get("links", {}),
        categories=raw.get("categories", []),
        weather=raw.get("weather", {}),
        search=raw.get("search", ""),
        views=raw.get("view", []),
        ui=raw.get("ui", {}),
        auth=raw.get("auth", {}),
        vitals=raw.get("vitals") or raw.get("brock", {}),   # [brock]: its name before the rename
        config_dir=path.parent,
        check_interval=float(raw.get("check_interval", 15)),
        identify_interval=float(raw.get("identify_interval", 300)),
    )

    env = os.environ
    cfg.title = env.get("SETT_TITLE", cfg.title)
    cfg.theme = env.get("SETT_THEME", cfg.theme)
    cfg.port = int(env.get("SETT_PORT", cfg.port))
    if env.get("SETT_PROXMOX_URL"):
        cfg.proxmox["url"] = env["SETT_PROXMOX_URL"]
    if env.get("SETT_PROXMOX_TOKEN_FILE"):
        cfg.proxmox["token_file"] = env["SETT_PROXMOX_TOKEN_FILE"]
    if "SETT_DOCKER" in env:   # empty or "none" turns Docker off, even with a socket mounted
        cfg.docker["endpoints"] = [e.strip() for e in env["SETT_DOCKER"].split(",") if e.strip() not in ("", "none")]
    if env.get("SETT_SCAN"):
        cfg.scan["hosts"] = [h.strip() for h in env["SETT_SCAN"].split(",") if h.strip()]

    secrets_file = env.get("SETT_SECRETS_FILE") or raw.get("secrets", {}).get("file") or "/secrets/apps.toml"
    sp = Path(secrets_file)
    files[str(sp)] = _mtime(sp)
    if sp.is_file():
        cfg.secrets = _read_toml(sp)
        if os.name == "posix" and sp.stat().st_mode & 0o077:
            log.warning("secrets file %s is readable by group/other; chmod 600 it", sp)
        log.info("secrets: %d app(s) from %s", len(cfg.secrets), sp)
    for key in ("brock", "vitals"):   # the token can live with the other secrets (under either name)
        if isinstance(cfg.secrets.get(key), dict):
            cfg.vitals = {**cfg.vitals, **cfg.secrets[key]}
    caddy_file = (raw.get("caddy") or {}).get("file") or env.get("SETT_CADDYFILE")
    if caddy_file:
        from . import caddy
        files[str(caddy_file)] = _mtime(Path(caddy_file))   # edits to the Caddyfile reload too
        cfg.caddy_links = caddy.load(caddy_file)
    cfg.files = files
    return cfg


def data_dir() -> Path | None:
    """Where Sett keeps what should outlive a restart (sparkline history, the
    session key). SETT_DATA if set, else /data when it is mounted and writable,
    else the cache folder. None when nothing is writable: Sett still runs."""
    cands = [os.environ.get("SETT_DATA"), "/data",
             str(Path(os.environ.get("SETT_CACHE", "/tmp/sett-cache")))]
    for i, c in enumerate(cands):
        if not c:
            continue
        d = Path(c)
        if i == 1 and not d.is_dir():    # /data is only used when someone mounted it
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
            return d
        except OSError:
            continue
    return None


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def changed(cfg: Config) -> bool:
    """True when a file the config was read from was edited, created or removed."""
    return any(_mtime(Path(f)) != m for f, m in cfg.files.items())


PROVIDER_KEYS = ("proxmox", "docker", "scan")


def providers_changed(old: Config, new: Config) -> bool:
    return any(getattr(old, k) != getattr(new, k) for k in PROVIDER_KEYS) or old.secrets != new.secrets
