"""The flash station (fetserver/flashstation.py): USB serial ports from sysfs, identification with esptool / avrdude
(recorded output, never run), and installing FeT Bridge with a backup, a full erase on first install, write and verify.
On hardware only: real programmers (PROG switch vs auto-reset), timings, esptool versions on Raspberry Pi OS."""
import binascii
import binascii
import hashlib
import json
import os
import pty
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fetserver.firmware import FirmwareStore
from fetserver.flashstation import FlashError, FlashStation, list_ports, run_tool, settings_line, why_esptool
from fetserver.seriallink import SerialLink
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


def no_serial(port):
    raise OSError("no serial port in tests")


class FakeBridge:
    """The other end of the serial port: FeT Bridge as firmware src/core/provision.cpp answers, or an older one that only
    prints its start-up line, or a Mega. gone: that many opens fail first (unplugged); drop: the first write fails."""
    def __init__(self, dev="fet-123456", fw="0.3.0", answers=True, old=None, errors=(), gone=0, drop=False, mega=None):
        self.dev, self.fw, self.answers, self.old, self.errors = dev, fw, answers, old, list(errors)
        self.gone, self.drop, self.mega = gone, drop, mega
        self.out, self.written, self.settings = [], [], None
        self.opens = self.restarts = self.closes = self.settings_lines = 0

    def open(self, port):
        self.opens += 1
        if self.gone:
            self.gone -= 1
            raise OSError("No such file or directory")
        self.port = port
        if self.mega and self.mega[0] == "banner":
            self.out.append(self.mega[1])
        return self

    def restart_esp(self):
        self.restarts += 1
        return True

    def write(self, data):
        if self.drop:
            self.drop = False
            raise OSError("Input/output error")
        self.written.append(data)
        line = data.decode().rstrip("\n")
        if self.mega:
            if self.mega[0] == "asked" and line == '{"VERSION":0}':
                self.out.append(self.mega[1])
            return
        if self.old:
            self.out.append(f"FET bridge firmware {self.old} (fet-123456)")
        elif not self.answers:
            return
        elif line == "FETCFG HELLO":
            self.out.append(f"FETCFG HELLO {self.dev} {self.fw}")
        elif line.startswith("FETCFG "):
            self.settings_lines += 1
            hexed, crc = line[7:].split(" ")
            body = bytes.fromhex(hexed)
            assert binascii.crc32(body) == int(crc, 16)
            if self.errors:
                self.out.append("FETCFG ERROR " + self.errors.pop(0))
            else:
                self.settings = json.loads(body)
                self.out.append(f"FETCFG OK {self.dev}")

    def readline(self, timeout):
        if self.out:
            return self.out.pop(0)
        time.sleep(min(timeout, 0.005))
        return None

    def close(self):
        self.closes += 1


SETTINGS = {"ssid": "club", "pass": "password1", "host": "192.168.1.20", "port": 8080, "token": "the-token", "name": "Lane-3"}


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

    def station(self, tools, link=None):
        self.tools, self.link = tools, link
        st = FlashStation(self.store, self.root, run=tools, sys_tty=str(self.sys / "class/tty"), now=lambda: 1_800_000_000, background=False,
                          opener=link.open if link else no_serial)
        st.hello_wait, st.hello_every, st.reply_wait, st.mega_wait = 0.6, 0.05, 0.2, 0.3
        return st


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

    def test_one_mb_modules_hear_about_four_mb_and_new_bridges_take_settings(self):
        self.image("b", "0.3.0", 1)
        self.image("c", "0.3.0", 4)
        r = self.station(Tools(fet=b"fet-monitor-bridge/0.3.0")).identify("/dev/ttyUSB0")
        self.assertIn("4 MB ESP-01S", r["advice"])
        self.assertTrue(r["configurable"])
        r = self.station(Tools(size="4MB")).identify("/dev/ttyUSB0")
        self.assertEqual(r["advice"], "")
        self.assertFalse(r["configurable"], "0.2.0 cannot take settings over USB")

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
        self.assertIn("no freETarget start-up message", r["label"])

    def test_mega_freetarget_version(self):
        avr = (0, "avrdude: device signature = 0x1e9801 (probably m2560)\n")
        tools = Tools(chip_out=(2, "Timed out waiting for packet header"), avr=avr)
        r = self.station(tools, FakeBridge(mega=("banner", 'freETarget "4.3.0 December 21, 2023"'))).identify("/dev/ttyUSB0")
        self.assertEqual((r["device"], r["freetarget"]), ("mega", "4.3.0 December 21, 2023"))
        self.assertIn("freETarget 4.3.0", r["label"])
        self.assertEqual(self.link.written, [], "the start-up message was enough: nothing sent")
        r = self.station(tools, FakeBridge(mega=("asked", '{"VERSION": "4.3.1 March 2, 2024"}'))).identify("/dev/ttyUSB0")
        self.assertEqual(r["freetarget"], "4.3.1 March 2, 2024")
        self.assertEqual(self.link.written, [b'{"VERSION":0}\n', b"FT1 I\n"], "asked both ways")
        r = self.station(tools, FakeBridge(mega=("silent", ""))).identify("/dev/ttyUSB0")
        self.assertIsNone(r["freetarget"])
        self.assertEqual(self.link.closes, 1)

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


