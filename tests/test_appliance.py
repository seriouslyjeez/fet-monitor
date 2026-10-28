"""Range Control's System page, server side (fetserver/appliance.py): what it shows and the request it writes for the
root helper. Over HTTP: Range Control login only."""
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from fetserver.admin import Admin, write_password
from fetserver.appliance import Appliance, ApplianceError
from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server
from tests.test_match_http import MatchHttpBase


class FakeFacts:
    def facts(self): return {"hostname": "range", "timezone": "UTC", "ntp": False, "ssh": False, "connections": []}
    def zones(self): return ["UTC"]
    def countries(self): return [("DE", "Germany")]
    def scan_cached(self): return [{"ssid": "Club", "signal": 70, "secure": True}]


class Requests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.unit = self.dir / "fet-system.path"
        self.unit.write_text("[Path]\n")
        self.t = [1000.0]
        self.a = Appliance(self.dir, FakeFacts(), self.unit, now=lambda: self.t[0])

    def test_a_request_wakes_the_helper(self):
        self.a.request({"action": "admin", "user": "clubadmin", "password": "secret-pw"})
        p = self.dir / "system/request.json"
        self.assertEqual(json.loads(p.read_text())["user"], "clubadmin")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, "it may hold a password")
        self.assertTrue(self.a.busy())
        with self.assertRaisesRegex(ApplianceError, "still working"):
            self.a.request({"action": "restart"})
        v = self.a.view()
        self.assertEqual((v["busy"], v["helper_installed"], v["facts"]["hostname"], v["networks"][0]["ssid"], v["clock_ms"]), (True, True, "range", "Club", 1_000_000))

    def test_refused(self):
        for req in ({"action": "format-disk"}, {}, None, "restart"):
            with self.assertRaisesRegex(ApplianceError, "unknown request"):
                self.a.request(req)
        self.unit.unlink()
        with self.assertRaisesRegex(ApplianceError, "not installed"):
            self.a.request({"action": "restart"})
        with self.assertRaisesRegex(ApplianceError, "no data folder"):
            Appliance(None, FakeFacts(), self.unit).request({"action": "restart"})

    def test_a_running_change_that_never_finished_does_not_block_forever(self):
        (self.dir / "system").mkdir()
        (self.dir / "system/status.json").write_text(json.dumps({"state": "running", "action": "network", "message": "", "time": 1_000_000}))
        self.assertTrue(self.a.busy())
        self.t[0] += 6 * 60
        self.assertFalse(self.a.busy(), "stale after 5 minutes (the Pi restarted mid-change)")
        self.a.request({"action": "restart"})


class WithoutTheSetupCode(unittest.TestCase):
    def test_the_server_still_starts(self):
        import builtins
        real = builtins.__import__

        def no_fetsetup(name, *a, **k):
            if name.startswith("fetsetup"):
                raise ImportError("no fetsetup here")
            return real(name, *a, **k)
        builtins.__import__ = no_fetsetup
        try:
            a = Appliance(Path(tempfile.mkdtemp()))
        finally:
            builtins.__import__ = real
        self.assertEqual((a.view()["helper_installed"], a.view()["facts"]), (False, {}))
        with self.assertRaisesRegex(ApplianceError, "not installed"):
            a.request({"action": "restart"})


class OverHttp(MatchHttpBase):
    def setUp(self):
        super().setUp()
        unit = self.dir / "fet-system.path"
        unit.write_text("[Path]\n")
        self.server.appliance = Appliance(self.dir, FakeFacts(), unit)

    def admin_login(self):
        st, body, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.assertEqual(st, 200)
        self.cookies["admin"] = cookie.split(";")[0]

    def test_range_control_only(self):
        self.assertEqual(self.req("GET", "/api/v1/admin/system")[0], 401)
        self.assertEqual(self.req("POST", "/api/v1/admin/system", {"action": "restart"})[0], 401)
        self.admin_login()
        self.assertEqual(self.req("POST", "/api/v1/admin/system", {"action": "restart"}, "admin", header=False)[0], 403, "cross-site guard")
        st, body, _ = self.req("GET", "/api/v1/admin/system", None, "admin")
        self.assertEqual((st, body["facts"]["hostname"], body["busy"]), (200, "range", False))
        st, body, _ = self.req("POST", "/api/v1/admin/system", {"action": "shutdown"}, "admin")
        self.assertEqual((st, json.loads((self.dir / "system/request.json").read_text())), (202, {"action": "shutdown"}))
        st, body, _ = self.req("POST", "/api/v1/admin/system", {"action": "restart"}, "admin")
        self.assertEqual((st, "still working" in body["error"]), (400, True))


if __name__ == "__main__":
    unittest.main()
