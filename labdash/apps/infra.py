"""Infrastructure, monitoring, network and general self-hosted apps."""
from __future__ import annotations

import json

from ..model import Stat
from .base import App, Ctx, any_of, body, count, header, json_field, json_has, register, title


def pihole_stats(ctx: Ctx) -> list[Stat]:
    """Pi-hole v6. Unauthenticated when no password is set; otherwise one session,
    kept and reused — v6 limits concurrent sessions, so never log in per check."""
    def summary():
        h = {"X-FTL-SID": ctx.cache["sid"]} if ctx.cache.get("sid") else {}
        return ctx.get("/api/stats/summary", headers=h)

    r = summary()
    if r.status == 401:
        pw = ctx.secret.get("password")
        if not pw:
            return []
        auth = ctx.get("/api/auth", method="POST", headers={"Content-Type": "application/json"},
                       data=json.dumps({"password": pw}).encode()).json() or {}
        sid = auth.get("session", {}).get("sid")
        if not sid:
            raise RuntimeError("Pi-hole login failed")
        ctx.cache["sid"] = sid
        r = summary()
    j = r.json() or {}
    q = j.get("queries")
    if not q:
        return []
    return [Stat("Queries", count(q.get("total", 0))),
            Stat("Blocked", f"{q.get('percent_blocked', 0):.1f}%"),
            Stat("Clients", str(j.get("clients", {}).get("active", 0)))]


def kuma_stats(ctx: Ctx) -> list[Stat]:
    slug = ctx.secret.get("status_page")
    if not slug:
        return []
    hb = ctx.json(f"/api/status-page/heartbeat/{slug}").get("heartbeatList", {})
    latest = [beats[-1].get("status") for beats in hb.values() if beats]
    up = sum(1 for s in latest if s == 1)
    down = sum(1 for s in latest if s == 0)
    out = [Stat("Up", f"{up}/{len(latest)}", "ok" if up == len(latest) else "")]
    out.append(Stat("Down", str(down), "bad" if down else ""))
    return out


def ollama_stats(ctx: Ctx) -> list[Stat]:
    ctx.version = (ctx.json("/api/version") or {}).get("version")
    models = ctx.json("/api/ps").get("models", [])
    return [Stat("Loaded", ", ".join(m.get("name", "?") for m in models) or "none")]


def portainer_version(r):
    return (r.json() or {}).get("Version")


register(
    # --- infrastructure
    App("proxmox", "Proxmox VE", "Infrastructure", [8006], scheme="https", match=title("Proxmox"),
        icon="proxmox"),
    App("synology", "Synology DSM", "Infrastructure", [5001, 5000], scheme="https",
        match=any_of(title("Synology"), body("synology")), icon="synology"),
    App("portainer", "Portainer", "Infrastructure", [9000, 9443], probe="/api/status",
        match=json_has("InstanceID"), images=["portainer"], version=portainer_version),
    App("homepage", "Homepage", "Infrastructure", [3000], images=["gethomepage"]),
    App("caddy", "Caddy", "Network", [80, 443], match=header("server", "Caddy"), images=["caddy"]),
    App("nginx-proxy-manager", "Nginx Proxy Manager", "Network", [81], match=title("Nginx Proxy Manager"),
        images=["nginx-proxy-manager"]),
    App("pihole", "Pi-hole", "Network", [80], probe="/admin/", match=any_of(title("Pi-hole"), body("pi-hole")),
        images=["pihole"], icon="pi-hole", stats=pihole_stats, link_path="/admin/"),
    App("adguard", "AdGuard Home", "Network", [3000, 80], match=title("AdGuard Home"), images=["adguard"],
        icon="adguard-home"),
    # --- monitoring
    App("uptime-kuma", "Uptime Kuma", "Monitoring", [3001], match=any_of(title("Uptime Kuma"), body("uptime kuma")),
        images=["uptime-kuma"], stats=kuma_stats, needs="status_page"),
    App("beszel", "Beszel", "Monitoring", [8090], match=title("Beszel"), images=["beszel"]),
    App("grafana", "Grafana", "Monitoring", [3000], probe="/api/health", match=json_has("database"),
        images=["grafana"], version=json_field("version")),
    # --- AI
    App("open-webui", "Open WebUI", "AI", [8080, 3000], probe="/api/config", match=json_has("features"),
        images=["open-webui"], version=json_field("version")),
    App("ollama", "Ollama", "AI", [11434], match=body("ollama is running"), images=["ollama"], stats=ollama_stats),
    # --- apps
    App("home-assistant", "Home Assistant", "Apps", [8123], match=title("Home Assistant"),
        images=["home-assistant", "homeassistant"]),
    App("nextcloud", "Nextcloud", "Apps", [80, 443], probe="/status.php", match=json_has("productname", "Nextcloud"),
        images=["nextcloud"], version=json_field("versionstring")),
    App("vaultwarden", "Vaultwarden", "Apps", [80], match=title("Vaultwarden", "Bitwarden"), images=["vaultwarden"]),
    App("gitea", "Gitea", "Apps", [3000], match=title("Gitea", "Forgejo"), images=["gitea", "forgejo"]),
    App("paperless", "Paperless-ngx", "Apps", [8000], match=title("Paperless"), images=["paperless"],
        icon="paperless-ngx"),
    App("tandoor", "Tandoor Recipes", "Apps", [8080], match=title("Tandoor"), images=["tandoor"],
        icon="tandoor-recipes"),
    App("dokuwiki", "DokuWiki", "Apps", [80], match=body("dokuwiki"), images=["dokuwiki"]),
    App("crafty", "Crafty Controller", "Games", [8443], scheme="https", match=title("Crafty"),
        images=["crafty"], icon="crafty-controller"),
    App("minecraft", "Minecraft", "Games", [25565, 25566], kind="minecraft", images=["minecraft"], multi=True),
)
