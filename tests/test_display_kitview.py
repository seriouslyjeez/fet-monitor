"""A portable kit scores on the display (firmware/display/lib/kitview.py) exactly as FeT Monitor does: the same lines
(a Mega's shot lines; an ESP32 board's signed lines at its 10 MHz clock and at r=80000000, one with a microphone
missing) through FeT Monitor's ingest and through the display's view, under CPython and under MicroPython: the same
ring, tenths and inner ten for every shot, the same totals."""
import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fetcore import faces as F
from fetcore.shotsolve import Geometry, forward
from fetserver.core import Core
from fetserver.db import Database
from fetserver.simtarget import shot_line
from tests.test_target_server import hello, shot

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    """A display module by its path: the display's lib never goes on this process's sys.path (its http.py would hide
    the standard library's http for every test after this one)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("display_" + name, ROOT / "firmware/display/lib" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


kitview = _load("kitview")

MP = shutil.which("micropython")
RUNNER = r"""
import sys, json
sys.path.insert(0, %r)
sys.path.insert(0, %r)
sys.path.insert(2, %r)
import kitview
k = kitview.KitView()
for line in open(%r):
    rec = json.loads(line)
    before = k.count
    k.line(rec)
    if k.count != before:
        print(json.dumps([rec["d"], k.last[3], k.last[4], k.last[5], bool(k.last[6])]))
