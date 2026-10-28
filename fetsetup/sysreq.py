"""The root side of Range Control's System page: `python3 -m fetsetup system` (fet-system.service, started by
fet-system.path when the server writes system/request.json).

The server is not root; it only writes a request. This helper reads it, deletes it at once (it may hold a
password), checks it again with the setup's own rules (it never trusts the server), applies it and writes
system/status.json: {"state": "running"|"done"|"failed", "action", "message", "time"}.

A network change never leaves the Pi unreachable if it can help it: the club WiFi is joined on trial first; after
any change the Pi must have a network within a minute, otherwise the last network that worked comes back
(network.json, root only), or, if there is none on record, the setup network opens (fet-setup.service).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .config import SetupError
from .osupdate import OsUpdate, quiet
from .system import System, SystemError_

log = logging.getLogger("fetsetup")
REQ_DIR = Path("/var/lib/fet-monitor/system")
LAST_GOOD = Path("/var/lib/fet-setup/network.json")
ACTIONS = ("network", "hostname", "timezone", "clock", "country", "ssh", "admin", "restart", "shutdown", "os_update")


def save_last_good(net: dict, path: Path = LAST_GOOD) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(net, f)
    os.replace(tmp, path)


def load_last_good(path: Path = LAST_GOOD) -> Optional[dict]:
    try:
        return config.check_network(json.loads(path.read_text()))
    except (OSError, ValueError, SetupError):
        return None


class Helper:
    def __init__(self, system: System, req_dir: Path = REQ_DIR, last_good: Path = LAST_GOOD,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time, os_update: Optional[OsUpdate] = None):
        self.sys, self.dir, self.last_good, self.sleep, self.now = system, Path(req_dir), Path(last_good), sleep, now
        self.os = os_update or OsUpdate(system)

    def status(self, state: str, action: str, message: str) -> None:
        tmp = self.dir / "status.json.tmp"
        tmp.write_text(json.dumps({"state": state, "action": action, "message": message, "time": int(self.now() * 1000)}))
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.dir / "status.json")

    def take_request(self) -> Optional[dict]:
        p = self.dir / "request.json"
        try:
            raw = p.read_text()
        except FileNotFoundError:
            return None
        finally:
            try:
                p.unlink()                        # at once: it may hold a password
            except FileNotFoundError:
                pass
        try:
            req = json.loads(raw)
        except ValueError:
            return {}
        return req if isinstance(req, dict) else {}

    def run(self) -> Optional[str]:
        """Handle one request; returns what to do with the Pi afterwards ('restart', 'shutdown') or None."""
        req = self.take_request()
        if req is None:
            return None
        action = req.get("action")
        if action not in ACTIONS:
            self.status("failed", str(action)[:20], "unknown request")
            return None
        self.status("running", action, "working")
        try:
            message = self.apply(action, req)
        except (SetupError, SystemError_) as exc:
            self.status("failed", action, str(exc))
            return None
        self.status("done", action, message)
        return action if action in ("restart", "shutdown") else None

    def apply(self, action: str, req: dict) -> str:
        s = self.sys
        if action == "hostname":
            name = str(req.get("hostname") or "").strip().lower()
            if not config.HOSTNAME_RE.match(name):
                raise SetupError("Pi name: letters a-z, digits and dashes, up to 63, not starting or ending with a dash")
            s.set_hostname(name)
            return f"the Pi is now called {name}"
        if action == "timezone":
            tz = req.get("timezone")
            if tz not in s.zones():
                raise SetupError("unknown time zone")
            s.set_timezone(tz)
            return f"time zone {tz}"
        if action == "country":
            cc = req.get("country")
            if cc not in {c for c, _ in s.countries()}:
                raise SetupError("unknown country")
            s.set_country(cc)
            return f"WiFi country {cc}"
        if action == "clock":
            ms = req.get("clock_ms")
            if not isinstance(ms, int) or isinstance(ms, bool) or not 1_700_000_000_000 <= ms <= 4_102_444_800_000:
                raise SetupError("not a usable time")
            if s.ntp_synced():
                return "the Pi gets its time from the internet: kept"
            s.set_clock(ms)
            return "clock set from your device"
        if action == "ssh":
            on = req.get("on") is True
            s.set_ssh(on)
            return "SSH is on" if on else "SSH is off"
        if action == "admin":
            user, pw = str(req.get("user") or ""), str(req.get("password") or "")
            if not config.LINUX_USER_RE.match(user) or user in config.RESERVED_USERS:
                raise SetupError("admin login: 2 to 32 lowercase letters, digits, dashes or underscores (not root, pi or a system name)")
            if len(pw) < config.MIN_ADMIN_PASSWORD:
                raise SetupError(f"admin password: at least {config.MIN_ADMIN_PASSWORD} characters")
            s.set_admin(user, pw)
            return f"admin login {user} set"
        if action == "os_update":              # on request: refused during a match; it never restarts by itself
            ok, why = quiet(self.os.activity(), False)
            if not ok:
                raise SetupError(f"not now: {why}")
            res = self.os.update("button")
            if not res["ok"]:
                raise SetupError(res["message"])
            return res["message"] + ("; restart the Pi to finish (Restart)" if self.os.flag.exists() else "")
        if action in ("restart", "shutdown"):
            return "restarting" if action == "restart" else "shutting down: wait 20 seconds before unplugging"
        return self.network(config.check_network(req.get("network")))

    def network(self, net: dict) -> str:
        s = self.sys
        if net["mode"] == "wifi":                # a trial join first: a wrong password changes nothing
            prev = s.active_wifi()
            got = s.try_wifi(net["ssid"], net["psk"])
            if not got["ok"]:
                if prev:
                    s.reconnect(prev)
                raise SetupError(f"could not join {net['ssid']}: {got['why']}; nothing changed")
        s.use_network(net)
        for _ in range(30):
            if s.has_network():
                save_last_good(net, self.last_good)
                return {"cable": "now on the cable", "wifi": f"now on the WiFi {net.get('ssid')}",
                        "own": f"now running its own network {net.get('ssid')} at {config.OWN_IP}"}[net["mode"]]
            self.sleep(2)
        prev = load_last_good(self.last_good)
        if prev and prev != net:
            s.use_network(prev)
            raise SetupError("the new network did not come up: back on the previous one")
        s.start_setup()
        raise SetupError("the new network did not come up: the setup network FeTMonitor-Setup opens for the network settings")
