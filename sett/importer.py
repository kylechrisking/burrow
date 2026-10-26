"""Turn a Homepage config folder into a sett.toml.

    python -m sett import-homepage /path/to/homepage/config -o sett.toml --secrets apps.toml

- Services with a widget for an app Sett discovers (Sonarr, Plex, Pi-hole...)
  become [override] descriptions/names and [links] friendly URLs; discovery
  does the rest. Their API keys go to the secrets file.
- customapi widgets become [[card]]s with the same field mappings.
- Everything else becomes a [[service]] link card, checked via siteMonitor.
- bookmarks.yaml -> [[bookmark]], settings.yaml layout/tabs -> categories and
  [[view]]s, widgets.yaml weather/search/clock -> [weather], search, [ui].

Credentials are written only to the secrets file (mode 600), never to stdout.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import miniyaml

# Homepage widget type -> Sett app id
APP = {
    "sonarr": "sonarr", "radarr": "radarr", "lidarr": "lidarr", "readarr": "readarr", "prowlarr": "prowlarr",
    "bazarr": "bazarr", "jackett": "jackett", "sabnzbd": "sabnzbd", "nzbget": "nzbget", "qbittorrent": "qbittorrent",
    "transmission": "transmission", "deluge": "deluge", "plex": "plex", "jellyfin": "jellyfin", "emby": "emby",
    "tautulli": "tautulli", "overseerr": "seerr", "jellyseerr": "seerr", "navidrome": "navidrome",
    "audiobookshelf": "audiobookshelf", "kavita": "kavita", "komga": "komga", "calibreweb": "calibre-web",
    "immich": "immich", "photoprism": "photoprism", "pihole": "pihole", "adguard": "adguard",
    "nextcloud": "nextcloud", "gitea": "gitea", "grafana": "grafana", "prometheus": "prometheus",
    "netdata": "netdata", "glances": "glances", "scrutiny": "scrutiny", "uptimekuma": "uptime-kuma",
    "homeassistant": "home-assistant", "frigate": "frigate", "traefik": "traefik", "npm": "nginx-proxy-manager",
    "portainer": "portainer", "proxmox": "proxmox", "proxmoxbackupserver": "proxmox-backup", "truenas": "truenas",
    "openmediavault": "openmediavault", "unifi": "unifi", "omada": "omada", "technitium": "technitium",
    "tdarr": "tdarr", "paperlessngx": "paperless", "mealie": "mealie", "speedtest": "speedtest-tracker",
    "gotify": "gotify", "changedetectionio": "changedetection", "healthchecks": "healthchecks",
    "minecraft": "minecraft", "wgeasy": "wg-easy", "authentik": "authentik", "homebox": "homebox",
    "beszel": "beszel", "romm": "romm", "tandoor": "tandoor", "vikunja": "vikunja", "syncthing": "syncthing",
}
# which Homepage widget fields hold credentials, and the Sett secret name for each
SECRET_FIELDS = {"key": "api_key", "username": "username", "password": "password", "slug": "status_page"}
KEY_IS = {"plex": "token", "home-assistant": "token", "paperless": "token", "technitium": "token", "pihole": "password"}
# Homepage's Tailwind colour names -> an accent hex (the 400 shade)
COLORS = {"slate": "#94a3b8", "gray": "#9ca3af", "zinc": "#a1a1aa", "neutral": "#a3a3a3", "stone": "#a8a29e",
          "red": "#f87171", "orange": "#fb923c", "amber": "#fbbf24", "yellow": "#facc15", "lime": "#a3e635",
          "green": "#4ade80", "emerald": "#34d399", "teal": "#2dd4bf", "cyan": "#22d3ee", "sky": "#38bdf8",
          "blue": "#60a5fa", "indigo": "#818cf8", "violet": "#a78bfa", "purple": "#c084fc", "fuchsia": "#e879f9",
          "pink": "#f472b6", "rose": "#fb7185"}


def q(v) -> str:
    """A TOML value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(q(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def icon_name(icon) -> str | None:
    if not icon or not isinstance(icon, str):
        return None
    if icon.startswith(("http://", "https://", "mdi-")):
        return icon
    if icon.startswith("si-") or "/" in icon:
        return None                   # simple-icons and local paths have no Sett equivalent yet
    return re.sub(r"\.(png|svg|webp|jpe?g)$", "", icon)


def hostport(url: str) -> str | None:
    try:
        u = urlparse(url)
        return f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}" if u.hostname else None
    except ValueError:
        return None


def _read(folder: Path, name: str):
    f = folder / name
    return miniyaml.load(f.read_text(encoding="utf-8")) if f.is_file() else None


def _walk_services(items, group: str, out: list):
    """services.yaml: [{Group: [{Service: {...}} | {SubGroup: [...]}]}]"""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for name, body in item.items():
            if isinstance(body, list):
                _walk_services(body, str(name), out)
            elif isinstance(body, dict):
                out.append((group, str(name), body))


def convert(folder: Path) -> tuple[str, dict, list[str]]:
    """Returns (sett.toml text, secrets {app: {name: value}}, notes for the human)."""
    services_yaml = folder if folder.is_file() else folder / "services.yaml"
    folder = services_yaml.parent
    services_raw = miniyaml.load(services_yaml.read_text(encoding="utf-8")) if services_yaml.is_file() else []
    settings = _read(folder, "settings.yaml") or {}
    widgets = _read(folder, "widgets.yaml") or []
    bookmarks = _read(folder, "bookmarks.yaml") or []

    services: list = []
    for group in services_raw or []:
        if isinstance(group, dict):
            for gname, items in group.items():
                _walk_services(items, str(gname), services)

    notes: list[str] = []
    secrets: dict[str, dict] = {}
    overrides: dict[str, dict] = {}
    links: dict[str, str] = {}
    manual: list[dict] = []
    cards: list[dict] = []
    app_count: dict[str, int] = {}
    for _, _, body in services:
        t = (body.get("widget") or {}).get("type") if isinstance(body.get("widget"), dict) else None
        if t in APP:
            app_count[APP[t]] = app_count.get(APP[t], 0) + 1

    for group, name, body in services:
        widget = body.get("widget") if isinstance(body.get("widget"), dict) else {}
        wtype = widget.get("type")
        href = body.get("href")
        icon = icon_name(body.get("icon"))
        if wtype == "customapi" and widget.get("url"):
            fields = []
            for m in widget.get("mappings") or []:
                if not isinstance(m, dict) or not isinstance(m.get("field"), str):
                    notes.append(f"{name}: a nested customapi field was skipped; map it by hand")
                    continue
                spec = m["field"]
                if m.get("prefix") or m.get("suffix"):
                    spec = f"{m.get('prefix', '')}{{{m['field']}}}{m.get('suffix', '')}"
                fields.append([spec, m.get("label") or m["field"]])
            if widget.get("headers"):
                notes.append(f"{name}: customapi headers are not supported; the card will fetch without them")
            card = {"name": name, "category": group, "url": href, "description": body.get("description"),
                    "icon": icon, "data": widget["url"], "fields": fields}
            if widget.get("refreshInterval"):
                card["refresh"] = max(15, int(widget["refreshInterval"]) // 1000)
            cards.append(card)
            continue
        app = APP.get(wtype)
        if app:
            for hk, bk in SECRET_FIELDS.items():
                if widget.get(hk):
                    sk = KEY_IS.get(app, bk) if hk == "key" else bk
                    secrets.setdefault(app, {})[sk] = str(widget[hk])
            hp = hostport(widget.get("url", ""))
            if href and hp and hostport(href) != hp:
                links[hp] = href
            if app_count.get(app, 0) == 1:
                ov = {k: v for k, v in (("name", name), ("description", body.get("description")),
                                        ("category", group)) if v}
                overrides[app] = ov
            else:
                notes.append(f"{name}: one of {app_count[app]} {app} widgets; descriptions for repeated apps "
                             f"go under [override.\"{app}@<host>\"] by hand")
            continue
        if wtype:
            notes.append(f"{name}: widget type {wtype!r} has no Sett equivalent yet; kept as a link card")
        if not href:
            continue
        check = body.get("siteMonitor") or href
        if body.get("ping") and not body.get("siteMonitor"):
            check = False
            notes.append(f"{name}: Homepage pinged it; Sett checks over HTTP, so it is a plain link")
        manual.append({"name": name, "category": group, "url": href, "check": check,
                       "icon": icon, "description": body.get("description")})

    # ---- settings, widgets, bookmarks
    top: dict = {"title": settings.get("title") or "HomeLab"}
    theme = settings.get("theme")
    if theme in ("light", "dark"):
        top["theme"] = "daylight" if theme == "light" else "midnight"
    ui: dict = {}
    if settings.get("color") in COLORS:
        ui["accent"] = COLORS[settings["color"]]
    if settings.get("background"):
        bg = settings["background"]
        ui["wallpaper"] = bg.get("image") if isinstance(bg, dict) else bg
    weather: dict = {}
    for w in widgets or []:
        if not isinstance(w, dict):
            continue
        for wname, conf in w.items():
            conf = conf or {}
            if wname in ("openmeteo", "openweathermap", "weatherapi") and conf.get("latitude") is not None:
                weather = {"label": conf.get("label", ""), "latitude": conf["latitude"],
                           "longitude": conf["longitude"], "units": conf.get("units", "metric")}
            elif wname == "search" and conf.get("provider") in ("google", "duckduckgo", "bing"):
                top["search"] = conf["provider"]
            elif wname == "datetime" and (conf.get("format") or {}).get("hour12") is False:
                ui["clock"] = "24h"
    layout = settings.get("layout") or {}
    groups_seen = list(dict.fromkeys(g for g, _, _ in services))
    if isinstance(layout, dict) and layout:
        top["categories"] = list(dict.fromkeys(list(layout) + groups_seen))
    tabs: dict[str, list[str]] = {}
    for g, conf in (layout.items() if isinstance(layout, dict) else []):
        if isinstance(conf, dict) and conf.get("tab"):
            tabs.setdefault(conf["tab"], []).append(g)
    bm_out = []
    for group in bookmarks or []:
        if not isinstance(group, dict):
            continue
        for gname, items in group.items():
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                for bname, entries in item.items():
                    e = (entries or [{}])[0] if isinstance(entries, list) else (entries or {})
                    if e.get("href"):
                        bm_out.append({"group": gname, "name": bname, "abbr": e.get("abbr"), "url": e["href"]})

    # ---- write
    L = ["# Generated by `python -m sett import-homepage` from a Homepage config.",
         "# Discovery finds most services on its own; this file carries over what it cannot know:",
         "# your names, descriptions, links, custom cards, bookmarks and layout.", ""]
    for k, v in top.items():
        L.append(f"{k} = {q(v)}")
    if ui:
        L += ["", "[ui]"] + [f"{k} = {q(v)}" for k, v in ui.items()]
    if weather:
        L += ["", "[weather]"] + [f"{k} = {q(v)}" for k, v in weather.items() if v != ""]
    for tab, groups in tabs.items():
        bm_groups = [g for g in groups if g in {b["group"] for b in bm_out}]
        L += ["", "[[view]]", f"name = {q(tab)}", f"categories = {q([g for g in groups if g not in bm_groups])}"]
        if bm_groups:
            L.append(f"links = {q(bm_groups)}")
    if links:
        L += ["", "# Friendly URLs for discovered services: \"ip:port\" -> link", "[links]"]
        L += [f"{q(hp)} = {q(url)}" for hp, url in links.items()]
    for app, ov in overrides.items():
        L += ["", f"[override.{key(app)}]"] + [f"{k} = {q(v)}" for k, v in ov.items()]
    for m in manual:
        L += ["", "[[service]]"] + [f"{k} = {q(v)}" for k, v in m.items() if v not in (None, "")]
    for c in cards:
        L += ["", "[[card]]"] + [f"{k} = {q(v)}" for k, v in c.items() if v not in (None, "", [])]
    for b in bm_out:
        L += ["", "[[bookmark]]"] + [f"{k} = {q(v)}" for k, v in b.items() if v not in (None, "")]
    return "\n".join(L) + "\n", secrets, notes


def main(args: list[str]) -> int:
    if not args or args[0].startswith("-"):
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    src = Path(args[0])
    out = Path(args[args.index("-o") + 1]) if "-o" in args else None
    secrets_path = Path(args[args.index("--secrets") + 1]) if "--secrets" in args else Path("apps.toml")
    toml, secrets, notes = convert(src)
    if out:
        out.write_text(toml, encoding="utf-8")
    else:
        sys.stdout.write(toml)
    if secrets:
        lines = []
        for app, kv in secrets.items():
            lines += [f"[{key(app)}]"] + [f"{k} = {q(v)}" for k, v in kv.items()] + [""]
        fd = os.open(secrets_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"secrets for {len(secrets)} app(s) written to {secrets_path} (mode 600): "
              + ", ".join(sorted(secrets)), file=sys.stderr)
    for n in notes:
        print(f"note: {n}", file=sys.stderr)
    return 0
