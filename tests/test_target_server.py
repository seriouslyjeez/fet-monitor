"""FeT Monitor's side of the FeT target firmware: keys found by fingerprint, signed hello / shots / replies, the
position solved from raw times (fetcore/shotsolve.py), and signed commands with boot and id."""
import json
import math
import os
import shutil
import tempfile
import unittest

from fetserver.core import BadRequest, Core
from fetserver.db import Database
from fetcore.shotsolve import Geometry, forward
from fetcore.targetproto import key_fingerprint, mac

KEY = bytes(range(1, 33))
FP = key_fingerprint(KEY)
GEOM = Geometry.from_upstream({"SENSOR": 230, "ANGLE": 45})


def signed(head, key=KEY):
    return f"{head} h={mac(key, head.encode()) if key else '-'}"


def hello(boot, key=KEY, fp=FP, fw="0.1.0", rev=310):
    return signed(f"FT1 H b={boot} fw={fw} rev={rev} k=1234abcd key={fp or 'none'}", key)


def shot(seq, counts, mask=15, temp="200", face="-", key=KEY, rate=None):
    n, e, s, w = (int(v) for v in counts)
    r = "" if rate is None else f" r={rate}"
    return signed(f"FT1 S b=3 q={seq} t=1000 n={n} e={e} s={s} w={w} m={mask:x} f={face} c={temp} k=1234abcd d=0{r}", key)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = Core(Database(os.path.join(tmp.name, "t.db")))
        self.seq = 0

    def send(self, etype, raw, dev="t1"):
        self.seq += 1
        r = self.core.handle_events({"device_id": dev, "boot_count": 1, "uptime_ms": 10_000 + self.seq,
                                     "events": [{"seq": self.seq, "type": etype, "uptime_ms": 9_000 + self.seq, "raw": raw}]})
        self.assertEqual(len(r["acked"]), 1, r)

    def target(self, dev="t1"):
        return self.core.db.conn().execute("SELECT * FROM targets WHERE device_id=?", (dev,)).fetchone()

    def shots(self):
        return self.core.db.conn().execute("SELECT * FROM shots ORDER BY id").fetchall()

    def last_error(self):
        return self.core.db.conn().execute("SELECT parse_error FROM events ORDER BY id DESC LIMIT 1").fetchone()[0]

    def bound(self, boot=3):
        self.core.add_target_key(KEY, "test")
        self.send("target_hello", hello(boot))


class Sleep(Base):
    """Lights out, shots ignored: on a FeT target's own word (its replies), woken by Range Control, its lights button
    (it says hello: the server asks), a new start, a shooter taking it, or a match."""
    def lines(self):
        return [r[0].rsplit(" h=", 1)[0] for r in self.core.db.conn().execute("SELECT line FROM commands ORDER BY id")]

    def asleep(self):
        return self.target()["target_asleep"]

    def test_slept_and_woken_by_the_target_s_word(self):
        self.bound()
        self.core.target_command("t1", "sleep")
        self.assertEqual(self.lines()[-1], "FT1 C b=3 i=3 sleep")
        self.assertEqual(self.asleep(), 0, "not until the target says so")
        self.send("target_reply", signed("FT1 R b=3 i=3 r=ok"))
        self.assertEqual(self.asleep(), 1)
        self.assertTrue(self.core.admin_view()["targets"][0]["fet_target"]["asleep"])
        self.core.target_command("t1", "status")
        self.send("target_reply", signed("FT1 R b=3 i=4 r=ok k=0a0b0c0d up=5 d=0 q=0 sl=0"))
        self.assertEqual(self.asleep(), 0, "its status says awake")
        self.assertNotIn("sl", json.loads(self.target()["target_settings_json"] or "{}"), "not a setting")

    def test_a_refusal_changes_nothing(self):
        self.bound()
        self.core.target_command("t1", "sleep")
        self.send("target_reply", signed("FT1 R b=3 i=3 r=not_supported"))
        self.assertEqual(self.asleep(), 0)

    def test_its_lights_button_and_a_new_start_wake_it(self):
        self.bound()
        self.core.target_command("t1", "sleep")
        self.send("target_reply", signed("FT1 R b=3 i=3 r=ok"))
        self.send("target_hello", hello(3))                     # the same start: woken by its button?
        self.assertEqual(self.lines()[-1], "FT1 C b=3 i=4 status", "asked")
        self.send("target_reply", signed("FT1 R b=3 i=4 r=ok k=0a0b0c0d up=5 d=0 q=0 sl=0"))
        self.assertEqual(self.asleep(), 0)
        n = len(self.lines())
        self.send("target_hello", hello(3))
        self.assertEqual(len(self.lines()), n, "awake: a repeated hello asks nothing")
        self.core.target_command("t1", "sleep")
        self.send("target_reply", signed("FT1 R b=3 i=5 r=ok"))
        self.send("target_hello", hello(4))
        self.assertEqual(self.asleep(), 0, "a new start: awake (sleep is not saved)")

    def test_taking_control_wakes_it_once(self):
        self.bound()
        self.core.target_command("t1", "sleep")                 # not answered yet: still meant to sleep
        self.core.acquire_control("t1", "client-0000001", "Jo")
        self.assertEqual(self.lines()[-1], "FT1 C b=3 i=4 wake")
        self.core.acquire_control("t1", "client-0000001", "Jo")   # renewing
        self.assertEqual(self.lines()[-1], "FT1 C b=3 i=4 wake", "only when taken")
        self.core.release_control("t1", "client-0000001")
        self.core.acquire_control("t1", "client-0000002", "Al")
        self.assertEqual(len([l for l in self.lines() if l.endswith("wake")]), 1, "awake already: nothing more")

    def test_never_asleep_in_a_match(self):
        self.bound()
        self.core.target_command("t1", "sleep")
        fid = self.core.matches.save_format({"name": "M", "book": "ISSF", "scoring": "decimal",
                                             "phases": [{"kind": "record", "label": "R", "seconds": 600, "shots": 10}]}, "md")["id"]
        self.core.matches.create("Night", fid, [{"device_id": "t1"}], "md")
        self.assertEqual(self.lines()[-1], "FT1 C b=3 i=4 wake", "put into a match: woken")
        with self.assertRaisesRegex(BadRequest, "cannot sleep"):
            self.core.target_command("t1", "sleep")
        self.core.target_command("t1", "wake")                  # always allowed


