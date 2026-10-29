"""Uptime from Vitals (https://github.com/kylechrisking/vitals) on Sett's cards.

    [vitals]
    url = "http://vitals.lan:8098"
    token = "..."            # one of Vitals' [tokens]; better in the secrets file under [vitals]
    window = "30d"           # 24h | 7d | 30d | 90d: the number shown
    style = "tag"            # tag: a small badge  stat: a stat tile  bars: a 30-day strip under the card
    warn_below = 99.5        # amber under this
    bad_below = 98           # red under this
    hide_perfect = false     # leave the badge off cards at 100%
    link = true              # the badge opens Vitals' page
    every = 60               # seconds between reads

Per card, in [override."<name or app>"]:
    uptime = false           # no uptime on this card
    uptime_window = "7d"
    uptime_style = "bars"

Vitals reports the services it watches through Sett under their Sett key, so
those match exactly; Vitals' own checks match a card with the same name.
"""
from __future__ import annotations

import logging

from . import net

log = logging.getLogger(__name__)

WINDOWS = ("24h", "7d", "30d", "90d")
STYLES = ("tag", "stat", "bars")


class Vitals:
    def __init__(self, conf: dict):
        self.url = conf["url"].rstrip("/")
        self.token = conf.get("token", "")
        self.every = float(conf.get("every", 60))
        self.by_key: dict[str, dict] = {}
        self.by_name: dict[str, dict] = {}
        self.hosts: dict[str, dict] = {}     # agent host name (lower) -> {summary, status, problems}
        self.error: str | None = None
        self.ok: bool | None = None
        self.at: float | None = None

    def poll(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            doc = net.get_json(f"{self.url}/api/status", headers=headers, timeout=10)
        except Exception as e:
            msg = str(e)
            if "HTTP 401" in msg:
                msg = "Vitals wants a token: set [vitals] token to one of Vitals' [tokens]"
            self.ok, self.error = False, msg[:200]
            log.info("vitals: %s", self.error)
            return
        by_key, by_name, hosts = {}, {}, {}
        for c in doc.get("checks", []):
            cid = str(c.get("id", ""))
            if cid.startswith("sett:"):
                by_key[cid[5:]] = c
            elif cid.startswith("agent:"):
                # Vitals' host agents: "agent:pve1/host" is the host, "agent:pve1/disk" one of its checks.
                host, _, part = cid[6:].partition("/")
                h = hosts.setdefault(host.lower(), {"name": host, "status": "pending", "summary": "", "problems": []})
                if part == "host":
                    h["status"], h["summary"] = c.get("status", "pending"), c.get("summary") or ""
                elif c.get("status") == "down":
                    label = str(c.get("name", part)).split("·")[-1].strip()
                    h["problems"].append(f"{label}: {c['detail']}" if c.get("detail") else label)
            else:
                by_name[str(c.get("name", "")).lower()] = c
        self.by_key, self.by_name, self.hosts = by_key, by_name, hosts
        self.ok, self.error = True, None

    def find(self, key: str, *names: str) -> dict | None:
        row = self.by_key.get(key)
        if row:
            return row
        for n in names:
            if n and n.lower() in self.by_name:
                return self.by_name[n.lower()]
        return None

    def host(self, *names: str) -> dict | None:
        """What Vitals' agent says about a host, matched by any of its names."""
        for n in names:
            if n and n.lower() in self.hosts:
                return self.hosts[n.lower()]
        return None

    def describe(self) -> str:
        return f"{len(self.by_key) + len(self.by_name)} checks"


def badge(row: dict, conf: dict, override: dict, page_url: str) -> dict | None:
    """What a card shows for one Vitals check, after the options and the card's override."""
    if override.get("uptime") is False or conf.get("enabled") is False:
        return None
    window = override.get("uptime_window") or conf.get("window", "30d")
    window = window if window in WINDOWS else "30d"
    style = override.get("uptime_style") or conf.get("style", "tag")
    style = style if style in STYLES else "tag"
    value = (row.get("uptime") or {}).get(window)
    if value is None:
        return None
    if conf.get("hide_perfect") and value >= 99.995:
        return None
    bad, warn = float(conf.get("bad_below", 98)), float(conf.get("warn_below", 99.5))
    tone = "bad" if value < bad else "warn" if value < warn else "ok"
    out = {"value": value, "text": _pct(value), "window": window, "style": style, "tone": tone,
           "url": page_url if conf.get("link", True) else None}
    if style == "bars":
        out["bars"] = [None if v is None else ("bad" if v < bad else "warn" if v < warn else "ok")
                       for v in row.get("bars") or []]
    return out


def _pct(v: float) -> str:
    if v >= 99.995:
        return "100%"
    return f"{v:.2f}%" if v >= 99 else f"{v:.1f}%"
