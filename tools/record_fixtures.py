"""Record what every app in a Docker test lab answers, for offline regression tests.

Run on the Docker host of the test lab (see testlab/compose.yaml):

    python3 tools/record_fixtures.py [ADDRESS] > tests/fixtures/testlab.json

ADDRESS defaults to this machine's LAN IP. Not 127.0.0.1: inside an LXC,
Docker's published ports often don't answer on loopback.

For each published port it saves the response to "/" and to every probe path
a candidate fingerprint would ask for, plus which app the container really is
(its compose service name, or EXPECT below). tests/test_fixtures.py replays them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sett import apps, net                  # noqa: E402
from sett.apps.base import REGISTRY         # noqa: E402
from sett.providers.docker import DockerProvider  # noqa: E402

# compose service -> app id, where they differ. None: not an app we fingerprint.
EXPECT = {
    "overseerr": "seerr", "jellyseerr": "seerr", "paperless-redis": None,
    "whoami": None, "pihole": "pihole", "home-assistant": "home-assistant",
}
# (service, host port) -> app id, for ports of a container that are not the app's UI.
EXPECT_PORT = {("traefik", 8001): None}
KEEP_HEADERS = ("server", "content-type", "location", "x-version", "x-powered-by")
MAX_BODY = 24 * 1024


def record(address: str, endpoint: str = "unix:///var/run/docker.sock") -> dict:
    d = DockerProvider(endpoint, address)
    inv = d.discover()
    services = {}
    for e in inv.endpoints:
        svc = e.labels.get("com.docker.compose.service") or e.container
        expect = EXPECT.get(svc, svc if svc in REGISTRY else None)
        expect = EXPECT_PORT.get((svc, e.port), expect)
        image = e.hint.split()[0]
        paths = {"/"}
        for a in apps.candidates(e.port, e.hint):
            if a.kind == "http":
                paths.add(a.probe)
        responses = {}
        for path in sorted(paths):
            got = apps.fetch(address, e.port, path, "http", timeout=8)
            if got:
                scheme, r = got
                responses[path] = {
                    "scheme": scheme, "status": r.status,
                    "headers": {k: v for k, v in r.headers.items() if k in KEEP_HEADERS},
                    "body": r.body[:MAX_BODY].decode("utf-8", "replace"),
                }
        key = svc if svc not in services else f"{svc}:{e.port}"
        services[key] = {"image": image, "port": e.port, "expect": expect, "responses": responses}
        print(f"recorded {key:22} :{e.port:<6} {len(responses)} path(s)", file=sys.stderr)
    return services


if __name__ == "__main__":
    print(json.dumps(record(sys.argv[1] if len(sys.argv) > 1 else net.primary_ip()),
                     indent=1, sort_keys=True))
