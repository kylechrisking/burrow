"""Thresholds, stat shaping, history, the YAML reader and the Homepage importer."""
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from sett import miniyaml
from sett.engine import _card_stats, _shape_stats
from sett.history import History, apply_rules, magnitude, number
from sett.importer import convert
from sett.model import Service, Stat

HOMEPAGE = {
    "services.yaml": """
- Media:
    - Sonarr:
        href: http://sonarr.lan
        description: TV shows   # trailing comment
        icon: sonarr.png
        widget:
          type: sonarr
          url: http://10.0.0.5:8989
          key: s3cr3tk3y
    - Spotify:
        href: https://open.spotify.com
        icon: mdi-spotify
        widget:
          type: customapi
          url: http://10.0.0.9:8000/spotify
          refreshInterval: 20000
          mappings:
            - field: track
              label: Track
            - field: free_gb
              label: Free
              format: number
              suffix: " GB"
- Infra:
    - Network:
        - Router:
            href: "https://10.0.0.1"
            ping: 10.0.0.1
    - NAS:
        href: http://nas.lan
        siteMonitor: http://10.0.0.2:5000
""",
    "bookmarks.yaml": """
- Admin:
    - Proxmox:
        - abbr: PX
          href: https://10.0.0.3:8006
""",
    "settings.yaml": """
title: My Lab
theme: dark
color: emerald
layout:
  Media:
    tab: Home
  Infra:
    tab: Infra
  Admin:
    tab: Infra
""",
    "widgets.yaml": """
- openmeteo:
    label: Home
    latitude: 51.5
    longitude: -0.12
    units: metric
- search:
    provider: duckduckgo
- datetime:
    format:
      hour12: false
""",
}


class Rules(unittest.TestCase):
    def test_numbers_as_seen(self):
        self.assertEqual(number("3,327 GB"), 3327)
        self.assertEqual(number("7.4%"), 7.4)
        self.assertEqual(number("12.6k"), 12600)
        self.assertEqual(number("74 KB/s"), 74)
        self.assertIsNone(number("Paused"))

    def test_magnitude_puts_rates_on_one_scale(self):
        self.assertGreater(magnitude("3.0 MB/s"), magnitude("740 KB/s"))

    def test_threshold_and_text_rules(self):
        self.assertEqual(apply_rules(Stat("Free", "346 GB"), {"warn_below": 1500, "bad_below": 500}), "bad")
        self.assertEqual(apply_rules(Stat("Free", "900 GB"), {"warn_below": 1500, "bad_below": 500}), "warn")
        self.assertEqual(apply_rules(Stat("Free", "3,327 GB"), {"warn_below": 1500}), "ok")
        self.assertEqual(apply_rules(Stat("Guard", "ok"), {"ok_if": "ok", "otherwise": "warn"}), "ok")
        self.assertEqual(apply_rules(Stat("Guard", "paused"), {"ok_if": "ok", "otherwise": "warn"}), "warn")
        self.assertEqual(apply_rules(Stat("State", "Off"), {"bad_if": ["off", "error"]}), "bad")

    def test_card_field_rule(self):
        [st] = _card_stats([["{free:,} GB", "Free", {"bad_below": 500}]], {"free": 346})
        self.assertEqual((st.value, st.tone), ("346 GB", "bad"))

    def test_override_rename_pick_and_tone(self):
        s = Service("a:1", "sonarr", "Sonarr", "Media", "sonarr", None, "a", 1,
                    stats=[Stat("Queue", "5"), Stat("Missing", "12"), Stat("Health", "OK", "ok")])
        out = _shape_stats(s, {"rename": {"Queue": "Downloading"}, "stats": ["Missing", "Downloading"],
                               "tones": {"Missing": {"warn_above": 0}}})
        self.assertEqual([(d["label"], d["tone"], d["series"]) for d in out],
                         [("Missing", "warn", "a:1|Missing"), ("Downloading", "", "a:1|Queue")])

    def test_history_keeps_one_sample_per_interval(self):
        h = History(size=3, every=0)
        for v in ("1 MB/s", "2 MB/s", "3 MB/s", "4 MB/s"):
            h.record("x", v)
        self.assertEqual(h.snapshot()["x"], [2e6, 3e6, 4e6])


class Yaml(unittest.TestCase):
    def test_homepage_shapes(self):
        d = miniyaml.load(HOMEPAGE["services.yaml"])
        sonarr = d[0]["Media"][0]["Sonarr"]
        self.assertEqual(sonarr["description"], "TV shows")
        self.assertEqual(sonarr["widget"]["key"], "s3cr3tk3y")
        self.assertEqual(d[0]["Media"][1]["Spotify"]["widget"]["mappings"][1]["suffix"], " GB")
        self.assertEqual(d[1]["Infra"][0]["Network"][0]["Router"]["href"], "https://10.0.0.1")

    def test_scalars(self):
        self.assertEqual(miniyaml.load("a: [1, 'two', true]\nb: ~\nc: 'it''s'\nd: http://x:80/y"),
                         {"a": [1, "two", True], "b": None, "c": "it's", "d": "http://x:80/y"})


class Importer(unittest.TestCase):
    def test_convert_homepage_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in HOMEPAGE.items():
                Path(tmp, name).write_text(text)
            toml, secrets, notes = convert(Path(tmp))
        cfg = tomllib.loads(toml)
        self.assertNotIn("s3cr3tk3y", toml)                       # credentials never in the config
        self.assertEqual(secrets, {"sonarr": {"api_key": "s3cr3tk3y"}})
        self.assertEqual(cfg["title"], "My Lab")
        self.assertEqual(cfg["theme"], "midnight")
        self.assertEqual(cfg["search"], "duckduckgo")
        self.assertEqual(cfg["ui"], {"accent": "#34d399", "clock": "24h"})
        self.assertEqual(cfg["weather"]["latitude"], 51.5)
        self.assertEqual(cfg["override"]["sonarr"]["description"], "TV shows")
        self.assertEqual(cfg["links"]["10.0.0.5:8989"], "http://sonarr.lan")
        [card] = cfg["card"]
        self.assertEqual(card["fields"], [["track", "Track"], ["{free_gb} GB", "Free"]])
        self.assertEqual(card["refresh"], 20)
        self.assertEqual(card["icon"], "mdi-spotify")
        router = next(s for s in cfg["service"] if s["name"] == "Router")
        self.assertEqual((router["category"], router["check"]), ("Network", False))
        nas = next(s for s in cfg["service"] if s["name"] == "NAS")
        self.assertEqual(nas["check"], "http://10.0.0.2:5000")
        self.assertEqual([v["name"] for v in cfg["view"]], ["Home", "Infra"])
        self.assertEqual(cfg["view"][1]["links"], ["Admin"])
        self.assertEqual(cfg["bookmark"][0]["abbr"], "PX")

    def test_secrets_file_is_private(self):
        if os.name != "posix":
            self.skipTest("file modes are POSIX-only")
        from sett.importer import main
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in HOMEPAGE.items():
                Path(tmp, name).write_text(text)
            main([tmp, "-o", f"{tmp}/sett.toml", "--secrets", f"{tmp}/apps.toml"])
            self.assertEqual(os.stat(f"{tmp}/apps.toml").st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
