"""Media servers and request front-ends."""
from __future__ import annotations

import re

from ..model import Stat
from .base import App, Ctx, body, count, json_field, json_has, register, title, title_is


def _plex_version(r):
    m = re.search(r'<MediaContainer[^>]*\sversion="([^"]+)"', r.text)
    return m.group(1).split("-")[0] if m else None


def plex_stats(ctx: Ctx) -> list[Stat]:
    token = ctx.secret.get("token")
    if not token:
        return []
    h = {"X-Plex-Token": token, "Accept": "application/json"}
    mc = ctx.json("/status/sessions", headers=dict(h)).get("MediaContainer", {})
    sessions = mc.get("Metadata", []) or []
    transcodes = sum(1 for s in sessions if s.get("TranscodeSession", {}).get("videoDecision") == "transcode")
    libs = ctx.json("/library/sections", headers=dict(h)).get("MediaContainer", {}).get("size", 0)
    out = [Stat("Streams", str(len(sessions)), "ok" if sessions else "")]
    if sessions:
        out.append(Stat("Transcoding", str(transcodes)))
        now = sessions[0]
        title_ = now.get("grandparentTitle") or now.get("title", "")
        who = now.get("User", {}).get("title", "")
        out.append(Stat("Playing", f"{title_} — {who}" if who else title_))
    out.append(Stat("Libraries", str(libs)))
    return out


def jellyfin_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    sessions = ctx.json("/Sessions?activeWithinSeconds=120", headers={"X-Emby-Token": key})
    playing = [s for s in sessions if s.get("NowPlayingItem")]
    return [Stat("Streams", str(len(playing)), "ok" if playing else ""), Stat("Clients", str(len(sessions)))]


def seerr_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    c = ctx.json("/api/v1/request/count", headers={"X-Api-Key": key})
    pending = c.get("pending", 0)
    return [Stat("Pending", str(pending), "warn" if pending else ""), Stat("Requests", count(c.get("total", 0)))]


def navidrome_stats(ctx: Ctx) -> list[Stat]:
    user, pw = ctx.secret.get("username"), ctx.secret.get("password")
    if not (user and pw):
        return []
    q = f"u={user}&p=enc:{pw.encode().hex()}&v=1.16.1&c=burrow&f=json"
    j = ctx.json(f"/rest/getScanStatus.view?{q}").get("subsonic-response", {})
    ctx.version = j.get("serverVersion")
    scan = j.get("scanStatus", {})
    out = [Stat("Songs", count(scan.get("count", 0)))]
    if scan.get("scanning"):
        out.append(Stat("Scan", "running", "warn"))
    return out


def tautulli_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    d = ctx.json(f"/api/v2?apikey={key}&cmd=get_activity").get("response", {}).get("data", {})
    n = int(d.get("stream_count", 0) or 0)
    return [Stat("Streams", str(n), "ok" if n else ""), Stat("Transcoding", str(d.get("stream_count_transcode", 0)))]


def immich_stats(ctx: Ctx) -> list[Stat]:
    key = ctx.secret.get("api_key")
    if not key:
        return []
    s = ctx.json("/api/server/statistics", headers={"x-api-key": key})
    return [Stat("Photos", count(s.get("photos", 0))), Stat("Videos", count(s.get("videos", 0)))]


def _emby(r) -> bool:
    j = r.json()
    return isinstance(j, dict) and "Version" in j and "Jellyfin" not in str(j.get("ProductName", ""))


register(
    App("emby", "Emby", "Media", [8096, 8920], probe="/emby/System/Info/Public", match=_emby, images=["emby"],
        version=json_field("Version")),
    App("audiobookshelf", "Audiobookshelf", "Media", [13378, 80], probe="/status",
        match=json_has("app", "audiobookshelf"), images=["audiobookshelf"], version=json_field("serverVersion")),
    App("kavita", "Kavita", "Media", [5000], match=title_is("Kavita"), images=["kavita"]),
    App("komga", "Komga", "Media", [25600], match=title_is("Komga"), images=["komga"]),
    App("calibre-web", "Calibre-Web", "Media", [8083], match=title("Calibre-Web"), images=["calibre-web"]),
    App("ombi", "Ombi", "Media", [3579], match=title_is("Ombi"), images=["ombi"]),
    App("tdarr", "Tdarr", "Media management", [8265], probe="/api/v2/status", match=json_has("isProduction"),
        images=["tdarr"], version=json_field("version")),
    App("plex", "Plex", "Media", [32400], probe="/identity", match=body("machineIdentifier"),
        images=["plex"], version=_plex_version, stats=plex_stats, needs="token", link_path="/web"),
    App("jellyfin", "Jellyfin", "Media", [8096], probe="/System/Info/Public",
        match=json_has("ProductName", "Jellyfin"), images=["jellyfin"], version=json_field("Version"),
        stats=jellyfin_stats, needs="api_key"),
    App("navidrome", "Navidrome", "Media", [4533], match=title("Navidrome"), images=["navidrome"],
        stats=navidrome_stats, needs="username + password"),
    App("seerr", "Seerr", "Media", [5055], probe="/api/v1/status", match=json_has("commitTag"),
        images=["seerr", "overseerr", "jellyseerr"], icon="jellyseerr", version=json_field("version"),
        variants={"jellyseerr": "Jellyseerr", "overseerr": "Overseerr"},
        stats=seerr_stats, needs="api_key"),
    App("tautulli", "Tautulli", "Media", [8181], match=title("Tautulli"), images=["tautulli"],
        stats=tautulli_stats, needs="api_key"),
    App("immich", "Immich", "Media", [2283], probe="/api/server/version", match=json_has("major"),
        images=["immich"], version=lambda r: "{major}.{minor}.{patch}".format(**r.json()),
        stats=immich_stats, needs="api_key"),
    App("romm", "RomM", "Games", [8080, 80], probe="/api/heartbeat", match=json_has("SYSTEM"),
        images=["romm"], version=json_field("SYSTEM", "VERSION")),
)
