"""Command line: serve (default), backup, rescore, check, set-password, match-user."""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import secrets
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .admin import MIN_PASSWORD, Admin, write_password
from .core import Core
from .db import Database
from .firmware import FirmwareStore
from .releases import Updates
from .rulebookfiles import RulebookFiles
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
                heartbeat_interval_ms=int(args.heartbeat_interval * 1000),
                lease_ms=int(args.lease * 1000), shot_lag_ms=int(args.shot_lag * 1000))
    rulebooks = RulebookFiles(data_dir)
    server = make_server(core, load_token(data_dir), args.host, args.port, admin=Admin(data_dir),
                         firmware=FirmwareStore(data_dir), updates=Updates(data_dir), rulebooks=rulebooks)
    stop = threading.Event()

    def on_signal(signum, _frame):
        log.info("signal %d: shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def match_clock():                       # Match Control: end phases on time (the clock itself lives in the database)
        last_err, last_at = None, 0.0
        while not stop.wait(0.25):
            try:
                core.matches.tick()
            except Exception as exc:              # keep going; the same error is logged at most once a minute
                if repr(exc) != last_err or time.monotonic() - last_at > 60:
                    log.exception("match clock")
                    last_err, last_at = repr(exc), time.monotonic()

    threading.Thread(target=match_clock, daemon=True).start()
    if not args.no_rulebook_download:          # the rulebooks not stored yet, from their publishers (needs internet once)
        threading.Thread(target=rulebooks.download_missing, daemon=True).start()
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


def cmd_set_password(args, data_dir: Path) -> int:
    """Set the Range Control password. Never opens the database. Run it as the service user so it can read the file:
    sudo -u fet-monitor python3 -m fetserver set-password --data-dir /var/lib/fet-monitor"""
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        print(f"Range Control password (at least {MIN_PASSWORD} characters). It travels unencrypted on the LAN:")
        print("use one you do not use anywhere else.")
        pw = getpass.getpass("New password: ")
        if pw != getpass.getpass("Again: "):
            print("The two entries differ; nothing changed.", file=sys.stderr)
            return 1
    try:
        path = write_password(data_dir, pw)
    except ValueError as exc:
        print(f"{exc}; nothing changed.", file=sys.stderr)
        return 1
    print(f"Range Control password saved in {path}. Anyone logged in is logged out.")
    return 0


def cmd_match_user(args, db: Database, data_dir: Path) -> int:
    """Match Control users without the web page:
        match-user list
        match-user add NAME --role director|officer      (asks for the password, or --password-stdin)
        match-user passwd NAME                           (new password)
        match-user role NAME --role director|officer
        match-user remove NAME
    Changes take effect at once; sessions of a changed user end at their next request."""
    from .matches import MatchError
    mc = Core(db).matches
    sub = args.args[0] if args.args else "list"
    name = args.args[1] if len(args.args) > 1 else None
    if sub == "list":
        for u in mc.users():
            print(f"{u['username']:<32} {u['role']}")
        return 0
    if sub not in ("add", "passwd", "role", "remove") or not name:
        print("usage: match-user list | add NAME --role ROLE | passwd NAME | role NAME --role ROLE | remove NAME", file=sys.stderr)
        return 2
    try:
        if sub == "remove":
            if not mc.remove_user(name):
                print(f"no user {name}", file=sys.stderr)
                return 1
            print(f"removed {name}")
            return 0
        current = mc.user(name)
        if sub in ("passwd", "role") and current is None:
            print(f"no user {name}", file=sys.stderr)
            return 1
        role = args.role or (current["role"] if current else None)
        password = None
        if sub in ("add", "passwd"):
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\n")
            else:
                password = getpass.getpass(f"Password for {name}: ")
                if password != getpass.getpass("Again: "):
                    print("The two entries differ; nothing changed.", file=sys.stderr)
                    return 1
        mc.set_user(name, role, password)
    except MatchError as exc:
        print(f"{exc}; nothing changed.", file=sys.stderr)
        return 1
    print(f"{name}: {role}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetserver")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "backup", "rescore", "check", "set-password", "match-user"])
    ap.add_argument("args", nargs="*", help="match-user: list | add NAME | passwd NAME | role NAME | remove NAME")
    ap.add_argument("--role", choices=["director", "officer"], help="match-user: the user's role")
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--offline-after", type=float, default=15.0, help="seconds without contact before a target is offline")
    ap.add_argument("--heartbeat-interval", type=float, default=5.0, help="seconds, suggested to targets")
    ap.add_argument("--lease", type=float, default=60.0, help="seconds a control claim lasts without renewal")
    ap.add_argument("--shot-lag", type=float, default=0.0,
                    help="seconds between a shot being detected and its line reaching the ESP (measure on the bench)")
    ap.add_argument("--to", default="./backups", help="backup destination directory")
    ap.add_argument("--keep", type=int, default=14, help="backups to keep")
    ap.add_argument("--password-stdin", action="store_true", help="set-password: read the password from stdin (scripts)")
    ap.add_argument("--no-rulebook-download", action="store_true",
                    help="do not fetch missing rulebooks from their publishers at start-up (Match Control can still)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "set-password":
        return cmd_set_password(args, data_dir)
    db = Database(str(data_dir / "fetserver.db"))
    check = db.integrity_check()
    if check != "ok":
        log.critical("database integrity check FAILED: %s - not starting. Restore from a backup.", check)
        return 2
    return {"serve": cmd_serve, "backup": cmd_backup, "rescore": cmd_rescore, "check": cmd_check,
            "match-user": cmd_match_user}[args.command](args, db, data_dir)


if __name__ == "__main__":
    sys.exit(main())
