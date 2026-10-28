"""python3 -m fetsetup serve   the first-time setup portal (root; port 80) until the setup is done."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .portal import Portal, serve
from .system import System

DONE = "/var/lib/fet-setup/setup.done"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetsetup")
    ap.add_argument("command", choices=["serve"])
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--done-file", default=DONE)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(Portal(System(), Path(args.done_file)), port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
