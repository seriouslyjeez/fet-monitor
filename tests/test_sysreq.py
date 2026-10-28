"""The root helper behind Range Control's System page (fetsetup/sysreq.py): it takes one request, deletes it at once,
checks it again itself, applies it, writes the status, and never leaves the Pi without a network if it can help it."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from fetsetup.sysreq import Helper, load_last_good, save_last_good
from fetsetup.system import SystemError_

ZONES = ["Europe/Berlin", "UTC"]


class FakePi:
    def __init__(self, comes_up=True, trial_ok=True, ntp=False, active="Home"):
        self.comes_up, self.trial_ok, self.ntp, self.active = comes_up, trial_ok, ntp, active
        self.log = []

    def zones(self): return ZONES
    def countries(self): return [("DE", "Germany"), ("US", "United States")]
    def ntp_synced(self): return self.ntp
    def active_wifi(self): return self.active
    def set_hostname(self, n): self.log.append(("hostname", n))
    def set_timezone(self, z): self.log.append(("timezone", z))
    def set_country(self, c): self.log.append(("country", c))
    def set_clock(self, ms): self.log.append(("clock", ms))
    def set_ssh(self, on): self.log.append(("ssh", on))
    def set_admin(self, u, p): self.log.append(("admin", u, p))
    def reconnect(self, name): self.log.append(("reconnect", name))
    def start_setup(self): self.log.append(("setup",))
    def has_network(self): return self.comes_up

    def try_wifi(self, ssid, psk):
        self.log.append(("try", ssid))
        return {"ok": True, "lease": {"ip": "192.168.1.9"}} if self.trial_ok else {"ok": False, "why": "wrong WiFi password"}

    def use_network(self, net):
        if net.get("ssid") == "boom":
            raise SystemError_("nmcli connection: failed")
        self.log.append(("use", net["mode"], net.get("ssid")))


class HelperCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "system"
        self.dir.mkdir()
        self.last = Path(tmp.name) / "fet-setup" / "network.json"

    def helper(self, pi):
        self.pi = pi
        return Helper(pi, self.dir, self.last, sleep=lambda s: None, now=lambda: 1000.0)

    def ask(self, pi, req):
        (self.dir / "request.json").write_text(json.dumps(req))
        after = self.helper(pi).run()
        st = json.loads((self.dir / "status.json").read_text())
        self.assertFalse((self.dir / "request.json").exists(), "the request is deleted (it may hold a password)")
        return after, st


class Requests(HelperCase):
    def test_settings(self):
        for req, want_log, msg in (({"action": "hostname", "hostname": "Range2"}, ("hostname", "range2"), "called range2"),
                                   ({"action": "timezone", "timezone": "UTC"}, ("timezone", "UTC"), "UTC"),
                                   ({"action": "country", "country": "DE"}, ("country", "DE"), "DE"),
                                   ({"action": "ssh", "on": True}, ("ssh", True), "SSH is on"),
                                   ({"action": "ssh", "on": "yes"}, ("ssh", False), "SSH is off"),
                                   ({"action": "admin", "user": "clubadmin", "password": "new-pass1"}, ("admin", "clubadmin", "new-pass1"), "clubadmin"),
                                   ({"action": "clock", "clock_ms": 1_800_000_000_000}, ("clock", 1_800_000_000_000), "from your device")):
            after, st = self.ask(FakePi(), req)
            self.assertEqual((after, st["state"], self.pi.log), (None, "done", [want_log]), req)
            self.assertIn(msg, st["message"])
            self.assertEqual(st["time"], 1_000_000)

    def test_checked_again_here(self):
        for req, why in (({"action": "hostname", "hostname": "-x"}, "Pi name"), ({"action": "timezone", "timezone": "Mars/Base"}, "time zone"),
                         ({"action": "country", "country": "XX"}, "country"), ({"action": "admin", "user": "root", "password": "long-enough"}, "admin login"),
                         ({"action": "admin", "user": "clubadmin", "password": "short"}, "at least"), ({"action": "clock", "clock_ms": 5}, "usable time"),
                         ({"action": "network", "network": {"mode": "wifi", "ssid": "Club", "psk": "short"}}, "8 to 63"),
                         ({"action": "rm -rf"}, "unknown request"), ({"x": 1}, "unknown request")):
            after, st = self.ask(FakePi(), req)
            self.assertEqual((st["state"], self.pi.log), ("failed", []), req)
            self.assertIn(why, st["message"], req)
        (self.dir / "request.json").write_text("not json")
        self.helper(FakePi()).run()
        self.assertEqual(json.loads((self.dir / "status.json").read_text())["state"], "failed")
        self.assertIsNone(self.helper(FakePi()).run(), "no request: nothing")

    def test_clock_with_network_time_is_kept(self):
        _, st = self.ask(FakePi(ntp=True), {"action": "clock", "clock_ms": 1_800_000_000_000})
        self.assertEqual((st["state"], self.pi.log), ("done", []))
        self.assertIn("from the internet", st["message"])

    def test_restart_and_shutdown_after_the_status(self):
        after, st = self.ask(FakePi(), {"action": "shutdown"})
        self.assertEqual((after, st["state"]), ("shutdown", "done"))
        self.assertIn("20 seconds", st["message"])
        self.assertEqual(self.ask(FakePi(), {"action": "restart"})[0], "restart")
        self.assertEqual(oct(stat.S_IMODE(os.stat(self.dir / "status.json").st_mode)), "0o644", "the server can read it")


class Network(HelperCase):
    WIFI = {"mode": "wifi", "ssid": "Club2", "psk": "club-pass2", "address": "dhcp"}

    def test_wifi_tried_first_then_kept(self):
        _, st = self.ask(FakePi(), {"action": "network", "network": self.WIFI})
        self.assertEqual((st["state"], self.pi.log), ("done", [("try", "Club2"), ("use", "wifi", "Club2")]))
        self.assertEqual(load_last_good(self.last)["ssid"], "Club2", "remembered as the last network that worked")
        self.assertEqual(oct(stat.S_IMODE(os.stat(self.last).st_mode)), "0o600", "it holds the WiFi password: root only")

    def test_a_wrong_wifi_password_changes_nothing(self):
        _, st = self.ask(FakePi(trial_ok=False), {"action": "network", "network": self.WIFI})
        self.assertEqual((st["state"], self.pi.log), ("failed", [("try", "Club2"), ("reconnect", "Home")]))
        self.assertIn("nothing changed", st["message"])
        self.assertFalse(self.last.exists())

    def test_a_network_that_does_not_come_up_falls_back(self):
        save_last_good({"mode": "cable", "address": "dhcp"}, self.last)
        _, st = self.ask(FakePi(comes_up=False), {"action": "network", "network": {"mode": "own", "ssid": "Range", "psk": "range-wifi"}})
        self.assertEqual((st["state"], self.pi.log[-1]), ("failed", ("use", "cable", None)))
        self.assertIn("back on the previous one", st["message"])
        self.assertEqual(load_last_good(self.last)["mode"], "cable", "the failed one is not remembered")

    def test_nothing_to_fall_back_to_opens_the_setup_network(self):
        _, st = self.ask(FakePi(comes_up=False), {"action": "network", "network": {"mode": "cable", "address": "dhcp"}})
        self.assertEqual((st["state"], self.pi.log[-1]), ("failed", ("setup",)))
        self.assertIn("setup network", st["message"])

    def test_the_same_network_failing_again_opens_the_setup_network(self):
        save_last_good({"mode": "cable", "address": "dhcp"}, self.last)
        _, st = self.ask(FakePi(comes_up=False), {"action": "network", "network": {"mode": "cable", "address": "dhcp"}})
        self.assertEqual((st["state"], self.pi.log), ("failed", [("use", "cable", None), ("setup",)]), "not the same failing network again")

    def test_a_failing_command_is_reported(self):
        _, st = self.ask(FakePi(), {"action": "network", "network": {"mode": "own", "ssid": "boom", "psk": "range-wifi"}})
        self.assertEqual(st["state"], "failed")
        self.assertIn("nmcli", st["message"])

    def test_a_damaged_record_is_ignored(self):
        self.last.parent.mkdir(parents=True)
        self.last.write_text('{"mode": "wifi", "ssid": "x", "psk": "1"}')
        self.assertIsNone(load_last_good(self.last))


if __name__ == "__main__":
    unittest.main()
