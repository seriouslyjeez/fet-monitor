import os
import sqlite3
import tempfile
import unittest

from fetserver.core import BadRequest, Core, NotFound
from fetserver.db import Database

HERE = os.path.dirname(__file__)
REAL_SHOT = ('{"shot":1, "miss":0, "name":"TARGET", "time":752.59 ,"x":-3.61, "y":-14.56 , "N":427, "E":593, '
             '"S":71, "W":0 , "V_REF":4.94, , "T":19.00, , "VERSION":"4.3.0 December 21, 2023" }')
REAL_MISS = '{"shot":2, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":0, "E":5596, "S":13199, "W":0 , "face":0 }'


class Clock:
    def __init__(self, t=1_000_000_000):
        self.t = t

    def __call__(self):
        return self.t


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.db = Database(os.path.join(self.tmp.name, "t.db"))
        self.core = Core(self.db, now_ms=self.clock, offline_after_ms=15000)

    def ev(self, seq, raw=REAL_SHOT, uptime=4000, etype="shot", **kw):
        return {"seq": seq, "type": etype, "uptime_ms": uptime, "raw": raw, **kw}

    def send(self, events, boot=1, uptime=5000, dev="a1"):
        return self.core.handle_events({"device_id": dev, "boot_count": boot, "uptime_ms": uptime, "events": events})

    def rows(self, sql, *a):
        return self.db.conn().execute(sql, a).fetchall()


