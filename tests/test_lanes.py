"""Lanes (2026-11-08): every target has a lane number, each used once on the range; a target without one is a spare.
Matches use the real lanes: a shooter's key in a match is their target's lane when they join, and their firing point
(fp) follows them when they move. Old databases are numbered in the order targets were first seen."""
import os
import sqlite3
import tempfile
import unittest

from fetserver.core import BadRequest, Core, Forbidden, LaneTaken, NotFound
from fetserver.db import MIGRATIONS, Database, _split
from fetserver.matches import MatchError
from tests.test_match_http import MatchHttpBase
from tests.test_matches import BASE, MatchCase


def lanes(core):
    return {t["device_id"]: t["lane"] for t in core.list_targets()}


class Numbering(MatchCase):
    def hb(self, did):
        self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})

    def test_new_targets_take_the_lowest_free_lane(self):
        self.assertEqual(lanes(self.core), {"lane-1": 1, "lane-2": 2}, "in the order first seen")
        self.hb("c")
        self.assertEqual(lanes(self.core)["c"], 3)
        self.core.set_lane("lane-1", None)                        # a spare now: lane 1 is free
        self.hb("d")
        self.assertEqual(lanes(self.core)["d"], 1, "the gap is filled first")
        self.hb("lane-1")
        self.assertIsNone(lanes(self.core)["lane-1"], "a target that reports again keeps what Range Control set")

    def test_set_take_and_refuse(self):
        with self.assertRaises(LaneTaken) as ctx:
            self.core.set_lane("lane-1", 2)
        self.assertEqual((ctx.exception.lane, ctx.exception.holder), (2, "lane-2"))
        self.assertEqual(lanes(self.core), {"lane-1": 1, "lane-2": 2}, "nothing changed")
        out = self.core.set_lane("lane-1", 2, take=True)
        self.assertEqual(out["freed"], "lane-2")
        self.assertEqual(lanes(self.core), {"lane-1": 2, "lane-2": None}, "the holder became a spare")
        self.assertEqual(self.core.set_lane("lane-2", 7)["freed"], None)
        for bad in (0, -1, 1000, 2.5, "3", True):
            with self.assertRaises(BadRequest, msg=repr(bad)):
                self.core.set_lane("lane-2", bad)
        with self.assertRaises(NotFound):
            self.core.set_lane("nobody", 4)

    def test_not_while_in_a_match(self):
        mid = self.m.create("M", "issf-ar60", [{"device_id": "lane-1"}], "md")
        with self.assertRaises(Forbidden):
            self.core.set_lane("lane-1", 9)
        with self.assertRaises(Forbidden, msg="nor take its lane"):
            self.core.set_lane("lane-2", 1, take=True)
        self.m.start(mid, "md")
        self.m.command(mid, "abort", "md")
        self.core.set_lane("lane-2", 1, take=True)
        self.assertEqual(lanes(self.core), {"lane-1": None, "lane-2": 1})

    def test_removed_banned_and_reset_targets_free_their_lane(self):
        self.core.remove_target("lane-1")
        self.hb("c")
        self.assertEqual(lanes(self.core)["c"], 1)
        self.core.set_banned("c", True)
        self.hb("d")
        self.assertEqual(lanes(self.core)["d"], 1)

    def test_the_database_keeps_lanes_unique(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.core.db.write() as c:
                c.execute("UPDATE targets SET lane=2 WHERE device_id='lane-1'")


class Matches(MatchCase):
    def setUp(self):
        super().setUp()
        for did in ("lane-3", "lane-4"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})
        self.core.set_lane("lane-3", 7)
        self.core.set_lane("lane-4", 5)

    def keys(self, mid):
        return [(l["device_id"], l["lane"], l["fp"]) for l in self.m.detail(mid)["lanes"]]

    def test_real_lanes_in_lane_order(self):
        mid = self.m.create("M", "issf-ar60", [{"device_id": "lane-3"}, {"device_id": "lane-1"}, {"device_id": "lane-4"}], "md")
        self.assertEqual(self.keys(mid), [("lane-1", 1, 1), ("lane-4", 5, 5), ("lane-3", 7, 7)], "whatever order they were picked in")

    def test_a_spare_cannot_join(self):
        self.core.set_lane("lane-2", None)
        with self.assertRaisesRegex(MatchError, "spare"):
            self.m.create("M", "issf-ar60", [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "md")
        mid = self.m.create("M", "issf-ar60", [{"device_id": "lane-1"}], "md")
        self.m.start(mid, "md")
        with self.assertRaisesRegex(MatchError, "spare"):
            self.m.add_late(mid, "lane-2", "Late", "md")

    def test_a_move_keeps_the_key_and_follows_the_firing_point(self):
        mid = self.m.create("M", "issf-ar60", [{"device_id": "lane-1"}], "md")
        self.m.start(mid, "md")
        self.m.move(mid, "lane-1", "lane-3", "md")                     # to Lane 7's target
        self.assertEqual(self.keys(mid), [("lane-3", 1, 7)], "corrections still find them by key 1")
        self.core.set_lane("lane-2", None)                             # a spare, put on the same stand
        self.m.move(mid, "lane-3", "lane-2", "md")
        self.assertEqual(self.keys(mid), [("lane-2", 1, 7)], "onto a spare: still Lane 7")

    def test_a_late_shooter_on_a_lane_someone_moved_away_from(self):
        mid = self.m.create("M", "issf-ar60", [{"device_id": "lane-1"}], "md")
        self.m.start(mid, "md")
        self.m.move(mid, "lane-1", "lane-3", "md")                     # key 1 is now on Lane 7
        self.m.add_late(mid, "lane-1", "Late", "md")                   # Lane 1's target again
        self.assertEqual(sorted(self.keys(mid)), [("lane-1", 2, 1), ("lane-3", 1, 7)], "a new key; the firing point is Lane 1")


class Migration(unittest.TestCase):
    def test_an_old_database_is_numbered_in_the_order_first_seen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "old.db")
        c = sqlite3.connect(path, isolation_level=None)
        for i, script in enumerate(MIGRATIONS[:27]):
            for stmt in _split(script):
                c.execute(stmt)
            c.execute(f"PRAGMA user_version={i + 1}")
        rows = [("b", 200, 0, 0), ("a", 100, 0, 0), ("gone", 50, 1, 0), ("bad", 60, 0, 1), ("c", 200, 0, 0)]
        for did, seen, removed, banned in rows:
            c.execute("INSERT INTO targets (device_id, first_seen_ms, last_seen_ms, removed, banned) VALUES (?,?,?,?,?)", (did, seen, seen, removed, banned))
        c.close()
        db = Database(path)
        got = dict(db.conn().execute("SELECT device_id, lane FROM targets").fetchall())
        self.assertEqual(got, {"a": 1, "b": 2, "c": 3, "gone": None, "bad": None}, "first seen first; ties by id; removed and banned: none")


class OverHttp(MatchHttpBase):
    def test_range_control_sets_and_takes_a_lane(self):
        st, _, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["admin"] = cookie.split(";")[0]
        url = "/api/v1/admin/targets/lane-1/lane"
        self.assertEqual(self.req("POST", url, {"lane": 2})[0], 401, "Range Control only")
        st, body, _ = self.req("POST", url, {"lane": 2}, "admin")
        self.assertEqual((st, body["lane"], body["holder"]), (409, 2, "lane-2"), "taken: who holds it, for the question")
        st, body, _ = self.req("POST", url, {"lane": 2, "take": True}, "admin")
        self.assertEqual((st, body["freed"]), (200, "lane-2"))
        st, body, _ = self.req("POST", url, {"lane": "x"}, "admin")
        self.assertEqual(st, 400)
        st, body, _ = self.req("POST", "/api/v1/admin/targets/lane-2/lane", {"lane": None}, "admin")
        self.assertEqual((st, body["lane"]), (200, None))


if __name__ == "__main__":
    unittest.main()
