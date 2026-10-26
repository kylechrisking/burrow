"""Monitoring, metrics and notification apps."""
from __future__ import annotations

from ..model import Stat
from .base import App, Ctx, any_of, count, json_at, json_field, json_has, register, title, title_is


def glances_stats(ctx: Ctx) -> list[Stat]:
    for api in ("4", "3"):
        r = ctx.get(f"/api/{api}/quicklook")
        q = r.json() if r.status == 200 else None
        if isinstance(q, dict) and "cpu" in q:
            out = [Stat("CPU", f"{q['cpu']:.0f}%"), Stat("RAM", f"{q.get('mem', 0):.0f}%")]
            if q.get("swap") is not None:
                out.append(Stat("Swap", f"{q['swap']:.0f}%"))
            return out
    return []


def netdata_stats(ctx: Ctx) -> list[Stat]:
    info = ctx.json("/api/v1/info")
    ctx.version = info.get("version")
    a = info.get("alarms", {})
    warn, crit = a.get("warning", 0), a.get("critical", 0)
    return [Stat("Critical", str(crit), "bad" if crit else "ok"), Stat("Warning", str(warn), "warn" if warn else ""),
            Stat("Normal", str(a.get("normal", 0)))]


def prometheus_stats(ctx: Ctx) -> list[Stat]:
    targets = ctx.json("/api/v1/targets?state=active").get("data", {}).get("activeTargets", [])
    up = sum(1 for t in targets if t.get("health") == "up")
    return [Stat("Targets up", f"{up}/{len(targets)}", "ok" if up == len(targets) else "warn")]


def scrutiny_stats(ctx: Ctx) -> list[Stat]:
    summary = ctx.json("/api/summary").get("data", {}).get("summary", {}) or {}
    failed = sum(1 for d in summary.values() if (d.get("device") or {}).get("device_status", 0))
    return [Stat("Drives", str(len(summary))), Stat("Failing", str(failed), "bad" if failed else "ok")]


def speedtest_stats(ctx: Ctx) -> list[Stat]:
    h = {"Authorization": f"Bearer {ctx.secret['token']}"} if ctx.secret.get("token") else {}
    r = ctx.get("/api/speedtest/latest", headers=h)
    d = (r.json() or {}).get("data") if r.status == 200 else None
    if not d:
        return []
    return [Stat("Down", f"{float(d.get('download', 0)):.0f} Mbps"), Stat("Up", f"{float(d.get('upload', 0)):.0f} Mbps"),
            Stat("Ping", f"{float(d.get('ping', 0)):.0f} ms")]


register(
    App("glances", "Glances", "Monitoring", [61208], match=title("Glances"), images=["glances"], stats=glances_stats),
    App("netdata", "Netdata", "Monitoring", [19999], probe="/api/v1/info", match=json_has("mirrored_hosts"),
        images=["netdata"], version=json_field("version"), stats=netdata_stats),
    App("prometheus", "Prometheus", "Monitoring", [9090], probe="/api/v1/status/buildinfo",
        match=json_at("data.goVersion"), images=["prometheus"], version=json_field("data", "version"),
        stats=prometheus_stats),
    App("influxdb", "InfluxDB", "Monitoring", [8086], probe="/health", match=json_has("name", "influxdb"),
        images=["influxdb"], version=json_field("version")),
    App("scrutiny", "Scrutiny", "Monitoring", [8080], probe="/web/dashboard", match=title_is("Scrutiny"),
        images=["scrutiny"], stats=scrutiny_stats),
    App("dozzle", "Dozzle", "Monitoring", [8080], match=title_is("Dozzle"), images=["dozzle"]),
    App("gotify", "Gotify", "Monitoring", [80], probe="/version", match=json_has("buildDate"),
        images=["gotify"], version=json_field("version")),
    App("ntfy", "ntfy", "Monitoring", [80], match=title_is("ntfy"), images=["ntfy"]),
    App("changedetection", "changedetection.io", "Monitoring", [5000], match=title("Change Detection"),
        images=["changedetection"]),
    App("speedtest-tracker", "Speedtest Tracker", "Monitoring", [80, 8765], match=title("Speedtest Tracker"),
        images=["speedtest-tracker"], stats=speedtest_stats),
    App("healthchecks", "Healthchecks", "Monitoring", [8000], match=title("Healthchecks"), images=["healthchecks"]),
    App("zabbix", "Zabbix", "Monitoring", [80, 8080], match=title("Zabbix"), images=["zabbix"]),
    App("netalertx", "NetAlertX", "Monitoring", [20211], match=any_of(title("NetAlertX"), title("Pi.Alert")),
        images=["netalertx", "pialert"]),
)