class Keys(Base):
    def test_a_signed_hello_binds_the_key(self):
        self.bound()
        t = self.target()
        self.assertEqual((t["key_fp"], t["target_boot"], t["arduino_version"]), (FP, 3, "FeT target 0.1.0"))

    def test_add_key_checks(self):
        for bad in (b"short", bytes(32), "x" * 32):
            with self.assertRaises(BadRequest):
                self.core.add_target_key(bad)
        self.assertEqual(self.core.add_target_key(KEY), FP)
        self.assertEqual(self.core.add_target_key(KEY), FP, "the same key again is fine")
        other = bytes(range(5, 37))
        with self.core.db.write() as c:                                         # pretend a fingerprint collision
            c.execute("UPDATE target_keys SET fingerprint=? WHERE fingerprint=?", (key_fingerprint(other), FP))
        with self.assertRaisesRegex(BadRequest, "same fingerprint"):
            self.core.add_target_key(other)

    def test_a_forged_hello_binds_nothing(self):
        self.core.add_target_key(KEY)
        self.send("target_hello", hello(3, key=bytes(range(2, 34))))           # claims our fingerprint, wrong key
        self.assertEqual(self.last_error(), "signature does not match")
        self.assertIsNone(self.target()["key_fp"])

    def test_an_unsigned_hello_cannot_unbind(self):
        self.bound()
        self.send("target_hello", hello(4, key=None, fp=None, fw="9.9.9"))     # "I have no key"
        t = self.target()
        self.assertEqual((t["key_fp"], t["target_boot"], t["arduino_version"]), (FP, 3, "FeT target 0.1.0"))

    def test_unknown_key_only_shows_the_version(self):
        self.send("target_hello", hello(3))                                     # key not on this server
        t = self.target()
        self.assertEqual((t["key_fp"], t["target_boot"], t["arduino_version"]), (None, None, "FeT target 0.1.0"))

    def test_boot_never_moves_back(self):
        self.bound(5)
        self.send("target_hello", hello(4))                                     # delivered late
        self.assertEqual(self.target()["target_boot"], 5)
        self.send("target_hello", hello(6))
        self.assertEqual(self.target()["target_boot"], 6)