class Configure(Case):
    def setUp(self):
        super().setUp()
        usb_tree(self.sys, "ttyUSB0", "1a86", "7523")

    def configure(self, link, settings=SETTINGS, port="/dev/ttyUSB0"):
        return self.station(Tools(), link).start_configure(port, settings, "rc")

    def test_settings_line(self):
        line = settings_line({"ssid": "a b"})
        hexed, crc = line.decode().split()[1:]
        self.assertEqual(json.loads(bytes.fromhex(hexed)), {"ssid": "a b"})
        self.assertEqual(int(crc, 16), binascii.crc32(bytes.fromhex(hexed)))
        self.assertNotIn(b"{", line)
        self.assertTrue(line.endswith(b"\n") and line.count(b"\n") == 1)

    def test_configured(self):
        link = FakeBridge()
        job = self.configure(link, dict(SETTINGS, ip="", gw="", extra="dropped"))
        self.assertEqual((job["state"], job["device_id"], job["configured"]), ("done", "fet-123456", True))
        self.assertEqual(link.settings, SETTINGS, "only the keys the bridge knows; empty optional ones left out")
        self.assertEqual(link.restarts, 1, "an auto-reset programmer restarts the ESP once")
        self.assertNotIn("password1", json.dumps(job), "the WiFi password is never in the job (the page shows it)")
        self.assertEqual(link.closes, 1)

    def test_fixed_ip_and_port_as_text(self):
        link = FakeBridge()
        self.configure(link, dict(SETTINGS, port="8080", ip="192.168.1.60", gw="192.168.1.1"))
        self.assertEqual((link.settings["port"], link.settings["ip"], link.settings["gw"]), (8080, "192.168.1.60", "192.168.1.1"))

    def test_refused_before_anything_is_sent(self):
        for bad, why in ((dict(SETTINGS, ssid=""), "WiFi name"), (dict(SETTINGS, name=" "), "target name"),
                         ({k: v for k, v in SETTINGS.items() if k != "token"}, "token"), (dict(SETTINGS, port="x"), "number"),
                         (dict(SETTINGS, ssid=5), "ssid must be text"), (None, "no settings")):
            link = FakeBridge()
            with self.assertRaisesRegex(FlashError, why):
                self.configure(link, bad)
            self.assertEqual(link.opens, 0)
        with self.assertRaisesRegex(FlashError, "not a USB serial port"):
            self.configure(FakeBridge(), port="/etc/passwd")

    def test_the_bridge_says_what_is_wrong(self):
        job = self.configure(FakeBridge(errors=["WiFi password must be 8 to 63 characters"]))
        self.assertEqual(job["state"], "failed")
        self.assertIn("refused the settings: WiFi password must be 8 to 63 characters", job["message"])

    def test_a_damaged_line_is_sent_again(self):
        link = FakeBridge(errors=["checksum mismatch: send it again"])
        self.assertEqual(self.configure(link)["state"], "done")
        self.assertEqual(link.settings_lines, 2)
        link = FakeBridge(errors=["checksum mismatch: send it again"] * 3)
        job = self.configure(link)
        self.assertEqual((job["state"], link.settings_lines), ("failed", 3))
        self.assertIn("USB cable", job["message"])

    def test_no_answer_explains_both_programmer_kinds(self):
        job = self.configure(FakeBridge(answers=False))
        self.assertEqual(job["state"], "failed")
        self.assertIn("PROG/UART switch", job["message"])
        self.assertIn("auto-reset", job["message"])

    def test_an_older_bridge_is_named(self):
        link = FakeBridge(old="0.2.2")
        job = self.configure(link)
        self.assertIn("(0.2.2) is too old", job["message"])
        self.assertEqual(link.settings_lines, 0, "the password never goes to a bridge that would log it as a line")
        self.assertFalse(any(not w.startswith(b"FETCFG HELLO") for w in link.written))

    def test_plugged_in_again_while_waiting(self):
        link = FakeBridge(gone=4, drop=True)                 # PROG/UART switch: unplugged, back, the port drops once
        job = self.configure(link)
        self.assertEqual(job["state"], "done", job.get("message"))
        self.assertEqual(link.opens, 6)
        self.assertEqual(link.restarts, 1)
        self.assertEqual(link.closes, 2)

    def test_one_job_at_a_time(self):
        st = self.station(Tools(), FakeBridge())
        st.job = {"state": "running"}
        with self.assertRaisesRegex(FlashError, "busy"):
            st.start_configure("/dev/ttyUSB0", SETTINGS, "rc")

    def test_install_and_configure(self):
        sha = self.image("b", "0.3.0", 1)
        link = FakeBridge()
        st = self.station(Tools(), link)
        st.identify("/dev/ttyUSB0")
        job = st.start_flash("/dev/ttyUSB0", sha, "rc", SETTINGS)
        self.assertEqual((job["state"], job["configured"], link.settings), ("done", True, SETTINGS))
        self.assertIn("installed and verified and configured (fet-123456)", job["message"])
        self.assertEqual(self.tools.subcmds()[-1], "verify_flash", "settings only after the write is verified")
        pr = st.view()["probes"]["/dev/ttyUSB0"]
        self.assertEqual((pr["running"], pr["configurable"], pr["first_install"]), ("0.3.0", True, False))
        self.assertTrue(pr["label"].endswith("FeT Bridge 0.3.0"), pr["label"])

    def test_installed_but_not_configured(self):
        sha = self.image("b", "0.3.0", 1)
        st = self.station(Tools(), FakeBridge(answers=False))
        st.identify("/dev/ttyUSB0")
        job = st.start_flash("/dev/ttyUSB0", sha, "rc", SETTINGS)
        self.assertEqual((job["state"], job["configured"]), ("done", False))
        self.assertIn("installed and verified, not configured yet", job["message"])
        self.assertIn("PROG/UART switch", job["message"])

    def test_an_older_image_takes_no_settings(self):
        sha = self.image("b", "0.2.2", 1)
        st = self.station(Tools(), FakeBridge())
        st.identify("/dev/ttyUSB0")
        with self.assertRaisesRegex(FlashError, "cannot take settings"):
            st.start_flash("/dev/ttyUSB0", sha, "rc", SETTINGS)
        self.assertNotIn("write_flash", self.tools.subcmds())


