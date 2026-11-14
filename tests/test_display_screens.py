"""Every FeT Display screen drawn under MicroPython (firmware/display/lib/target.py, ui.py) from real views of a real
FeT Monitor: the real screen driver on a stand-in SPI bus, so drawing off the 320x240 screen fails here as it would on
the CYD (2026-11-14: the keypad's last row did, and froze the display). Skipped without `micropython`."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_athletes import AL, JO, Case as LinkedCase

ROOT = Path(__file__).resolve().parent.parent
MP = shutil.which("micropython")
FAKE_MACHINE = """
class Pin:
    OUT = IN = 0
    def __init__(self, *a, **k): self.v = 1
    def __call__(self, *a):
        if a: self.v = a[0]
        return self.v
class SPI:
    def __init__(self, *a, **k): self.n = 0
    def write(self, b): self.n += len(b)
    def write_readinto(self, a, b): pass
SoftSPI = SPI
"""
RUNNER = r"""
import sys, json
sys.path.insert(0, %r)
sys.path.insert(0, %r)
import ili9341, target, qr, ui
W, H = ili9341.W, ili9341.H


class Capture(ili9341.Screen):
    # the real driver's checks, and a copy of every pixel on the screen (to compare pictures)
    def __init__(self):
        super().__init__()
        self.px = bytearray(W * H * 2)

    def blit(self, x, y, w, h, buf):
        super().blit(x, y, w, h, buf)
        for r in range(h):
            self.px[((y + r) * W + x) * 2:((y + r) * W + x + w) * 2] = buf[r * w * 2:(r + 1) * w * 2]

    def fill(self, x, y, w, h, colour):
        super().fill(x, y, w, h, colour)
        line = bytes((colour & 0xFF, colour >> 8)) * w
        for r in range(h):
            self.px[((y + r) * W + x) * 2:((y + r) * W + x + w) * 2] = line


s = Capture()
views = json.loads(open(%r).read())
n = 0
def check(name, buttons):
    global n
    for b in buttons:
        x, y, w, h, key = b[:5]
        assert 0 <= x and 0 <= y and x + w <= 320 and y + h <= 240, (name, key, x, y, w, h)
        assert h >= 44 or not key, (name, key, "a button under 44 px")
    n += 1
target.pairing(s, "K7M2QX", "FeT Display 0.1.0  cyd-7e08f8"); n += 1
for name, v in views.items():
    check(name, target.target_with_button(s, v))
    check(name + " reconnecting", target.target_with_button(s, v, "RECONNECTING"))
    target.claim_band(s, v, v["server_time_ms"])
    target.clock(s, v, v["server_time_ms"])
    target.message(s, "display error: something long that does not fit at all", ui.BAD)
    check(name + " signin", target.signin_screen(s, v, qr.encode(v["signin_url"]) if v.get("signin_url") else None))
    check(name + " signin without QR", target.signin_screen(s, dict(v, signin_url=None, pin_signin=True), None))
names = list(views)
bad = []
for a in names:                                        # every view drawn over every other: only the changes are drawn,
    for b in names:                                    # and the picture is the one a full drawing gives
        for pa, pb in ((None, None), (None, "RECONNECTING"), ("RECONNECTING", None)):
            prev = target.target_with_button(s, views[a], pa)
            check(a + " then " + b, target.target_with_button(s, views[b], pb, (views[a], pa), prev))
            updated = bytes(s.px)
            s.px[:] = bytes(len(s.px))
            target.target_with_button(s, views[b], pb)
            if bytes(s.px) != updated:
                bad.append((a, b, pa, pb))
assert not bad, ("an update is not the picture a full drawing gives", bad[:5], len(bad))
for a in names:
    target.target_with_button(s, views[a])
    target.message(s, "this lane is in a match: you are signed out after it, and this message is long enough to need two lines or more", ui.BAD)
    target.top(s, views[a])
for phase in ("number", "pin"):
    for digits in ("", "1", "12345", "123456"):
        for msg in (None, "Signing in...", "wrong athlete number or PIN", "too many wrong PINs: try again in 15 minutes"):
            check("keypad", target.keypad_screen(s, phase, digits, msg))
            target.keypad_top(s, phase, digits, msg)
            check("keypad ok", [target.keypad_ok(s, phase, digits)])
            for b in target.keypad_screen(s, phase, digits, msg):
                target.press(s, b, True)
                target.press(s, b, False)
