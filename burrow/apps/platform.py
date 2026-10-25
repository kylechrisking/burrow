"""Platforms, network gear, proxies, auth and DNS."""
from __future__ import annotations

from ..model import Stat
from .base import App, Ctx, basic_auth, json_at, json_field, json_has, register, title, title_is


def traefik_stats(ctx: Ctx) -> list[Stat]:
    http = ctx.json("/api/overview").get("http", {})
    routers, services = http.get("routers", {}), http.get("services", {})
    errors = routers.get("errors", 0) + services.get("errors", 0)
    warnings = routers.get("warnings", 0) + services.get("warnings", 0)
    out = [Stat("Routers", str(routers.get("total", 0))), Stat("Services", str(services.get("total", 0)))]
    if warnings and not errors:
        return out + [Stat("Warnings", str(warnings), "warn")]
    return out + [Stat("Errors", str(errors), "bad" if errors else "ok")]


def technitium_stats(ctx: Ctx) -> list[Stat]:
    token = ctx.secret.get("token")
    if not token:
        return []
    s = ctx.json(f"/api/dashboard/stats/get?token={token}&type=LastDay").get("response", {}).get("stats", {})
    total, blocked = s.get("totalQueries", 0), s.get("totalBlocked", 0)
    return [Stat("Queries", f"{total:,}"), Stat("Blocked", f"{(blocked / total * 100) if total else 0:.1f}%")]


def adguard_stats(ctx: Ctx) -> list[Stat]:
    user, pw = ctx.secret.get("username"), ctx.secret.get("password")
    if not (user and pw):
        return []
    s = ctx.json("/control/stats", headers=basic_auth(user, pw))
    total, blocked = s.get("num_dns_queries", 0), s.get("num_blocked_filtering", 0)
    return [Stat("Queries", f"{total:,}"), Stat("Blocked", f"{(blocked / total * 100) if total else 0:.1f}%")]


register(
    # --- hypervisors, NAS and server OSes
    App("proxmox-backup", "Proxmox Backup Server", "Infrastructure", [8007], scheme="https",
        match=title("Proxmox Backup Server"), icon="proxmox"),
    App("proxmox-mail", "Proxmox Mail Gateway", "Infrastructure", [8006], scheme="https",
        match=title("Proxmox Mail Gateway"), icon="proxmox"),
    App("truenas", "TrueNAS", "Infrastructure", [443, 80], scheme="https", match=title("TrueNAS"), icon="truenas"),
    App("unraid", "Unraid", "Infrastructure", [80, 443], match=title("Unraid"), icon="unraid"),
    App("openmediavault", "OpenMediaVault", "Infrastructure", [80], match=title("openmediavault")),
    App("webmin", "Webmin", "Infrastructure", [10000], scheme="https", match=title("Webmin")),
    # --- network gear
    App("unifi", "UniFi Network", "Network", [8443], scheme="https", probe="/status",
        match=json_at("meta.server_version"), images=["unifi"], version=json_field("meta", "server_version")),
    App("unifi-os", "UniFi OS", "Network", [443], scheme="https", match=title_is("UniFi OS"), icon="unifi"),
    App("omada", "Omada Controller", "Network", [8043], scheme="https", match=title("Omada"), images=["omada"]),
    # --- reverse proxies, auth, VPN, DNS
    App("traefik", "Traefik", "Network", [8080], probe="/api/version", match=json_has("Codename"),
        images=["traefik"], version=json_field("Version"), stats=traefik_stats),
    App("technitium", "Technitium DNS", "Network", [5380], match=title("Technitium DNS"), images=["technitium"],
        stats=technitium_stats, needs="token"),
    App("authentik", "authentik", "Network", [9000, 9443], match=title_is("authentik"), images=["authentik"]),
    App("authelia", "Authelia", "Network", [9091], match=title("Authelia"), images=["authelia"]),
    App("keycloak", "Keycloak", "Network", [8080], match=title("Keycloak"), images=["keycloak"]),
    App("wg-easy", "WireGuard Easy", "Network", [51821], match=title_is("WireGuard"), images=["wg-easy"],
        icon="wireguard"),
    App("guacamole", "Guacamole", "Network", [8080], probe="/guacamole/", match=title("Guacamole"),
        images=["guacamole"], link_path="/guacamole/"),
    App("meshcentral", "MeshCentral", "Network", [443, 80], match=title("MeshCentral"), images=["meshcentral"]),
    App("netbox", "NetBox", "Network", [8080, 80], match=title("NetBox"), images=["netbox"]),
    App("adguard", "AdGuard Home", "Network", [3000, 80], match=title("AdGuard Home"), images=["adguard"],
        icon="adguard-home", stats=adguard_stats, needs="username + password"),
)
