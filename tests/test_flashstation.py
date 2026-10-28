"""The flash station (fetserver/flashstation.py): USB serial ports from sysfs, identification with esptool / avrdude
(recorded output, never run), and installing FeT Bridge with a backup, a full erase on first install, write and verify.
On hardware only: real programmers (PROG switch vs auto-reset), timings, esptool versions on Raspberry Pi OS."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from fetserver.firmware import FirmwareStore
from fetserver.flashstation import FlashError, FlashStation, list_ports, run_tool, why_esptool
from tests.test_match_http import MatchHttpBase

MAC = "84:f3:eb:12:34:56"
V4 = ("esptool.py v4.7.0\nSerial port /dev/ttyUSB0\nConnecting....\nDetecting chip type... ESP8266\nChip is ESP8266EX\n"
      f"Features: WiFi\nCrystal is 26MHz\nMAC: {MAC}\nUploading stub...\nManufacturer: 85\nDevice: 6014\nDetected flash size: %s\n")
V5 = ("esptool v5.0.0\nConnected to ESP8266 on /dev/ttyUSB0:\nChip type:          ESP8266EX\nFeatures:           WiFi\n"
      f"MAC:                {MAC}\nFlash Memory Information:\nDetected flash size: %s\n")
ESP32 = "Chip is ESP32-S3 (QFN56) (revision v0.2)\nMAC: 7c:df:a1:00:11:22\nDetected flash size: 8MB\n"


def usb_tree(root: Path, name: str, vid: str, pid: str, product="USB Serial"):
    """/sys/class/tty/<name>/device -> .../usb1/1-1/1-1:1.0/<name> with idVendor/idProduct two levels up."""
    usbdev = root / "devices" / "usb1" / f"1-{name[-1]}"
    iface = usbdev / f"1-{name[-1]}:1.0" / name
    iface.mkdir(parents=True)
    for k, v in (("idVendor", vid), ("idProduct", pid), ("manufacturer", "QinHeng"), ("product", product), ("serial", "")):
        (usbdev / k).write_text(v + "\n")
    tty = root / "class/tty" / name
    tty.mkdir(parents=True)
    os.symlink(iface, tty / "device")


class Tools:
    """esptool/avrdude played from a script: {first argv element after the tool name pattern: (code, output)}."""
    def __init__(self, size="1MB", fet=b"... fet-monitor-bridge/0.2.0 ...", chip_out=None, avr=None, mac_later=MAC):
        self.size, self.fet, self.chip_out, self.avr, self.mac_later = size, fet, chip_out, avr, mac_later
        self.calls, self.flash_ids = [], 0

    def __call__(self, argv, timeout, on_line):
        self.calls.append(argv)
        if argv[0] == "avrdude":
            return self.avr or (1, "avrdude: stk500v2_getsync(): timeout communicating with programmer")
        if argv[1:] == ["version"]:
            return 0, "4.7.0"
        if "flash_id" in argv:
            self.flash_ids += 1
            if self.chip_out is not None:
                return self.chip_out
            out = V4 % self.size
            return 0, out if self.flash_ids == 1 else out.replace(MAC, self.mac_later)
        if "read_flash" in argv:
            Path(argv[-1]).write_bytes(self.fet if argv[-2] == hex(0x80000) else b"\xff" * 16)
            if on_line:
                on_line("Read 4096 bytes at 0x00000000 (50 %)")
            return 0, "Read ok"
        if "write_flash" in argv:
            if on_line:
                for p in (10, 60, 100):
                    on_line(f"Writing at 0x000{p:02d}000... ({p} %)")
            return 0, "Hash of data verified."
        return 0, "ok"

    def subcmds(self):
        return [next(a for a in c if a in ("flash_id", "read_flash", "erase_flash", "write_flash", "verify_flash", "version")) for c in self.calls if c[0] != "avrdude"]


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sys = self.root / "sys"
        self.store = FirmwareStore(self.root)
        self.store.dir.mkdir(parents=True)

    def image(self, sha, version, size_mb, uploaded=1):
        meta = {"sha256": sha * 64, "size": 400000, "version": version, "flash_size": size_mb << 20, "flash_mode": "DOUT", "uploaded": uploaded}
        (self.store.dir / (sha * 64 + ".json")).write_text(json.dumps(meta))
        (self.store.dir / (sha * 64 + ".bin")).write_bytes(b"\xe9" + b"\0" * 10)
        return sha * 64

    def station(self, tools):
        self.tools = tools
        return FlashStation(self.store, self.root, run=tools, sys_tty=str(self.sys / "class/tty"), now=lambda: 1_800_000_000, background=False)


class Ports(Case):
    def test_usb_serial_ports(self):
        usb_tree(self.sys, "ttyUSB0", "1a86", "7523")
        usb_tree(self.sys, "ttyACM1", "2341", "0042", "Arduino Mega 2560")
        usb_tree(self.sys, "ttyACM2", "303a", "1001")
        (self.sys / "class/tty/ttyS0").mkdir(parents=True)          # not USB: ignored
        ports = {p["port"]: p for p in list_ports(str(self.sys / "class/tty"))}
        self.assertEqual(sorted(ports), ["/dev/ttyACM1", "/dev/ttyACM2", "/dev/ttyUSB0"])
        self.assertEqual((ports["/dev/ttyUSB0"]["hint"], ports["/dev/ttyUSB0"]["adapter"]), ("adapter", "CH340"))
        self.assertEqual((ports["/dev/ttyACM1"]["hint"], ports["/dev/ttyACM1"]["product"]), ("arduino", "Arduino Mega 2560"))
        self.assertEqual(ports["/dev/ttyACM2"]["hint"], "esp32")

    def test_reasons(self):
        self.assertIn("PROG", why_esptool("A fatal error occurred: Failed to connect to ESP8266: Timed out waiting for packet header"))
        self.assertIn("could not be opened", why_esptool("could not open port /dev/ttyUSB0: [Errno 13] Permission denied"))

    def test_run_tool_splits_progress_lines(self):
        seen = []
        code, out = run_tool(["sh", "-c", r"printf 'a (10 %%)\rb (20 %%)\nc\n'"], 10, seen.append)
        self.assertEqual((code, seen), (0, ["a (10 %)", "b (20 %)", "c"]))
        self.assertEqual(run_tool(["no-such-tool-here"], 5)[0], 127)
        self.assertEqual(run_tool(["sleep", "5"], 0.5)[0], 124)


class Identify(Case):
    def setUp(self):
        super().setUp()
        usb_tree(self.sys, "ttyUSB0", "1a86", "7523")

    def test_fet_bridge_with_a_newer_image(self):
        self.image("a", "0.2.0", 1, uploaded=5)                        # uploaded later, but older: not chosen
        new = self.image("b", "0.2.2", 1)
        self.image("c", "0.3.0", 4)                                    # the 4 MB build: not for this module
        r = self.station(Tools()).identify("/dev/ttyUSB0")
        self.assertEqual((r["device"], r["mac"], r["flash_size"], r["running"], r["first_install"], r["same"]),
                         ("esp8266", MAC, 1 << 20, "0.2.0", False, False))
        self.assertEqual(r["offer"]["sha256"], new)
        self.assertIn("FeT Bridge 0.2.0", r["label"])
        self.assertEqual(self.tools.subcmds(), ["version", "flash_id", "read_flash"], "read-only")

    def test_esptool_5_output(self):
        self.image("b", "0.2.2", 4)
        r = self.station(Tools(chip_out=(0, V5 % "4MB"))).identify("/dev/ttyUSB0")
        self.assertEqual((r["chip"], r["flash_size"], r["offer"]["version"]), ("ESP8266EX", 4 << 20, "0.2.2"))

    def test_original_firmware_means_first_install(self):
        self.image("b", "0.2.2", 1)
        r = self.station(Tools(fet=b"AT version:1.2.0.0 ...")).identify("/dev/ttyUSB0")
        self.assertEqual((r["running"], r["first_install"]), (None, True))
        self.assertIn("first install", r["label"])

    def test_nothing_to_offer(self):
        r = self.station(Tools()).identify("/dev/ttyUSB0")
        self.assertIsNone(r["offer"])
        self.assertIn("no 1 MB FeT Bridge image", r["why_not"])
        self.image("c", "0.2.2", 4)
        r = self.station(Tools(size="2MB")).identify("/dev/ttyUSB0")
        self.assertIn("built for 1 MB and 4 MB", r["why_not"])

    def test_esp32_identified_not_flashed(self):
        r = self.station(Tools(chip_out=(0, ESP32))).identify("/dev/ttyUSB0")
        self.assertEqual((r["device"], r["offer"], r["flash_size"]), ("esp32", None, 8 << 20))
        self.assertIn("freETarget's PC client", r["why_not"])
        self.assertNotIn("read_flash", self.tools.subcmds())

    def test_arduino_mega(self):
        avr = (0, "avrdude: AVR device initialized and ready to accept instructions\navrdude: device signature = 0x1e9801 (probably m2560)\n")
        r = self.station(Tools(chip_out=(2, "Failed to connect to Espressif device: Timed out waiting for packet header"), avr=avr)).identify("/dev/ttyUSB0")
        self.assertEqual((r["device"], r["offer"]), ("mega", None))
        avr_call = next(c for c in self.tools.calls if c[0] == "avrdude")
        self.assertIn("-n", avr_call, "read-only")

    def test_a_genuine_arduino_is_never_reset_by_esptool(self):
        usb_tree(self.sys, "ttyACM0", "2341", "0042", "Arduino Mega 2560")
        avr = (0, "avrdude: device signature = 0x1e9801 (probably m2560)\n")
        r = self.station(Tools(avr=avr)).identify("/dev/ttyACM0")
        self.assertEqual((r["device"], [c for c in self.tools.calls if "flash_id" in c]), ("mega", []))

    def test_another_avr_is_not_a_mega(self):
        avr = (0, "avrdude: device signature = 0x1e950f (probably m328p)\n")
        with self.assertRaisesRegex(FlashError, "PROG"):
            self.station(Tools(chip_out=(2, "Failed to connect to ESP8266: Timed out waiting for packet header"), avr=avr)).identify("/dev/ttyUSB0")

    def test_an_esp8285_is_not_offered_fet_bridge(self):
        self.image("b", "0.2.2", 1)
        r = self.station(Tools(chip_out=(0, "Chip is ESP8285H16\nMAC: 84:f3:eb:12:34:56\nDetected flash size: 1MB\n"))).identify("/dev/ttyUSB0")
        self.assertEqual((r["device"], r["offer"]), ("esp", None))

    def test_an_unplugged_device_drops_off_the_page(self):
        self.image("b", "0.2.2", 1)
        st = self.station(Tools())
        st.identify("/dev/ttyUSB0")
        self.assertIn("/dev/ttyUSB0", st.view()["probes"])
        import shutil
        shutil.rmtree(self.sys / "class/tty/ttyUSB0")
        self.assertEqual((st.view()["ports"], st.view()["probes"]), ([], {}))

    def test_nothing_answers(self):
        with self.assertRaisesRegex(FlashError, "PROG"):
            self.station(Tools(chip_out=(2, "Failed to connect to ESP8266: Timed out waiting for packet header"))).identify("/dev/ttyUSB0")
        with self.assertRaisesRegex(FlashError, "port is gone"):
            self.station(Tools()).identify("/dev/ttyUSB7")
        with self.assertRaisesRegex(FlashError, "not installed"):
            st = self.station(Tools())
            st.run = lambda argv, t, cb: (127, "not installed")
            st.identify("/dev/ttyUSB0")


class Flash(Case):
    def setUp(self):
        super().setUp()
        usb_tree(self.sys, "ttyUSB0", "1a86", "7523")

    def test_update_an_existing_bridge(self):
        sha = self.image("b", "0.2.2", 1)
        st = self.station(Tools())
        st.identify("/dev/ttyUSB0")
        job = st.start_flash("/dev/ttyUSB0", sha, "rc")
        self.assertEqual((job["state"], job["percent"], job.get("backup")), ("done", 100, None))
        self.assertEqual(self.tools.subcmds()[3:], ["flash_id", "write_flash", "verify_flash"], "same device checked; no erase, no backup")
        write = next(c for c in self.tools.calls if "write_flash" in c)
        self.assertEqual(write[write.index("--flash_mode") + 1], "keep")
        self.assertEqual(write[-2:], ["0x0", str(self.store.path(sha))])

    def test_first_install_saves_then_erases(self):
        sha = self.image("b", "0.2.2", 1)
        st = self.station(Tools(fet=b"AT firmware"))
        st.identify("/dev/ttyUSB0")
        job = st.start_flash("/dev/ttyUSB0", sha, "rc")
        self.assertEqual(self.tools.subcmds()[3:], ["flash_id", "read_flash", "erase_flash", "write_flash", "verify_flash"])
        backup = next(c for c in self.tools.calls if "read_flash" in c and c[-2] == hex(1 << 20))
        self.assertTrue(Path(backup[-1]).exists() and job["backup"].startswith("84f3eb123456-"))
        self.assertEqual(st.backup_path(job["backup"]), Path(backup[-1]))
        self.assertEqual(st.view()["backups"][0]["name"], job["backup"])

    def test_refused(self):
        sha = self.image("b", "0.2.2", 1)
        st = self.station(Tools())
        with self.assertRaisesRegex(FlashError, "identify the device"):
            st.start_flash("/dev/ttyUSB0", sha, "rc")
        st.identify("/dev/ttyUSB0")
        with self.assertRaisesRegex(FlashError, "identify the device"):
            st.start_flash("/dev/ttyUSB0", "c" * 64, "rc")
        st2 = self.station(Tools(mac_later="aa:bb:cc:dd:ee:ff"))
        st2.identify("/dev/ttyUSB0")
        job = st2.start_flash("/dev/ttyUSB0", sha, "rc")
        self.assertEqual(job["state"], "failed")
        self.assertIn("different device", job["message"])
        self.assertNotIn("write_flash", self.tools.subcmds(), "nothing written to another device")
        (self.root / "secret.bin").write_bytes(b"x")
        (self.root / "flash-backups").mkdir(exist_ok=True)
        self.assertIsNone(st.backup_path("../secret.bin"), "only names of our backups, inside their folder")

    def test_a_failed_write_is_reported(self):
        sha = self.image("b", "0.2.2", 1)
        t = Tools()
        st = self.station(t)
        st.identify("/dev/ttyUSB0")
        real = t.__call__
        st.run = lambda argv, timeout, cb: (2, "A fatal error occurred: Packet content transfer stopped") if "write_flash" in argv else real(argv, timeout, cb)
        job = st.start_flash("/dev/ttyUSB0", sha, "rc")
        self.assertEqual(job["state"], "failed")
        self.assertIn("Packet content transfer stopped", job["message"])

    def test_progress_while_writing(self):
        sha = self.image("b", "0.2.2", 1)
        t = Tools()
        st = self.station(t)
        st.identify("/dev/ttyUSB0")
        seen = []

        def run(argv, timeout, cb):
            out = t(argv, timeout, (lambda line: (cb(line), seen.append(st.job["percent"]))) if cb else None)
            return out
        st.run = run
        st.start_flash("/dev/ttyUSB0", sha, "rc")
        self.assertEqual(seen, [10, 60, 100], "the page sees the write progress")

    def test_one_at_a_time(self):
        sha = self.image("b", "0.2.2", 1)
        st = self.station(Tools())
        st.identify("/dev/ttyUSB0")
        st.job = {"state": "running"}
        with self.assertRaisesRegex(FlashError, "busy"):
            st.start_flash("/dev/ttyUSB0", sha, "rc")


class OverHttp(MatchHttpBase):
    def setUp(self):
        super().setUp()
        self.sysroot = self.dir / "sys"
        usb_tree(self.sysroot, "ttyUSB0", "1a86", "7523")
        store = FirmwareStore(self.dir)
        store.dir.mkdir(parents=True)
        self.server.flash = FlashStation(store, self.dir, run=Tools(), sys_tty=str(self.sysroot / "class/tty"), background=False)

    def test_range_control_only(self):
        self.assertEqual(self.req("GET", "/api/v1/admin/flash")[0], 401)
        self.assertEqual(self.req("POST", "/api/v1/admin/flash/identify", {"port": "/dev/ttyUSB0"})[0], 401)
        st, _, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["admin"] = cookie.split(";")[0]
        st, body, _ = self.req("GET", "/api/v1/admin/flash", None, "admin")
        self.assertEqual((st, body["ports"][0]["port"]), (200, "/dev/ttyUSB0"))
        st, body, _ = self.req("POST", "/api/v1/admin/flash/identify", {"port": "/dev/ttyUSB0"}, "admin")
        self.assertEqual((st, body["device"], body["offer"]), (200, "esp8266", None))
        st, body, _ = self.req("POST", "/api/v1/admin/flash/start", {"port": "/dev/ttyUSB0", "sha256": "a" * 64}, "admin")
        self.assertEqual((st, "identify" in body["error"]), (400, True))
        self.assertEqual(self.req("GET", "/api/v1/admin/flash-backups/84f3eb123456-20261028-120000.bin", None, "admin")[0], 404)


if __name__ == "__main__":
    unittest.main()