class SerialOverPty(unittest.TestCase):
    def test_lines_both_ways(self):
        master, slave = pty.openpty()
        name = os.ttyname(slave)
        link = SerialLink(name)
        self.addCleanup(link.close)
        link.write(b"FETCFG HELLO\n")
        got = b""
        while not got.endswith(b"\n"):
            got += os.read(master, 64)
        self.assertEqual(got, b"FETCFG HELLO\n", "raw: no \\r added, nothing echoed")
        os.write(master, b"noise\r\nFETCFG HELLO fet-1 0.3.0\r\npart")
        self.assertEqual(link.readline(1), "noise")
        self.assertEqual(link.readline(1), "FETCFG HELLO fet-1 0.3.0")
        self.assertIsNone(link.readline(0.1), "an unfinished line waits")
        os.write(master, b"ial\n")
        self.assertEqual(link.readline(1), "partial")
        link.restart_esp()                                   # a pty has no modem lines: no error either way
        os.close(master)
        os.close(slave)
        with self.assertRaises(OSError):
            link.readline(1)


SIG_OK = (0, "avrdude: device signature = 0x1e9801 (probably m2560)\n")


class FakeAvrdude:
    """avrdude played from a script: signature checks, reads that create the backup files, writes recorded."""
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on
        self.sig = SIG_OK

    def __call__(self, argv, timeout, on_line):
        self.calls.append(argv)
        if argv[0] != "avrdude":
            return 2, "Failed to connect to ESP32: Timed out waiting for packet header"   # esptool: not an ESP
        if self.fail_on and any(self.fail_on in a for a in argv):
            return 1, "avrdude: verification error, first mismatch at byte 0x0000"
        for a in argv:
            if a.startswith(("flash:r:", "eeprom:r:")):
                Path(a.split(":")[2]).write_text(":00000001FF\n")
        return self.sig if "-n" in argv else (0, "avrdude: 24748 bytes of flash verified")

    def ops(self):
        return [" ".join(a for a in c if a.startswith(("flash:", "eeprom:")) or a in ("-n", "-D")).replace(str(Path.home()), "~")
                for c in self.calls if c[0] == "avrdude"]


