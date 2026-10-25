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
    services: list[dict] = field(default_factory=list)
    secrets: dict[str, dict] = field(default_factory=dict)
    check_interval: float = 15
    identify_interval: float = 300

    def secret(self, app_id: str) -> dict:
        return self.secrets.get(app_id, {})


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load() -> Config:
    path = Path(os.environ.get("LABDASH_CONFIG", "/config/labdash.toml"))
    raw: dict = {}
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
        check_interval=float(raw.get("check_interval", 15)),
        identify_interval=float(raw.get("identify_interval", 300)),
    )

    env = os.environ
    cfg.title = env.get("LABDASH_TITLE", cfg.title)
    cfg.theme = env.get("LABDASH_THEME", cfg.theme)
    cfg.port = int(env.get("LABDASH_PORT", cfg.port))
    if env.get("LABDASH_PROXMOX_URL"):
        cfg.proxmox["url"] = env["LABDASH_PROXMOX_URL"]
    if env.get("LABDASH_PROXMOX_TOKEN_FILE"):
        cfg.proxmox["token_file"] = env["LABDASH_PROXMOX_TOKEN_FILE"]
    if env.get("LABDASH_DOCKER"):
        cfg.docker["endpoints"] = [e.strip() for e in env["LABDASH_DOCKER"].split(",") if e.strip()]
    if env.get("LABDASH_SCAN"):
        cfg.scan["hosts"] = [h.strip() for h in env["LABDASH_SCAN"].split(",") if h.strip()]

    secrets_file = env.get("LABDASH_SECRETS_FILE") or raw.get("secrets", {}).get("file") or "/secrets/apps.toml"
    sp = Path(secrets_file)
    if sp.is_file():
        cfg.secrets = _read_toml(sp)
        if os.name == "posix" and sp.stat().st_mode & 0o077:
            log.warning("secrets file %s is readable by group/other; chmod 600 it", sp)
        log.info("secrets: %d app(s) from %s", len(cfg.secrets), sp)
    return cfg