class Shots(Base):
    def test_position_from_raw_times(self):
        self.bound()
        for x, y in ((0.0, 0.0), (5.0, -8.0), (-14.0, 3.0)):
            self.send("target_shot", shot(self.seq, forward(x, y, GEOM, 20.0)))
        rows = self.shots()
        self.assertEqual(len(rows), 3)
        for r, (x, y) in zip(rows, ((0.0, 0.0), (5.0, -8.0), (-14.0, 3.0))):
            tx, ty = GEOM.to_target(x, y)
            self.assertLess(math.hypot(r["x_h"] / 100 - tx, r["y_h"] / 100 - ty), 0.06)
            self.assertEqual((r["auth"], r["mic_mask"], r["is_miss"]), (1, 15, 0))
            self.assertLess(r["misfit_um"], 60)
            self.assertEqual(r["temp_c"], 20.0)
        self.assertEqual(rows[0]["ring"], 10)

    def test_paper_only_for_shots_inside(self):
        """paper_eco_mm: the target leaves the paper to FeT Monitor, which advances it (a signed paper command) only for
        a shot that close to the centre. Before 0.6.36 nothing asked, so the paper never moved with it set."""
        self.bound()
        papers = lambda: [r[0] for r in self.core.db.conn().execute("SELECT line FROM commands WHERE line LIKE '% paper %'")]
        self.send("target_shot", shot(1, forward(3.0, 4.0, GEOM, 20.0)))
        self.assertEqual(papers(), [], "not set: the target advances the paper itself")
        with self.core.db.write() as c:
            c.execute("UPDATE targets SET target_settings_json=?", (json.dumps({"paper_eco_mm": 10}),))
        self.send("target_shot", shot(2, forward(3.0, 4.0, GEOM, 20.0)))            # 5 mm from the centre
        self.assertEqual(len(papers()), 1)
        line = papers()[0]
        head, _, tag = line.rpartition(" h=")
        self.assertTrue(head.startswith("FT1 C b=3 i=") and head.endswith(" paper"))
        self.assertEqual(tag, mac(KEY, head.encode()), "signed with the target's key")
        self.send("target_shot", shot(3, forward(20.0, 0.0, GEOM, 20.0)))           # 20 mm out: the paper stays
        self.send("target_shot", shot(4, forward(3.0, 4.0, GEOM, 20.0), mask=3))   # a miss: nothing to place
        self.assertEqual(len(papers()), 1)
        old = self.core.ECO_FRESH_MS
        self.core.ECO_FRESH_MS = -1                                                   # a shot delivered late
        self.send("target_shot", shot(5, forward(1.0, 1.0, GEOM, 20.0)))
        self.core.ECO_FRESH_MS = old
        self.assertEqual(len(papers()), 1, "a late delivery moves no paper")

    def test_a_target_whose_key_this_pi_does_not_know_is_listed(self):
        """The key was set by another Pi (or the hello was lost): the target still shows in Range Control, with what it
        says its key is, so the page can say what to do (2026-11-03)."""
        other = bytes(range(32, 64))
        from fetcore.targetproto import key_fingerprint
        self.send("target_hello", hello(3, key=other, fp=key_fingerprint(other)))
        t = self.target()
        self.assertEqual((t["key_fp"], t["hello_key_fp"]), (None, key_fingerprint(other)), "not bound, but noted")
        self.assertIsNotNone(t["target_seen_ms"])
        ft = next(x for x in self.core.admin_view()["targets"] if x["device_id"] == "t1")["fet_target"]
        self.assertEqual((ft["key_fp"], ft["hello_key_fp"]), (None, key_fingerprint(other)))
        self.send("target_shot", shot(1, forward(1.0, 2.0, GEOM, 20.0), key=other), dev="t2")   # shots only, no hello yet
        ft2 = next(x for x in self.core.admin_view()["targets"] if x["device_id"] == "t2")["fet_target"]
        self.assertEqual((ft2["hello_key_fp"], ft2["key_fp"]), (None, None), "listed from its shots alone")

    def test_the_full_80_mhz_resolution(self):
        """ESP32 firmware 0.3.2 sends its capture timers' counts at 80 MHz with r=80000000: the same shot as at 10 MHz,
        8 times finer. walk_ticks is kept in microseconds (Core.at_rate)."""
        self.core.add_target_key(KEY, "test")
        self.send("target_hello", hello(3, rev=620))
        esp = Geometry.from_upstream({"SENSOR": 232, "ANGLE": 45}, clock_mhz=10.0)
        fine = Core.at_rate(esp, 80_000_000)
        self.assertEqual((fine.clock_mhz, fine.walk_ticks), (80.0, esp.walk_ticks * 8))
        self.assertIs(Core.at_rate(esp, None), esp)
        worst = {}
        for rate, g in ((None, esp), (80_000_000, fine)):
            errs = []
            for k, (x, y) in enumerate([(5.0, -8.0), (-31.7, 12.2), (0.4, 44.9), (-17.3, -26.1)]):
                self.send("target_shot", shot(len(self.shots()) + 1, forward(x, y, g, 20.0), rate=rate))
                r = self.shots()[-1]
                tx, ty = g.to_target(x, y)
                errs.append(math.hypot(r["x_h"] / 100 - tx, r["y_h"] / 100 - ty))
            worst[rate] = max(errs)
        self.assertLess(worst[80_000_000], 0.012, "placed from 12.5 ns counts (stored to 0.01 mm)")
        self.assertLess(worst[80_000_000], worst[None], "finer than the 10 MHz counts of the same shots")

    def test_a_bad_counter_rate_is_refused(self):
        self.core.add_target_key(KEY, "test")
        self.send("target_hello", hello(3, rev=620))
        for rate in (999_999, 1_000_000_001):
            self.send("target_shot", shot(1, forward(1.0, 1.0, GEOM, 20.0), rate=rate))
        self.assertEqual(len(self.shots()), 0, "outside 1 MHz to 1 GHz: not a shot")

    def test_an_esp32_board_counts_at_10_mhz(self):
        """The ESP32-S3 board 6.2 counts the 10 MHz oscillator (PCNT); upstream's circle there is 232 mm. The board
        revision comes from the signed hello; an unsigned one cannot change it once a key is bound."""
        self.core.add_target_key(KEY, "test")
        self.send("target_hello", hello(3, rev=620))
        self.assertEqual(self.target()["board_rev"], 620)
        esp = Geometry.from_upstream({"SENSOR": 232, "ANGLE": 45}, clock_mhz=10.0)
        self.send("target_shot", shot(1, forward(5.0, -8.0, esp, 20.0)))
        r = self.shots()[0]
        tx, ty = esp.to_target(5.0, -8.0)
        self.assertLess(math.hypot(r["x_h"] / 100 - tx, r["y_h"] / 100 - ty), 0.06, "solved with the 10 MHz clock")
        self.send("target_hello", signed("FT1 H b=4 fw=0.1.0 rev=310 k=1234abcd key=none", None))
        self.assertEqual(self.target()["board_rev"], 620, "an unsigned hello does not change it")
        self.send("target_hello", hello(4, rev=310))
        self.assertEqual(self.target()["board_rev"], 310, "a signed one does")
        self.send("target_shot", shot(2, forward(5.0, -8.0, GEOM, 20.0)))
        r = self.shots()[1]
        tx, ty = GEOM.to_target(5.0, -8.0)
        self.assertLess(math.hypot(r["x_h"] / 100 - tx, r["y_h"] / 100 - ty), 0.06, "a Mega board: 8 MHz")

    def test_a_forged_shot_is_no_shot(self):
        self.bound()
        line = shot(1, forward(1.0, 1.0, GEOM, 20.0))
        self.send("target_shot", line.replace("n=", "n=1"))
        self.assertEqual(self.shots(), [])
        self.assertEqual(self.last_error(), "signature does not match")

    def test_no_key_here_means_not_authenticated(self):
        self.send("target_shot", shot(1, forward(1.0, 1.0, GEOM, 20.0)))
        self.assertEqual(self.shots()[0]["auth"], 0)

    def test_missing_microphone_and_face_only(self):
        self.bound()
        t = forward(3.0, 4.0, GEOM, 20.0)
        t[3] = 0
        self.send("target_shot", shot(1, t, mask=7))                              # West did not trigger
        self.send("target_shot", shot(2, [0, 0, 0, 0], mask=0, face="-4500"))    # the plate, no microphone
        a, b = self.shots()
        tx, ty = GEOM.to_target(3.0, 4.0)
        self.assertLess(math.hypot(a["x_h"] / 100 - tx, a["y_h"] / 100 - ty), 0.06)
        self.assertIsNone(a["misfit_um"], "three microphones: no misfit to report")
        self.assertEqual((b["is_miss"], b["ring"], b["face_us"], b["mic_mask"]), (1, 0, -4500, 0))

    def test_a_failed_solve_is_a_miss_not_a_centre_hit(self):
        self.bound()
        t = forward(3.0, 4.0, GEOM, 20.0)
        self.send("target_shot", shot(1, t, mask=3))                              # only North and East
        r = self.shots()[0]
        self.assertEqual((r["is_miss"], r["ring"], r["x_h"]), (1, 0, None))

    def test_a_misfit_is_recorded(self):
        self.bound()
        t = forward(3.0, 4.0, GEOM, 20.0)
        t[1] += 40                                                                 # East 5 us late
        self.send("target_shot", shot(1, t))
        self.assertGreater(self.shots()[0]["misfit_um"], 300)

    def test_calibration_per_target(self):
        self.bound()
        g = Geometry(sensors=[(0, 118.0), (115.0, 0), (0, -118.0), (-115.0, 0)], z_mm=13, angle_deg=45, clock_mhz=8, walk_ticks=50)
        with self.core.db.write() as c:
            c.execute("UPDATE targets SET geometry_json=? WHERE device_id='t1'",
                      (json.dumps({"sensors": g.sensors, "z_mm": 13, "angle_deg": 45, "clock_mhz": 8, "walk_ticks": 50}),))
        self.send("target_shot", shot(1, forward(6.0, -2.0, g, 20.0)))
        r = self.shots()[0]
        tx, ty = g.to_target(6.0, -2.0)
        self.assertLess(math.hypot(r["x_h"] / 100 - tx, r["y_h"] / 100 - ty), 0.06, "solved with this target's geometry")


