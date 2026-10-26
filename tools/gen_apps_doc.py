"""Regenerate docs/apps.md from the fingerprint catalog:  python tools/gen_apps_doc.py"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sett.apps.base import REGISTRY  # noqa: E402

verified = set()
fx = ROOT / "tests" / "fixtures" / "testlab.json"
if fx.exists():
    verified = {f["expect"] for f in json.loads(fx.read_text()).values() if f["expect"]}

by_cat = defaultdict(list)
for a in REGISTRY.values():
    by_cat[a.category].append(a)

lines = [
    "# Supported apps",
    "",
    f"Sett recognises **{len(REGISTRY)} apps**. Generated from `sett/apps/` by `tools/gen_apps_doc.py`.",
    "",
    "- **Ports** are where it looks when it has no Docker metadata. With Docker, the image name finds the app on any port.",
    "- **Stats** shows what the card adds beyond up/down, latency and version, and what (if anything) it needs in `secrets.toml`.",
    f"- ✓ means the fingerprint is checked against a real install in the test lab ({len(verified)} apps).",
    "",
]
for cat in sorted(by_cat):
    lines += [f"## {cat}", "", "| App | Ports | Stats | Tested |", "|---|---|---|---|"]
    for a in sorted(by_cat[cat], key=lambda a: a.name.lower()):
        ports = ", ".join(str(p) for p in a.ports) or "-"
        if a.kind != "http":
            stats = f"yes ({a.kind} handshake)"
        elif a.stats:
            stats = f"yes, needs `{a.needs}`" if a.needs else "yes"
        else:
            stats = "-"
        lines.append(f"| {a.name} | {ports} | {stats} | {'✓' if a.id in verified else ''} |")
    lines.append("")
(ROOT / "docs" / "apps.md").write_text("\n".join(lines), encoding="utf-8")
print(f"docs/apps.md: {len(REGISTRY)} apps, {len(verified)} verified")
