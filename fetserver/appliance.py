"""Range Control's System page: the Pi's network, name, time and SSH, and Restart / Shut down.

The server is not root. It reads what it can (NetworkManager and systemd answer read-only questions without root)
and writes a request to <data-dir>/system/request.json, which wakes the root helper (fet-system.path ->
python3 -m fetsetup system). The helper checks the request again itself and reports in system/status.json.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

ACTIONS = ("network", "hostname", "timezone", "clock", "country", "ssh", "admin", "restart", "shutdown", "os_update")
UNIT = Path("/etc/systemd/system/fet-system.path")
REBOOT_REQUIRED = Path("/run/reboot-required")
STALE_MS = 5 * 60 * 1000
STALE_UPDATE_MS = 65 * 60 * 1000              # an OS update may take long on a Pi Zero
OLD_DAYS = 30                                 # no successful OS update for this long: the page warns


class ApplianceError(ValueError):
    pass


class Appliance:
    def __init__(self, data_dir: Optional[Path], system=None, unit: Path = UNIT, now=time.time, reboot_flag: Path = REBOOT_REQUIRED):
        self.dir = Path(data_dir) / "system" if data_dir is not None else None
        self.unit, self.now, self.reboot_flag = unit, now, reboot_flag
        if system is None:
            try:                                  # the server must start even without the setup code next to it
                from fetsetup.system import System
                system = System()
            except ImportError:
                system = None
        self.sys = system

    def status(self) -> Optional[dict]:
        if self.dir is None:
            return None
        try:
            return json.loads((self.dir / "status.json").read_text())
        except (OSError, ValueError):
            return None

    def busy(self) -> bool:
        if self.dir is None:
            return False
        st = self.status() or {}
        stale = STALE_UPDATE_MS if st.get("action") == "os_update" else STALE_MS
        running = st.get("state") == "running" and self.now() * 1000 - st.get("time", 0) < stale
        return (self.dir / "request.json").exists() or running

    def os_state(self) -> dict:
        """The Pi's own software: last successful update, last attempt, restart needed, too old."""
        st = {}
        if self.dir is not None:
            try:
                st = json.loads((self.dir / "os.json").read_text())
            except (OSError, ValueError):
                st = {}
        ok = st.get("last_ok_ms")
        age = None if ok is None else int((self.now() * 1000 - ok) // 86_400_000)
        return dict(st, reboot_required=self.reboot_flag.exists(), age_days=age, old=age is not None and age >= OLD_DAYS)

    def view(self) -> dict:
        """Everything the page shows: the facts, the WiFi networks around (cached by NetworkManager), the choices."""
        s = self.sys
        if s is None:
            return {"helper_installed": False, "status": None, "busy": False, "facts": {}, "zones": [], "countries": [], "networks": [],
                    "clock_ms": int(self.now() * 1000), "os": self.os_state()}
        return {"helper_installed": self.unit.is_file(), "status": self.status(), "busy": self.busy(), "facts": s.facts(),
                "zones": s.zones(), "countries": [{"code": c, "name": n} for c, n in s.countries()],
                "networks": s.scan_cached(), "clock_ms": int(self.now() * 1000), "os": self.os_state()}

    def request(self, req: dict) -> None:
        """A first check (the helper checks everything again), then the request file that wakes the helper."""
        if self.dir is None:
            raise ApplianceError("this server has no data folder")
        if self.sys is None or not self.unit.is_file():
            raise ApplianceError("the System helper is not installed: run sudo ./install.sh from release 0.6.30 or newer once")
        if not isinstance(req, dict) or req.get("action") not in ACTIONS:
            raise ApplianceError("unknown request")
        if self.busy():
            raise ApplianceError("the Pi is still working on the last change")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "request.json.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:   # it may hold a password
            json.dump(req, f)
        os.replace(tmp, self.dir / "request.json")
