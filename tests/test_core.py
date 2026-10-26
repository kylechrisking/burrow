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


class Cards(unittest.TestCase):
    def test_keys_templates_and_dynamic_labels(self):
        from burrow.engine import _card_stats
        data = {"a": "698.58 -0.4%", "a_label": "VOO", "media_free_gb": 3327, "clients": 30}
        stats = _card_stats([["a", "{a_label}"], ["{media_free_gb:,} GB", "Free"], ["clients", "Clients"], ["nope", "Missing"]], data)
        self.assertEqual([(s.label, s.value) for s in stats],
                         [("VOO", "698.58 -0.4%"), ("Free", "3,327 GB"), ("Clients", "30"), ("Missing", "")])


class Labels(unittest.TestCase):
    def test_homepage_labels_are_borrowed(self):
        from burrow.providers.docker import with_borrowed_labels
        out = with_borrowed_labels({"homepage.name": "Who", "homepage.icon": "sonarr.png",
                                    "homepage.group": "Tools", "homepage.description": "d"})
        self.assertEqual((out["burrow.name"], out["burrow.icon"], out["burrow.category"], out["burrow.description"]),
                         ("Who", "sonarr", "Tools", "d"))

    def test_traefik_rule_becomes_link_and_tls_router_wins(self):
        from burrow.providers.docker import with_borrowed_labels
        out = with_borrowed_labels({
            "traefik.http.routers.a.rule": "Host(`app.lan`)",
            "traefik.http.routers.b.rule": "Host(`app.example.com`) && PathPrefix(`/`)",
            "traefik.http.routers.b.tls": "true"})
        self.assertEqual(out["burrow.url"], "https://app.example.com")

    def test_explicit_burrow_label_wins_and_caddy_is_read(self):
        from burrow.providers.docker import with_borrowed_labels
        self.assertEqual(with_borrowed_labels({"caddy": "media.example.com"})["burrow.url"], "https://media.example.com")
        self.assertEqual(with_borrowed_labels({"caddy": "x.com", "burrow.url": "http://mine"})["burrow.url"], "http://mine")


class Merge(unittest.TestCase):
    def test_tagged_guest_and_its_endpoints_are_hidden(self):
        e = Engine(Config(hide={"hosts": ["secret"]}))
        e.inventories = {
            "proxmox": Inventory(hosts=[
                Host("pve:lxc/160", "burrow-lab", "lxc", "running", "10.0.0.160", tags=["burrow-hide", "test"]),
                Host("pve:lxc/170", "secret", "lxc", "running", "10.0.0.170"),
                Host("pve:lxc/131", "ai", "lxc", "running", "10.0.0.131")]),
            "docker:x": Inventory(hosts=[Host("docker:burrow-lab", "burrow-lab", "docker")],
                                  endpoints=[Endpoint("docker:burrow-lab", 8096, hint="jellyfin")]),
        }
        hosts, endpoints = e.merged()
        self.assertEqual(sorted(hosts), ["pve:lxc/131"])
        self.assertEqual(endpoints, [])

    def test_docker_engine_folds_into_guest_by_name(self):
        e = Engine(Config())
        e.inventories = {
            "proxmox": Inventory(hosts=[Host("pve:lxc/131", "ai", "lxc", "running", "10.0.0.131", "pve:node/a")]),
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