def same(what, redraw):
    # the picture now must be the one a full drawing (redraw()) gives
    upd = bytes(s.px)
    s.px[:] = bytes(len(s.px))
    redraw()
    assert bytes(s.px) == upd, ("not the picture a full drawing gives", what)
for phase in ("number", "pin"):                         # typing: only the new or deleted digit is drawn
    target.keypad_screen(s, phase, "", None)
    for d, msg in (("1", None), ("12", None), ("123", None), ("12", None), ("", None), ("", "wrong athlete number or PIN"),
                   ("1", None), ("1234", None), ("123456", None), ("12345", None), ("12345", "Signing in..."), ("", "too many wrong PINs")):
        target.keypad_top(s, phase, d, msg)
        target.keypad_ok(s, phase, d)
        same((phase, d, msg), lambda: target.keypad_screen(s, phase, d, msg))
        n += 1
mv = views["match"]
target.target_with_button(s, mv)                        # the clock: only the digits that change
for k in range(5):
    target.clock(s, mv, mv["server_time_ms"] + k * 1000 + 999)
def full_then_tick():
    target.target_with_button(s, mv)
    target.clock(s, mv, mv["server_time_ms"] + 4 * 1000 + 999)
same("clock", full_then_tick)
check("confirm", target.confirm_screen(s, "Sign out A Very Long Public Name Indeed?", "SIGN OUT", "signout"))
print("SCREENS", n)
"""


@unittest.skipUnless(MP, "micropython not installed (brew install micropython)")
class EveryScreen(LinkedCase):
    def test_every_screen_fits(self):
        ld = self.core.lane_displays
        views = {}

        def paired(did):
            p = ld.pair("cyd-" + did, "0.1.0")
            ld.approve(p["code"], did)
            return p["token"]

        tok = paired("lane-1")
        views["empty"] = ld.view(ld.auth(tok))
        self.shoot("lane-1", "1.00")
        self.shoot("lane-1", "-30.00")
        self.core.handle_events({"device_id": "lane-1", "boot_count": 1, "uptime_ms": 99, "events": [
            {"seq": 99, "type": "shot", "uptime_ms": 99, "raw": '{"shot":3, "miss":1, "x":0, "y":0, "N":0, "E":0, "S":0, "W":0}'}]})
        views["shots"] = ld.view(ld.auth(tok))
        self.a.pin_sign_in("lane-1", "123456", "2580")
        views["signed in"] = ld.view(ld.auth(tok))
        with self.core.db.write() as c:
            c.execute("UPDATE targets SET name=? WHERE device_id='lane-1'", ("A target with a very long name indeed",))
        views["long names"] = dict(ld.view(ld.auth(tok)), athlete={"public_name": "Somebody With A Very Long Public Name", "via": "pin", "started_ms": 0})
        tok2 = paired("lane-2")
        self.core.set_face("lane-2", "nra-a17")
        self.shoot("lane-2", "2.00")
        views["whole rings"] = ld.view(ld.auth(tok2))
        fid = self.custom([("stage", 300, 2)])
        mid = self.m.create("N", fid, [{"device_id": "lane-2"}], "dir", publish=False)
        self.m.start(mid, "ro")
        self.at(10)
        views["match"] = ld.view(ld.auth(tok2))
        self.assertEqual(views["match"]["match"]["buttons"], ["start_record"])
        views["claim"] = dict(views["empty"], claim={"public_name": "Al Aim", "until_ms": views["empty"]["server_time_ms"] + 90000, "left_ms": 90000})
        self.assertTrue(views["empty"]["signin_url"], "linked: the sign-in address")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "machine.py").write_text(FAKE_MACHINE)
            Path(tmp, "views.json").write_text(json.dumps(views))
            Path(tmp, "run.py").write_text(RUNNER % (str(ROOT / "firmware/display/lib"), tmp, str(Path(tmp, "views.json"))))
            r = subprocess.run([MP, "-X", "heapsize=16m", str(Path(tmp, "run.py"))], capture_output=True, text=True, timeout=300)
        self.assertIn("SCREENS", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertGreater(int(r.stdout.split("SCREENS")[1]), 250, "every view over every other (with and without a problem), keypad states and typing, the question")


if __name__ == "__main__":
    unittest.main()
