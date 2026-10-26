"""Numbers out of display strings, threshold rules, and short history for sparklines."""
from __future__ import annotations

import re
import threading
import time
from collections import deque

from .model import Stat

_NUM = re.compile(r"(-?\d+(?:\.\d+)?)\s*([kKmMgGtT]?)")
_UNIT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(k|m|g|t)?(b|bit)?(/s|ps)?\b", re.I)
_SCALE = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}


def number(value) -> float | None:
    """The number a person sees: "3,327 GB" -> 3327, "7.4%" -> 7.4, "12.6k" -> 12600,
    "20/20" -> 20. Used by thresholds, so units are *not* converted."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = _NUM.search(str(value).replace(",", ""))
    if not m:
        return None
    n = float(m.group(1))
    return n * 1e3 if m.group(2) in ("k", "K") and not re.search(r"\d\s*[kK][bB]", str(value)) else n


def magnitude(value) -> float | None:
    """Comparable size for a sparkline: "74 KB/s" and "3.0 MB/s" land on one scale."""
    s = str(value).replace(",", "")
    m = _UNIT.search(s)
    if not m:
        return number(value)
    return float(m.group(1)) * _SCALE[(m.group(2) or "").lower()]


def _matches(value: str, rule) -> bool:
    options = rule if isinstance(rule, list) else [rule]
    return str(value).strip().lower() in {str(o).strip().lower() for o in options}


def apply_rules(stat: Stat, rule: dict) -> str:
    """Tone for a stat from a rule table:
        warn_above / bad_above / warn_below / bad_below  -> compare the number
        ok_if / warn_if / bad_if                         -> match the text (one value or a list)
        otherwise                                        -> tone when no text rule matched
    The worst tone that applies wins."""
    tones = []
    n = number(stat.value)
    if n is not None:
        if "bad_above" in rule and n > rule["bad_above"] or "bad_below" in rule and n < rule["bad_below"]:
            tones.append("bad")
        elif "warn_above" in rule and n > rule["warn_above"] or "warn_below" in rule and n < rule["warn_below"]:
            tones.append("warn")
        elif any(k in rule for k in ("warn_above", "bad_above", "warn_below", "bad_below")):
            tones.append("ok")
    text_rules = [t for t in ("bad", "warn", "ok") if f"{t}_if" in rule]
    hit = next((t for t in text_rules if _matches(stat.value, rule[f"{t}_if"])), None)
    if hit:
        tones.append(hit)
    elif text_rules and rule.get("otherwise"):
        tones.append(rule["otherwise"])
    for t in ("bad", "warn", "ok"):
        if t in tones:
            return t
    return stat.tone


class History:
    """Up to `size` samples per series, at most one per `every` seconds:
    by default an hour at one-minute resolution."""

    def __init__(self, size: int = 60, every: float = 60):
        self.size, self.every = size, every
        self._data: dict[str, deque] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def record(self, key: str, value) -> None:
        v = magnitude(value) if not isinstance(value, (int, float)) else float(value)
        if v is None:
            return
        now = time.time()
        with self._lock:
            if now - self._last.get(key, 0) < self.every:
                return
            self._last[key] = now
            self._data.setdefault(key, deque(maxlen=self.size)).append(round(v, 3))

    def snapshot(self, prefix: str = "") -> dict[str, list[float]]:
        with self._lock:
            return {k: list(v) for k, v in self._data.items() if k.startswith(prefix) and len(v) >= 2}
