"""python -m labdash"""
from __future__ import annotations

import logging
import os

from . import config
from .engine import Engine
from .server import serve


def main() -> None:
    logging.basicConfig(level=os.environ.get("LABDASH_LOG", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = config.load()
    engine = Engine(cfg)
    engine.start()
    serve(engine, cfg.port)


if __name__ == "__main__":
    main()
