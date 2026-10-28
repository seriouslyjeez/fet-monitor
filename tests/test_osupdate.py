"""Keeping the Pi's own software up to date (fetsetup/osupdate.py): nightly only when the range is quiet, with a restart
when an update needs one; on request from Range Control never during a match and never restarting by itself."""
import json
import tempfile
import unittest
from pathlib import Path

from fetsetup.osupdate import KEEP_CONFIG, OsUpdate, QUIET_MIN, quiet
from fetsetup.sysreq import Helper
from fetserver.appliance import Appliance

NOW = 1_800_000_000.0
UPGRADE_OUT = "Reading package lists...\n7 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n"


class Apt:
    """The programs: records argv, answers apt-get update / full-upgrade as told."""
    def __init__(self, update=(0, "Hit:1 http://deb.debian.org"), upgrade=(0, UPGRADE_OUT)):
        self.calls, self.update, self.upgrade = [], update, upgrade

    def run_(self, argv, stdin=None, timeout=60.0):
        self.calls.append(argv)
        if "update" in argv:
            return self.update
        if "full-upgrade" in argv:
            return self.upgrade
        return 0, ""


def act(matches=0, shot_min_ago=None):
    now = int(NOW * 1000)
    return {"matches_running": matches, "last_shot_ms": None if shot_min_ago is None else now - shot_min_ago * 60_000, "now_ms": now}


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.flag = self.dir / "reboot-required"

    def os(self, apt=None, activity=None):
        self.apt = apt or Apt()
        self.act = [activity if activity is not None else act()]
        return OsUpdate(self.apt, self.dir / "os.json", self.flag, activity=lambda: self.act[0], now=lambda: NOW)


