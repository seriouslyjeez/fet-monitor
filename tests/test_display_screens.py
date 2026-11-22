"""Every FeT Display screen drawn under MicroPython (firmware/display/lib/target.py, ui.py) from real views of a real
FeT Monitor: the real screen driver on a stand-in SPI bus, so drawing off the 320x240 screen fails here as it would on
the CYD (2026-11-14: the keypad's last row did, and froze the display). Skipped without `micropython`."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_athletes import Case as LinkedCase

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
target.pairing(s, "K7M2QX", "FeT Display 0.1.0  cyd-7e08f8", True); n += 1
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
# zoom (the BOOT button): the phone's rules; then every change of zoom gives the picture a full drawing gives
AR = {"id": "issf-ar10", "rings": [{"value": 10 - i, "dia_mm": 0.5 + 5 * i} for i in range(10)], "black_mm": 30.5, "projectile_mm": 4.5, "ring_mm": 2.5}
AP = {"id": "issf-ap10", "rings": [{"value": 10 - i, "dia_mm": 11.5 + 16 * i} for i in range(10)], "black_mm": 59.5, "projectile_mm": 4.5}
def hv(face, shots, zoom):
    return round(target.half_mm({"face": face, "shots": shots}, zoom), 6)
shot = [{"n": 1, "miss": False, "x_h": 1000, "y_h": 0}]
assert hv(AR, [], "full") == 26.0, hv(AR, [], "full")           # the phone's FULL for the air rifle face
assert hv(AR, [], "auto") == 6.75, hv(AR, [], "auto")           # Auto starts at the 8 ring
assert hv(AR, shot, "auto") == 14.25, hv(AR, shot, "auto")      # a shot 10 mm out: the 5 ring holds it
assert hv(AR, [], "bull") == 16.75, hv(AR, [], "bull")
assert hv(AP, [], "bull") == 34.55, hv(AP, [], "bull")          # no ring_mm (an older FeT Monitor): from the rings
assert hv(dict(AR, black_mm=None), [], "bull") == 26.0, "no black: the whole face"
for name in ("shots", "whole rings", "match"):
    for za in target.ZOOMS:
        for zb in target.ZOOMS:
            prev = target.target_with_button(s, views[name], zoom=za)
            target.target_with_button(s, views[name], None, (views[name], None, za), prev, zb)
            same((name, za, zb), lambda: target.target_with_button(s, views[name], zoom=zb))
            n += 1
T = [{"device_id": "fet-%%06d" %% i, "name": "Target number %%d with a long name" %% i, "online": i %% 2 == 0} for i in range(7)]
for k in (0, 2, 7):
    for pg in (0, 1, 2, 5):
        check("choose", target.choose_screen(s, T[:k], pg))
home = dict(views["empty"], home=True, pin_signin=False)
b = target.signin_screen(s, home, qr.encode(home["signin_url"]))
check("home signin", b)
assert [x[4] for x in b] == ["targets", "dismiss"], [x[4] for x in b]
assert target.short_name("C. D.") == "C.D." and target.short_name("J. Q. S.") == "J.Q.S."
assert target.short_name("Al Aim") == "Al Aim" and target.short_name("") == "" and target.short_name(None) == ""
check("confirm", target.confirm_screen(s, "Sign out A Very Long Public Name Indeed?", "SIGN OUT", "signout"))
sys.path.insert(0, %r)                                 # fetcore: a kit scores on the display
import kitview
kv = kitview.KitView("issf-ar10", "Kit")
kit = [kv.view(False, 0)]
check("kit empty", target.target_with_button(s, kit[0]))
SHOT = '{"shot":%%d, "miss":0, "x":%%s, "y":%%s, "N":0, "E":0, "S":0, "W":0}'
for i, (x, y) in enumerate((("1.00", "0.50"), ("-3.20", "2.10"), ("0.10", "-0.30"), ("12.0", "9.0"))):
    kv.line({"d": "fet-kit", "b": 1, "q": i + 1, "t": "shot", "u": 1000 * (i + 1), "r": SHOT %% (i + 1, x, y)})
    kit.append(kv.view(True, 0))
for fid, _ in kitview.KIT_FACES:                       # every face a kit offers, with shots on it
    kv.set_face(fid)
    kv.line({"d": "fet-kit", "b": 1, "q": 99, "t": "shot", "u": 9000, "r": SHOT %% (1, "8.0", "-6.0")})
    kit.append(kv.view(True, 0))
for v in kit:
    for z in target.ZOOMS:
        check("kit " + v["face"]["id"], target.target_with_button(s, v, zoom=z))
for a in kit:                                          # a kit's views over each other: the picture a full drawing gives
    for b in kit:
        prev = target.target_with_button(s, a)
        target.target_with_button(s, b, None, (a, None, "full"), prev)
        same((a["face"]["id"], b["face"]["id"]), lambda: target.target_with_button(s, b))
        n += 1
assert target.panel_button(kit[0]) == ("FACE", "faces")
for pg in range(4):
    b = target.face_screen(s, list(kitview.KIT_FACES), "nra-a17", pg)
    check("faces", b)
    assert {x[4] for x in b} >= {"faces_close", "info", "faces_more"}, [x[4] for x in b]
for status in (None, "deepTENS: type K7M2QX at deeptens.com/display to send this kit's shots there",
               "deepTENS: all sent (3 not taken: not one of your targets on deepTENS)"):
    check("kit info", target.kit_info(s, "FeT-Kit-7e08f8", "abcdefghjk", "kit_abcdefghjkmnpqrs", 8080, "FeT Display 0.1.0  cyd-7e08f8", status))
sv = [k for k in views if k.startswith("sights")]      # sights (user 2026-11-22): every state, with and without a problem
for a in sv:
    for pa in (None, "RECONNECTING"):
        target.sights(s, views[a], pa)
        n += 1
for a in sv:                                           # each over each other: the picture a full drawing gives
    for b in sv:
        for pa, pb in ((None, None), (None, "RECONNECTING"), ("RECONNECTING", None)):
            target.sights(s, views[a], pa)
            target.sights(s, views[b], pb, views[a], pa)
            same(("sights", a, pa, b, pb), lambda: target.sights(s, views[b], pb))
            n += 1
assert target.panel_button(views["phone control"]) == ("CONTROL HERE", "control_here")
assert target.panel_button(views["signed in"])[1] == "signout?"
assert [w for w, _ in target._click_lines({"x": -2, "y": 1})] == ["UP", "LEFT"]
assert target._click_lines({"x": 0, "y": 0}) == []
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
        self.core.handle_heartbeat({"device_id": "lane-3", "boot_count": 1, "uptime_ms": 0})
        self.core.set_lane("lane-3", 3)
        lane3 = paired("lane-3")
        self.core.set_mode("lane-3", None, "practice", admin=True)
        self.core.set_sights("lane-3", "start")
        views["sights empty"] = ld.view(ld.auth(lane3))
        self.shoot("lane-3", "1.00")
        views["sights fire more"] = ld.view(ld.auth(lane3))
        for x in ("1.20", "0.80", "-14.00"):
            self.shoot("lane-3", x)
        views["sights estimate"] = ld.view(ld.auth(lane3))
        self.core.set_sights("lane-3", "show")
        views["sights clicks"] = ld.view(ld.auth(lane3))
        self.core.set_sights("lane-3", "show", 100)
        views["sights many clicks"] = ld.view(ld.auth(lane3))
        views["sights no change"] = dict(views["sights clicks"], sights=dict(views["sights clicks"]["sights"], clicks={"x": 0, "y": 0}))
        views["sights one way"] = dict(views["sights clicks"], sights=dict(views["sights clicks"]["sights"], clicks={"x": 1, "y": 0}))
        self.assertEqual(views["sights many clicks"]["sights"]["clicks"]["x"], -40, "100 per ring")
        views["sights huge clicks"] = dict(views["sights clicks"], sights=dict(views["sights clicks"]["sights"], clicks={"x": -250, "y": 12345}))
        views["phone control"] = dict(views["signed in"], control="phone")   # CONTROL HERE (user 2026-11-22)
        views["claim"] = dict(views["empty"], claim={"public_name": "Al Aim", "until_ms": views["empty"]["server_time_ms"] + 90000, "left_ms": 90000})
        self.assertTrue(views["empty"]["signin_url"], "linked: the sign-in address")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "machine.py").write_text(FAKE_MACHINE)
            Path(tmp, "views.json").write_text(json.dumps(views))
            Path(tmp, "run.py").write_text(RUNNER % (str(ROOT / "firmware/display/lib"), tmp, str(Path(tmp, "views.json")), str(ROOT)))
            r = subprocess.run([MP, "-X", "heapsize=16m", str(Path(tmp, "run.py"))], capture_output=True, text=True, timeout=300)
        self.assertIn("SCREENS", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertGreater(int(r.stdout.split("SCREENS")[1]), 250, "every view over every other (with and without a problem), keypad states and typing, the question")


if __name__ == "__main__":
    unittest.main()
