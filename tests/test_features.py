"""Sign-in and 2FA, live events, persisted history, stable ids, Proxmox health,
and the discovery fixes that came with them."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="sett-test-")
os.environ["SETT_DATA"] = TMP   # before Engine is imported anywhere: keep tests off /tmp/sett-cache

from sett import auth as A  # noqa: E402
from sett.config import Config  # noqa: E402
from sett.engine import Engine  # noqa: E402
from sett.history import History, number  # noqa: E402
from sett.model import Endpoint, Service  # noqa: E402
from sett.providers import docker as D  # noqa: E402
from sett.providers import proxmox as P  # noqa: E402
from sett.server import make_handler  # noqa: E402

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # ggignore: "12345678901234567890", the RFC 6238 test vector


class Numbers(unittest.TestCase):
    def test_count_suffixes_scale_and_units_do_not(self):
        self.assertEqual(number("1.5M"), 1.5e6)
        self.assertEqual(number("2 G"), 2e9)
        self.assertEqual(number("3 GB"), 3)
        self.assertEqual(number("5m"), 5)          # minutes, not mega
        self.assertEqual(number("12 ms"), 12)


class Persistence(unittest.TestCase):
    def test_round_trip_and_stale_series_dropped(self):
        h = History(size=5, every=0)
        for v in (1, 2, 3):
            h.record("a|x", v)
        f = Path(TMP) / "h.json"
        h.save(f)
        doc = json.loads(f.read_text())
        doc["series"]["old|x"] = {"last": time.time() - 99999, "values": [1, 2]}
        f.write_text(json.dumps(doc))
        h2 = History(size=5, every=60)
        self.assertEqual(h2.load(f), 1)
        self.assertEqual(h2.snapshot(), {"a|x": [1.0, 2.0, 3.0]})

    def test_missing_or_broken_file_is_harmless(self):
        h = History()
        self.assertEqual(h.load(Path(TMP) / "nope.json"), 0)
        (Path(TMP) / "bad.json").write_text("{not json")
        self.assertEqual(h.load(Path(TMP) / "bad.json"), 0)


class StableKeys(unittest.TestCase):
    def test_key_follows_app_and_host_not_address(self):
        from sett.apps.base import REGISTRY
        e = Engine(Config())
        a = e._service(REGISTRY["sonarr"], "10.0.0.5", 8989, {"host": "pve:lxc/105", "ep": None, "linkable": True}, None)
        b = e._service(REGISTRY["sonarr"], "10.0.0.77", 8989, {"host": "pve:lxc/105", "ep": None, "linkable": True}, None)
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.key, b.key)
        self.assertEqual(a.key, "sonarr@pve:lxc/105")

    def test_service_without_host_keeps_its_address(self):
        s = Service("1.2.3.4:80", "x", "X", "c", "x", None, "1.2.3.4", 80)
        self.assertEqual(s.key, "1.2.3.4:80")

    def test_down_since_set_once_and_cleared(self):
        e = Engine(Config())
        s = Service("manual:x", "manual", "X", "Links", "x", None, "127.0.0.1", 9, check="http://127.0.0.1:9/",
                    manual=True)
        e._check(s)
        self.assertEqual(s.status, "down")
        first = s.down_since
        self.assertIsNotNone(first)
        e._check(s)
        self.assertEqual(s.down_since, first)
        self.assertEqual(s.error, "Connection refused")


class DockerPorts(unittest.TestCase):
    def test_loopback_only_ports_are_skipped(self):
        p = D.DockerProvider("tcp://10.0.0.9:2375")
        answers = {
            "/info": {"Name": "box"},
            "/containers/json?all=1": [{"Id": "abc", "Names": ["/app"], "Image": "img", "State": "running",
                                        "Ports": [{"PublicPort": 8080, "Type": "tcp", "IP": "127.0.0.1"},
                                                  {"PublicPort": 9000, "Type": "tcp", "IP": "0.0.0.0"}]}],
        }
        p._get = answers.__getitem__
        inv = p.discover()
        self.assertEqual([e.port for e in inv.endpoints], [9000])


class ProxmoxGuests(unittest.TestCase):
    def provider(self, answers):
        p = P.ProxmoxProvider("https://pve:8006", "t")

        def get(path):
            v = answers.get(path)
            if isinstance(v, Exception):
                raise v
            if v is None:
                raise RuntimeError("HTTP 404")
            return v
        p._get = get
        return p

    def test_hanging_agent_does_not_break_discovery(self):
        p = self.provider({"/nodes/a/qemu/100/config": {},
                           "/nodes/a/qemu/100/agent/network-get-interfaces": TimeoutError("timed out")})
        self.assertIsNone(p._guest_ip("qemu", "a", 100, True))

    def test_docker_bridge_is_not_the_guest_ip(self):
        p = self.provider({
            "/nodes/a/qemu/100/config": {},
            "/nodes/a/qemu/100/agent/network-get-interfaces": {"result": [
                {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
                {"name": "docker0", "ip-addresses": [{"ip-address": "172.17.0.1", "ip-address-type": "ipv4"}]},
                {"name": "ens18", "ip-addresses": [{"ip-address": "10.0.0.50", "ip-address-type": "ipv4"}]}]},
            "/nodes/a/lxc/101/config": {},
            "/nodes/a/lxc/101/interfaces": [{"name": "br-1234", "inet": "172.18.0.1/16"},
                                            {"name": "eth0", "inet": "10.0.0.51/24"}],
        })
        self.assertEqual(p._guest_ip("qemu", "a", 100, True), "10.0.0.50")
        self.assertEqual(p._guest_ip("lxc", "a", 101, True), "10.0.0.51")

    def test_node_health(self):
        now = time.time()
        p = self.provider({
            "/nodes/a/disks/zfs": [{"name": "rpool", "health": "ONLINE", "frag": 3},
                                   {"name": "tank", "health": "DEGRADED"}],
            "/nodes/a/disks/list": [{"devpath": "/dev/sda", "health": "PASSED", "wearout": 95},
                                    {"devpath": "/dev/nvme0n1", "health": "OK", "wearout": 8},
                                    {"devpath": "/dev/sdb", "health": "FAILED"}],
            "/nodes/a/tasks?typefilter=vzdump&limit=5": [
                {"type": "vzdump", "status": "OK", "starttime": now - 7200},
                {"type": "vzdump", "status": "job errors", "starttime": now - 3600}],
        })
        h = {x["label"]: x for x in p.node_health("a")}
        self.assertEqual((h["ZFS rpool"]["tone"], h["ZFS tank"]["tone"]), ("ok", "warn"))
        self.assertEqual((h["SMART"]["tone"], h["SMART"]["detail"]), ("bad", "sdb"))
        self.assertEqual((h["Wear"]["value"], h["Wear"]["tone"]), ("8% left", "bad"))
        self.assertEqual((h["Backup"]["value"], h["Backup"]["tone"]), ("failed", "bad"))

    def test_node_without_permissions_shows_nothing(self):
        self.assertEqual(self.provider({}).node_health("a"), [])

    def test_backup_states(self):
        now = 1_000_000
        self.assertEqual(P.backup_health({"status": "OK", "starttime": now - 3600}, 0, now)["tone"], "ok")
        self.assertEqual(P.backup_health({"status": "OK", "starttime": now - 9 * 86400}, 8 * 86400, now)["tone"], "warn")
        self.assertEqual(P.backup_health({"status": "WARNINGS: 2", "starttime": now}, 0, now)["tone"], "warn")
        self.assertEqual(P.backup_health({"starttime": now}, 0, now)["value"], "running")


class MoreHealth(unittest.TestCase):
    def test_docker_health_from_status(self):
        self.assertEqual(D._health("Up 3 hours (unhealthy)"), "unhealthy")
        self.assertEqual(D._health("Up 5 seconds (health: starting)"), "starting")
        self.assertEqual(D._health("Up 2 days"), "")

    def test_unhealthy_container_reaches_its_card(self):
        from sett.model import Container, Host, Inventory
        e = Engine(Config())
        e.inventories = {"docker:x": Inventory(hosts=[Host("docker:box", "box", "docker", "running", "10.0.0.9",
                                                            containers=[Container("web", "nginx", "running",
                                                                                  "Up 1h (unhealthy)", "unhealthy")])])}
        e.services = {"10.0.0.9:80": Service("10.0.0.9:80", "container", "web", "Containers", "nginx", "docker:box",
                                             "10.0.0.9", 80, container="web", status="up")}
        [svc] = e.state()["services"]
        self.assertEqual(svc["health"], "unhealthy")

    def test_certificate_expiry(self):
        now = 1_000_000
        self.assertEqual(P.cert_health(now + 400 * 86400, now)["tone"], "ok")
        self.assertEqual(P.cert_health(now + 20 * 86400, now)["value"], "20d left")
        self.assertEqual(P.cert_health(now + 20 * 86400, now)["tone"], "warn")
        self.assertEqual(P.cert_health(now - 1, now)["tone"], "bad")

    def test_updates_and_expiring_cert_on_node(self):
        soon = time.time() + 3 * 86400
        p = ProxmoxGuests().provider({
            "/nodes/a/certificates/info": [{"filename": "pve-ssl.pem", "notafter": time.time() + 900 * 86400},
                                           {"filename": "pveproxy-ssl.pem", "notafter": soon}],
            "/nodes/a/apt/update": [{"Package": "pve-manager"}, {"Package": "openssl"}],
        })
        h = {x["label"]: x for x in p.node_health("a")}
        self.assertEqual(h["Certificate"]["tone"], "bad")
        self.assertEqual((h["Updates"]["value"], h["Updates"]["detail"]), ("2", "openssl, pve-manager"))


class Qr(unittest.TestCase):
    def test_reed_solomon_known_answer(self):
        from sett.qr import _rs_divisor, _rs_remainder
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]   # "HELLO WORLD", 1-M
        self.assertEqual(_rs_remainder(data, _rs_divisor(10)), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_shape_finders_and_format(self):
        from sett import qr
        uri = A.otpauth_uri("kyle", A.new_totp_secret())
        m = qr.encode(uri)
        size = len(m)
        self.assertEqual((size - 17) % 4, 0)
        finder = [[max(abs(x - 3), abs(y - 3)) != 2 for x in range(7)] for y in range(7)]
        for ox, oy in ((0, 0), (size - 7, 0), (0, size - 7)):
            self.assertEqual([row[ox:ox + 7] for row in m[oy:oy + 7]], finder)
        # both copies of the 15 format bits agree and are a valid BCH codeword
        first = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
        second = [m[8][size - 1 - i] for i in range(8)] + [m[size - 15 + i][8] for i in range(8, 15)]
        self.assertEqual(first, second)
        bits = sum(b << i for i, b in enumerate(first)) ^ 0x5412
        rem = bits
        for i in range(14, 9, -1):
            if rem >> i & 1:
                rem ^= 0x537 << (i - 10)
        self.assertEqual(rem, 0)
        self.assertEqual(bits >> 13, 0)   # error correction level M

    def test_too_long(self):
        from sett import qr
        with self.assertRaises(ValueError):
            qr.encode("x" * 300)


CADDYFILE = """
{
    auto_https off   # plain HTTP on .homelab, by choice
}