class Commands(Base):
    def test_needs_a_bound_key(self):
        self.send("target_hello", hello(3))
        with self.assertRaisesRegex(BadRequest, "key"):
            self.core.target_command("t1", "get")

    def test_needs_a_boot(self):
        self.bound()
        with self.core.db.write() as c:
            c.execute("UPDATE targets SET target_boot=NULL")
        with self.assertRaisesRegex(BadRequest, "key"):
            self.core.target_command("t1", "get")

    def test_signed_with_boot_and_rising_ids(self):
        self.bound()
        ids = [self.core.target_command("t1", v) for v in ("get", "status")]
        lines = [r["line"] for r in self.core.db.conn().execute("SELECT line FROM commands ORDER BY id")]
        self.assertTrue(lines[0].startswith("FT1 C b=3 i=1 get h="), "the server asked for the settings itself")
        self.assertTrue(lines[1].startswith("FT1 C b=3 i=2 status h="), "... and for the status (the trip point)")
        self.assertTrue(lines[2].startswith("FT1 C b=3 i=3 get h="))
        self.assertTrue(lines[3].startswith("FT1 C b=3 i=4 status h="))
        head, _, tag = lines[2].rpartition(" h=")
        self.assertEqual(tag, mac(KEY, head.encode()))
        self.assertEqual(len(ids), 2)
        self.send("target_hello", hello(4))                                        # restarted: ids start over
        self.send("target_hello", hello(4))                                        # a repeated hello: nothing new
        self.core.target_command("t1", "paper")
        last = [r[0] for r in self.core.db.conn().execute("SELECT line FROM commands ORDER BY id DESC LIMIT 4")]
        self.assertTrue(last[0].startswith("FT1 C b=4 i=3 paper"), last)
        self.assertTrue(last[1].startswith("FT1 C b=4 i=2 status "), "one get + status per new start, not per hello")
        self.assertTrue(last[2].startswith("FT1 C b=4 i=1 get "))
        self.assertTrue(last[3].startswith("FT1 C b=3 i=4 status"))

    def test_checks(self):
        self.bound()
        for verb, settings in (("dance", None), ("set", None), ("get", {"led_pct": 5}), ("set", {"led pct": 5})):
            with self.assertRaises(BadRequest, msg=(verb, settings)):
                self.core.target_command("t1", verb, settings)
        self.core.matches.lock_of = lambda c, d: "Final"
        with self.assertRaisesRegex(BadRequest, "match"):
            self.core.target_command("t1", "set", {"led_pct": 5})
        self.core.target_command("t1", "paper")                                    # paper is fine in a match

    def test_replies(self):
        self.bound()
        self.core.target_command("t1", "get")
        self.core.target_command("t1", "set", {"led_pct": 80})
        self.send("target_reply", signed("FT1 R b=3 i=3 r=ok k=0a0b0c0d led_pct=50 wait_ms=2"))
        self.send("target_reply", signed("FT1 R b=3 i=4 r=ok k=99887766"))
        replies = [r[0] for r in self.core.db.conn().execute("SELECT reply FROM commands ORDER BY id")]
        self.assertEqual(replies, [None, None, "ok", "ok"], "the automatic get and status (1, 2) not answered in this test")
        t = self.target()
        self.assertEqual(t["target_settings_id"], "99887766")
        self.assertEqual(json.loads(t["target_settings_json"]), {"led_pct": 80, "wait_ms": 2})
        self.core.target_command("t1", "status")
        self.send("target_reply", signed("FT1 R b=3 i=5 r=ok k=99887766 up=5000 d=1 q=4 vr=650"))
        self.assertEqual(json.loads(self.target()["target_settings_json"]), {"led_pct": 80, "wait_ms": 2},
                         "uptime, dropped and muted counts and the trip point are not settings")
        st = json.loads(self.target()["target_status_json"])
        self.assertEqual((st["up"], st["d"], st["q"], st["vr"]), (5000, 1, 4, 650), "the status is kept on its own")
        self.assertNotIn("k", st)
        self.core.target_command("t1", "status")                     # newer firmware: a status field this server does not know
        self.send("target_reply", signed("FT1 R b=3 i=6 r=ok k=11223344 up=6000 d=1 q=4 vr=650 wq=7"))
        self.assertEqual(json.loads(self.target()["target_settings_json"]), {"led_pct": 80, "wait_ms": 2}, "is not a setting")
        self.assertEqual(self.target()["target_settings_id"], "11223344", "the settings id in a status reply still counts")
        v = self.core.list_targets()[0]["v_ref"]
        self.assertEqual((v["volts"], v["from"]), (0.65, "target"), "the trip point on the target's page, in volts")

    def test_the_12_volt_supply(self):
        """ESP32 boards from 0.4.2 say what their 12 V supply measures (ps, mV): below 10 V the board runs from USB alone.
        Plugging the supply in does not restart it, so until it has 12 V it is asked again, at most once a minute."""
        t = [1_800_000_000_000]
        self.core.now_ms = lambda: t[0]
        self.bound()
        self.assertIsNone(self.core.list_targets()[0]["power"], "not measured yet (and never on the Mega)")
        self.send("target_reply", signed("FT1 R b=3 i=2 r=ok k=99887766 up=5000 d=0 q=0 vr=1250 ps=750"))
        self.assertEqual({k: v for k, v in self.core.list_targets()[0]["power"].items() if k != "at"}, {"volts": 0.8, "ok": False}, "USB alone")
        status_cmds = lambda: [r[0] for r in self.core.db.conn().execute("SELECT line FROM commands WHERE line LIKE '% status h=%' ORDER BY id")]
        hb = lambda: self.core.handle_heartbeat({"device_id": "t1", "boot_count": 1, "uptime_ms": 20_000})
        hb()
        self.assertEqual(len(status_cmds()), 1, "asked a moment ago: not again yet")
        t[0] += 61_000
        hb(); hb()
        self.assertEqual(len(status_cmds()), 2, "a minute on: asked once more, not twice")
        self.assertIn("i=3 status", status_cmds()[-1])
        self.send("target_reply", signed("FT1 R b=3 i=3 r=ok k=99887766 up=66000 d=0 q=0 ps=11900"))
        p = self.core.list_targets()[0]["power"]
        self.assertEqual((p["volts"], p["ok"]), (11.9, True), "the supply plugged in")
        t[0] += 3_600_000
        hb()
        self.assertEqual(len(status_cmds()), 2, "with 12 V it is not asked again")

    def test_a_refusal_is_recorded_and_changes_nothing(self):
        self.bound()
        self.core.target_command("t1", "set", {"led_pct": 80})
        self.send("target_reply", signed("FT1 R b=3 i=1 r=out_of_range:led_pct"))
        self.assertEqual(self.core.db.conn().execute("SELECT reply FROM commands").fetchone()[0], "out_of_range:led_pct")
        self.assertIsNone(self.target()["target_settings_json"])

    def test_unsigned_or_forged_replies_are_not_believed(self):
        self.bound()
        self.core.target_command("t1", "get")
        self.send("target_reply", signed("FT1 R b=3 i=1 r=ok led_pct=99", key=None))
        self.assertEqual(self.last_error(), "reply not authenticated")
        self.send("target_reply", signed("FT1 R b=3 i=1 r=ok led_pct=99", key=bytes(32)))
        self.assertTrue(self.last_error().startswith("signature does not match"))
        self.assertEqual(self.core.db.conn().execute("SELECT reply FROM commands").fetchone()[0], "no_key",
                         "noted as a refusal (Range Control says why), never as ok")
        self.assertIsNone(self.target()["target_settings_json"], "nothing from them applied")


