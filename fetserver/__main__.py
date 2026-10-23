"""Command line: serve (default), backup, rescore, check."""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .core import Core
from .db import Database
from .server import make_server

log = logging.getLogger("fetserver")


def load_token(data_dir: Path) -> str:
    path = data_dir / "device_token"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(18) + "\n")
        os.chmod(path, 0o600)
        log.warning("created a new device token at %s - enter it in each target's setup portal", path)
    return path.read_text().strip()


def cmd_serve(args, db: Database, data_dir: Path) -> int:
    core = Core(db, offline_after_ms=int(args.offline_after * 1000),
                heartbeat_interval_ms=int(args.heartbeat_interval * 1000))
    server = make_server(core, load_token(data_dir), args.host, args.port)
    stop = threading.Event()

    def on_signal(signum, _frame):
        log.info("signal %d: shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("listening on %s:%d, data in %s", args.host, args.port, data_dir)
    stop.wait()
    server.shutdown()
    server.server_close()
    return 0


def cmd_backup(args, db: Database, data_dir: Path) -> int:
    dest_dir = Path(args.to)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / time.strftime("fet-%Y%m%d-%H%M%S.db")
    src = db.conn()
    out = sqlite3.connect(dest)
    try:
        src.backup(out)                  # consistent snapshot even while the server is running
        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        out.close()
    if ok != "ok":
        log.error("backup %s failed integrity check: %s", dest, ok)
        return 2
    backups = sorted(dest_dir.glob("fet-*.db"))
    for old in backups[:-args.keep]:
        old.unlink()
    log.info("backup written to %s (%d kept)", dest, min(len(backups), args.keep))
    return 0


def cmd_rescore(args, db: Database, data_dir: Path) -> int:
    log.info("rescored %d shots", Core(db).rescore())
    return 0


def cmd_check(args, db: Database, data_dir: Path) -> int:
    result = db.integrity_check()
    print("integrity_check:", result)
    return 0 if result == "ok" else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetserver")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "backup", "rescore", "check"])
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--offline-after", type=float, default=15.0, help="seconds without contact before a target is offline")
    ap.add_argument("--heartbeat-interval", type=float, default=5.0, help="seconds, suggested to targets")
    ap.add_argument("--to", default="./backups", help="backup destination directory")
    ap.add_argument("--keep", type=int, default=14, help="backups to keep")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(str(data_dir / "fetserver.db"))
    check = db.integrity_check()
    if check != "ok":
        log.critical("database integrity check FAILED: %s - not starting. Restore from a backup.", check)
        return 2
    return {"serve": cmd_serve, "backup": cmd_backup, "rescore": cmd_rescore, "check": cmd_check}[args.command](args, db, data_dir)


if __name__ == "__main__":
    sys.exit(main())