v = k.view(True, 0)
print("TOTALS " + json.dumps(v["totals"]))
"""


def records():
    """(device, boot, type, raw) in order: a Mega's 20 shots, then an ESP32 board's hello and 20 signed shots."""
    rng = random.Random(1114)
    out = []
    for i in range(1, 21):
        x, y = round(rng.gauss(0, 4), 2), round(rng.gauss(0, 4), 2)
        out.append(("mega-kit", 41, "shot", shot_line(i, x, y, [100, 200, 300, 400], 1.0 + i)))
    out.append(("esp-kit", 3, "target_hello", hello(3, rev=520)))
    g10 = Geometry.from_upstream({"SENSOR": 232, "ANGLE": 45}, clock_mhz=10.0)
    for i in range(1, 21):
        x, y = rng.gauss(0, 5), rng.gauss(0, 5)
        rate = 80_000_000 if i % 2 else None
        g = g10.at_rate(rate)
        counts = [round(c) for c in forward(x, y, g, temp_c=20.0, t0_ticks=600000.0 if rate else 60000.0)]
        mask = 7 if i == 5 else 15
        out.append(("esp-kit", 3, "target_shot", shot(i, counts, mask=mask, rate=rate, key=None)))
    return out


class SameAsFeTMonitor(unittest.TestCase):
    def pi(self, recs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = Core(Database(os.path.join(tmp.name, "t.db")))
        for q, (d, b, t, raw) in enumerate(recs, 1):
            core.handle_events({"device_id": d, "boot_count": b, "uptime_ms": 1000 * q, "events": [{"seq": q, "type": t, "uptime_ms": 1000 * q, "raw": raw}]})
        rows = core.db.conn().execute("SELECT device_id, is_miss, ring, tenths, inner FROM shots ORDER BY id").fetchall()
        return [[r["device_id"], bool(r["is_miss"]), r["ring"], r["tenths"], bool(r["inner"])] for r in rows]

    def recs(self):
        return [{"d": d, "b": b, "q": q, "t": t, "u": 1000 * q, "r": raw} for q, (d, b, t, raw) in enumerate(records(), 1)]

    def test_cpython(self):
        recs = self.recs()
        want = self.pi(records())
        self.assertEqual(len(want), 40, "every shot placed")
        k = kitview.KitView()
        got, totals = [], {}
        for rec in recs:
            before = k.count
            k.line(rec)
            if k.count != before:
                got.append([rec["d"], k.last[3], k.last[4], k.last[5], bool(k.last[6])])
            if rec["d"] == "mega-kit" and rec["q"] == 20:
                totals["mega"] = k.view(True, 0)["totals"]
        self.assertEqual(got, want)
        mega = [w for w in want if w[0] == "mega-kit"]
        self.assertEqual(totals["mega"], {"shots": 20, "score": "%.1f" % (sum(w[3] for w in mega) / 10), "x": sum(w[4] for w in mega), "misses": 0})
        v = k.view(True, 0)
        self.assertEqual((v["target"]["device_id"], v["count"], v["bull"], len(v["shots"])), ("esp-kit", 20, 2, 10), "a new target: a new session")
        self.assertEqual(v["face"]["id"], F.DEFAULT_FACE)

    @unittest.skipUnless(MP, "micropython not installed")
    def test_micropython(self):
        recs = self.recs()
        want = self.pi(records())
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "r.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            Path(tmp, "run.py").write_text(RUNNER % (str(ROOT), str(ROOT / "firmware/display/lib"), str(ROOT / "firmware/display/lib"), str(Path(tmp, "r.jsonl"))))
            r = subprocess.run([MP, "-X", "heapsize=16m", str(Path(tmp, "run.py"))], capture_output=True, text=True, timeout=300)
        lines = r.stdout.splitlines()
        self.assertTrue(lines and lines[-1].startswith("TOTALS"), r.stdout[-1500:] + r.stderr[-500:])
        self.assertEqual([json.loads(x) for x in lines[:-1]], want)


REPLAY = r"""
import sys, json
sys.path.insert(0, %r)
sys.path.insert(0, %r)
import kitview
k = kitview.KitView(%r)
n = kitview.replay(k, %r)
print("VIEW " + json.dumps([n, k.view(True, 0)]))
"""


class ARestart(unittest.TestCase):
    """A display started again shows what it showed: the latest session from the stored lines (kitview.replay)."""

    def stories(self):
        recs = SameAsFeTMonitor().recs()
        mega = [r for r in recs if r["d"] == "mega-kit"]
        faced = [dict(r, f="issf-ar10" if r["q"] <= 10 else "issf-r50") for r in mega]
        gap = [dict(r, u=r["u"] + (kitview.SESSION_GAP_MS + 1 if r["q"] > 12 else 0)) for r in mega]
        return {"everything": recs, "mid series": mega[:15], "a face changed": faced, "a long pause": gap, "nothing": []}

    def live(self, recs, face="issf-ar10"):
        k = kitview.KitView(face)
        for r in recs:
            k.line(r)
        return k

    def write(self, tmp, recs, torn=False):
        path = os.path.join(tmp, "events.jsonl")
        with open(path, "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in recs) + ('{"d": "mega-kit", "b"' if torn else ""))
        return path

    def test_the_same_screen_after_a_restart(self):
        for name, recs in self.stories().items():
            for torn in (False, True):
                with self.subTest(name, torn=torn), tempfile.TemporaryDirectory() as tmp:
                    k = kitview.KitView()
                    kitview.replay(k, self.write(tmp, recs, torn))
                    self.assertEqual(k.view(True, 0), self.live(recs).view(True, 0))
        k = kitview.KitView()
        self.assertEqual(kitview.replay(k, "/nonexistent/events.jsonl"), 0)

    def test_only_the_latest_session_is_scored(self):
        recs = self.stories()["a long pause"]
        with tempfile.TemporaryDirectory() as tmp:
            k = kitview.KitView()
            self.assertEqual(kitview.replay(k, self.write(tmp, recs)), 8, "q 13..20: after the pause")
            self.assertEqual(k.count, 8)

    def test_a_face_change(self):
        recs = SameAsFeTMonitor().recs()[:3]
        k = kitview.KitView()
        k.set_face("issf-r50")
        self.assertEqual((k.face.id, k.count), ("issf-r50", 0), "no shots yet: the same series")
        for r in recs:
            k.line(r)
        k.set_face("issf-r50")
        self.assertEqual(k.count, 3, "the same face: nothing changes")
        k.set_face("nra-a17")
        v = k.view(True, 0)
        self.assertEqual((v["face"]["id"], v["count"], v["shots"]), ("nra-a17", 0, []), "another face: a new series")
        self.assertIs(v["face"]["decimal"], False)
        k.line(dict(recs[0], q=50, f="issf-ap10"))
        self.assertEqual((k.face.id, k.count), ("issf-ap10", 1), "a stored line keeps the face it was shot on")
        k.line(dict(recs[0], q=51, f="no-such-face"))
        self.assertEqual(k.face.id, "issf-ap10", "an unknown face: ignored")
        self.assertEqual([f for f, _ in kitview.KIT_FACES][0], F.DEFAULT_FACE)
        self.assertTrue(all(f in F.FACES for f, _ in kitview.KIT_FACES))
        self.assertNotIn("issf-p25", dict(kitview.KIT_FACES), "larger than any FeT target box")

    @unittest.skipUnless(MP, "micropython not installed")
    def test_micropython(self):
        for name, recs in self.stories().items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = self.write(tmp, recs, True)
                Path(tmp, "run.py").write_text(REPLAY % (str(ROOT), str(ROOT / "firmware/display/lib"), "issf-ar10", path))
                r = subprocess.run([MP, "-X", "heapsize=16m", str(Path(tmp, "run.py"))], capture_output=True, text=True, timeout=300)
                self.assertIn("VIEW ", r.stdout, r.stdout[-1500:] + r.stderr[-1500:])
                n, v = json.loads(r.stdout.split("VIEW ", 1)[1])
                self.assertEqual(v, json.loads(json.dumps(self.live(recs).view(True, 0))))


if __name__ == "__main__":
    unittest.main()
