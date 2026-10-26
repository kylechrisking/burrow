"""Home automation."""
from __future__ import annotations

from ..model import Stat
from .base import App, Ctx, count, register, title, title_is


def hass_stats(ctx: Ctx) -> list[Stat]:
    token = ctx.secret.get("token")
    if not token:
        return []
    h = {"Authorization": f"Bearer {token}"}
    ctx.version = ctx.json("/api/config", headers=dict(h)).get("version")
    states = ctx.json("/api/states", headers=dict(h))
    unavailable = sum(1 for s in states if s.get("state") == "unavailable")
    return [Stat("Entities", count(len(states))), Stat("Unavailable", str(unavailable), "warn" if unavailable else "")]


def frigate_stats(ctx: Ctx) -> list[Stat]:
    s = ctx.json("/api/stats")
    ctx.version = (s.get("service") or {}).get("version")
    cams = s.get("cameras", {}) or {}
    fps = sum(float(c.get("camera_fps", 0)) for c in cams.values())
    return [Stat("Cameras", str(len(cams))), Stat("FPS", f"{fps:.0f}")]


register(
    App("home-assistant", "Home Assistant", "Home", [8123], match=title("Home Assistant"),
        images=["home-assistant", "homeassistant"], stats=hass_stats, needs="token"),
    App("node-red", "Node-RED", "Home", [1880], match=title_is("Node-RED"), images=["node-red"]),
    App("esphome", "ESPHome", "Home", [6052], match=title("ESPHome"), images=["esphome"]),
    App("zigbee2mqtt", "Zigbee2MQTT", "Home", [8080], match=title("Zigbee2MQTT"), images=["zigbee2mqtt"]),
    App("frigate", "Frigate", "Home", [5000, 8971], match=title_is("Frigate"), images=["frigate"], stats=frigate_stats),
    App("scrypted", "Scrypted", "Home", [10443], scheme="https", match=title("Scrypted"), images=["scrypted"]),
    App("mosquitto", "Mosquitto (MQTT)", "Home", [1883], kind="mqtt", images=["mosquitto", "mqtt"]),
)