OTHER_KEY = bytes(range(101, 133))                      # e.g. the key deeptens.com/setup gave the board


class DeafTarget(Base):
    """A target that takes no command from this Pi says why (user 2026-11-21: a target set up at deeptens.com/setup kept
    deepTENS's key through a flash station update; every command was refused in silence), and a board whose start count
    went back is followed by its own signed word."""
    def cmds(self):
        return self.core.db.conn().execute("SELECT line, reply FROM commands ORDER BY id").fetchall()

    def heads(self):
        return [r["line"].rsplit(" h=", 1)[0] for r in self.cmds()]

    def assert_signed(self, key=KEY):
        for r in self.cmds():
            head, _, tag = r["line"].rpartition(" h=")
            self.assertEqual(tag, mac(key, head.encode()), r["line"])

    def test_a_reply_signed_with_another_key(self):
        self.bound()
        self.core.target_command("t1", "sleep")
        self.send("target_reply", signed("FT1 R b=3 i=3 r=bad_signature", key=OTHER_KEY))
        self.assertTrue(self.last_error().startswith("signature does not match"))
        self.assertEqual(self.cmds()[-1]["reply"], "key_mismatch")
        self.assertEqual(self.target()["target_asleep"], 0)
        self.assertEqual(self.target()["target_boot"], 3, "an unchecked reply moves nothing")

    def test_an_unsigned_reply_from_a_board_without_a_key(self):
        self.bound()
        self.core.target_command("t1", "status")
        self.send("target_reply", signed("FT1 R b=1 i=3 r=not_provisioned", key=None))     # its own start, not 3
        self.assertEqual(self.cmds()[-1]["reply"], "no_key")
        self.assertIsNone(self.target()["target_status_json"])

    def test_a_refusal_is_noted_on_the_newest_unanswered_command_with_that_id(self):
        self.bound()
        self.core.target_command("t1", "status")                                        # i=3
        self.send("target_reply", signed("FT1 R b=3 i=3 r=ok k=1234abcd up=1 d=0 q=0"))
        self.send("target_hello", hello(4))                                             # a new start: i=1, 2 again
        self.send("target_reply", signed("FT1 R b=4 i=2 r=bad_signature", key=OTHER_KEY))
        self.assertEqual([r["reply"] for r in self.cmds()], [None, None, "ok", None, "key_mismatch"])
        self.send("target_reply", signed("FT1 R b=3 i=3 r=bad_signature", key=None))
        self.assertEqual(self.cmds()[2]["reply"], "ok", "an answered command keeps its answer")

    def test_an_unsigned_wrong_boot_moves_nothing(self):
        self.bound(40)
        self.core.target_command("t1", "sleep")
        self.send("target_reply", signed("FT1 R b=2 i=3 r=wrong_boot", key=None))
        self.assertEqual(self.target()["target_boot"], 40)
        self.assertEqual(len(self.cmds()), 3, "nothing sent again")

    def test_a_signed_wrong_boot_moves_to_the_boards_start(self):
        self.bound(40)
        self.core.target_command("t1", "set", {"led_pct": 80})                          # b=40 i=3
        self.send("target_reply", signed("FT1 R b=2 i=3 r=wrong_boot"))
        self.assertEqual(self.target()["target_boot"], 2)
        self.assertEqual(self.cmds()[2]["reply"], "wrong_boot")
        self.assertEqual(self.heads()[3:], ["FT1 C b=2 i=4 set led_pct=80", "FT1 C b=2 i=5 get", "FT1 C b=2 i=6 status"],
                         "the refused command signed again for its start, then its settings and status")
        self.assert_signed()
        self.send("target_reply", signed("FT1 R b=2 i=4 r=ok k=0a0b0c0d led_pct=80"))
        self.assertEqual(self.cmds()[3]["reply"], "ok")
        self.core.target_command("t1", "sleep")
        self.assertEqual(self.heads()[-1], "FT1 C b=2 i=7 sleep", "and every command after")

    def test_get_or_status_refused_is_not_asked_twice(self):
        self.bound(40)
        self.core.target_command("t1", "status")
        self.send("target_reply", signed("FT1 R b=2 i=3 r=wrong_boot"))
        self.assertEqual(self.heads()[3:], ["FT1 C b=2 i=4 get", "FT1 C b=2 i=5 status"])

    def test_an_old_refused_command_is_not_sent_again(self):
        t = [1_000_000_000_000]
        self.core.now_ms = lambda: t[0]
        self.bound(40)
        self.core.target_command("t1", "sleep")
        t[0] += self.core.REANCHOR_RESEND_MS + 1
        self.send("target_reply", signed("FT1 R b=2 i=3 r=wrong_boot"))
        self.assertEqual(self.target()["target_boot"], 2)
        self.assertEqual(self.heads()[3:], ["FT1 C b=2 i=4 get", "FT1 C b=2 i=5 status"], "a sleep long after is a surprise")

    def test_no_start_count_is_not_followed(self):
        self.bound(40)
        self.core.target_command("t1", "status")
        self.send("target_reply", signed("FT1 R b=0 i=3 r=wrong_boot"))
        self.assertEqual(self.target()["target_boot"], 40)

    def test_a_lower_start_in_a_hello_is_probed_not_believed(self):
        self.bound(40)
        self.send("target_reply", signed("FT1 R b=40 i=1 r=ok k=1234abcd"))            # its first start's get and status,
        self.send("target_reply", signed("FT1 R b=40 i=2 r=ok k=1234abcd up=1 d=0 q=0"))   # answered long ago
        self.send("target_hello", hello(2))
        self.assertEqual(self.target()["target_boot"], 40, "a hello never moves the start back")
        self.assertEqual(self.heads()[2:], ["FT1 C b=40 i=3 status"], "one status, for the start this Pi knows")
        self.send("target_hello", hello(2))
        self.assertEqual(len(self.cmds()), 3, "not again while it is on its way")
        self.send("target_reply", signed("FT1 R b=2 i=3 r=wrong_boot"))
        self.assertEqual(self.target()["target_boot"], 2)
        self.assertEqual(self.heads()[3:], ["FT1 C b=2 i=4 get", "FT1 C b=2 i=5 status"])
        self.send("target_hello", hello(2))
        self.assertEqual(len(self.cmds()), 5, "the same start again: nothing new")

    def test_an_unknown_key_hello_with_a_lower_start_is_not_probed(self):
        self.bound(40)
        self.send("target_hello", hello(2, OTHER_KEY, key_fingerprint(OTHER_KEY)))
        self.assertEqual(len(self.cmds()), 2)
        self.assertEqual(self.target()["hello_key_fp"], key_fingerprint(OTHER_KEY))


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class SkippingBridge(Base):
    """A bridge whose saved command number is higher than this Pi's skips every command as "already done" (user
    2026-11-22: a target set up at deeptens.com/setup took no command from the Pi afterwards: deepTENS numbers its
    commands higher; replies stayed "-" and its 12 V status never came). A skip in the same bridge start the command
    was handed over in is told apart from a command run before a restart; the Pi numbers past it and sends again."""
    def hb(self, boot, results=(), up=60000):
        return self.core.handle_heartbeat({"device_id": "t1", "boot_count": boot, "uptime_ms": up,
                                           "cmd_results": list(results)})["commands"]

    def done(self, cmds, reply="already done"):
        return [{"id": x["id"], "status": "done", "reply": reply} for x in cmds]

    def rows(self):
        return self.core.db.conn().execute("SELECT id, type, line, status, result, sent_boot FROM commands ORDER BY id").fetchall()

    def heads(self, cmds):
        return [x["line"].rsplit(" h=", 1)[0] for x in cmds]

    def assert_signed(self):
        for r in self.rows():
            if r["type"] == "target":
                head, _, tag = r["line"].rpartition(" h=")
                self.assertEqual(tag, mac(KEY, head.encode()), r["line"])

    def test_skipped_commands_go_again_numbered_past(self):
        self.bound()                                            # get (i=1) and status (i=2) waiting
        first = self.hb(7)
        self.assertEqual([x["id"] for x in first], [1, 2])
        self.core.target_command("t1", "sleep")                 # i=3, waiting (id 3)
        again = self.hb(7, self.done(first))
        self.assertEqual([x["id"] for x in again], [25, 26, 27], "past 8 times the highest number in use (3)")
        self.assertEqual(self.heads(again), ["FT1 C b=3 i=4 get", "FT1 C b=3 i=5 status", "FT1 C b=3 i=6 sleep"],
                         "signed again with new ids, in their order")
        self.assert_signed()
        self.assertEqual([(r["status"], r["result"]) for r in self.rows()], [("sent", None)] * 3)
        self.assertNotIn("already done", [r["result"] for r in self.rows()])
        self.assertEqual(self.target()["target_cmd_id"], 6)
        self.assertEqual(self.hb(7, self.done(again, "sent")), [])
        self.assertEqual([r["status"] for r in self.rows()], ["done"] * 3)
        self.core.target_command("t1", "wake")
        self.assertEqual(self.rows()[-1]["id"], 28, "new commands are numbered from there on")
        self.assertEqual(self.heads(self.hb(7))[-1], "FT1 C b=3 i=7 wake")

    def test_it_jumps_again_until_the_bridge_takes_them(self):
        self.bound()
        cmds = self.hb(7)
        for want in (17, 18 * 8 + 1, (18 * 8 + 2) * 8 + 1):
            cmds = self.hb(7, self.done(cmds))
            self.assertEqual(cmds[0]["id"], want)
        self.assertEqual(self.hb(7, self.done(cmds, "sent")), [])
        self.assertEqual(len(self.rows()), 2)

    def test_run_before_a_restart_stays_done(self):
        self.bound()
        first = self.hb(7)
        self.assertEqual([r["sent_boot"] for r in self.rows()], [7, 7])
        self.assertEqual(self.hb(8, self.done(first), up=3000), [], "a new start: it ran them, then restarted")
        self.assertEqual([(r["id"], r["status"], r["result"]) for r in self.rows()],
                         [(1, "done", "already done"), (2, "done", "already done")])

    def test_first_handed_over_in_an_earlier_start(self):
        self.bound()
        self.hb(7)                                              # handed over; the reply is lost and the bridge restarts
        again = self.hb(8, up=3000)                             # sent again: it keeps the start it was first sent in
        self.assertEqual([r["sent_boot"] for r in self.rows()], [7, 7])
        self.assertEqual(self.hb(8, self.done(again)), [])
        self.assertEqual([r["result"] for r in self.rows()], ["already done"] * 2)

    def test_range_control_sees_what_the_wifi_module_said(self):
        self.bound()
        first = self.hb(7)
        self.hb(8, self.done(first), up=3000)                   # ran before a restart: "already done", no answer
        ft = [t for t in self.core.admin_view()["targets"] if t["device_id"] == "t1"][0]["fet_target"]
        self.assertEqual([(c["status"], c["result"], c["reply"]) for c in ft["commands"]], [("done", "already done", None)] * 2)

    def test_other_commands_move_unchanged(self):
        self.bound()
        self.hb(7, self.done(self.hb(7), "sent"))
        cid = self.core.enqueue_command("t1", "reboot")
        sent = self.hb(7)
        self.assertEqual([x["id"] for x in sent], [cid])
        again = self.hb(7, self.done(sent))
        self.assertEqual([(x["type"], x.get("line")) for x in again], [("reboot", None)])
        self.assertEqual(again[0]["id"], cid * 8 + 1)

    def test_another_devices_commands_stay(self):
        self.bound()
        self.core.handle_heartbeat({"device_id": "t2", "boot_count": 1, "uptime_ms": 1})
        other = self.core.enqueue_command("t2", "reboot")
        self.hb(7, self.done(self.hb(7)))
        self.assertIn(other, [r["id"] for r in self.rows()])
        self.assertEqual([x["id"] for x in self.core.handle_heartbeat(
            {"device_id": "t2", "boot_count": 1, "uptime_ms": 2})["commands"]], [other])


