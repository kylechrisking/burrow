"""Productivity, documents, files, finance and developer tools."""
from __future__ import annotations

from ..model import Stat
from .base import App, Ctx, any_of, body, count, json_at, json_field, json_has, register, title, title_is


def paperless_stats(ctx: Ctx) -> list[Stat]:
    token = ctx.secret.get("token")
    if not token:
        return []
    r = ctx.get("/api/statistics/", headers={"Authorization": f"Token {token}", "Accept": "application/json"})
    ctx.version = r.header("x-version") or None
    s = r.json() or {}
    if "documents_total" not in s:
        return []
    inbox = s.get("documents_inbox") or 0
    return [Stat("Documents", count(s["documents_total"])), Stat("Inbox", str(inbox), "warn" if inbox else "")]


register(
    App("vaultwarden", "Vaultwarden", "Apps", [80, 8000], probe="/api/config",
        match=json_at("server.name", "Vaultwarden"), images=["vaultwarden"], version=json_field("server", "version")),
    App("paperless", "Paperless-ngx", "Apps", [8000], match=title("Paperless"), images=["paperless"],
        icon="paperless-ngx", stats=paperless_stats, needs="token"),
    App("mealie", "Mealie", "Apps", [9000, 9925], probe="/api/app/about", match=json_has("production"),
        images=["mealie"], version=json_field("version")),
    App("homebox", "Homebox", "Apps", [7745], probe="/api/v1/status", match=json_at("build.version"),
        images=["homebox"], version=json_field("build", "version")),
    App("grocy", "Grocy", "Apps", [80, 9283], match=title("Grocy"), images=["grocy"]),
    App("actual", "Actual Budget", "Apps", [5006], probe="/info", match=json_at("build.name", "actual"),
        images=["actual"], icon="actual-budget", version=json_field("build", "version")),
    App("firefly", "Firefly III", "Apps", [8080], match=title("Firefly III"), images=["firefly"], icon="firefly-iii"),
    App("linkding", "linkding", "Apps", [9090], probe="/health", match=json_has("status", "healthy"),
        images=["linkding"], version=json_field("version")),
    App("freshrss", "FreshRSS", "Apps", [80], match=title("FreshRSS"), images=["freshrss"]),
    App("miniflux", "Miniflux", "Apps", [8080], match=title("Miniflux"), images=["miniflux"]),
    App("wallabag", "wallabag", "Apps", [80], match=title("wallabag"), images=["wallabag"]),
    App("bookstack", "BookStack", "Apps", [80, 6875], match=title("BookStack"), images=["bookstack"]),
    App("wikijs", "Wiki.js", "Apps", [3000], match=title("Wiki.js"), images=["requarks/wiki", "wikijs"], icon="wikijs"),
    App("outline", "Outline", "Apps", [3000], match=title_is("Outline"), images=["outline"]),
    App("vikunja", "Vikunja", "Apps", [3456], match=title_is("Vikunja"), images=["vikunja"]),
    App("stirling-pdf", "Stirling PDF", "Apps", [8080], match=title("Stirling PDF"), images=["stirling"]),
    App("it-tools", "IT Tools", "Apps", [80], match=title("IT Tools"), images=["it-tools"]),
    App("searxng", "SearXNG", "Apps", [8080], match=title_is("SearXNG"), images=["searxng"]),
    App("filebrowser", "File Browser", "Files", [80, 8080], match=title_is("File Browser"), images=["filebrowser"]),
    App("syncthing", "Syncthing", "Files", [8384], probe="/rest/noauth/health", match=json_has("status", "ok"),
        images=["syncthing"]),
    App("duplicati", "Duplicati", "Files", [8200], match=any_of(title_is("Duplicati"), body("duplicati")),
        images=["duplicati"]),
    App("minio", "MinIO", "Files", [9000, 9001], match=any_of(title("MinIO"), json_has("Code", "AccessDenied")),
        images=["minio"]),
    App("seafile", "Seafile", "Files", [80, 8000], match=title("Seafile"), images=["seafile"]),
    App("photoprism", "PhotoPrism", "Media", [2342], match=title_is("PhotoPrism"), images=["photoprism"]),
    App("code-server", "code-server", "Dev", [8443, 8080], probe="/healthz", match=json_has("lastHeartbeat"),
        images=["code-server"]),
    App("n8n", "n8n", "Dev", [5678], match=title("n8n"), images=["n8n"]),
    App("gitea", "Gitea", "Dev", [3000], match=title("Gitea"), images=["gitea"]),
    App("forgejo", "Forgejo", "Dev", [3000], match=title("Forgejo"), images=["forgejo"]),
    App("gitlab", "GitLab", "Dev", [80, 443], match=title("GitLab"), images=["gitlab"]),
    App("jenkins", "Jenkins", "Dev", [8080], match=title("Jenkins"), images=["jenkins"]),
)
