"""Keeping the Pi's own software up to date (Raspberry Pi OS: Debian and the Raspberry Pi archive).

- Every night (fet-os-update.timer, about 04:00, after the 03:30 database backup): `python3 -m fetsetup osupdate`
  runs apt-get update + full-upgrade, then restarts the Pi if an update needs it (/run/reboot-required). Only when the
  range is quiet: no match running and no shot in the last QUIET_MIN minutes (FeT Monitor's /api/v1/health says);
  otherwise it waits for the next night.
- On request (Range Control -> This Pi -> Update the Pi now, through the System helper): the same update, refused
  while a match runs; it never restarts by itself (the page shows "restart needed").
FeT Monitor keeps running during an update (Debian does not restart it). The result goes to system/os.json for the
page: last successful update, last attempt, what happened, packages upgraded; os.json is readable by the server.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

log = logging.getLogger("fetsetup")
OS_FILE = Path("/var/lib/fet-monitor/system/os.json")
REBOOT_REQUIRED = Path("/run/reboot-required")
QUIET_MIN = 30
HEALTH = "http://127.0.0.1:8080/api/v1/health"
APT = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-q", "-o", "DPkg::Lock::Timeout=600"]
KEEP_CONFIG = ["-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]   # never ask, keep local config
COUNT_RE = re.compile(r"(\d+) upgraded, (\d+) newly installed, (\d+) to remove")


def read_activity(url: str = HEALTH) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r).get("activity")
    except (OSError, ValueError):
        return None                               # FeT Monitor not answering: nothing to disturb


def quiet(activity: Optional[dict], for_night: bool) -> Tuple[bool, str]:
    """(ok, why not). At night: no match and no shot for QUIET_MIN minutes. On request: no match running."""
    if not activity:
        return True, ""
    if activity.get("matches_running"):
        return False, "a match is running"
    last = activity.get("last_shot_ms")
    if for_night and last is not None and activity.get("now_ms", 0) - last < QUIET_MIN * 60_000:
        return False, f"shots in the last {QUIET_MIN} minutes"
    return True, ""


class OsUpdate:
    def __init__(self, system, os_file: Path = OS_FILE, reboot_flag: Path = REBOOT_REQUIRED,
                 activity: Callable[[], Optional[dict]] = read_activity, now: Callable[[], float] = time.time):
        self.sys, self.file, self.flag, self.activity, self.now = system, Path(os_file), Path(reboot_flag), activity, now

    def state(self) -> dict:
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, **kw) -> dict:
        st = dict(self.state(), **kw)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.file)
        return st

    def update(self, how: str) -> dict:
        """apt-get update + full-upgrade. Returns {"ok", "message", "upgraded"}; os.json records it."""
        now_ms = int(self.now() * 1000)
        code, out = self.sys.run_(APT + ["update"], None, 900)
        if code != 0:
            msg = "no internet: could not reach the update servers" if re.search(r"Temporary failure|Could not resolve|unreachable|Failed to fetch", out) \
                else "apt-get update failed: " + (out.strip().splitlines() or ["?"])[-1][:200]
            self._save(last_try_ms=now_ms, result="failed", message=msg, how=how)
            return {"ok": False, "message": msg, "upgraded": 0}
        code, out = self.sys.run_(APT + KEEP_CONFIG + ["-y", "full-upgrade"], None, 3600)
        if code != 0:
            msg = "the upgrade failed: " + (out.strip().splitlines() or ["?"])[-1][:200]
            self._save(last_try_ms=now_ms, result="failed", message=msg, how=how)
            return {"ok": False, "message": msg, "upgraded": 0}
        m = COUNT_RE.search(out)
        n = int(m.group(1)) + int(m.group(2)) if m else 0
        self.sys.run_(APT + ["-y", "autoremove", "--purge"], None, 900)
        msg = f"{n} package{'s' if n != 1 else ''} updated" if n else "everything was up to date"
        self._save(last_try_ms=now_ms, last_ok_ms=now_ms, result="ok", message=msg, how=how, upgraded=n)
        return {"ok": True, "message": msg, "upgraded": n}

    def nightly(self) -> Optional[str]:
        """The nightly run; returns "restart" when the Pi should restart now."""
        ok, why = quiet(self.activity(), True)
        if not ok:
            self._save(last_try_ms=int(self.now() * 1000), result="skipped", message=f"skipped tonight: {why}", how="night")
            return None
        self.update("night")
        if self.flag.exists():
            ok, why = quiet(self.activity(), True)     # still quiet after the upgrade?
            if ok:
                self._save(restarted_ms=int(self.now() * 1000))
                return "restart"
        return None