class EndToEnd(Base):
    def test_a_line_signed_by_the_firmware_core_scores(self):
        from tests.test_target_core import KEY as CORE_KEY, Harness, build
        h = Harness(build())
        self.addCleanup(h.close)
        self.core.add_target_key(CORE_KEY)
        self.send("target_hello", signed("FT1 H b=7 fw=0.1.0 rev=310 k=1234abcd key=" + key_fingerprint(CORE_KEY), CORE_KEY))
        counts = [int(v) for v in forward(4.0, -6.0, GEOM, 19.6)]
        h.run("key " + CORE_KEY.hex())
        h.run("temp 196")
        h.run("counts %d %d %d %d" % tuple(counts))
        h.run("run f")
        h.run("tick 10 10000")
        self.send("target_shot", h.run("pop"))
        r = self.shots()[0]
        tx, ty = GEOM.to_target(4.0, -6.0)
        self.assertEqual(r["auth"], 1)
        self.assertLess(math.hypot(r["x_h"] / 100 - tx, r["y_h"] / 100 - ty), 0.06)


    def test_a_board_whose_start_went_back_takes_commands_again(self):
        """The firmware core (boot 7) against a Pi that has start 40: its own signed wrong_boot moves the Pi."""
        from tests.test_target_core import KEY as CORE_KEY, Harness, build
        h = Harness(build())
        self.addCleanup(h.close)
        h.run("sload")
        h.run("ckey " + CORE_KEY.hex())
        self.core.add_target_key(CORE_KEY)
        fp = key_fingerprint(CORE_KEY)
        self.send("target_hello", signed(f"FT1 H b=40 fw=0.1.0 rev=310 k=1234abcd key={fp}", CORE_KEY))
        lines = lambda: [r[0] for r in self.core.db.conn().execute("SELECT line FROM commands ORDER BY id")]
        for line in lines():                                    # get, status for start 40: refused
            self.send("target_reply", h.run("cmd " + line))
        self.assertEqual(self.target()["target_boot"], 7)
        self.core.target_command("t1", "paper")
        for line in lines()[2:]:
            self.send("target_reply", h.run("cmd " + line))
        replies = [r[0] for r in self.core.db.conn().execute("SELECT reply FROM commands ORDER BY id")]
        self.assertEqual(replies[:2], ["wrong_boot", None], "the second, for the old start, never reached a board in it")
        self.assertEqual(replies[2:], ["ok"] * 3, "its settings, its status, then a paper advance: taken again")
        self.assertIsNotNone(self.target()["target_settings_json"])
        self.assertEqual(h.run("acts"), "1 0", "the paper moved once")


