"""Match Control engine: phases on a fake clock, target modes, pause/extra/next, the lock, and out-of-time flags."""
import os
import tempfile
import unittest

from fetserver.core import BadRequest, Core, Forbidden
from fetserver.db import Database
from fetserver.matches import MatchError
from fetserver.matchformats import BUILTIN, record_shots, validate

BASE = 1_000_000_000
SHOT = '{"shot":%d, "miss":0, "name":"TARGET", "time":1.00 ,"x":%s, "y":0.50 , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'
ALICE = "alice-client-01"


class Clock:
    def __init__(self):
        self.t = BASE

    def __call__(self):
        return self.t


def tiny(kind_list):
    """A custom format from (kind, seconds, shots) tuples."""
    return {"name": "Tiny", "phases": [{"kind": k, "label": f"{k} {i}", "seconds": s, "shots": n} for i, (k, s, n) in enumerate(kind_list)]}


class MatchCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.core = Core(Database(os.path.join(tmp.name, "t.db")), now_ms=self.clock, lease_ms=10 ** 9)
        self.m = self.core.matches
        self.seq = {}
        for did in ("lane-1", "lane-2"):
            self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 0})

    def at(self, s):
        self.clock.t = BASE + int(s * 1000)
        self.m.tick()

    def shoot(self, did="lane-1", x="1.00"):
        n = self.seq[did] = self.seq.get(did, 0) + 1
        self.core.handle_events({"device_id": did, "boot_count": 1, "uptime_ms": self.clock.t - BASE, "events": [
            {"seq": n, "type": "shot", "uptime_ms": self.clock.t - BASE, "raw": SHOT % (n, x)}]})

    def mode(self, did="lane-1"):
        return next(t for t in self.core.list_targets() if t["device_id"] == did)["mode"]

    def custom(self, kinds, name="Tiny"):
        return self.m.save_format(dict(tiny(kinds), name=name), "dir")["id"]

    def flags(self, mid):
        return [(f["device_id"], f["n"], f["stream"], f["why"]) for f in self.m.detail(mid)["flags"]]


