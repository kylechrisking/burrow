"""Just enough YAML to read a Homepage config, without a dependency.

Supports block mappings and sequences (including "- key: value" items),
plain/quoted scalars, numbers, booleans, null, simple [a, b] lists, comments
and | / > block scalars. Not supported: anchors, tags, multi-document files,
flow mappings. Homepage's files only use the supported part.
"""
from __future__ import annotations

import json
import re

_NUMBER = re.compile(r"^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def load(text: str):
    lines = []
    for raw in text.replace("\t", "    ").splitlines():
        s = _strip_comment(raw).rstrip()
        if not s.strip() or s.strip() in ("---", "..."):
            continue
        lines.append([len(s) - len(s.lstrip(" ")), s.strip(), raw])
    if not lines:
        return None
    value, _ = _block(lines, 0, lines[0][0])
    return value


def _strip_comment(line: str) -> str:
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'" and (i == 0 or line[i - 1] in " :-[,"):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _is_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _key_split(content: str):
    """'key: value' -> (key, value); None if the line is not a mapping entry."""
    quote = None
    for i, c in enumerate(content):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'" and i == 0:
            quote = c
        elif c == ":" and (i + 1 == len(content) or content[i + 1] == " "):
            return _scalar(content[:i]) if content[0] in "\"'" else content[:i].strip(), content[i + 1:].strip()
    return None


def _block(lines, i, ind):
    return _seq(lines, i, ind) if _is_item(lines[i][1]) else _map(lines, i, ind)


def _seq(lines, i, ind):
    out = []
    while i < len(lines) and lines[i][0] == ind and _is_item(lines[i][1]):
        content = lines[i][1]
        rest = content[1:].strip()
        if not rest:
            if i + 1 < len(lines) and lines[i + 1][0] > ind:
                v, i = _block(lines, i + 1, lines[i + 1][0])
            else:
                v, i = None, i + 1
            out.append(v)
        elif _key_split(rest) and rest[0] not in "\"'[":
            col = ind + len(content) - len(rest)      # the item's mapping starts at this column
            lines[i] = [col, rest, lines[i][2]]
            v, i = _map(lines, i, col)
            out.append(v)
        else:
            out.append(_scalar(rest))
            i += 1
    return out, i


def _map(lines, i, ind):
    out = {}
    while i < len(lines) and lines[i][0] == ind and not _is_item(lines[i][1]):
        kv = _key_split(lines[i][1])
        if kv is None:
            raise ValueError(f"cannot read YAML line: {lines[i][2].strip()!r}")
        key, rest = kv
        i += 1
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            body = []
            while i < len(lines) and lines[i][0] > ind:
                body.append(lines[i][1])
                i += 1
            out[key] = ("\n" if rest[0] == "|" else " ").join(body)
        elif rest:
            out[key] = _scalar(rest)
        elif i < len(lines) and (lines[i][0] > ind or (lines[i][0] == ind and _is_item(lines[i][1]))):
            out[key], i = _block(lines, i, lines[i][0])
        else:
            out[key] = None
    return out, i


def _scalar(s: str):
    s = s.strip()
    if not s:
        return None
    if s[0] == '"' and s.endswith('"') and len(s) > 1:
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    if s[0] == "'" and s.endswith("'") and len(s) > 1:
        return s[1:-1].replace("''", "'")
    if s[0] == "[" and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if _NUMBER.match(s):
        return float(s) if any(c in s for c in ".eE") else int(s)
    return s


def _split_flow(s: str) -> list[str]:
    parts, cur, quote, depth = [], "", None, 0
    for c in s:
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'":
            quote = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += c
    return parts + [cur]