if __name__ == "__main__":
    unittest.main()


class OverHttp(unittest.TestCase):
    """Range Control: the Mega's firmware store, target settings commands, the target's state in the admin view."""
    def setUp(self):
        from tests.test_firmware import ServerCase
        self.sc = ServerCase("setUp")
        self.sc.setUp()
        self.addCleanup(self.sc.tmp.cleanup)
        self.addCleanup(self.sc.server.server_close)
        self.addCleanup(self.sc.server.shutdown)
        self.sc.login()
        self.core = self.sc.core
        from fetserver.targetfw import TargetFirmwareStore
        self.sc.server.target_firmware = TargetFirmwareStore(self.sc.dir)

    def hexfile(self, version="0.1.0"):
        prog = b"fet-target-mega/" + version.encode()
        r = bytes([len(prog), 0, 0, 0]) + prog
        return (":" + (r + bytes([(-sum(r)) & 0xFF])).hex().upper() + "\n:00000001FF\n").encode()

    def test_mega_firmware_store(self):
        st, body, _ = self.sc.req("POST", "/api/v1/admin/target-firmware", raw=self.hexfile(), ctype="application/octet-stream")
        self.assertEqual(st, 200, body)
        sha = json.loads(body)["sha256"]
        st, body, _ = self.sc.req("GET", "/api/v1/admin/target-firmware")
        self.assertEqual([m["version"] for m in json.loads(body)["images"]], ["0.1.0"])
        st, body, _ = self.sc.req("POST", "/api/v1/admin/target-firmware", raw=b":00000001FF\n", ctype="application/octet-stream")
        self.assertEqual((st, json.loads(body)["error"]), (400, "no program in the file"))
        self.assertEqual(self.sc.req("POST", "/api/v1/admin/target-firmware/%s/delete" % sha, {})[0], 200)
        self.assertEqual(self.sc.req("POST", "/api/v1/admin/target-firmware/%s/delete" % sha, {})[0], 404)
        self.sc.cookie = None
        self.assertEqual(self.sc.req("POST", "/api/v1/admin/target-firmware", raw=self.hexfile(), ctype="application/octet-stream")[0], 401)

    def test_target_settings_from_range_control(self):
        self.core.add_target_key(KEY)
        self.core.handle_events({"device_id": "t1", "boot_count": 1, "uptime_ms": 10_000,
                                 "events": [{"seq": 1, "type": "target_hello", "uptime_ms": 9_000, "raw": hello(3)}]})
        st, body, _ = self.sc.req("POST", "/api/v1/admin/targets/t1/target-command", {"verb": "set", "settings": {"led_pct": 70}})
        self.assertEqual(st, 200, body)
        st, body, _ = self.sc.req("POST", "/api/v1/admin/targets/t1/target-command", {"verb": "set", "settings": {"led_pct": "x"}})
        self.assertEqual(st, 400)
        st, body, _ = self.sc.req("GET", "/api/v1/admin/targets")
        t = next(t for t in json.loads(body)["targets"] if t["device_id"] == "t1")
        self.assertEqual((t["fet_target"]["key_fp"], t["fet_target"]["boot"]), (FP, 3))
        self.assertEqual([(c["verb"], c["status"]) for c in t["fet_target"]["commands"]], [("set", "queued"), ("status", "queued"), ("get", "queued")],
                         "newest first; the get and status the server sent by itself at the hello")

    def test_mega_routes_need_an_identified_board(self):
        for path, body in (("mega-install", {"port": "/dev/ttyACM0", "sha256": "a" * 64}), ("mega-key", {"port": "/dev/ttyACM0"}),
                           ("mega-restore", {"port": "/dev/ttyACM0", "name": "mega-x-20261031-120000"})):
            st, payload, _ = self.sc.req("POST", "/api/v1/admin/flash/" + path, body)
            self.assertEqual(st, 400, path)
            self.assertIn("identify", json.loads(payload)["error"])