class Ingest(CoreCase):
    def test_real_shot_scored_and_raw_preserved(self):
        r = self.send([self.ev(1)])
        self.assertEqual(r["acked"], [[1, 1]])
        shot = self.core.list_shots("a1")[0]
        self.assertEqual((shot["ring"], shot["decimal"]), (4, 4.9))
        self.assertEqual((shot["x_mm"], shot["y_mm"]), (-3.61, -14.56))
        self.assertEqual(self.rows("SELECT raw FROM events")[0]["raw"], REAL_SHOT)

    def test_time_reconstruction(self):
        self.send([self.ev(1, uptime=4000)], uptime=5000)
        self.assertEqual(self.rows("SELECT event_ms FROM events")[0][0], self.clock.t - 5000 + 4000)

    def test_duplicates_are_acked_but_not_stored_twice(self):
        self.send([self.ev(1)])
        r = self.send([self.ev(1)])
        self.assertEqual(r["acked"], [[1, 1]])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events")[0][0], 1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM shots")[0][0], 1)

    def test_same_seq_in_different_boots_are_different_events(self):
        self.send([self.ev(1)], boot=1)
        self.send([self.ev(1)], boot=2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM shots")[0][0], 2)

    def test_delayed_delivery_keeps_the_original_time(self):
        self.send([self.ev(1, uptime=4000)], uptime=5000)           # boot 1 seen: start = t0 - 5000
        t0 = self.clock.t
        self.clock.t += 3_600_000                                    # an hour later
        self.send([self.ev(2, uptime=3_000_000)], uptime=3_605_000)
        times = [r[0] for r in self.rows("SELECT event_ms FROM events ORDER BY seq")]
        self.assertEqual(times, [t0 - 1000, t0 - 5000 + 3_000_000])

    def test_boot_start_keeps_the_minimum_estimate(self):
        self.send([], uptime=5000)
        start = self.rows("SELECT boot_start_ms FROM boots")[0][0]
        self.clock.t += 5300                                         # next request, 300 ms of extra delay
        self.send([], uptime=10000)
        self.assertEqual(self.rows("SELECT boot_start_ms FROM boots")[0][0], start)

    def test_old_boot_unknown_vs_known(self):
        self.send([self.ev(1)], boot=2, uptime=9000)                 # boot 1 never seen by the server
        self.send([self.ev(1, boot_count=1, uptime=3000)], boot=2, uptime=9500, dev="a1")
        q = self.rows("SELECT boot_count, time_quality FROM events ORDER BY boot_count")
        self.assertEqual([(r[0], r[1]) for r in q], [(1, "unknown"), (2, "ok")])
        self.send([self.ev(1, uptime=100)], boot=3, dev="b1")        # a different device: separate boot table
        self.send([], boot=1, uptime=200, dev="c1")
        self.send([self.ev(5, boot_count=1, uptime=150)], boot=2, uptime=50, dev="c1")
        self.assertEqual(self.rows("SELECT time_quality FROM events WHERE device_id='c1'")[0][0], "ok")

    def test_miss_is_never_a_centre_hit(self):
        self.send([self.ev(1, raw=REAL_MISS)])
        s = self.core.list_shots("a1")[0]
        self.assertTrue(s["miss"])
        self.assertEqual((s["ring"], s["decimal"]), (0, 0.0))
        self.assertIsNone(s["x_mm"])
        self.assertIsNone(s["y_mm"])
        self.assertEqual(s["counts"]["S"], 13199)

    def test_unparseable_event_is_stored_flagged_and_acked(self):
        r = self.send([self.ev(1, raw="garbage")])
        self.assertEqual(r["acked"], [[1, 1]])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM shots")[0][0], 0)
        row = self.rows("SELECT raw, parse_error FROM events")[0]
        self.assertEqual(row["raw"], "garbage")
        self.assertTrue(row["parse_error"])

    def test_data_object_used_when_raw_is_missing(self):
        e = {"seq": 1, "type": "shot", "uptime_ms": 10,
             "data": {"shot": 3, "miss": 0, "x": 0.0, "y": 0.0, "N": 1, "E": 2, "S": 3, "W": 0}}
        self.send([e])
        s = self.core.list_shots("a1")[0]
        self.assertEqual((s["ring"], s["decimal"]), (10, 10.9))

    def test_bad_event_rejected_others_still_stored(self):
        r = self.send([self.ev(1), {"type": "shot", "uptime_ms": 1, "raw": REAL_SHOT}, self.ev(3)])
        self.assertEqual(r["acked"], [[1, 1], [1, 3]])
        self.assertEqual(len(r["rejected"]), 1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events")[0][0], 2)

    def test_envelope_validation(self):
        bad = [{}, {"device_id": "x y", "boot_count": 1, "uptime_ms": 1, "events": []},
               {"device_id": "a", "boot_count": "1", "uptime_ms": 1, "events": []},
               {"device_id": "a", "boot_count": 1, "uptime_ms": -1, "events": []},
               {"device_id": "a", "boot_count": 1, "uptime_ms": 1, "events": "no"},
               {"device_id": "a", "boot_count": 1, "uptime_ms": 1, "events": [self.ev(i) for i in range(51)]}]
        for payload in bad:
            with self.subTest(payload=str(payload)[:60]):
                with self.assertRaises(BadRequest):
                    self.core.handle_events(payload)

    def test_batch_is_all_or_nothing(self):
        original = self.core._derive
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return original(*a, **k)

        self.core._derive = flaky
        with self.assertRaises(sqlite3.OperationalError):
            self.send([self.ev(1), self.ev(2), self.ev(3)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM events")[0][0], 0)    # nothing half-stored
        self.core._derive = original
        self.assertEqual(self.send([self.ev(1), self.ev(2), self.ev(3)])["acked"], [[1, 1], [1, 2], [1, 3]])


class Alignment(CoreCase):
    def test_alignment_applies_to_score_but_raw_is_kept(self):
        self.send([], dev="a1")
        with self.db.write() as c:
            c.execute("UPDATE targets SET align_x_h=300 WHERE device_id='a1'")
        raw = REAL_SHOT.replace('"x":-3.61', '"x":-3.00').replace('"y":-14.56', '"y":0.00')
        self.send([self.ev(1, raw=raw)])
        s = self.core.list_shots("a1")[0]
        self.assertEqual((s["raw_x_mm"], s["x_mm"], s["y_mm"]), (-3.0, 0.0, 0.0))
        self.assertEqual((s["ring"], s["decimal"]), (10, 10.9))


class Config(CoreCase):
    def test_echo_updates_target_facts(self):
        with open(os.path.join(HERE, "fixtures", "echo_4_3_0.txt")) as fh:
            echo = fh.read()
        self.send([self.ev(1, raw=echo, etype="config")])
        t = self.core.list_targets()[0]
        self.assertEqual((t["arduino_sn"], t["calibre_x10"], t["arduino_version"]), (302, 45, "4.3.0 December 21, 2023"))

    def test_boot_banner_sets_version(self):
        self.send([self.ev(1, raw='freETarget "4.3.0 December 21, 2023"', etype="arduino_boot")])
        self.assertEqual(self.core.list_targets()[0]["arduino_version"], "4.3.0 December 21, 2023")

    def test_silly_calibre_is_ignored(self):
        self.send([self.ev(1, raw='{"CALIBREx10": 5000, "SN": 1}', etype="config")])
        self.assertEqual(self.core.list_targets()[0]["calibre_x10"], 45)


class Heartbeat(CoreCase):
    def hb(self, **kw):
        body = {"device_id": "a1", "boot_count": 1, "uptime_ms": 1000, "name": "Lane 3"}
        body.update(kw)
        return self.core.handle_heartbeat(body)

    def test_online_then_offline(self):
        self.hb()
        self.assertTrue(self.core.list_targets()[0]["online"])
        self.clock.t += 14_000
        self.assertTrue(self.core.list_targets()[0]["online"])
        self.clock.t += 2_000
        self.assertFalse(self.core.list_targets()[0]["online"])
        self.assertEqual(self.core.list_targets()[0]["name"], "Lane 3")

    def test_command_allowlist(self):
        self.hb()
        for ok in ('{"ECHO":0}', '{"VERSION":0}', '{"NEW":1}'):
            self.core.enqueue_command("a1", "serial", ok)
        for bad in ('{"INIT":0}', '{"BYE":0}', '{"TEST":18}', '{"ECHO":0}{"INIT":0}', "garbage", None):
            with self.subTest(bad=bad):
                with self.assertRaises(BadRequest):
                    self.core.enqueue_command("a1", "serial", bad)
        with self.assertRaises(BadRequest):
            self.core.enqueue_command("a1", "format_disk")
        with self.assertRaises(NotFound):
            self.core.enqueue_command("nobody", "reboot")

    def test_command_redelivered_until_acknowledged(self):
        self.hb()
        cid = self.core.enqueue_command("a1", "serial", '{"NEW":1}')
        first = self.hb()["commands"]
        again = self.hb()["commands"]
        self.assertEqual(first, [{"id": cid, "type": "serial", "line": '{"NEW":1}'}])
        self.assertEqual(again, first)                       # still pending: a lost reply cannot lose a command
        self.assertEqual(self.hb(cmd_results=[{"id": cid, "status": "done", "reply": "ok"}])["commands"], [])
        self.assertEqual(self.rows("SELECT status, result FROM commands")[0][0], "done")

    def test_commands_only_go_to_their_target(self):
        self.hb()
        self.core.handle_heartbeat({"device_id": "b1", "boot_count": 1, "uptime_ms": 1})
        self.core.enqueue_command("a1", "reboot")
        self.assertEqual(self.core.handle_heartbeat({"device_id": "b1", "boot_count": 1, "uptime_ms": 2})["commands"], [])
        self.assertEqual(len(self.hb()["commands"]), 1)


class Maintenance(CoreCase):
    def test_rescore_is_stable(self):
        self.send([self.ev(1), self.ev(2, raw=REAL_MISS)])
        before = self.core.list_shots("a1")
        self.assertEqual(self.core.rescore(), 1)
        self.assertEqual(self.core.list_shots("a1"), before)

    def test_reopening_database_keeps_data_and_schema(self):
        self.send([self.ev(1)])
        db2 = Database(os.path.join(self.tmp.name, "t.db"))
        self.assertEqual(Core(db2).list_shots("a1")[0]["ring"], 4)
        self.assertEqual(db2.integrity_check(), "ok")


if __name__ == "__main__":
    unittest.main()