(proxy_headers) {
    header_up X-Real-IP {remote_host}
}

sonarr.homelab {
    reverse_proxy 10.0.0.130:8989
}
http://plex.homelab, plex.lan {
    import proxy_headers
    reverse_proxy http://10.0.0.130:32400 {
        header_up Host {upstream_hostport}
    }
}
home.homelab {
    handle /api/* {
        reverse_proxy 10.0.0.131:8099
    }
    handle {
        reverse_proxy 10.0.0.131:8099
    }
}
recipes.homelab {
    reverse_proxy tandoor:8080
}
:8443 {
    reverse_proxy 10.0.0.9:443
}
"""


class Caddy(unittest.TestCase):
    def test_links_from_a_caddyfile(self):
        from sett import caddy
        got = caddy.links(CADDYFILE, resolve=lambda n: {"tandoor": "10.0.0.140"}.get(n))
        self.assertEqual(got, {"10.0.0.130:8989": "http://sonarr.homelab", "10.0.0.130:32400": "http://plex.homelab",
                               "10.0.0.131:8099": "http://home.homelab", "10.0.0.140:8080": "http://recipes.homelab"})

    def test_https_unless_turned_off(self):
        from sett import caddy
        self.assertEqual(caddy.links("app.example.com {\n reverse_proxy 10.0.0.5:3000\n}\n", resolve=lambda n: None),
                         {"10.0.0.5:3000": "https://app.example.com"})

    def test_card_link_uses_the_name_but_labels_win(self):
        e = Engine(Config(caddy_links={"10.0.0.130:32400": "http://plex.homelab", "10.0.0.130:8989": "http://sonarr.homelab"}))
        e.services = {
            "p": Service("10.0.0.130:32400", "plex", "Plex", "Media", "plex", None, "10.0.0.130", 32400,
                         url="http://10.0.0.130:32400/web"),
            "s": Service("10.0.0.130:8989", "sonarr", "Sonarr", "Media", "sonarr", None, "10.0.0.130", 8989,
                         url="https://tv.example.com")}
        got = {d["name"]: d["url"] for d in e.state()["services"]}
        self.assertEqual(got, {"Plex": "http://plex.homelab/web", "Sonarr": "https://tv.example.com"})


class VitalsUptime(unittest.TestCase):
    ROW = {"id": "sett:sonarr@pve:lxc/105", "name": "Sonarr (media)",
           "uptime": {"24h": 100.0, "7d": 99.2, "30d": 97.5, "90d": 99.9}, "bars": [100.0, None, 90.0]}

    def test_badge_options(self):
        from sett.vitals import badge
        b = badge(self.ROW, {}, {}, "http://vitals/")
        self.assertEqual((b["text"], b["window"], b["tone"], b["style"], b["url"]), ("97.5%", "30d", "bad", "tag", "http://vitals/"))
        b = badge(self.ROW, {"window": "7d", "link": False}, {}, "http://vitals/")
        self.assertEqual((b["text"], b["tone"], b["url"]), ("99.20%", "warn", None))
        self.assertIsNone(badge(self.ROW, {"window": "24h", "hide_perfect": True}, {}, ""))
        self.assertIsNone(badge(self.ROW, {}, {"uptime": False}, ""))
        b = badge(self.ROW, {}, {"uptime_style": "bars", "uptime_window": "90d"}, "")
        self.assertEqual((b["window"], b["bars"]), ("90d", ["ok", None, "bad"]))

    def test_cards_get_uptime_by_key_or_name(self):
        from sett.vitals import Vitals
        doc = {"checks": [self.ROW, {"id": "config:Printer", "name": "Printer", "uptime": {"30d": 100.0}, "bars": []}]}
        e = Engine(Config(vitals={"url": "http://vitals:8098"}, overrides={"Wiki": {"uptime": False}}))
        e.vitals = Vitals(e.cfg.vitals)
        import sett.vitals as sb
        real = sb.net.get_json
        sb.net.get_json = lambda url, **kw: doc
        try:
            e.vitals.poll()
        finally:
            sb.net.get_json = real
        e.services = {
            "a": Service("a", "sonarr", "Sonarr", "Media", "sonarr", "pve:lxc/105", "10.0.0.5", 8989,
                         key="sonarr@pve:lxc/105", status="up"),
            "b": Service("manual:Printer", "manual", "Printer", "Links", "p", None, "", 80, manual=True),
            "c": Service("manual:Wiki", "manual", "Wiki", "Links", "w", None, "", 80, manual=True)}
        st = e.state()
        got = {s["name"]: s["uptime"] for s in st["services"]}
        self.assertEqual(got["Sonarr"]["text"], "97.5%")
        self.assertEqual(got["Printer"]["text"], "100%")
        self.assertIsNone(got["Wiki"])
        self.assertEqual(st["providers"][-1]["name"], "vitals")

    def test_agent_data_on_host_cards(self):
        from sett.vitals import Vitals
        from sett.model import Host, Inventory
        import sett.vitals as sb
        doc = {"checks": [
            {"id": "agent:pve1/host", "name": "pve1", "status": "up", "summary": "/ 40% · RAM 61% · 3 updates"},
            {"id": "agent:pve1/disk", "name": "pve1 · disk", "status": "down", "detail": "/mnt/tank 93%"},
            {"id": "agent:pve1/services", "name": "pve1 · services", "status": "up", "detail": "all running"},
            {"id": "agent:media/host", "name": "media", "status": "down", "summary": ""},
            {"id": "site:HomeLab", "name": "HomeLab", "status": "up", "detail": "all 6 up"}]}
        e = Engine(Config(vitals={"url": "http://vitals"}))
        e.vitals = Vitals(e.cfg.vitals)
        real = sb.net.get_json
        sb.net.get_json = lambda url, **kw: doc
        try:
            e.vitals.poll()
        finally:
            sb.net.get_json = real
        e.inventories = {"proxmox": Inventory(hosts=[
            Host("pve:node/pve1", "pve1", "node", "online", "10.0.0.124"),
            Host("pve:lxc/130", "media", "lxc", "running", "10.0.0.130", parent="pve:node/pve1"),
            Host("pve:lxc/131", "ai", "lxc", "running", "10.0.0.131", parent="pve:node/pve1")])}
        st = e.state()
        [node] = st["nodes"]
        self.assertEqual(node["agent"], {"name": "pve1", "status": "up", "summary": "/ 40% · RAM 61% · 3 updates",
                                         "problems": ["disk: /mnt/tank 93%"]})
        guests = {g["name"]: g["agent"] for g in node["guests"]}
        self.assertEqual(guests["media"]["status"], "down")
        self.assertIsNone(guests["ai"])
        self.assertEqual(st["sites"], [{"name": "HomeLab", "status": "up", "detail": "all 6 up", "url": "http://vitals/"}])

    def test_old_brock_section_still_read(self):
        import os
        import tempfile as tf
        from sett import config as C
        d = tf.mkdtemp()
        open(f"{d}/sett.toml", "w").write('[brock]\nurl = "http://old:8098"\n')
        os.environ["SETT_CONFIG"] = f"{d}/sett.toml"
        try:
            self.assertEqual(C.load().vitals["url"], "http://old:8098")
        finally:
            del os.environ["SETT_CONFIG"]

    def test_unauthorized_vitals_says_what_to_do(self):
        from sett.vitals import Vitals
        import sett.vitals as sb
        real = sb.net.get_json

        def boom(url, **kw):
            raise RuntimeError("HTTP 401 from http://vitals/api/status")
        sb.net.get_json = boom
        try:
            b = Vitals({"url": "http://vitals"})
            b.poll()
        finally:
            sb.net.get_json = real
        self.assertFalse(b.ok)
        self.assertIn("[vitals] token", b.error)


class CardEdits(unittest.TestCase):
    def setUp(self):
        f = Path(TMP) / "edits.json"
        if f.exists():
            f.unlink()

    tearDown = setUp

    def engine(self, **cfg):
        e = Engine(Config(**cfg))
        e.services = {"a": Service("10.0.0.5:8989", "sonarr", "Sonarr", "Media", "sonarr", "pve:lxc/105", "10.0.0.5", 8989,
                                   key="sonarr@pve:lxc/105", url="http://10.0.0.5:8989")}
        return e

    def test_edit_and_put_back(self):
        e = self.engine()
        e.set_edit("sonarr@pve:lxc/105", {"name": "TV", "category": "Downloads", "bogus": "x"})
        [d] = e.state()["services"]
        self.assertEqual((d["name"], d["category"], d["edited"]), ("TV", "Downloads", True))
        self.assertEqual(self.engine().edits, {"sonarr@pve:lxc/105": {"name": "TV", "category": "Downloads"}})  # kept
        e.set_edit("sonarr@pve:lxc/105", None)
        [d] = e.state()["services"]
        self.assertEqual((d["name"], d["edited"]), ("Sonarr", False))
        with self.assertRaises(ValueError):
            e.set_edit("sonarr@pve:lxc/105", {"url": "javascript:alert(1)"})

    def http(self, engine, body, headers=None):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/edit", method="POST",
                                       data=json.dumps(body).encode(),
                                       headers={"Content-Type": "application/json", **(headers or {})})
            try:
                with urllib.request.urlopen(r, timeout=5) as resp:
                    return resp.status
            except urllib.error.HTTPError as err:
                return err.code
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_who_may_edit(self):
        body = {"key": "sonarr@pve:lxc/105", "fields": {"name": "TV"}}
        self.assertEqual(self.http(self.engine(), body), 200)                                  # no sign-in configured
        self.assertEqual(self.http(self.engine(ui={"edit": False}), body), 403)               # turned off
        self.assertEqual(self.http(self.engine(), body, {"Origin": "http://evil.example"}), 403)
        signed = self.engine(auth={"api_tokens": {"me": "tok"}})
        self.assertEqual(self.http(signed, body), 401)                                        # sign-in on, not signed in
        self.assertEqual(self.http(signed, body, {"Authorization": "Bearer tok"}), 200)


class Passwords(unittest.TestCase):
    def test_hash_and_check(self):
        h = A.hash_password("correct horse", rounds=1000)
        self.assertTrue(A.check_password("correct horse", h))
        self.assertFalse(A.check_password("wrong", h))
        self.assertFalse(A.check_password("x", "garbage"))

    def test_totp_rfc_vector(self):
        self.assertEqual(A.totp(RFC_SECRET, 59 // 30), "287082")
        self.assertEqual(A.totp_counter("287 082", RFC_SECRET, now=59), 1)
        self.assertEqual(A.totp_counter("287082", RFC_SECRET, now=59 + 30), 1)   # one step of drift
        self.assertIsNone(A.totp_counter("287082", RFC_SECRET, now=59 + 90))
        self.assertIsNone(A.totp_counter("12", RFC_SECRET))


def auth_engine(**auth):
    e = Engine(Config(auth=auth))
    return e, A.Auth(e)


class Sessions(unittest.TestCase):
    def setUp(self):
        self.engine, self.auth = auth_engine(users={"kyle": {"password_hash": A.hash_password("pw-123456", rounds=1000)}})

    def test_session_round_trip_and_tamper(self):
        v, _ = self.auth.make_session("kyle")
        self.assertEqual(self.auth.check_session(v), "kyle")
        self.assertIsNone(self.auth.check_session(v[:-2] + "xx"))
        self.assertIsNone(self.auth.check_session("kyle|9999999999|x|y"))

    def test_password_change_signs_out(self):
        v, _ = self.auth.make_session("kyle")
        self.engine.cfg.auth["users"]["kyle"]["password_hash"] = A.hash_password("new-password", rounds=1000)
        self.assertIsNone(self.auth.check_session(v))

    def test_lockout_after_repeated_failures(self):
        for _ in range(A.MAX_FAILURES):
            self.assertFalse(self.auth.login("kyle", "nope", "", "9.9.9.9")[0])
        ok, msg = self.auth.login("kyle", "pw-123456", "", "9.9.9.9")
        self.assertFalse(ok)
        self.assertIn("Too many", msg)
        self.assertTrue(self.auth.login("kyle", "pw-123456", "", "8.8.8.8")[0])

    def test_totp_required_and_not_replayable(self):
        self.engine.cfg.auth["users"]["kyle"]["totp_secret"] = RFC_SECRET
        self.assertEqual(self.auth.login("kyle", "pw-123456", "", "1.1.1.1"), (False, "code required"))
        code = A.totp(RFC_SECRET, int(time.time() // 30))
        self.assertTrue(self.auth.login("kyle", "pw-123456", code, "1.1.1.1")[0])
        self.assertFalse(self.auth.login("kyle", "pw-123456", code, "1.1.1.1")[0])

    def test_proxy_header_only_from_trusted_proxy(self):
        self.engine.cfg.auth.update(proxy_header="Remote-User", trusted_proxies=["10.0.0.2/32"])
        self.assertEqual(self.auth.identify({"Remote-User": "amy"}, "10.0.0.2"), "amy")
        self.assertIsNone(self.auth.identify({"Remote-User": "amy"}, "10.0.0.3"))

    def test_secrets_file_auth_wins(self):
        self.engine.cfg.secrets = {"auth": {"api_tokens": {"agent": "tok"}}}
        self.assertEqual(self.auth.identify({"Authorization": "Bearer tok"}, "1.1.1.1"), "token:agent")
        self.assertIsNone(self.auth.identify({"Authorization": "Bearer nope"}, "1.1.1.1"))


class Http(unittest.TestCase):
    """The real handler on a local port."""

    @classmethod
    def setUpClass(cls):
        cls.engine, _ = auth_engine(users={"kyle": {"password_hash": A.hash_password("pw-123456", rounds=1000),
                                                    "totp_secret": RFC_SECRET}},
                                    api_tokens={"agent": "s3cret-token"})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.engine))
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, data=None, headers=None, method=None):
        h = {"Content-Type": "application/json"} if data is not None else {}
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
                                   headers=h, method=method)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(r, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_signed_out_gets_redirect_or_401(self):
        self.assertEqual(self.req("/")[0], 302)
        self.assertEqual(self.req("/api/state")[0], 401)
        self.assertEqual(self.req("/login")[0], 200)
        self.assertEqual(self.req("/healthz")[0], 200)
        self.assertEqual(self.req("/themes/midnight.css")[0], 200)

    def test_login_with_code_then_state(self):
        status, _, body = self.req("/api/login", {"user": "kyle", "password": "pw-123456"})
        self.assertEqual((status, json.loads(body)), (401, {"ok": False, "code": True}))
        code = A.totp(RFC_SECRET, int(time.time() // 30))
        status, headers, _ = self.req("/api/login", {"user": "kyle", "password": "pw-123456", "code": code})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        status, _, body = self.req("/api/state", headers={"Cookie": cookie})
        self.assertEqual((status, json.loads(body)["user"]), (200, "kyle"))

    def test_login_refuses_form_posts(self):
        r = urllib.request.Request(self.base + "/api/login", data=b"user=kyle", method="POST",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=5)
        self.assertEqual(cm.exception.code, 415)

    def test_api_token_reads_status(self):
        status, _, body = self.req("/api/status", headers={"Authorization": "Bearer s3cret-token"})
        self.assertEqual(status, 200)
        self.assertIn("services", json.loads(body))

    def test_unknown_icon_urls_are_not_fetched(self):
        h = {"Authorization": "Bearer s3cret-token"}
        self.assertEqual(self.req("/icons/url?u=http://169.254.169.254/latest", headers=h)[0], 404)

    def test_events_stream_sends_state(self):
        r = urllib.request.Request(self.base + "/api/events", headers={"Authorization": "Bearer s3cret-token"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            self.assertEqual(resp.headers["Content-Type"], "text/event-stream")
            line = resp.readline()
        self.assertTrue(line.startswith(b"data: "))
        self.assertIn("services", json.loads(line[6:]))


class ProxyOnly(unittest.TestCase):
    def test_no_redirect_loop_without_password_users(self):
        engine, _ = auth_engine(proxy_header="Remote-User", trusted_proxies=["10.9.9.9/32"])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            opener = urllib.request.build_opener(_NoRedirect)
            for path in ("/", "/login"):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    opener.open(base + path, timeout=5)
                self.assertEqual(cm.exception.code, 401 if path == "/" else 302)
        finally:
            httpd.shutdown()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


if __name__ == "__main__":
    unittest.main()
