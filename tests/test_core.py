"""Offline tests: fingerprint matching, candidate ordering, host merging. python -m unittest"""
import unittest

from burrow import apps
from burrow.apps.base import REGISTRY
from burrow.apps.minecraft import _varint, motd
from burrow.config import Config
from burrow.engine import Engine
from burrow.model import Container, Endpoint, Host, Inventory
from burrow.net import Response


def resp(body: str, status=200, headers=None):
    return Response("http://x", status, headers or {}, body.encode())


class Fingerprints(unittest.TestCase):
    def test_title_match(self):
        self.assertTrue(REGISTRY["sonarr"].match(resp("<html><title>Sonarr</title>")))
        self.assertFalse(REGISTRY["sonarr"].match(resp("<html><title>Radarr</title>")))

    def test_json_match(self):
        self.assertTrue(REGISTRY["flaresolverr"].match(resp('{"msg": "FlareSolverr is ready!", "version": "3.3"}')))
        self.assertEqual(REGISTRY["flaresolverr"].version(resp('{"msg": "FlareSolverr is ready!", "version": "3.3"}')), "3.3")

    def test_arr_version_from_html(self):
        r = resp("<script>window.Sonarr = { apiRoot: '/api/v3', version: '4.0.9.2244' };</script><title>Sonarr</title>")
        self.assertEqual(REGISTRY["sonarr"].version(r), "4.0.9.2244")

    def test_shared_port_candidates(self):
        ids = [a.id for a in apps.candidates(8080)]
        self.assertIn("qbittorrent", ids)
        self.assertIn("open-webui", ids)

    def test_hint_first_and_generic_last(self):
        self.assertEqual(apps.candidates(8080, "linuxserver/qbittorrent:latest qbittorrent")[0].id, "qbittorrent")
        self.assertEqual(apps.candidates(80)[-1].id, "caddy")

    def test_minecraft_helpers(self):
        self.assertEqual(_varint(300), b"\xac\x02")
        self.assertEqual(motd({"text": "A ", "extra": [{"text": "server"}]}), "A server")


class Merge(unittest.TestCase):
    def test_docker_engine_folds_into_guest_by_name(self):
        e = Engine(Config())
        e.inventories = {
            "proxmox": Inventory(hosts=[Host("pve:lxc/131", "ai", "lxc", "running", "192.168.1.131", "pve:node/a")]),
            "docker:local": Inventory(
                hosts=[Host("docker:ai", "ai", "docker", "running", None, containers=[Container("x", "img", "running")])],
                endpoints=[Endpoint("docker:ai", 3001, hint="louislam/uptime-kuma:1")]),
        }
        hosts, endpoints = e.merged()
        self.assertNotIn("docker:ai", hosts)
        self.assertEqual(len(hosts["pve:lxc/131"].containers), 1)
        self.assertEqual(endpoints[0].host_id, "pve:lxc/131")


if __name__ == "__main__":
    unittest.main()
