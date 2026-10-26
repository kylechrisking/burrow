"""MQTT: send a CONNECT, read the CONNACK. Proves a broker is there and whether it wants credentials."""
from __future__ import annotations

import socket
import time

# CONNECT, MQTT 3.1.1, clean session, keepalive 60, empty client id.
_CONNECT = bytes([0x10, 12, 0, 4]) + b"MQTT" + bytes([4, 0x02, 0, 60, 0, 0])
_RC = {0: "open", 4: "bad credentials", 5: "auth required"}


def connack(host: str, port: int = 1883, timeout: float = 3.0) -> tuple[int, int]:
    """Return (return code, latency ms). Raises if nothing MQTT-shaped answers."""
    t0 = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(_CONNECT)
        resp = s.recv(4)
        if len(resp) < 4 or resp[0] != 0x20 or resp[1] != 2:
            raise ValueError("not an MQTT broker")
        s.sendall(bytes([0xE0, 0]))   # DISCONNECT, politely
    return resp[3], int((time.monotonic() - t0) * 1000)


def describe(rc: int) -> str:
    return _RC.get(rc, f"refused ({rc})")
