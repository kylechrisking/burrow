"""Numbers out of display strings, threshold rules, and short history for sparklines."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque

from .model import Stat

log = logging.getLogger(__name__)

# A count suffix (12.6k, 1.5M) scales; a unit (74 KB/s, 3 GB, 5m, 12 ms) does not.
_NUM = re.compile(r"(-?\d+(?:\.\d+)?)(?:\s?([kKMGT])(?![A-Za-z]))?")
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
    return float(m.group(1)) * _SCALE[(m.group(2) or "").lower()]


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

    # ---- persistence: sparklines survive a restart ---------------------------

    def save(self, path) -> None:
        """Write every series to `path` atomically (temp file, then rename)."""
        with self._lock:
            doc = {"v": 1, "every": self.every, "saved": time.time(),
                   "series": {k: {"last": self._last.get(k, 0), "values": list(v)} for k, v in self._data.items()}}
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"))
        os.replace(tmp, path)

    def load(self, path) -> int:
        """Read a saved history back. Series whose newest sample is older than
        the window are dropped; returns how many series were kept."""
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as e:
            log.warning("history: could not read %s: %s", path, e)
            return 0
        if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(doc.get("series"), dict):
            return 0
        horizon = time.time() - self.size * self.every
        kept = 0
        with self._lock:
            for k, d in doc["series"].items():
                try:
                    last = float(d["last"])
                    values = [float(x) for x in d["values"]][-self.size:]
                except (KeyError, TypeError, ValueError):
                    continue
                if last < horizon or not values:
                    continue
                self._data[k] = deque(values, maxlen=self.size)
                self._last[k] = last
                kept += 1
        return kept
