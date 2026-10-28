"""python3 -m fetsetup boot    at every start (fet-setup.service, root): the full setup on first boot; on a set-up
                             Pi only when it finds no network (network settings only); otherwise nothing.
python3 -m fetsetup serve   the full setup now (port 80), until it is done.
python3 -m fetsetup system  Range Control's System page: one request from the server (fet-system.service, root).
python3 -m fetsetup osupdate the nightly Raspberry Pi OS update (fet-os-update.timer, root): only when the range is
                             quiet; restarts the Pi if an update needs it."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .portal import Portal, boot, serve
from .system import System

DONE = "/var/lib/fet-setup/setup.done"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetsetup")
    ap.add_argument("command", choices=["boot", "serve", "system", "osupdate"])
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--done-file", default=DONE)
    ap.add_argument("--wait", type=int, default=180, help="boot: seconds to wait for a network before reopening the setup")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    system = System()
    if args.command == "osupdate":
        import subprocess
        from .osupdate import OsUpdate
        if OsUpdate(system).nightly() == "restart":
            subprocess.run(["systemctl", "reboot"], check=False)
        return 0
    if args.command == "system":
        import subprocess
        from .sysreq import Helper
        after = Helper(system).run()
        if after:                                 # the status says so before the Pi goes away
            subprocess.run(["systemctl", "reboot" if after == "restart" else "poweroff"], check=False)
        return 0
    portal = boot(system, Path(args.done_file), args.wait) if args.command == "boot" else Portal(system, Path(args.done_file))
    if portal is not None:
        serve(portal, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
