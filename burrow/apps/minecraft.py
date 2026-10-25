"""Minecraft Java server-list ping: version, players and MOTD, no plugin or RCON needed."""
from __future__ import annotations

import json
import socket
import struct
import time


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_varint(sock: socket.socket) -> int:
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("connection closed")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7
        if shift > 35:
            raise ValueError("varint too long")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def ping(host: str, port: int = 25565, timeout: float = 3.0) -> tuple[dict, int]:
    """Return (status json, latency ms)."""
    t0 = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        h = host.encode()
        hs = _varint(0) + _varint(767) + _varint(len(h)) + h + struct.pack(">H", port) + _varint(1)
        s.sendall(_varint(len(hs)) + hs + b"\x01\x00")
        _read_varint(s)                  # packet length
        if _read_varint(s) != 0:         # packet id
            raise ValueError("unexpected packet")
        data = _recv_exact(s, _read_varint(s))
    return json.loads(data), int((time.monotonic() - t0) * 1000)


def motd(desc) -> str:
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return (desc.get("text", "") + "".join(motd(e) for e in desc.get("extra", []))).strip()
    if isinstance(desc, list):
        return "".join(motd(e) for e in desc)
    return ""
