"""The *arr family, plus download clients and their helpers."""
from __future__ import annotations

import re
import time
import urllib.parse

from ..model import Stat
from .base import App, Ctx, any_of, basic_auth, count, header, json_field, json_has, rate, register, title


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
        ctx.version = ctx.json(f"/api/{api}/system/status", headers=dict(h)).get("version")
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
    ctx.version = ctx.json("/api/v1/system/status", headers=dict(h)).get("version")
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
    ctx.version = ctx.json("/api/system/status", headers={"X-API-KEY": key}).get("data", {}).get("bazarr_version")
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
        if login.status >= 300 or login.text.strip() == "Fails." or "=" not in cookie:
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


def sabnzbd_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    q = ctx.json(f"/api?mode=queue&output=json&apikey={key}").get("queue", {})
    out = [Stat("Speed", f"{q.get('speed', '0').strip() or '0'}B/s"), Stat("Queue", str(q.get("noofslots", 0)))]
    if q.get("noofslots"):
        out.append(Stat("Left", q.get("timeleft", "")))
    return out


def nzbget_stats(ctx: Ctx) -> list[Stat]:
    user, pw = ctx.secret.get("username"), ctx.secret.get("password")
    if not (user and pw):
        return []
    r = ctx.get("/jsonrpc", method="POST", headers={**basic_auth(user, pw), "Content-Type": "application/json"},
                data=b'{"method":"status","params":[],"id":1}')
    s = (r.json() or {}).get("result") or {}
    if not s:
        return []
    return [Stat("Speed", rate(s.get("DownloadRate", 0))), Stat("Remaining", f"{s.get('RemainingSizeMB', 0):,} MB")]


def transmission_stats(ctx: Ctx) -> list[Stat]:
    """Transmission's RPC answers 409 with a session id to echo back; open by default."""
    body_ = b'{"method":"session-stats"}'
    h = {"Content-Type": "application/json"}
    if ctx.secret.get("username"):
        h.update(basic_auth(ctx.secret["username"], ctx.secret.get("password", "")))
    if ctx.cache.get("sid"):
        h["X-Transmission-Session-Id"] = ctx.cache["sid"]
    r = ctx.get("/transmission/rpc", method="POST", headers=dict(h), data=body_)
    if r.status == 409:
        ctx.cache["sid"] = h["X-Transmission-Session-Id"] = r.header("x-transmission-session-id")
        r = ctx.get("/transmission/rpc", method="POST", headers=h, data=body_)
    a = (r.json() or {}).get("arguments") if r.status == 200 else None
    if not a:
        return []
    return [Stat("Down", rate(a.get("downloadSpeed", 0))), Stat("Up", rate(a.get("uploadSpeed", 0))),
            Stat("Active", str(a.get("activeTorrentCount", 0)))]


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
    App("jackett", "Jackett", "Media management", [9117], images=["jackett"],
        # "/" answers 400 "Cookies required" from Kestrel until the UI has set a cookie
        match=any_of(title("Jackett"), lambda r: r.header("server") == "Kestrel" and "cookies required" in r.text.lower())),
    App("qbittorrent", "qBittorrent", "Downloads", [8080, 8090], match=title("qBittorrent"),
        images=["qbittorrent"], stats=qbit_stats),
    App("transmission", "Transmission", "Downloads", [9091], probe="/transmission/web/",
        match=title("Transmission"), images=["transmission"], stats=transmission_stats,
        link_path="/transmission/web/"),
    App("sabnzbd", "SABnzbd", "Downloads", [8080, 8085], match=title("SABnzbd"), images=["sabnzbd"],
        stats=sabnzbd_stats, needs="api_key"),
    App("nzbget", "NZBGet", "Downloads", [6789], match=any_of(header("server", "nzbget"), title("NZBGet")),
        images=["nzbget"], version=lambda r: r.header("server").partition("-")[2] or None,
        stats=nzbget_stats, needs="username + password"),
    App("deluge", "Deluge", "Downloads", [8112], match=title("Deluge"), images=["deluge"]),
    App("whisparr", "Whisparr", "Media management", [6969], match=title("Whisparr"), images=["whisparr"],
        version=_arr_version, stats=arr_stats("v3"), needs="api_key"),
    App("mylar3", "Mylar3", "Media management", [8090], match=title("Mylar"), images=["mylar"]),
    App("lazylibrarian", "LazyLibrarian", "Media management", [5299], match=title("LazyLibrarian"),
        images=["lazylibrarian"]),
    App("notifiarr", "Notifiarr", "Media management", [5454], match=title("Notifiarr"), images=["notifiarr"]),
    App("flaresolverr", "FlareSolverr", "Downloads", [8191], match=json_has("msg", "FlareSolverr"),
        images=["flaresolverr"], version=json_field("version")),
    App("nicotine", "Nicotine+", "Downloads", [6080], match=title("Nicotine"), images=["nicotine"],
        icon="nicotine-plus"),
)
