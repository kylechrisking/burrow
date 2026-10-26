"""python -m sett                 run the dashboard
python -m sett identify URL...  show what Sett sees at an address (for writing fingerprints)
python -m sett prefetch-icons [DIR]   download every catalog icon (used when building the image)
python -m sett import-homepage DIR    turn a Homepage config folder into sett.toml"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlparse

from . import config
from .engine import Engine
from .server import serve


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "identify":
        sys.exit(identify(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "prefetch-icons":
        from pathlib import Path
        from .icons import prefetch
        dest = Path(sys.argv[2] if len(sys.argv) > 2 else "/app/icons")
        print(f"prefetched {prefetch(dest)} icons into {dest}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "import-homepage":
        from .importer import main as import_main
        sys.exit(import_main(sys.argv[2:]))
    logging.basicConfig(level=os.environ.get("SETT_LOG", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = config.load()
    engine = Engine(cfg)
    engine.start()
    serve(engine, cfg.port)


def identify(args: list[str]) -> int:
    """Probe each URL like discovery does and print the evidence, so a new
    fingerprint can be written from facts rather than guesses."""
    from . import apps
    from .apps.base import REGISTRY

    if not args:
        print("usage: python -m sett identify http://host:port [--hint IMAGE] [--all]")
        return 2
    hint = args[args.index("--hint") + 1] if "--hint" in args else ""
    try_all = "--all" in args
    urls = [a for a in args if "://" in a or ":" in a and not a.startswith("--")]
    for url in urls:
        u = urlparse(url if "://" in url else f"http://{url}")
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        print(f"\n== {host}:{port}")
        got = apps.fetch(host, port, "/", u.scheme or "http")
        if got:
            scheme, r = got
            j = r.json()
            print(f"   GET / over {scheme}: HTTP {r.status} in {r.elapsed_ms} ms")
            print(f"   server: {r.header('server') or '-'}   title: {r.title or '-'}")
            if isinstance(j, dict):
                print(f"   json keys: {sorted(j)[:12]}")
        else:
            print("   no HTTP answer on / (trying non-HTTP protocols below)")
        cands = list(REGISTRY.values()) if try_all else apps.candidates(port, hint)
        cache: dict = {}
        hits = []
        for app in cands:
            pr = apps.probe(app, host, port, cache)
            if pr and (pr.matched or (hint and app.hinted_by(hint))):
                hits.append(f"{app.id}" + (f" (v{pr.version})" if pr.version else ""))
        print(f"   tried {len(cands)} fingerprint(s): " + (", ".join(hits) if hits else "no match"))
        if not hits and not try_all:
            print("   tip: --all tries every fingerprint, not just those for this port")
    return 0


if __name__ == "__main__":
    main()