class FakeMega:
    """A Mega on the end of the USB cable. runs: "fet" (FeT target firmware: hello on open, answers FT1 K/I) or
    "freetarget" (prints its banner) or "silent"."""
    def __init__(self, runs="fet", key_fp=None, refuse=None, wrong_fp=False):
        self.runs, self.key_fp, self.refuse, self.wrong_fp = runs, key_fp, refuse, wrong_fp
        self.out, self.written, self.keys = [], [], []
        self.opens = self.closes = 0

    def open(self, port):
        self.opens += 1
        self.out = []
        if self.runs == "fet":
            self.out.append("FT1 H b=4 fw=0.1.0 rev=310 k=1234abcd key=%s h=-" % (self.key_fp or "none"))
        elif self.runs == "freetarget":
            self.out.append('freETarget "4.3.1 March 2, 2024"')
        return self

    def write(self, data):
        self.written.append(data)
        line = data.decode().rstrip("\n")
        if self.runs == "fet" and line.startswith("FT1 K "):
            hexkey, crc = line[6:].split(" ")
            key = bytes.fromhex(hexkey)
            assert int(crc, 16) == binascii.crc32(key)
            if self.refuse:
                self.out.append("FT1 K err " + self.refuse)
                return
            self.keys.append(key)
            fp = hashlib.sha256(key).hexdigest()[:8]
            self.key_fp = "00000000" if self.wrong_fp else fp
            self.out.append("FT1 K ok fp=" + self.key_fp)

    def readline(self, timeout):
        if self.out:
            return self.out.pop(0)
        time.sleep(min(timeout, 0.005))
        return None

    def close(self):
        self.closes += 1


