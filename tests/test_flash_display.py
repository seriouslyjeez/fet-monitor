"""CYD shot monitors at the flash station (user 2026-11-14, before FeT Monitor 1.1.0): a classic ESP32 with 4 MB is a CYD;
Identify asks it over USB what it runs (MicroPython's raw REPL, fetserver/mpraw.py) and restarts it; Install erases a
first install (or when asked), writes FeT Display at 0x1000 and verifies it; with the range WiFi and a target, the
display becomes that target's lane display (lanedisplays.program) and gets its settings over USB, so it shows the
target as soon as it starts. Also the image store (displayfw.py) and the routes. On hardware: mpraw against the user's
CYD from the Mac (probe, settings, restart; 2026-11-14); esptool on a Pi with a CYD not yet tried."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from fetserver.core import Core
from fetserver.db import Database
from fetserver.displayfw import BadImage, DisplayFirmwareStore, inspect
from fetserver.firmware import FirmwareStore
from fetserver.flashstation import FlashError, FlashStation
from fetserver.lanedisplays import _hash
from tests import test_flashstation as T                 # (the module: its tests are not run twice)

CYD_OUT = "Chip is ESP32-D0WD-V3 (revision v3.1)\nMAC: 8c:94:df:7e:08:f8\nDetected flash size: %s\n"
WIFI = {"ssid": "club", "pass": "password1", "host": "192.168.1.20", "port": 8080}


def display_image(v, chip=b"\0\0", size=0x20000):
    head = bytes([0xE9, 3, 2, 0x20]) + bytes(8) + chip
    body = head + bytes(64) + b"MicroPython ... fet-display/" + v.encode() + b" ..."
    return body + b"\xff" * (size - len(body))


class FakeCyd:
    """The CYD on its USB port, as MicroPython answers: Ctrl-C stops the program (">>> "), Ctrl-A raw mode, a script
    and Ctrl-D runs it ("OK", output, \\x04, error, \\x04, ">"). runs: "fet" (FeT Display <version>, or None for one
    before 0.2.0), "other" (no MicroPython: never answers). fail: an error for the settings script."""
    def __init__(self, runs="fet", version="0.2.0", configured=False, fail=None, fail_after=None):
        self.runs, self.version, self.configured, self.fail, self.fail_after = runs, version, configured, fail, fail_after
        self.q, self.mode, self.code = [], "running", ""
        self.config, self.resets, self.opens, self.stops = None, 0, 0, 0

    def open(self, port):
        self.opens += 1
        return self

    def write(self, data, timeout=3.0):
        if self.runs != "fet":
            return
        if self.mode == "raw" and data.startswith(b"\x02"):    # Ctrl-B: out of raw mode
            self.mode = "repl"
            self.q.append(b"\r\nMicroPython v1.29.0\r\n>>> ")
            data = data[1:]
            if not data:
                return
        if b"\x01" in data and b"machine.reset()" in data:
            self.resets += 1
            self.mode = "running"
            return
        if self.mode == "raw":
            self.code += data.decode()
            if "\x04" in self.code:
                self.run(self.code.split("\x04")[0])
                self.code = ""
            return
        if b"\x01" in data and b"machine.reset()" in data:
            self.resets += 1
            self.mode = "running"
            return
        if b"\x03" in data or b"\x02" in data:
            if self.mode == "running":
                self.stops += 1
            self.mode = "repl"
            self.q.append(b"\r\nKeyboardInterrupt\r\nMicroPython v1.29.0\r\n>>> ")
            return
        if data == b"\x01" and self.mode == "repl":
            self.mode = "raw"
            self.q.append(b"raw REPL; CTRL-B to exit\r\n>")

    def run(self, code):
        if "FETD-PROBE" in code:
            self.q.append(b"OKFETD-PROBE %s %s\r\n\x04\x04>" % ((self.version or "-").encode(), str(self.configured).encode()))
        elif "FETDISPLAY-SAVED" in code:
            if self.fail:
                self.q.append(b"OK\x04Traceback (most recent call last):\r\n" + self.fail.encode() + b"\r\n\x04>")
                return
            line = next(ln for ln in code.splitlines() if ln.startswith("cfg = "))
            self.config = json.loads(json.loads(line[6:]))
            self.configured = True
            self.q.append(b"OKFETDISPLAY-SAVED %d\r\n\x04%s\x04>" % (len(line), (self.fail_after or "").encode()))
        self.mode = "raw"

    def read(self, timeout):
        if self.q:
            return self.q.pop(0)
        time.sleep(min(timeout, 0.005))
        return None

    def restart_esp(self):
        return True

    def readline(self, timeout):
        return None

    def close(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sys = self.root / "sys"
        T.usb_tree(self.sys, "ttyUSB0", "1a86", "7523")
        self.store = DisplayFirmwareStore(self.root)
        self.img = self.store.add(display_image("0.2.0"))
        self.programmed = []

    def station(self, board, size="4MB", program=None):
        fw = FirmwareStore(self.root)
        self.tools = T.Tools(chip_out=(0, CYD_OUT % size))
        st = FlashStation(fw, self.root, run=self.tools, sys_tty=str(self.sys / "class/tty"), now=lambda: 1_800_000_000, background=False,
                          opener=board.open, has_module=lambda name: False, display_images=self.store,
                          program_display=program or self.program)
        st.display_wait = 0.4
        return st

    def program(self, hw_id, device_id, name):
        self.programmed.append((hw_id, device_id))
        return {"name": "Display 7e08f8", "target_name": "Lane 3"}, "fetd_the-token"

    def args(self, sub):
        return [c for c in self.tools.calls if sub in c]


class Identify(Base):
    def test_a_new_cyd(self):
        board = FakeCyd("other")
        r = self.station(board).identify("/dev/ttyUSB0")
        self.assertEqual((r["device"], r["hw_id"], r["running"], r["micropython"], r["first_install"]), ("display", "cyd-7e08f8", None, False, True))
        self.assertIn("not FeT Display (first install)", r["label"])
        self.assertEqual((r["offer"]["version"], r["configurable"]), ("0.2.0", False))

    def test_one_running_fet_display_is_asked_then_restarted(self):
        board = FakeCyd("fet", "0.2.0", configured=True)
        r = self.station(board).identify("/dev/ttyUSB0")
        self.assertEqual((r["running"], r["same"], r["first_install"], r["configured"], r["configurable"]), ("0.2.0", True, False, True, True))
        self.assertEqual((board.stops, board.resets), (1, 1), "stopped to be asked, running again after")
        r = self.station(FakeCyd("fet", None)).identify("/dev/ttyUSB0")
        self.assertIn("FeT Display before 0.2.0", r["label"])
        self.assertFalse(r["first_install"], "MicroPython already: an update keeps its settings unless asked")

    def test_not_a_cyd(self):
        r = self.station(FakeCyd("other"), size="16MB").identify("/dev/ttyUSB0")
        self.assertIsNone(r["offer"])
        self.assertIn("4 MB", r["why_not"])
        self.store.delete(self.img["sha256"])
        r = self.station(FakeCyd("other")).identify("/dev/ttyUSB0")
        self.assertIn("no FeT Display image", r["why_not"])


class Install(Base):
    def test_first_install_erases_writes_verifies_and_sets_it_up(self):
        board = FakeCyd("other")
        st = self.station(board)
        st.identify("/dev/ttyUSB0")
        board.runs, board.version = "fet", "0.2.0"                   # what it runs once written
        job = st.start_display_flash("/dev/ttyUSB0", self.img["sha256"], "test", dict(WIFI), "fet-lane3")
        self.assertEqual(job["state"], "done", job.get("message"))
        self.assertEqual(self.tools.subcmds(), ["version", "flash_id", "flash_id", "erase_flash", "write_flash", "verify_flash"])
        w = self.args("write_flash")[0]
        self.assertIn("0x1000", w)
        self.assertIn(str(self.store.path(self.img["sha256"])), w)
        self.assertIn("no_reset", w, "verified before it starts and writes its file system")
        self.assertIn("0x1000", self.args("verify_flash")[0])
        self.assertEqual(self.programmed, [("cyd-7e08f8", "fet-lane3")])
        self.assertEqual(board.config, {"ssid": "club", "password": "password1", "server": "192.168.1.20:8080", "token": "fetd_the-token"},
                         "what the display's own setup writes for a range, with its token")
        self.assertEqual(board.resets, 1, "restarted with its settings")
        self.assertIn("it shows Lane 3", job["message"])
        self.assertIn("calibrated", job["message"])

    def test_an_update_keeps_its_settings_unless_asked(self):
        board = FakeCyd("fet", "0.1.0", configured=True)
        st = self.station(board)
        st.identify("/dev/ttyUSB0")
        job = st.start_display_flash("/dev/ttyUSB0", self.img["sha256"], "test")
        self.assertEqual(job["state"], "done", job.get("message"))
        self.assertNotIn("erase_flash", self.tools.subcmds())
        self.assertIn("its settings kept", job["message"])
        self.assertIsNone(board.config, "no settings given: none sent")
        st.identify("/dev/ttyUSB0")
        st.start_display_flash("/dev/ttyUSB0", self.img["sha256"], "test", erase=True)
        self.assertIn("erase_flash", self.tools.subcmds(), "asked: erased")

    def test_settings_checked_before_anything_is_written(self):
        board = FakeCyd("other")
        st = self.station(board)
        st.identify("/dev/ttyUSB0")
        for bad, why in ((dict(WIFI, host="fet-pi.local"), "IP address"), (dict(WIFI, ssid=""), "WiFi name"),
                         (dict(WIFI, **{"pass": "short"}), "8 to 63"), (dict(WIFI, port="x"), "port")):
            with self.assertRaisesRegex(FlashError, why):
                st.start_display_flash("/dev/ttyUSB0", self.img["sha256"], "test", bad, "fet-lane3")
        with self.assertRaisesRegex(FlashError, "choose the target"):
            st.start_display_flash("/dev/ttyUSB0", self.img["sha256"], "test", dict(WIFI), None)
        self.assertEqual(self.tools.subcmds(), ["version", "flash_id"], "nothing written")
        with self.assertRaisesRegex(FlashError, "identify"):
            st.start_display_flash("/dev/ttyUSB0", "b" * 64, "test")

    def test_a_display_that_cannot_keep_its_settings(self):
        board = FakeCyd("fet", "0.2.0", fail="OSError: 28")
        st = self.station(board)
        st.identify("/dev/ttyUSB0")
        job = st.start_display_configure("/dev/ttyUSB0", dict(WIFI), "fet-lane3", "test")
        self.assertEqual(job["state"], "failed")
        self.assertIn("OSError: 28", job["message"])
        self.assertIn("Configure sends the settings again", job["message"])

    def test_an_error_after_saving_still_counts(self):
        st = self.station(FakeCyd("fet", "0.2.0", fail_after="MemoryError"))
        st.identify("/dev/ttyUSB0")
        job = st.start_display_configure("/dev/ttyUSB0", dict(WIFI), "fet-lane3", "test")
        self.assertEqual(job["state"], "failed")
        self.assertIn("MemoryError", job["message"])

    def test_another_board_plugged_in_meanwhile(self):
        st = self.station(FakeCyd("other"))
        st.identify("/dev/ttyUSB0")
        self.tools.chip_out = (0, (CYD_OUT % "4MB").replace("8c:94:df:7e:08:f8", "8c:94:df:00:00:01"))
        job = st.start_display_flash("/dev/ttyUSB0", self.img["sha256"], "test")
        self.assertEqual(job["state"], "failed")
        self.assertIn("different board", job["message"])
        self.assertNotIn("write_flash", self.tools.subcmds())

    def test_configure_only(self):
        board = FakeCyd("fet", "0.2.0")
        st = self.station(board)
        st.identify("/dev/ttyUSB0")
        job = st.start_display_configure("/dev/ttyUSB0", dict(WIFI, **{"pass": ""}), "fet-lane3", "test")
        self.assertEqual(job["state"], "done", job.get("message"))
        self.assertEqual(board.config["password"], "", "a network without a password")
        self.assertNotIn("write_flash", self.tools.subcmds())
        st2 = self.station(FakeCyd("other"))
        st2.identify("/dev/ttyUSB0")
        with self.assertRaisesRegex(FlashError, "must run FeT Display"):
            st2.start_display_configure("/dev/ttyUSB0", dict(WIFI), "fet-lane3", "test")


class WithLaneDisplays(Base):
    """The real lane displays: the display is registered on its target and its token works."""
    def test_programmed_on_its_target(self):
        core = Core(Database(str(self.root / "t.db")))
        for dev in ("lane-1", "lane-2"):
            core.handle_events({"device_id": dev, "boot_count": 1, "uptime_ms": 1, "events": []})
        ld = core.lane_displays
        old = ld.approve(ld.pair("cyd-111111", "0.1.0")["code"], "lane-1")
        waiting = ld.pair("cyd-7e08f8", "0.1.0")
        board = FakeCyd("fet", "0.2.0")
        st = self.station(board, program=ld.program)
        st.identify("/dev/ttyUSB0")
        job = st.start_display_configure("/dev/ttyUSB0", dict(WIFI), "lane-1", "test")
        self.assertEqual(job["state"], "done", job.get("message"))
        row = ld.auth(board.config["token"])
        self.assertEqual((row["hw_id"], row["device_id"]), ("cyd-7e08f8", "lane-1"), "its token is the display's key")
        self.assertEqual([d["hw_id"] for d in ld.list()["displays"]], ["cyd-7e08f8"], "one display per target: the old one replaced")
        with self.assertRaises(Exception):
            ld.approve(waiting["code"], "lane-2")
        self.assertNotEqual(_hash(board.config["token"]), old.get("token_hash"))
        job = st.start_display_configure("/dev/ttyUSB0", dict(WIFI), "nope", "test")
        self.assertEqual(job["state"], "failed")
        self.assertIn("target", job["message"], "no such target: said, nothing registered")
        self.assertEqual([d["hw_id"] for d in ld.list()["displays"]], ["cyd-7e08f8"])


class Images(unittest.TestCase):
    def test_only_fet_display_for_the_cyd(self):
        self.assertEqual(inspect(display_image("0.2.0"))["version"], "0.2.0")
        for data, why in ((b"\xe9" + bytes(10), "0xE9"), (display_image("0.2.0", chip=b"\x09\0"), "another chip"),
                          (display_image("0.2.0", size=0x200000), "larger"), (display_image("0.2.0").replace(b"fet-display/", b"xxx-display/"), "not FeT Display"),
                          (display_image("0.2.0") + b"fet-display/0.1.9", "more than one")):
            with self.assertRaisesRegex(BadImage, why):
                inspect(data)
        with tempfile.TemporaryDirectory() as d:
            s = DisplayFirmwareStore(Path(d))
            a, b = s.add(display_image("0.2.0")), s.add(display_image("0.10.0"))
            self.assertEqual(s.best()["version"], "0.10.0")
            self.assertTrue(s.path(a["sha256"]).is_file())
            self.assertTrue(s.delete(b["sha256"]) and not s.delete(b["sha256"]))
            self.assertEqual([m["version"] for m in s.list()], ["0.2.0"])
            self.assertIsNone(s.get("../x"))


class OverHttp(T.MatchHttpBase):
    def setUp(self):
        super().setUp()
        T.usb_tree(self.dir / "sys", "ttyUSB0", "1a86", "7523")
        self.seen = []
        fl = self.server.flash
        fl.start_display_flash = lambda port, sha, by, settings=None, device_id=None, erase=None: (
            self.seen.append((settings, device_id, erase)), {"state": "done", "what": "x"})[1]
        fl.start_display_configure = lambda port, settings, device_id, by: (self.seen.append((settings, device_id)), {"state": "done", "what": "y"})[1]
        st, _, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["admin"] = cookie.split(";")[0]

    def test_the_routes(self):
        page = {"ssid": "club", "pass": "password1", "host": "192.168.1.20", "port": 1}
        for body in ({}, {"erase": "yes"}, {"erase": True}):
            st, _, _ = self.req("POST", "/api/v1/admin/flash/display-install", dict(body, port="/dev/ttyUSB0", sha256="a" * 64, settings=page,
                                                                                   device_id="lane-1"), "admin")
            self.assertEqual(st, 202)
        self.assertEqual([s[2] for s in self.seen], [None, None, True], "erase only when exactly true")
        self.assertEqual(self.seen[0][0]["port"], self.server.server_address[1], "this server's port, not the page's")
        st, _, _ = self.req("POST", "/api/v1/admin/flash/display-configure", {"port": "/dev/ttyUSB0", "settings": page, "device_id": "lane-1"}, "admin")
        self.assertEqual((st, self.seen[-1][1]), (202, "lane-1"))
        st, body, _ = self.req("GET", "/api/v1/admin/display-firmware", None, "admin")
        self.assertEqual((st, body["images"]), (200, []))


if __name__ == "__main__":
    unittest.main()