class Formats(MatchCase):
    def test_builtins_follow_the_rulebook_tables(self):
        f = BUILTIN["issf-ar60"]
        self.assertEqual([(p["kind"], p["seconds"]) for p in f["phases"]], [("setup", 600), ("sight", 900), ("pause", 30), ("record", 4500)])
        self.assertEqual([w["at"] for w in f["phases"][3]["warnings"]], [600, 300], "10 and 5 minutes (6.11.1.2 e)")
        cmp20 = [(p["kind"], p["seconds"] // 60, p["shots"]) for p in BUILTIN["cmp-3x20"]["phases"] if p["kind"] != "pause"]
        self.assertEqual(cmp20, [("setup", 5, 0), ("sight", 8, 0), ("record", 20, 20), ("changeover", 5, 0), ("sight", 5, 0),
                                 ("record", 20, 20), ("changeover", 5, 0), ("sight", 5, 0), ("record", 25, 20)])
        self.assertEqual([p["seconds"] // 60 for p in BUILTIN["cmp-3x10"]["phases"] if p["kind"] == "record"], [10, 10, 15])
        nra = [(p["kind"], p["position"], p["seconds"] // 60) for p in BUILTIN["nra-3x20"]["phases"]]
        self.assertEqual(nra, [("stage", "prone", 28), ("changeover", "", 15), ("stage", "standing", 30),
                               ("changeover", "", 15), ("stage", "kneeling", 25)], "8+20, 5+25, 5+20 (8.2 a)")
        self.assertEqual(BUILTIN["nra-s60"]["phases"][0]["seconds"], (8 + 75) * 60)
        for f in BUILTIN.values():
            validate(f)
        self.assertEqual(record_shots(BUILTIN["nra-3x40"]), 120)

    def test_custom_formats_are_checked(self):
        for bad, why in (({"name": "x", "phases": []}, "1 to"), ({"name": "", "phases": [{"kind": "record", "seconds": 60, "shots": 1}]}, "name"),
                         ({"name": "x", "phases": [{"kind": "fly", "seconds": 60}]}, "kind"),
                         ({"name": "x", "phases": [{"kind": "sight", "seconds": 60}]}, "at least one record"),
                         ({"name": "x", "phases": [{"kind": "record", "seconds": 0, "shots": 1}]}, "time"),
                         ({"name": "x", "phases": [{"kind": "record", "seconds": 60, "shots": 0}]}, "record shots"),
                         ({"name": "x", "phases": [{"kind": "record", "seconds": 60, "shots": 5, "warnings": [{"at": 90}]}]}, "inside")):
            with self.assertRaises(MatchError) as cm:
                self.m.save_format(bad, "dir")
            self.assertIn(why, str(cm.exception))
        fid = self.custom([("sight", 60, 0), ("record", 120, 10)], name="Club night 10")
        self.assertEqual(fid, "custom-club-night-10")
        self.assertIn(fid, [f["id"] for f in self.m.formats()])
        self.assertEqual(self.m.format(fid)["rulebook"], "Custom")
        with self.assertRaises(MatchError):
            self.m.save_format(dict(tiny([("record", 60, 1)]), id="Bad_ID"), "dir")


class Users(MatchCase):
    def test_users(self):
        self.m.set_user("ro.jane", "officer", "secret-pass")
        self.m.set_user("md-bob", "director", "other-pass")
        self.assertEqual(self.m.users(), [{"username": "md-bob", "role": "director"}, {"username": "ro.jane", "role": "officer"}])
        self.m.set_user("ro.jane", "director")                   # role change keeps the password
        self.assertEqual(self.m.user("ro.jane")["role"], "director")
        for name, role, pw in (("admin", "officer", "secret-pass"), ("x", "officer", "secret-pass"), ("ok-name", "boss", "secret-pass"),
                               ("ok-name", "officer", "short"), ("new-one", "officer", None)):
            with self.assertRaises(MatchError):
                self.m.set_user(name, role, pw)
        self.assertTrue(self.m.remove_user("md-bob"))
        self.assertFalse(self.m.remove_user("md-bob"))


class Running(MatchCase):
    def test_phases_modes_and_end(self):
        fid = self.custom([("setup", 60, 0), ("sight", 120, 0), ("pause", 30, 0), ("record", 300, 3)])
        mid = self.m.create("Club", fid, [{"device_id": "lane-1", "competitor": "Alice"}, {"device_id": "lane-2"}], "dir")
        self.assertEqual(self.m.detail(mid)["state"], "ready")
        self.m.start(mid, "ro")
        d = self.m.detail(mid)
        self.assertEqual((d["clock"]["phase"], d["clock"]["remaining_ms"], self.mode()), (0, 60000, "sighter"))
        lane1 = self.core.db.conn().execute("SELECT * FROM series WHERE id=?", (d["lanes"][0]["series_id"],)).fetchone()
        self.assertEqual((lane1["kind"], lane1["shooter"], lane1["total"]), ("match", "Alice", 3))
        self.assertEqual(self.core.db.conn().execute("SELECT shooter FROM series WHERE id=?", (d["lanes"][1]["series_id"],)).fetchone()[0], "Club")
        self.at(59.9)
        self.assertEqual(self.m.detail(mid)["clock"]["phase"], 0)
        self.at(60)
        self.assertEqual(self.m.detail(mid)["clock"]["phase"], 1)
        self.at(180)
        self.assertEqual((self.m.detail(mid)["clock"]["label"], self.mode()), ("pause 2", "sighter"))
        self.at(210)
        self.assertEqual((self.m.detail(mid)["clock"]["kind"], self.mode(), self.mode("lane-2")), ("record", "record", "record"))
        self.at(1000)                                             # long after: the phase ended at 510 s exactly
        d = self.m.detail(mid)
        self.assertEqual((d["state"], self.mode()), ("done", "sighter"))
        self.assertEqual(self.core.db.conn().execute("SELECT ended_ms FROM matches WHERE id=?", (mid,)).fetchone()[0], BASE + 510_000)

    def test_one_tick_catches_up_several_phases_at_their_exact_times(self):
        fid = self.custom([("sight", 10, 0), ("pause", 5, 0), ("record", 20, 1)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.clock.t = BASE + 100_000
        self.assertEqual(self.m.tick(), 3)
        starts = [r[0] for r in self.core.db.conn().execute("SELECT at_ms FROM match_log WHERE match_id=? AND kind='phase_start' ORDER BY id", (mid,))]
        self.assertEqual([s - BASE for s in starts], [0, 10_000, 15_000])

    def test_pause_resume_extra_next_abort(self):
        fid = self.custom([("sight", 100, 0), ("record", 100, 5)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(40)
        self.m.command(mid, "pause", "ro")
        self.at(500)                                              # paused: the clock does not run
        self.assertEqual(self.m.detail(mid)["clock"]["remaining_ms"], 60_000)
        self.m.command(mid, "resume", "ro")
        self.m.command(mid, "extra", "ro", seconds=30)
        self.at(500 + 89)
        self.assertEqual(self.m.detail(mid)["clock"]["phase"], 0)
        self.at(500 + 90)
        self.assertEqual(self.m.detail(mid)["clock"]["phase"], 1, "60 s left + 30 s extra")
        self.m.command(mid, "next", "ro")
        self.assertEqual(self.m.detail(mid)["state"], "done")
        mid2 = self.m.create("D", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid2, "ro")
        self.m.command(mid2, "abort", "ro")
        self.assertEqual(self.m.detail(mid2)["state"], "aborted")
        for bad in (("pause",), ("extra",), ("nonsense",)):
            with self.assertRaises(MatchError):
                self.m.command(mid2, bad[0], "ro")

    def test_lock_while_in_a_match(self):
        self.core.acquire_control("lane-1", ALICE, "Alice")
        fid = self.custom([("record", 60, 1)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")
        with self.assertRaises(Forbidden):
            self.core.set_mode("lane-1", ALICE, "practice")
        with self.assertRaises(Forbidden):
            self.core.set_mode("lane-1", None, "practice", admin=True)          # Range Control too
        for call in (lambda: self.core.remove_target("lane-1"), lambda: self.core.set_banned("lane-1", True),
                     lambda: self.core.enqueue_command("lane-1", "factory_reset")):
            with self.assertRaises(BadRequest):
                call()
        with self.assertRaises(MatchError):
            self.m.create("Other", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(61)
        self.assertEqual(self.m.detail(mid)["state"], "done")
        self.core.set_mode("lane-1", ALICE, "practice")                         # free again

    def test_target_screens_see_the_clock(self):
        fid = self.custom([("sight", 120, 0), ("record", 60, 1)])
        mid = self.m.create("Club", fid, [{"device_id": "lane-1", "competitor": "Alice"}], "dir")
        info = next(t for t in self.core.list_targets() if t["device_id"] == "lane-1")["match"]
        self.assertEqual((info["name"], info["state"], info["competitor"]), ("Club", "ready", "Alice"))
        self.m.start(mid, "ro")
        self.at(20)
        info = next(t for t in self.core.list_targets() if t["device_id"] == "lane-1")["match"]
        self.assertEqual((info["label"], info["remaining_ms"], info["ends_at_ms"], info["next"]), ("sight 0", 100_000, BASE + 120_000, "record 1"))
        self.assertIsNone(next(t for t in self.core.list_targets() if t["device_id"] == "lane-2")["match"])


class Flags(MatchCase):
    def test_out_of_time_shots(self):
        fid = self.custom([("setup", 60, 0), ("sight", 60, 0), ("pause", 30, 0), ("record", 120, 2), ("changeover", 60, 0)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "dir")
        self.shoot()                                              # before the match: not in its series (a practice shot)
        self.m.start(mid, "ro")
        self.at(10); self.shoot()                                 # set-up: no firing
        self.at(70); self.shoot()                                 # sighting: fine
        self.at(125); self.shoot()                                # pause (targets to match): flagged
        self.at(160); self.shoot(); self.shoot(); self.shoot()    # record: 2 fine, the 3rd is one too many
        self.at(200); self.m.command(mid, "pause", "ro")
        self.at(205); self.shoot()                                # clock paused
        self.at(210); self.m.command(mid, "resume", "ro")         # record ends at 280, changeover 280-340
        self.at(300); self.shoot()                                # changeover
        self.at(400); self.shoot("lane-2")                        # after the end
        got = self.flags(mid)
        self.assertEqual(got, [
            ("lane-1", 1, "sighter", "during setup 0 (no firing)"),
            ("lane-1", 3, "sighter", "during pause 2 (no firing)"),
            ("lane-1", 3, "scored", "more than 2 record shots in record 3"),
            ("lane-1", 4, "scored", "while the clock was paused"),
            ("lane-1", 4, "sighter", "during changeover 4 (no firing)"),
            ("lane-2", 1, "sighter", "after the end"),
        ])

    def test_nra_stage(self):
        fid = self.custom([("stage", 300, 2)])
        mid = self.m.create("N", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(10); self.shoot(); self.shoot()                   # sighters
        self.assertEqual(self.mode(), "sighter")
        self.m.command(mid, "start_record", "lane-1", device_id="lane-1")
        self.assertEqual(self.mode(), "record")
        with self.assertRaises(MatchError):
            self.m.command(mid, "start_record", "lane-1", device_id="lane-1")
        self.at(20); self.shoot(); self.shoot(); self.shoot()
        self.assertEqual(self.flags(mid), [("lane-1", 3, "scored", "more than 2 record shots in stage 0")])
        self.assertTrue(self.m.detail(mid)["lanes"][0]["in_record"])
        with self.assertRaises(MatchError):
            self.m.command(mid, "start_record", "x", device_id="lane-2")

    def test_decided_shots_are_marked_in_the_counted_shots(self):
        fid = self.custom([("setup", 60, 0), ("record", 60, 3)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(5); self.shoot()                                  # a sighter during setup: flagged
        self.at(70); self.shoot(); self.shoot()
        sid = self.m.detail(mid)["flags"][0]["shot_id"]
        c = self.core.db.conn()
        counted = lambda: self.m.counted(c, self.m._row(c, mid), self.m._lanes(c, mid)[0], {f["shot_id"]: f for f in self.m.detail(mid)["flags"]})
        self.assertEqual([s["decision"] for s in counted()], [None, None])
        self.m.decide(mid, sid, "penalty2_first", "md")
        self.assertEqual([s["decision"] for s in counted()], ["penalty2_first", None], "the penalised first record shot shows as decided")

    def test_decisions(self):
        fid = self.custom([("setup", 60, 0), ("record", 60, 1)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(5); self.shoot()
        self.at(70); self.shoot()
        flagged = self.m.detail(mid)["flags"]
        self.assertEqual(len(flagged), 1)
        sid = flagged[0]["shot_id"]
        self.m.decide(mid, sid, "penalty2_first", "md")
        self.assertEqual(self.m.detail(mid)["flags"][0]["decision"], "penalty2_first")
        self.m.decide(mid, sid, "undecided", "md")
        self.assertIsNone(self.m.detail(mid)["flags"][0]["decision"])
        good = self.core.db.conn().execute("SELECT id FROM shots WHERE stream='scored'").fetchone()[0]
        with self.assertRaises(MatchError):
            self.m.decide(mid, good, "penalty2", "md")           # not flagged
        with self.assertRaises(MatchError):
            self.m.decide(mid, sid, "double", "md")


if __name__ == "__main__":
    unittest.main()


class Pacing(MatchCase):
    def test_officer_reads_the_commands_and_presses_next(self):
        fid = self.custom([("sight", 60, 0), ("record", 120, 2)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir", paced="officer")
        self.m.start(mid, "ro")
        d = self.m.detail(mid)
        self.assertEqual((d["state"], d["clock"]["phase"], d["clock"]["remaining_ms"]), ("holding", 0, 60_000))
        self.at(20); self.shoot()                                 # before START of sighting: flagged
        self.at(30)
        self.assertEqual(self.m.detail(mid)["clock"]["remaining_ms"], 60_000, "a held phase's clock does not run")
        for bad in ("pause", "extra", "start_record"):
            with self.assertRaises(MatchError):
                self.m.command(mid, bad, "ro", seconds=10)
        self.m.command(mid, "go", "ro")                           # sighting runs 30-90
        self.at(60); self.shoot()                                 # sighting: fine
        self.at(95)
        d = self.m.detail(mid)
        self.assertEqual((d["state"], d["clock"]["phase"], self.mode()), ("holding", 1, "sighter"), "held at record: still sighter")
        self.assertEqual(d["clock"]["prev_end_say"], "", "custom phases have no end command")
        self.m.command(mid, "go", "ro")
        self.assertEqual(self.mode(), "record", "record starts at the officer's START")
        with self.assertRaises(MatchError):
            self.m.command(mid, "go", "ro")
        self.at(100); self.shoot()
        self.assertEqual(self.flags(mid), [("lane-1", 1, "sighter", "before START of sight 0")])

    def test_next_skips_a_held_phase(self):
        fid = self.custom([("setup", 60, 0), ("record", 60, 1)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir", paced="officer")
        self.m.start(mid, "ro")
        self.m.command(mid, "next", "ro")
        self.assertEqual((self.m.detail(mid)["state"], self.m.detail(mid)["clock"]["phase"]), ("holding", 1))
        with self.assertRaises(MatchError):
            self.m.create("D", fid, [{"device_id": "lane-2"}], "dir", paced="sometimes")


class Restart(MatchCase):
    def test_restart_a_phase_flags_its_old_shots_and_counts_afresh(self):
        fid = self.custom([("sight", 60, 0), ("record", 120, 2), ("changeover", 60, 0)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.at(30); self.shoot()                                # a sighter: phase 0 is NOT restarted, so not flagged
        self.at(70); self.shoot(); self.shoot()                  # two record shots, then a mistake is noticed
        self.at(80)
        self.m.restart(mid, 1, "md")
        d = self.m.detail(mid)
        self.assertEqual((d["state"], d["clock"]["phase"], d["clock"]["remaining_ms"]), ("running", 1, 120_000))
        self.at(90); self.shoot(); self.shoot()                  # the full 2 shots again: NOT "more than 2"
        self.assertEqual(self.flags(mid), [("lane-1", 1, "scored", "fired before record 1 was restarted"),
                                           ("lane-1", 2, "scored", "fired before record 1 was restarted")])
        res = self.m.results(mid)["rows"][0]
        self.assertEqual(res["shots"], 2, "only the shots of the restarted phase count until the director decides")
        f1, f2 = self.m.detail(mid)["flags"]
        self.m.decide(mid, f1["shot_id"], "count", "md")
        self.m.decide(mid, f2["shot_id"], "penalty2", "md")
        res = self.m.results(mid)["rows"][0]
        self.assertEqual(res["shots"], 4)
        self.assertAlmostEqual(res["total"], 4 * f1["decimal"] - 2.0, places=5)   # all four shots at the same spot
        self.m.decide(mid, f2["shot_id"], "annul", "md")
        res = self.m.results(mid)["rows"][0]
        self.assertEqual(res["shots"], 3, "an annulled shot does not count")
        self.assertAlmostEqual(res["total"], 3 * f1["decimal"], places=5)
        with self.assertRaises(MatchError):
            self.m.decide(mid, f1["shot_id"], "miss_first", "md")      # that one is for sighting shots

    def test_restart_from_the_beginning_and_after_the_end(self):
        fid = self.custom([("sight", 30, 0), ("record", 30, 1)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir", paced="officer")
        self.m.start(mid, "ro")
        self.m.command(mid, "go", "ro")
        self.at(10); self.shoot()
        self.at(12); self.m.command(mid, "pause", "ro")
        self.at(14); self.m.restart(mid, 0, "md")                 # from a paused phase, back to the start
        self.assertEqual((self.m.detail(mid)["state"], self.m.detail(mid)["clock"]["phase"]), ("holding", 0))
        self.m.command(mid, "go", "ro")                           # sighting 14-44
        self.at(45)
        self.m.command(mid, "go", "ro")                           # record 45-75
        self.at(80)
        self.assertEqual(self.m.detail(mid)["state"], "done")
        self.m.restart(mid, 1, "md")                              # reopen a finished match
        d = self.m.detail(mid)
        self.assertEqual((d["state"], d["clock"]["phase"]), ("holding", 1))
        self.assertIsNone(self.core.db.conn().execute("SELECT ended_ms FROM matches WHERE id=?", (mid,)).fetchone()[0])
        self.assertEqual([f[3] for f in self.flags(mid)], ["fired before sight 0 was restarted"])
        for bad in (-1, 2, "1", True):
            with self.assertRaises(MatchError):
                self.m.restart(mid, bad, "md")
        mid2 = self.m.create("Fresh", fid, [{"device_id": "lane-2"}], "dir")
        with self.assertRaises(MatchError):
            self.m.restart(mid2, 0, "md")

    def test_start_record_again_after_a_restart(self):
        fid = self.custom([("stage", 120, 1)])
        mid = self.m.create("N", fid, [{"device_id": "lane-1"}], "dir")
        self.m.start(mid, "ro")
        self.m.command(mid, "start_record", "s", device_id="lane-1")
        self.m.restart(mid, 0, "md")
        self.assertEqual(self.mode(), "sighter")
        self.assertFalse(self.m.detail(mid)["lanes"][0]["in_record"], "a restarted stage starts in sighting again")
        self.m.command(mid, "start_record", "s", device_id="lane-1")


class Results(MatchCase):
    def test_standings(self):
        fid = self.custom([("setup", 10, 0), ("record", 600, 12)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1", "competitor": "Alice"}, {"device_id": "lane-2", "competitor": "Bob"}], "dir")
        self.m.start(mid, "ro")
        self.at(5); self.shoot("lane-2", "0.00")                  # during set-up: flagged
        self.at(20)
        for _ in range(12):
            self.shoot("lane-1", "3.00")                          # about 9.8 each
        for _ in range(11):
            self.shoot("lane-2", "0.00")
        r = self.m.results(mid)
        alice, bob = (next(x for x in r["rows"] if x["competitor"] == n) for n in ("Alice", "Bob"))
        self.assertEqual((alice["shots"], len(alice["series"]), len(alice["last10"])), (12, 2, 10))
        self.assertEqual(bob["shots"], 11)
        self.assertEqual(bob["pending"], 1)
        self.assertEqual(r["rows"][0]["competitor"], "Bob", "11 centre shots beat 12 off-centre ones")
        self.assertEqual([x["place"] for x in r["rows"]], [1, 2])
        self.assertGreater(bob["x"], 0)
        flag = self.m.detail(mid)["flags"][0]
        self.assertEqual(flag["stream"], "sighter", "a shot during set-up is a sighting shot")
        sid, before = flag["shot_id"], bob["total"]
        with self.assertRaises(MatchError):
            self.m.decide(mid, sid, "count", "md")                # a sighter never becomes a record shot
        self.m.decide(mid, sid, "penalty2_first", "md")
        bob2 = next(x for x in self.m.results(mid)["rows"] if x["competitor"] == "Bob")
        self.assertEqual((bob2["shots"], bob2["pending"]), (11, 0))
        self.assertAlmostEqual(bob2["total"], before - 2.0, places=5)
        self.m.decide(mid, sid, "miss_first", "md")
        first = 10.8                                              # every Bob shot is at 0.5 mm: 10.8
        self.assertAlmostEqual(next(x for x in self.m.results(mid)["rows"] if x["competitor"] == "Bob")["total"], before - first, places=5)
        self.m.decide(mid, sid, "ignore", "md")
        self.assertAlmostEqual(next(x for x in self.m.results(mid)["rows"] if x["competitor"] == "Bob")["total"], before, places=5)
        self.assertEqual([b["id"] for b in self.m.board()], [mid])
        self.assertEqual(self.m.board([mid, 999]), [self.m.results(mid)])

    def test_ties_share_a_place(self):
        fid = self.custom([("record", 600, 2)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}, {"device_id": "lane-2"}], "dir")
        self.m.start(mid, "ro")
        self.at(5)
        for did in ("lane-1", "lane-2"):
            self.shoot(did, "1.00"); self.shoot(did, "1.00")
        self.assertEqual([x["place"] for x in self.m.results(mid)["rows"]], [1, 1])


class SwitchPacing(MatchCase):
    def test_switch_during_a_match_takes_effect_at_the_next_phase(self):
        fid = self.custom([("sight", 60, 0), ("record", 60, 1), ("changeover", 60, 0)])
        mid = self.m.create("C", fid, [{"device_id": "lane-1"}], "dir")            # automatic
        self.m.start(mid, "ro")
        self.at(10)
        self.m.command(mid, "pacing", "md", paced="officer")
        self.assertEqual(self.m.detail(mid)["state"], "running", "the running phase keeps going")
        self.at(60)
        d = self.m.detail(mid)
        self.assertEqual((d["state"], d["clock"]["phase"], d["clock"]["paced"]), ("holding", 1, "officer"), "next phase waits for Next")
        self.m.command(mid, "go", "md")
        self.m.command(mid, "pacing", "md", paced="auto")
        self.at(121)
        self.assertEqual((self.m.detail(mid)["state"], self.m.detail(mid)["clock"]["phase"]), ("running", 2))
        with self.assertRaises(MatchError):
            self.m.command(mid, "pacing", "md", paced="sometimes")


class PacingOptions(MatchCase):
    def test_default_is_spoken_and_only_officer_holds(self):
        fid = self.custom([("sight", 60, 0), ("record", 60, 1)])
        for paced, state in ((None, "running"), ("spoken", "running"), ("auto", "running"), ("officer", "holding")):
            did = "lane-1" if paced in (None, "auto") else "lane-2"
            kw = {} if paced is None else {"paced": paced}
            mid = self.m.create(f"M {paced}", fid, [{"device_id": did}], "dir", **kw)
            self.m.start(mid, "ro")
            d = self.m.detail(mid)
            self.assertEqual((d["clock"]["paced"], d["state"]), (paced or "spoken", state), paced)
            self.m.command(mid, "abort", "ro")
