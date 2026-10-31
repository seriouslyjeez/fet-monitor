"""FeT Monitor's side of the FeT target firmware: keys found by fingerprint, signed hello / shots / replies, the
position solved from raw times (fetserver/shotsolve.py), and signed commands with boot and id."""
import json
import math
import os
import shutil
import tempfile
import unittest

from fetserver.core import BadRequest, Core
from fetserver.db import Database
from fetserver.shotsolve import Geometry, forward
from fetserver.targetproto import key_fingerprint, mac

KEY = bytes(range(1, 33))
FP = key_fingerprint(KEY)
GEOM = Geometry.from_upstream({"SENSOR": 230, "ANGLE": 45})


def signed(head, key=KEY):
    return f"{head} h={mac(key, head.encode()) if key else '-'}"


def hello(boot, key=KEY, fp=FP, fw="0.1.0"):
    return signed(f"FT1 H b={boot} fw={fw} rev=310 k=1234abcd key={fp or 'none'}", key)


def shot(seq, counts, mask=15, temp="200", face="-", key=KEY):
    n, e, s, w = (int(v) for v in counts)
    return signed(f"FT1 S b=3 q={seq} t=1000 n={n} e={e} s={s} w={w} m={mask:x} f={face} c={temp} k=1234abcd d=0", key)


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
        self.assertTrue(lines[0].startswith("FT1 C b=3 i=1 get h="))
        self.assertTrue(lines[1].startswith("FT1 C b=3 i=2 status h="))
        head, _, tag = lines[0].rpartition(" h=")
        self.assertEqual(tag, mac(KEY, head.encode()))
        self.assertEqual(len(ids), 2)
        self.send("target_hello", hello(4))                                        # restarted: ids start over
        self.core.target_command("t1", "paper")
        self.assertTrue(self.core.db.conn().execute("SELECT line FROM commands ORDER BY id DESC").fetchone()[0]
                        .startswith("FT1 C b=4 i=1 paper"))

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
        self.send("target_reply", signed("FT1 R b=3 i=1 r=ok k=0a0b0c0d led_pct=50 wait_ms=2"))
        self.send("target_reply", signed("FT1 R b=3 i=2 r=ok k=99887766"))
        replies = [r[0] for r in self.core.db.conn().execute("SELECT reply FROM commands ORDER BY id")]
        self.assertEqual(replies, ["ok", "ok"])
        t = self.target()
        self.assertEqual(t["target_settings_id"], "99887766")
        self.assertEqual(json.loads(t["target_settings_json"]), {"led_pct": 80, "wait_ms": 2})

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
        self.assertEqual(self.last_error(), "signature does not match")
        self.assertIsNone(self.core.db.conn().execute("SELECT reply FROM commands").fetchone()[0])


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
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
        self.assertEqual([(c["verb"], c["status"]) for c in t["fet_target"]["commands"]], [("set", "queued")])

    def test_mega_routes_need_an_identified_board(self):
        for path, body in (("mega-install", {"port": "/dev/ttyACM0", "sha256": "a" * 64}), ("mega-key", {"port": "/dev/ttyACM0"}),
                           ("mega-restore", {"port": "/dev/ttyACM0", "name": "mega-x-20261031-120000"})):
            st, payload, _ = self.sc.req("POST", "/api/v1/admin/flash/" + path, body)
            self.assertEqual(st, 400, path)
            self.assertIn("identify", json.loads(payload)["error"])