class Mega(Case):
    def setUp(self):
        super().setUp()
        usb_tree(self.sys, "ttyACM0", "2341", "0042", "Arduino Mega 2560")
        (self.sys / "devices/usb1/1-0/serial").write_text("75830303934351C0B1A1\n")
        from fetserver.targetfw import TargetFirmwareStore
        self.tstore = TargetFirmwareStore(self.root)
        self.registered = []

    def mega_image(self, version="0.1.0"):
        import hashlib as h
        prog = b"\x0c\x94" * 64 + b"fet-target-mega/" + version.encode() + b"\x00"
        lines = []
        for off in range(0, len(prog), 16):
            chunk = prog[off:off + 16]
            rec = bytes([len(chunk), off >> 8, off & 255, 0]) + chunk
            lines.append(":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper())
        lines.append(":00000001FF")
        meta = self.tstore.add(("\n".join(lines) + "\n").encode(), "test")
        return meta["sha256"]

    def station(self, avr, mega):
        def register(key, note):
            self.registered.append((key, note))
            return hashlib.sha256(key).hexdigest()[:8]
        self.avr, self.mega = avr, mega
        st = FlashStation(self.store, self.root, run=avr, sys_tty=str(self.sys / "class/tty"), now=lambda: 1_800_000_000,
                          background=False, opener=mega.open, target_images=self.tstore, register_key=register)
        st.hello_wait, st.hello_every, st.reply_wait, st.mega_wait = 0.6, 0.05, 0.3, 0.3
        return st

    def test_identify_upstream_and_offer(self):
        sha = self.mega_image()
        r = self.station(FakeAvrdude(), FakeMega("freetarget")).identify("/dev/ttyACM0")
        self.assertEqual((r["device"], r["freetarget"], r["fet_target"], r["first_install"]), ("mega", "4.3.1 March 2, 2024", None, True))
        self.assertEqual(r["offer"]["sha256"], sha)
        self.assertEqual(r["serial"], "75830303934351C0B1A1")

    def test_identify_ours(self):
        self.mega_image()
        r = self.station(FakeAvrdude(), FakeMega("fet", key_fp="ab12cd34")).identify("/dev/ttyACM0")
        self.assertEqual((r["fet_target"], r["key_fp"], r["first_install"], r["same"], r["can_key"]), ("0.1.0", "ab12cd34", False, True, True))
        self.assertIn("FeT target 0.1.0 (key ab12cd34)", r["label"])

    def test_no_image_yet(self):
        r = self.station(FakeAvrdude(), FakeMega("freetarget")).identify("/dev/ttyACM0")
        self.assertIsNone(r["offer"])
        self.assertIn("upload its .hex", r["why_not"])

    def test_first_install_saves_writes_and_keys(self):
        sha = self.mega_image()
        mega = FakeMega("freetarget")
        st = self.station(FakeAvrdude(), mega)
        st.identify("/dev/ttyACM0")
        mega.runs = "fet"                                                  # what avrdude's write turns it into
        job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
        self.assertEqual(job["state"], "done", job.get("message"))
        ops = self.avr.ops()
        self.assertEqual(ops[-3][:2], "-n", "same board checked")
        self.assertIn("flash:r:", ops[-2])
        self.assertIn("eeprom:r:", ops[-2], "the EEPROM (its calibration) is saved too")
        self.assertTrue(ops[-1].startswith("-D flash:w:") and ops[-1].endswith(".hex:i"), ops[-1])
        name = job["backup"]
        self.assertTrue(name.startswith("mega-75830303934351C0B1A1-"))
        self.assertIsNotNone(st.backup_path(name + ".hex"))
        self.assertIsNotNone(st.backup_path(name + ".eep"))
        self.assertEqual(len(self.registered), 1)
        key = self.registered[0][0]
        self.assertEqual(mega.keys, [key], "the server knew the key before the board got it")
        self.assertIn(hashlib.sha256(key).hexdigest()[:8], job["message"])
        self.assertEqual(st.view()["probes"]["/dev/ttyACM0"]["fet_target"], "0.1.0")

    def test_an_update_keeps_no_backup(self):
        sha = self.mega_image("0.2.0")
        st = self.station(FakeAvrdude(), FakeMega("fet", key_fp="ab12cd34"))
        st.identify("/dev/ttyACM0")
        job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
        self.assertEqual(job["state"], "done", job.get("message"))
        self.assertNotIn("flash:r", " ".join(self.avr.ops()))
        self.assertNotIn("backup", job)

    def test_failures(self):
        sha = self.mega_image()
        st = self.station(FakeAvrdude(fail_on="flash:w"), FakeMega("freetarget"))
        st.identify("/dev/ttyACM0")
        job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
        self.assertEqual(job["state"], "failed")
        self.assertIn("verification error", job["message"])
        self.assertEqual(self.registered, [], "no key made for a board that was not written")
        st = self.station(FakeAvrdude(), FakeMega("freetarget"))
        st.identify("/dev/ttyACM0")
        (self.sys / "devices/usb1/1-0/serial").write_text("OTHERBOARD\n")
        job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
        self.assertIn("different board", job["message"])
        (self.sys / "devices/usb1/1-0/serial").write_text("75830303934351C0B1A1\n")
        self.avr.sig = (1, "avrdude: stk500v2_getsync(): timeout communicating with programmer")
        job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
        self.assertIn("does not answer", job["message"])
        self.avr.sig = (0, "avrdude: device signature = 0x1e950f (probably m328p)")   # another chip on the port
        job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
        self.assertIn("does not answer", job["message"])
        self.assertNotIn("flash:w", " ".join(self.avr.ops()), "nothing written to another board")

    def test_key_problems(self):
        sha = self.mega_image()
        for mega, why in ((FakeMega("fet", refuse="weak_key"), "refused the key: weak_key"),
                          (FakeMega("fet", wrong_fp=True), "different key"),
                          (FakeMega("silent"), "did not start FeT target firmware")):
            st = self.station(FakeAvrdude(), mega)
            mega_runs, mega.runs = mega.runs, "fet" if mega.runs != "silent" else "silent"
            mega.out = []
            st.probes["/dev/ttyACM0"] = {"device": "mega", "serial": "75830303934351C0B1A1", "first_install": False,
                                         "offer": {"sha256": sha}, "fet_target": "0.1.0"}
            job = st.start_mega_flash("/dev/ttyACM0", sha, "rc")
            self.assertEqual(job["state"], "failed")
            self.assertIn(why, job["message"])

    def test_new_key_only(self):
        st = self.station(FakeAvrdude(), FakeMega("freetarget"))
        st.identify("/dev/ttyACM0")
        with self.assertRaisesRegex(FlashError, "FeT target firmware"):
            st.start_mega_key("/dev/ttyACM0", "rc")                        # upstream firmware has no key to set
        st = self.station(FakeAvrdude(), FakeMega("fet", key_fp="ab12cd34"))
        with self.assertRaisesRegex(FlashError, "identify"):
            st.start_mega_key("/dev/ttyACM0", "rc")
        st.identify("/dev/ttyACM0")
        job = st.start_mega_key("/dev/ttyACM0", "rc")
        self.assertEqual(job["state"], "done", job.get("message"))
        self.assertEqual(len(self.mega.keys), 1)
        self.assertEqual([c for c in self.avr.ops() if "flash:w" in c], [], "no avrdude write for a key")

    def test_restore(self):
        sha = self.mega_image()
        st = self.station(FakeAvrdude(), FakeMega("freetarget"))
        st.identify("/dev/ttyACM0")
        self.mega.runs = "fet"
        name = st.start_mega_flash("/dev/ttyACM0", sha, "rc")["backup"]
        st.identify("/dev/ttyACM0")
        job = st.start_mega_restore("/dev/ttyACM0", name, "rc")
        self.assertEqual(job["state"], "done", job.get("message"))
        last = self.avr.ops()[-1]
        self.assertIn("flash:w:", last)
        self.assertIn(name + ".hex:i", last)
        self.assertIn("eeprom:w:", last)
        self.assertIn(name + ".eep:i", last)
        with self.assertRaisesRegex(FlashError, "no such saved"):
            st.start_mega_restore("/dev/ttyACM0", "../../etc/passwd", "rc")
        st.backup_path(name + ".eep").unlink()
        with self.assertRaisesRegex(FlashError, "no such saved"):
            st.start_mega_restore("/dev/ttyACM0", name, "rc")             # never the program without its settings


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

    def test_configure_uses_this_servers_port_and_token(self):
        link = FakeBridge()
        st = self.server.flash
        st.opener, st.hello_every, st.reply_wait = link.open, 0.05, 0.2
        st, _, cookie = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.cookies["admin"] = cookie.split(";")[0]
        st, body, _ = self.req("GET", "/api/v1/admin/flash", None, "admin")
        self.assertEqual(body["server_host"], "127.0.0.1")
        page = {"ssid": "club", "pass": "password1", "host": "192.168.1.20", "name": "Lane-3", "token": "guessed", "port": 1}
        st, body, _ = self.req("POST", "/api/v1/admin/flash/configure", {"port": "/dev/ttyUSB0", "settings": page}, "admin")
        self.assertEqual((st, body["state"]), (202, "done"))
        self.assertEqual((link.settings["token"], link.settings["port"]), (self.server.token, self.server.server_address[1]))
        st, body, _ = self.req("POST", "/api/v1/admin/flash/configure", {"port": "/dev/ttyUSB0"}, "admin")
        self.assertEqual((st, body["error"]), (400, "no settings"))


if __name__ == "__main__":
    unittest.main()


class MegaHex(unittest.TestCase):
    """fetserver/targetfw.py: only well-formed program files for the Mega, with our identification, ever stored."""
    def rec(self, addr, data, rtype=0):
        r = bytes([len(data), addr >> 8 & 255, addr & 255, rtype]) + data
        return ":" + (r + bytes([(-sum(r)) & 0xFF])).hex().upper()

    def hexfile(self, prog=b"fet-target-mega/0.1.0", base=0, extra=(), end=True):
        lines = list(extra)
        for off in range(0, len(prog), 16):
            lines.append(self.rec(base + off, prog[off:off + 16]))
        if end:
            lines.append(":00000001FF")
        return ("\n".join(lines) + "\n").encode()

    def test_good(self):
        from fetserver.targetfw import inspect
        m = inspect(self.hexfile())
        self.assertEqual((m["version"], m["board"], m["size"]), ("0.1.0", "mega2560", 21))
        hi = self.hexfile(extra=[self.rec(0, b"\x00\x03", 4)])                    # extended linear address 0x30000
        self.assertEqual(inspect(hi)["size"], 0x30000 + 21)
        seg = self.hexfile(extra=[self.rec(0, b"\x10\x00", 2)])                   # extended segment 0x1000 -> 0x10000
        self.assertEqual(inspect(seg)["size"], 0x10000 + 21)

    def test_refused(self):
        from fetserver.targetfw import BadImage, inspect
        good = self.hexfile().decode()
        bad = [
            (good.replace(":10000000", ":10000001", 1), "checksum"),
            (good.replace(":", "", 1), "not an Intel HEX"),
            (self.hexfile(end=False).decode(), "end record"),
            (good + ":00000001FF\n", "after the end"),
            (self.hexfile(extra=[self.rec(0, b"\x00\x03", 4), self.rec(0xE000, b"\x01")]).decode(), "boot loader"),
            (self.hexfile(prog=b"freETarget 4.3.1").decode(), "not FeT target firmware"),
            (self.hexfile(prog=b"fet-target-mega/0.1.0 fet-target-mega/0.2.0").decode(), "more than one"),
            (":00000001FF\n", "no program"),
            (good.replace(":10", ":11", 1), "wrong length"),
            (self.hexfile(extra=[self.rec(0, b"\x00", 6)]).decode(), "record type"),
            (":0000000GFF\n", "not an Intel HEX"),
        ]
        for text, why in bad:
            with self.assertRaisesRegex(BadImage, why, msg=why):
                inspect(text.encode())
        with self.assertRaisesRegex(BadImage, "too large"):
            inspect(b":" * (1024 * 1024 + 1))

    def test_store(self):
        from fetserver.targetfw import TargetFirmwareStore
        with tempfile.TemporaryDirectory() as d:
            st = TargetFirmwareStore(Path(d))
            a = st.add(self.hexfile(b"fet-target-mega/0.1.0"))
            b = st.add(self.hexfile(b"fet-target-mega/0.10.0"))
            self.assertEqual(st.best()["sha256"], b["sha256"], "0.10.0 is newer than 0.1.0")
            self.assertTrue(st.path(a["sha256"]).read_bytes().startswith(b":"))
            self.assertIsNone(st.get("../x"))
            self.assertTrue(st.delete(a["sha256"]))
            self.assertEqual([m["version"] for m in st.list()], ["0.10.0"])
