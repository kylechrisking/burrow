"""Replay real app responses recorded from the test lab (tools/record_fixtures.py).

For every recorded app:
  - with its Docker image as the hint, the right app is chosen;
  - by port alone (no Docker, no hint), the right app is chosen when the app
    was on one of its default ports;
  - no other fingerprint for that port claims it (no false positives).
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from sett import apps, net
from sett.apps.base import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures" / "testlab.json"


def _fake_fetch(responses):
    def fetch(address, port, path, scheme, cache=None, timeout=4.0):
        r = responses.get(path)
        if r is None:
            return None
        return r["scheme"], net.Response(f"{r['scheme']}://{address}:{port}{path}", r["status"],
                                         r["headers"], r["body"].encode())
    return fetch


def _first_match(port, hint, responses):
    with mock.patch.object(apps, "fetch", _fake_fetch(responses)):
        for app in apps.candidates(port, hint):
            if app.kind != "http":
                continue
            pr = apps.probe(app, "127.0.0.1", port)
            if pr and (pr.matched or (hint and app.hinted_by(hint))):
                return app.id
    return None


def _all_matches(port, responses):
    with mock.patch.object(apps, "fetch", _fake_fetch(responses)):
        return {a.id for a in apps.candidates(port) if a.kind == "http"
                and (pr := apps.probe(a, "127.0.0.1", port)) and pr.matched}


@unittest.skipUnless(FIXTURES.exists(), "no recorded fixtures")
class RecordedApps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURES.read_text())

    def test_identified_from_image(self):
        for name, f in self.data.items():
            if f["expect"] and REGISTRY[f["expect"]].kind == "http":
                with self.subTest(name):
                    self.assertEqual(_first_match(f["port"], f["image"], f["responses"]), f["expect"])

    def test_identified_by_port_alone(self):
        for name, f in self.data.items():
            exp = f["expect"]
            if exp and REGISTRY[exp].kind == "http" and f["port"] in REGISTRY[exp].ports:
                with self.subTest(name):
                    self.assertEqual(_first_match(f["port"], "", f["responses"]), exp)

    def test_no_false_positives(self):
        for name, f in self.data.items():
            with self.subTest(name):
                wrong = _all_matches(f["port"], f["responses"]) - {f["expect"]}
                self.assertFalse(wrong, f"{name} also matched {sorted(wrong)}")


if __name__ == "__main__":
    unittest.main()
