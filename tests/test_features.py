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

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # "12345678901234567890", RFC 6238 appendix B


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


if __name__ == "__main__":
    unittest.main()
