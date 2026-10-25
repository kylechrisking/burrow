"""The *arr family, plus download clients and their helpers."""
from __future__ import annotations

import re
import time
import urllib.parse

from ..model import Stat
from .base import App, Ctx, count, json_field, json_has, rate, register, title


def _arr_version(r):
    m = re.search(r"version:\s*'([^']+)'", r.text)
    return m.group(1) if m else None


def arr_stats(api: str = "v3", missing: bool = True):
    def stats(ctx: Ctx) -> list[Stat]:
        key = ctx.secret.get("api_key")
        if not key:
            return []
        h = {"X-Api-Key": key}
        out: list[Stat] = []
        q = ctx.json(f"/api/{api}/queue?pageSize=1", headers=dict(h)).get("totalRecords", 0)
        out.append(Stat("Queue", count(q)))
        if missing:
            m = ctx.json(f"/api/{api}/wanted/missing?pageSize=1", headers=dict(h)).get("totalRecords", 0)
            out.append(Stat("Missing", count(m), "warn" if m else ""))
        issues = ctx.json(f"/api/{api}/health", headers=dict(h))
        n = len(issues) if isinstance(issues, list) else 0
        out.append(Stat("Health", "OK" if not n else f"{n} issue{'s' * (n > 1)}", "ok" if not n else "warn"))
        return out
    return stats


def prowlarr_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    h = {"X-Api-Key": key}
    idx = ctx.json("/api/v1/indexer", headers=dict(h))
    enabled = sum(1 for i in idx if i.get("enable"))
    issues = ctx.json("/api/v1/health", headers=dict(h))
    n = len(issues)
    return [Stat("Indexers", f"{enabled}/{len(idx)}"),
            Stat("Health", "OK" if not n else f"{n} issue{'s' * (n > 1)}", "ok" if not n else "warn")]


def bazarr_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    b = ctx.json("/api/badges", headers={"X-API-KEY": key})
    return [Stat("Episodes missing subs", count(b.get("episodes", 0))),
            Stat("Movies missing subs", count(b.get("movies", 0)))]


def qbit_stats(ctx: Ctx) -> list[Stat]:
    """Works without credentials when the dashboard's subnet is whitelisted in
    qBittorrent; otherwise logs in once and reuses the session cookie."""
    def call(path: str):
        h = {"Referer": ctx.base}
        if ctx.cache.get("cookie"):
            h["Cookie"] = ctx.cache["cookie"]
        return ctx.get(path, headers=h)

    r = call("/api/v2/transfer/info")
    if r.status == 403:
        user, pw = ctx.secret.get("username"), ctx.secret.get("password")
        # qBittorrent bans an IP after repeated failed logins: back off for 10 minutes on failure.
        if not (user and pw) or ctx.cache.get("login_failed_at", 0) > time.time() - 600:
            return []
        login = ctx.get("/api/v2/auth/login", method="POST", headers={"Referer": ctx.base},
                        data=urllib.parse.urlencode({"username": user, "password": pw}).encode())
        cookie = login.header("set-cookie").split(";")[0]
        if login.status != 200 or "=" not in cookie:
            ctx.cache["login_failed_at"] = time.time()
            raise RuntimeError("qBittorrent login failed")
        ctx.cache["cookie"] = cookie
        r = call("/api/v2/transfer/info")
    j = r.json() or {}
    if "dl_info_speed" not in j:
        return []
    dl = call("/api/v2/torrents/info?filter=downloading").json() or []
    v = call("/api/v2/app/version")
    if v.status == 200 and len(v.text) < 30:
        ctx.version = v.text.strip()
    return [Stat("Down", rate(j["dl_info_speed"])), Stat("Up", rate(j["up_info_speed"])),
            Stat("Downloading", str(len(dl)))]


register(
    App("sonarr", "Sonarr", "Media management", [8989], match=title("Sonarr"), images=["sonarr"],
        version=_arr_version, stats=arr_stats("v3"), needs="api_key"),
    App("radarr", "Radarr", "Media management", [7878], match=title("Radarr"), images=["radarr"],
        version=_arr_version, stats=arr_stats("v3"), needs="api_key"),
    App("lidarr", "Lidarr", "Media management", [8686], match=title("Lidarr"), images=["lidarr"],
        version=_arr_version, stats=arr_stats("v1"), needs="api_key"),
    App("readarr", "Readarr", "Media management", [8787], match=title("Readarr"), images=["readarr"],
        version=_arr_version, stats=arr_stats("v1"), needs="api_key"),
    App("prowlarr", "Prowlarr", "Media management", [9696], match=title("Prowlarr"), images=["prowlarr"],
        version=_arr_version, stats=prowlarr_stats, needs="api_key"),
    App("bazarr", "Bazarr", "Media management", [6767], match=title("Bazarr"), images=["bazarr"],
        stats=bazarr_stats, needs="api_key"),
    App("jackett", "Jackett", "Media management", [9117], match=title("Jackett"), images=["jackett"]),
    App("qbittorrent", "qBittorrent", "Downloads", [8080, 8090], match=title("qBittorrent"),
        images=["qbittorrent"], stats=qbit_stats),
    App("transmission", "Transmission", "Downloads", [9091], probe="/transmission/web/",
        match=title("Transmission"), images=["transmission"]),
    App("sabnzbd", "SABnzbd", "Downloads", [8080, 8085], match=title("SABnzbd"), images=["sabnzbd"]),
    App("flaresolverr", "FlareSolverr", "Downloads", [8191], match=json_has("msg", "FlareSolverr"),
        images=["flaresolverr"], version=json_field("version")),
    App("nicotine", "Nicotine+", "Downloads", [6080], match=title("Nicotine"), images=["nicotine"],
        icon="nicotine-plus"),
)
