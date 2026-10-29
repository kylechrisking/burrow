"""Links from a Caddyfile: a card for 10.0.0.130:8989 opens http://sonarr.homelab
when Caddy proxies that name to that address.

    [caddy]
    file = "/config/Caddyfile"     # mount it read-only next to sett.toml

Reads site blocks and the reverse_proxy lines inside them (also inside handle,
route and snippets brought in with import). Upstreams given by name are
resolved once. Explicit [links] and sett.url labels still win.
"""
from __future__ import annotations

import logging
import re
import socket
from pathlib import Path

log = logging.getLogger(__name__)

_COMMENT = re.compile(r"(^|\s)#.*$")


def _blocks(text: str) -> list[tuple[str, str]]:
    """Top-level blocks as (header, body). Brace matching, ignoring braces in quotes."""
    out, depth, header, body, cur = [], 0, "", [], []
    in_quote = False
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).rstrip()
        if not line.strip():
            continue
        for ch in line:
            if ch == '"':
                in_quote = not in_quote
            if in_quote:
                (body if depth else cur).append(ch)
                continue
            if ch == "{":
                if depth == 0:
                    header, body = "".join(cur).strip(), []
                    cur = []
                else:
                    body.append(ch)
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append((header, "".join(body)))
                elif depth > 0:
                    body.append(ch)
                else:
                    depth = 0
            else:
                (body if depth else cur).append(ch)
        (body if depth else cur).append("\n")
    return out


def _upstreams(body: str, snippets: dict[str, str], seen: frozenset = frozenset()) -> list[str]:
    ups = []
    for line in body.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "import" and len(words) > 1 and words[1] in snippets and words[1] not in seen:
            ups += _upstreams(snippets[words[1]], snippets, seen | {words[1]})
        elif words[0] == "reverse_proxy":
            # reverse_proxy [matcher] upstreams... ; a matcher starts with @ or / or is *
            ups += [w for w in words[1:] if not w.startswith(("@", "/", "*")) and w != "{"]
    return ups


def parse(text: str) -> list[tuple[str, list[str]]]:
    """[(site URL, [upstream, ...]), ...] for every site block that proxies somewhere."""
    blocks = _blocks(text)
    auto_https_off = any(h == "" and re.search(r"\bauto_https\s+off\b", b) for h, b in blocks)
    snippets = {h[1:-1]: b for h, b in blocks if h.startswith("(") and h.endswith(")")}
    out = []
    for header, body in blocks:
        if not header or header.startswith("("):
            continue
        ups = _upstreams(body, snippets)
        if not ups:
            continue
        for addr in re.split(r"[,\s]+", header):
            url = _site_url(addr, auto_https_off)
            if url:
                out.append((url, ups))
                break   # the first address of a block is the one to link to
    return out


def _site_url(addr: str, auto_https_off: bool) -> str | None:
    addr = addr.strip()
    if not addr or addr.startswith((":", "*")) or "{" in addr:
        return None   # ":80" or a wildcard or a placeholder: no name to link to
    if addr.startswith(("http://", "https://")):
        scheme, rest = addr.split("://", 1)
    else:
        rest = addr
        scheme = "http" if auto_https_off or rest.endswith(":80") else "https"
    host, _, port = rest.partition("/")[0].partition(":")
    if not host or host == "localhost":
        return None
    default = {"http": "80", "https": "443"}[scheme]
    return f"{scheme}://{host}" + (f":{port}" if port and port != default else "")


def _target(upstream: str, resolve) -> str | None:
    """'10.0.0.130:8989', 'http://media.homelab:8989', 'sonarr:8989' -> 'ip:port'."""
    u = upstream.split("://", 1)[-1].rstrip("/")
    host, _, port = u.rpartition(":")
    if not host:
        host, port = u, "80"
    if not port.isdigit() or "{" in host:
        return None
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        host = resolve(host)
        if not host:
            return None
    return f"{host}:{port}"


def _resolve(name: str) -> str | None:
    try:
        return socket.gethostbyname(name)
    except OSError:
        return None


def links(text: str, resolve=_resolve) -> dict[str, str]:
    """{"ip:port": "http://name.homelab"} from a Caddyfile's text."""
    out: dict[str, str] = {}
    cache: dict[str, str | None] = {}

    def res(name):
        if name not in cache:
            cache[name] = resolve(name)
        return cache[name]
    for url, ups in parse(text):
        for up in ups:
            t = _target(up, res)
            if t and t not in out:
                out[t] = url
    return out


def load(path: str | Path) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        log.warning("caddy: can't read %s: %s", path, e)
        return {}
    got = links(text)
    log.info("caddy: %d links from %s", len(got), path)
    return got