class Quiet(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(quiet(act(matches=1), False), (False, "a match is running"))
        self.assertEqual(quiet(act(shot_min_ago=5), True)[0], False)
        self.assertEqual(quiet(act(shot_min_ago=QUIET_MIN), True)[0], True)
        self.assertEqual(quiet(act(shot_min_ago=5), False)[0], True, "on request only a running match stops it")
        self.assertEqual(quiet(None, True)[0], True, "FeT Monitor not answering: nothing to disturb")


class Updates(Case):
    def test_an_update(self):
        o = self.os()
        res = o.update("button")
        self.assertEqual(res, {"ok": True, "message": "8 packages updated", "upgraded": 8})
        upgrade = next(a for a in self.apt.calls if "full-upgrade" in a)
        self.assertIn("DEBIAN_FRONTEND=noninteractive", upgrade)
        self.assertTrue(set(KEEP_CONFIG) <= set(upgrade) and "-y" in upgrade, "never asks; keeps local configuration")
        self.assertTrue(any("autoremove" in a for a in self.apt.calls))
        st = o.state()
        self.assertEqual((st["result"], st["last_ok_ms"], st["how"]), ("ok", int(NOW * 1000), "button"))
        self.assertEqual(self.os(Apt(upgrade=(0, "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded."))).update("x")["message"], "everything was up to date")

    def test_failures_are_recorded_and_the_last_good_one_kept(self):
        self.os().update("night")
        o = self.os(Apt(update=(100, "W: Failed to fetch http://deb.debian.org  Temporary failure resolving")))
        res = o.update("night")
        st = o.state()
        self.assertEqual((res["ok"], st["result"], st["last_ok_ms"]), (False, "failed", int(NOW * 1000)))
        self.assertIn("no internet", st["message"])
        self.assertFalse(any("full-upgrade" in a for a in self.apt.calls), "no upgrade without fresh lists")
        res = self.os(Apt(upgrade=(100, "E: dpkg was interrupted"))).update("night")
        self.assertIn("the upgrade failed: E: dpkg was interrupted", res["message"])


class Nightly(Case):
    def test_quiet_night_with_a_restart(self):
        o = self.os()
        self.flag.write_text("*** System restart required ***")
        self.assertEqual(o.nightly(), "restart")
        self.assertEqual((o.state()["result"], o.state()["restarted_ms"]), ("ok", int(NOW * 1000)))

    def test_no_restart_needed(self):
        self.assertIsNone(self.os().nightly())

    def test_skipped_when_the_range_is_busy(self):
        for a in (act(matches=1), act(shot_min_ago=10)):
            o = self.os(activity=a)
            self.flag.write_text("x")
            self.assertIsNone(o.nightly())
            self.assertEqual(self.apt.calls, [], "nothing run")
            self.assertTrue(o.state()["message"].startswith("skipped tonight"))

    def test_a_match_started_during_the_update_stops_the_restart(self):
        o = self.os()
        self.flag.write_text("x")
        real = o.update

        def update_then_match(how):
            r = real(how)
            self.act[0] = act(matches=1)
            return r
        o.update = update_then_match
        self.assertIsNone(o.nightly())


class OnRequest(Case):
    def helper(self, activity):
        o = self.os(activity=activity)
        (self.dir / "system").mkdir(exist_ok=True)
        return Helper(self.apt, self.dir / "system", self.dir / "net.json", sleep=lambda s: None, now=lambda: NOW, os_update=o)

    def ask(self, activity):
        h = self.helper(activity)
        (self.dir / "system/request.json").write_text(json.dumps({"action": "os_update"}))
        after = h.run()
        return after, json.loads((self.dir / "system/status.json").read_text())

    def test_update_now(self):
        self.flag.write_text("x")
        after, st = self.ask(act(shot_min_ago=1))
        self.assertEqual((after, st["state"]), (None, "done"), "shots a minute ago are fine; never a restart by itself")
        self.assertIn("8 packages updated; restart the Pi to finish", st["message"])

    def test_refused_during_a_match(self):
        after, st = self.ask(act(matches=1))
        self.assertEqual((st["state"], self.apt.calls), ("failed", []))
        self.assertIn("a match is running", st["message"])

    def test_no_internet(self):
        h = self.helper(act())
        h.os.sys = Apt(update=(100, "Could not resolve 'deb.debian.org'"))
        (self.dir / "system/request.json").write_text(json.dumps({"action": "os_update"}))
        h.run()
        st = json.loads((self.dir / "system/status.json").read_text())
        self.assertEqual(st["state"], "failed")
        self.assertIn("no internet", st["message"])


class PageView(Case):
    def test_age_and_restart(self):
        unit = self.dir / "unit"
        unit.write_text("x")
        t = [NOW]
        a = Appliance(self.dir, system=None, unit=unit, now=lambda: t[0], reboot_flag=self.flag)
        a.sys = None
        self.assertEqual({k: a.os_state()[k] for k in ("age_days", "old", "reboot_required")}, {"age_days": None, "old": False, "reboot_required": False},
                         "never updated: a note, not the warning (a new image is recent)")
        (self.dir / "system").mkdir()
        (self.dir / "system/os.json").write_text(json.dumps({"last_ok_ms": int(NOW * 1000), "result": "ok"}))
        self.flag.write_text("x")
        t[0] = NOW + 29.9 * 86400
        self.assertEqual((a.os_state()["age_days"], a.os_state()["old"], a.os_state()["reboot_required"]), (29, False, True))
        t[0] = NOW + 30 * 86400
        self.assertTrue(a.os_state()["old"])

    def test_a_long_update_keeps_the_page_busy(self):
        (self.dir / "system").mkdir()
        (self.dir / "system/status.json").write_text(json.dumps({"state": "running", "action": "os_update", "time": int(NOW * 1000)}))
        t = [NOW + 20 * 60]
        a = Appliance(self.dir, system=None, unit=self.dir / "unit", now=lambda: t[0])
        self.assertTrue(a.busy(), "20 minutes into an OS update: still working")
        t[0] = NOW + 70 * 60
        self.assertFalse(a.busy())


if __name__ == "__main__":
    unittest.main()


from tests.test_matches import MatchCase  # noqa: E402


class Activity(MatchCase):
    def test_what_the_health_check_reports(self):
        a = self.core.activity()
        self.assertEqual((a["matches_running"], a["last_shot_ms"]), (0, None))
        mid = self.m.create("M", self.custom([("record", 600, 5)]), [{"device_id": "lane-1"}], "dir")
        self.assertEqual(self.core.activity()["matches_running"], 0, "created, not started: not running")
        self.m.start(mid, "dir")
        self.at(10); self.shoot("lane-1")
        self.at(20); self.shoot("lane-1")
        a = self.core.activity()
        self.assertEqual((a["matches_running"], a["last_shot_ms"], a["now_ms"]), (1, self.clock.t, self.clock.t), "the latest shot")
        self.m.command(mid, "pause", "dir")
        self.assertEqual(self.core.activity()["matches_running"], 1, "paused still counts")
        self.m.command(mid, "abort", "dir")
        self.assertEqual(self.core.activity()["matches_running"], 0)
