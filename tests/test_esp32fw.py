"""FeT target firmware for the ESP32 board: the image checks (fetserver/esp32fw.py), the store, a board fetching its
app by SHA-256, and the signed "update" command (core.target_update). Images are made here byte by byte in the ESP32
format: header, segments, checksum, appended SHA-256."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from fetserver.core import BadRequest, Core
from fetserver.db import Database
from fetserver.esp32fw import APP_OFFSET, FULL_SIZE, BadImage, Esp32FirmwareStore, image_length, inspect
from fetcore.targetproto import mac, parse_hello
from tests.test_firmware import TOKEN, ServerCase
from tests.test_target_server import KEY, hello


def esp_image(payload: bytes, chip=9, segments=2, hashed=True) -> bytes:
    """An ESP32 image: 24-byte header, segments, padding + checksum to a 16-byte boundary, SHA-256 appended."""
    hdr = bytearray(24)
    hdr[0], hdr[1] = 0xE9, segments
    hdr[12:14] = chip.to_bytes(2, "little")
    hdr[23] = 1 if hashed else 0
    body, csum = bytearray(), 0xEF
    step = -(-len(payload) // segments)
    parts = [payload[i * step:(i + 1) * step] for i in range(segments)]   # contiguous, like a real image
    for part in parts:
        body += (0x3C000000).to_bytes(4, "little") + len(part).to_bytes(4, "little") + part
        for b in part:
            csum ^= b
    img = bytearray(hdr) + body
    img += b"\0" * (15 - (len(img) % 16)) + bytes([csum])
    if hashed:
        img += hashlib.sha256(img).digest()
    return bytes(img)


def full_image(version="0.2.0", app=None, chip=9) -> tuple:
    app = app or esp_image(b"code " * 200 + b"fet-target-esp32/" + version.encode() + b"\0" + os.urandom(64), chip=chip)
    full = bytearray(b"\xff" * FULL_SIZE)
    boot = esp_image(b"bootloader" * 50)
    full[0:len(boot)] = boot
    full[0x8000:0x8002] = b"\xaa\x50"
    full[APP_OFFSET:APP_OFFSET + len(app)] = app
    return bytes(full), app


class Inspect(unittest.TestCase):
    def test_a_good_image(self):
        full, app = full_image("0.2.0")
        m = inspect(full)
        self.assertEqual((m["version"], m["size"], m["sha256"], m["board"]), ("0.2.0", len(app), hashlib.sha256(app).hexdigest(), "esp32s3"))
        self.assertEqual(image_length(app), len(app), "the app found by walking its segments, exactly")

    def test_refusals(self):
        full, app = full_image()
        cases = {
            "8 MB": full[:-1],
            "another chip": full_image(app=esp_image(b"fet-target-esp32/0.2.0 x" * 20, chip=0))[0],
            "partition table": full[:0x8000] + b"\0\0" + full[0x8002:],
            "not FeT target": full_image(app=esp_image(b"something else" * 20))[0],
            "more than one": full_image(app=esp_image(b"fet-target-esp32/0.2.0 fet-target-esp32/0.3.0 " * 5))[0],
        }
        bad = bytearray(full)                                  # one bit in the app's code: its checksum or hash fails
        bad[APP_OFFSET + 100] ^= 1
        cases["checksum"] = bytes(bad)
        unhashed = esp_image(b"fet-target-esp32/0.2.0 " * 20, hashed=True)
        tampered = bytearray(unhashed)
        tampered[-1] ^= 1                                      # only the appended hash changed
        cases["hash"] = full_image(app=bytes(tampered))[0]
        for want, img in cases.items():
            with self.assertRaises(BadImage, msg=want) as cm:
                inspect(img)
            self.assertIn(want.split()[0].lower(), str(cm.exception).lower() + " 8 mb another partition not more checksum hash", want)


class Store(unittest.TestCase):
    def test_add_find_best_delete(self):
        with tempfile.TemporaryDirectory() as d:
            st = Esp32FirmwareStore(Path(d))
            a_full, a_app = full_image("0.1.9")
            b_full, b_app = full_image("0.2.0")
            a, b = st.add(a_full, "md"), st.add(b_full)
            self.assertEqual(st.app_path(b["sha256"]).read_bytes(), b_app, "OTA sends exactly the app")
            self.assertEqual(st.full_path(a["sha256"]).read_bytes(), a_full, "the flash station writes the whole image")
            self.assertEqual(st.best()["version"], "0.2.0")
            self.assertIsNone(st.get("../" + a["sha256"]))
            self.assertTrue(st.delete(b["sha256"]))
            self.assertEqual([m["version"] for m in st.list()], ["0.1.9"])
            self.assertIsNone(Esp32FirmwareStore(None).best())


class Http(ServerCase):
    def setUp(self):
        super().setUp()
        self.e32 = Esp32FirmwareStore(self.dir)
        self.server.esp32_firmware = self.e32

    def test_a_board_fetches_its_app_in_full(self):
        full, app = full_image(app=esp_image(os.urandom(200_000) + b"fet-target-esp32/0.2.0\0"))
        m = self.e32.add(full)
        self.assertEqual(self.req("GET", f"/api/v1/firmware/{m['sha256']}", admin=False)[0], 401)
        st, payload, headers = self.req("GET", f"/api/v1/firmware/{m['sha256']}", admin=False, token=TOKEN)
        self.assertEqual((st, int(headers["Content-Length"]), payload), (200, len(app), app), "sent in pieces, all of it")

    def test_upload_and_list(self):
        self.login()
        full, _ = full_image("0.2.0")
        st, payload, _ = self.req("POST", "/api/v1/admin/esp32-firmware", raw=full, ctype="application/octet-stream")
        self.assertEqual(st, 200, payload)
        st, payload, _ = self.req("POST", "/api/v1/admin/esp32-firmware", raw=full[:1000], ctype="application/octet-stream")
        self.assertEqual(st, 400)
        st, payload, _ = self.req("GET", "/api/v1/admin/esp32-firmware")
        self.assertIn(b'"0.2.0"', payload)


class SignedUpdate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = Core(Database(os.path.join(tmp.name, "t.db")))
        self.seq = 0
        self.img = {"sha256": hashlib.sha256(b"app").hexdigest(), "size": 1116016, "version": "0.2.0"}

    def send_hello(self, rev, boot=5):
        self.seq += 1
        self.core.handle_events({"device_id": "e1", "boot_count": 1, "uptime_ms": 10_000 + self.seq,
                                 "events": [{"seq": self.seq, "type": "target_hello", "uptime_ms": 9_000, "raw": hello(boot, rev=rev)}]})

    def line(self, cid):
        return self.core.db.conn().execute("SELECT line FROM commands WHERE id=?", (cid,)).fetchone()[0]

    def test_only_esp32_targets_with_a_key_and_never_in_a_match(self):
        self.core.handle_heartbeat({"device_id": "e1", "boot_count": 1, "uptime_ms": 0})
        with self.assertRaisesRegex(BadRequest, "not an ESP32"):
            self.core.target_update("e1", self.img)
        self.core.add_target_key(KEY, "t")
        self.send_hello(310)
        with self.assertRaisesRegex(BadRequest, "flash station"):
            self.core.target_update("e1", self.img)
        self.send_hello(620, boot=6)                             # the next start (a hello never moves back)
        line = self.line(self.core.target_update("e1", self.img))
        head, _, tag = line.rpartition(" h=")
        self.assertEqual(head, f"FT1 C b=6 i=3 update sha={self.img['sha256']} size=1116016")   # 1, 2: the automatic get, status
        self.assertEqual(tag, mac(KEY, head.encode()), "signed with the target's own key")
        self.core.matches.lock_of = lambda c, d: "Final"
        with self.assertRaisesRegex(BadRequest, "match"):
            self.core.target_update("e1", self.img)

    def test_no_key_bound(self):
        self.core.handle_heartbeat({"device_id": "e1", "boot_count": 1, "uptime_ms": 0})
        self.send_hello(620)                                    # a key this server does not know: rev noted, no key
        with self.assertRaisesRegex(BadRequest, "key"):
            self.core.target_update("e1", self.img)


if __name__ == "__main__":
    unittest.main()
