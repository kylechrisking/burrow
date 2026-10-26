"""Icons, served same-origin from /icons/<name> so a dashboard works offline.

A name is looked up, in order, in:
  1. <config dir>/icons/<name>.svg|png|webp|jpg   your own files
  2. /app/icons (baked into the image at build time by `prefetch-icons`)
  3. the disk cache (BURROW_CACHE, default /tmp/burrow-cache)
  4. the network, once, then cached:
       "sonarr"        -> dashboard-icons (svg, then png)
       "mdi-harddisk"  -> Material Design Icons (svg; the page tints it with the theme)
       "https://..."   -> that URL
Misses are remembered for a day so an unknown name does not hit the CDN on every page load.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path

from . import net

log = logging.getLogger(__name__)

DASHBOARD_ICONS = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
MDI = "https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg"
BAKED = Path(os.environ.get("BURROW_ICONS", "/app/icons"))
CACHE = Path(os.environ.get("BURROW_CACHE", "/tmp/burrow-cache")) / "icons"
TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg"}
_SAFE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_misses: dict[str, float] = {}


def _key(name: str) -> str:
    return "url-" + hashlib.sha256(name.encode()).hexdigest()[:24] if name.startswith("http") else name


def _find(folder: Path, key: str) -> Path | None:
    for ext in TYPES:
        f = folder / f"{key}{ext}"
        if f.is_file():
            return f
    return None


def _sources(name: str) -> list[str]:
    if name.startswith(("http://", "https://")):
        return [name]
    if name.startswith("mdi-"):
        return [f"{MDI}/{name[4:]}.svg"]
    return [f"{DASHBOARD_ICONS}/svg/{name}.svg", f"{DASHBOARD_ICONS}/png/{name}.png"]


def download(name: str, dest: Path) -> Path | None:
    """Fetch one icon into dest; returns the file, or None if no source had it."""
    for url in _sources(name):
        try:
            r = net.request(url, timeout=8, max_bytes=512 * 1024)
        except Exception:
            continue
        ext = Path(url.split("?")[0]).suffix.lower()
        ctype = r.header("content-type").split(";")[0]
        if ext not in TYPES:
            ext = next((e for e, t in TYPES.items() if t == ctype), "")
        if r.status == 200 and r.body and ext and (ctype.startswith("image/") or ext == ".svg"):
            if ext == ".svg" and b"<svg" not in r.body[:2048]:
                continue
            dest.mkdir(parents=True, exist_ok=True)
            f = dest / f"{_key(name)}{ext}"
            f.write_bytes(r.body)
            return f
    return None


def resolve(name: str, config_dir: Path) -> tuple[bytes, str] | None:
    name = name.strip().lower() if not name.startswith("http") else name.strip()
    if not name.startswith(("http://", "https://")) and not _SAFE.match(name):
        return None
    key = _key(name)
    for folder in (config_dir / "icons", BAKED, CACHE):
        f = _find(folder, key)
        if f:
            return f.read_bytes(), TYPES[f.suffix]
    if time.time() - _misses.get(key, 0) < 86400:
        return None
    try:
        f = download(name, CACHE)
    except OSError as e:          # read-only filesystem and the like: serve uncached
        log.info("icon cache unavailable: %s", e)
        f = None
    if f:
        return f.read_bytes(), TYPES[f.suffix]
    _misses[key] = time.time()
    return None


def prefetch(dest: Path) -> int:
    """Download the icon of every app in the catalog (for baking into an image)."""
    from .apps.base import REGISTRY
    names = sorted({a.icon for a in REGISTRY.values()} | {"proxmox", "docker"})
    got = 0
    for n in names:
        if _find(dest, n) or download(n, dest):
            got += 1
    return got
