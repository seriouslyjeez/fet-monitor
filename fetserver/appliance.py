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

ACTIONS = ("network", "hostname", "timezone", "clock", "country", "ssh", "admin", "restart", "shutdown")
UNIT = Path("/etc/systemd/system/fet-system.path")
STALE_MS = 5 * 60 * 1000


class ApplianceError(ValueError):
    pass


class Appliance:
    def __init__(self, data_dir: Optional[Path], system=None, unit: Path = UNIT, now=time.time):
        self.dir = Path(data_dir) / "system" if data_dir is not None else None
        self.unit, self.now = unit, now
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
        running = st.get("state") == "running" and self.now() * 1000 - st.get("time", 0) < STALE_MS
        return (self.dir / "request.json").exists() or running

    def view(self) -> dict:
        """Everything the page shows: the facts, the WiFi networks around (cached by NetworkManager), the choices."""
        s = self.sys
        if s is None:
            return {"helper_installed": False, "status": None, "busy": False, "facts": {}, "zones": [], "countries": [], "networks": [],
                    "clock_ms": int(self.now() * 1000)}
        return {"helper_installed": self.unit.is_file(), "status": self.status(), "busy": self.busy(), "facts": s.facts(),
                "zones": s.zones(), "countries": [{"code": c, "name": n} for c, n in s.countries()],
                "networks": s.scan_cached(), "clock_ms": int(self.now() * 1000)}

    def request(self, req: dict) -> None:
        """A first check (the helper checks everything again), then the request file that wakes the helper."""
        if self.dir is None:
            raise ApplianceError("this server has no data folder")
        if self.sys is None or not self.unit.is_file():
            raise ApplianceError("the System helper is not installed: install a release from 0.6.30 on")
        if not isinstance(req, dict) or req.get("action") not in ACTIONS:
            raise ApplianceError("unknown request")
        if self.busy():
            raise ApplianceError("the Pi is still working on the last change")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "request.json.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:   # it may hold a password
            json.dump(req, f)
        os.replace(tmp, self.dir / "request.json")
