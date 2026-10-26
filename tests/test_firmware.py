"""Firmware updates from the Pi: image checks, the store, the queue rules, the HTTP side, and the whole update run
by the real firmware core (host harness) against the real server."""
import hashlib
import http.client
import json
import os
import random
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from fetserver.admin import Admin, write_password
from fetserver.core import BadRequest, Core, NotFound
from fetserver.db import Database
from fetserver.firmware import BadImage, FirmwareStore, best_for, inspect
from fetserver.server import make_server

from tests.test_firmware_host import Harness, build

ROOT = Path(__file__).resolve().parent.parent
EXPORTED = ROOT / "firmware/fet_bridge/build/esp8266.esp8266.generic/fet_bridge.ino.bin"
TOKEN = "fw-token"
MB = 1 << 20


def image(version="0.2.0", size=200_000, mode=3, flash=0x20, magic=0xE9, segs=2, extra=b"", seed=1):
    rnd = random.Random(seed)
    body = bytes([magic, segs, mode, flash]) + bytes(rnd.getrandbits(8) for _ in range(4092))
    body += b"\r\nUser-Agent: fet-monitor-bridge/" + version.encode() + b"\r\nX-FET-Token: " + extra
    return body + bytes(rnd.getrandbits(8) for _ in range(size - len(body)))


class Inspect(unittest.TestCase):
    def test_accepts_ours(self):
        m = inspect(image())
        self.assertEqual((m["version"], m["flash_size"], m["flash_mode"], m["size"]), ("0.2.0", MB, "DOUT", 200_000))
        self.assertEqual(m["sha256"], hashlib.sha256(image()).hexdigest())
        self.assertEqual(inspect(image(flash=0x40))["flash_size"], 4 * MB)

    def test_refusals(self):
        cases = [
            (image(size=10_000), "size"), (b"\xe9" + bytes(MB + 10), "size"),
            (image(magic=0x7F), "not an ESP8266"), (image(segs=0), "damaged"), (image(segs=40), "damaged"),
            (image(mode=2), "DOUT"), (image(flash=0x70), "unknown flash size"),
            (image().replace(b"fet-monitor-bridge/", b"something-else-xx/"), "not FeT Bridge firmware"),
            (image(extra=b"fet-monitor-bridge/0.1.9 "), "more than one"),
        ]
        for data, why in cases:
            with self.assertRaises(BadImage, msg=why) as cm:
                inspect(data)
            self.assertIn(why, str(cm.exception))

    @unittest.skipUnless(EXPORTED.exists(), "no exported firmware binary")
    def test_the_exported_build(self):
        data = EXPORTED.read_bytes()
        try:
            m = inspect(data)
        except BadImage as exc:                    # 0.1.x builds do not carry the identification
            self.assertIn("0.2.0 or newer", str(exc))
            return
        self.assertEqual(m["flash_mode"], "DOUT")
        self.assertIn(m["flash_size"], (MB, 4 * MB))


class Store(unittest.TestCase):
    def test_add_get_list_delete(self):
        with tempfile.TemporaryDirectory() as d:
            st = FirmwareStore(Path(d))
            a = st.add(image(seed=1), "1.2.3.4")
            b = st.add(image(version="0.2.1", seed=2))
            self.assertEqual([m["version"] for m in st.list()], ["0.2.1", "0.2.0"])
            self.assertEqual(st.path(a["sha256"]).read_bytes(), image(seed=1))
            for bad in ("../" + "a" * 61, "A" * 64, a["sha256"][:63], None, 5):
                self.assertIsNone(st.get(bad))
            (Path(d) / "evil.bin").write_bytes(b"x")                 # a real file outside the firmware folder
            (Path(d) / "evil.json").write_text(json.dumps({"sha256": "x", "size": 1}))
            self.assertIsNone(st.get("../evil"))
            self.assertIsNone(st.path("../evil"))
            self.assertTrue(st.delete(b["sha256"]))
            self.assertFalse(st.delete(b["sha256"]))
            self.assertEqual(sorted(os.listdir(Path(d) / "firmware")), sorted([a["sha256"] + ".bin", a["sha256"] + ".json"]))
            with self.assertRaises(BadImage):
                st.add(image(mode=0))
            self.assertEqual(len(st.list()), 1, "a refused image leaves nothing behind")
        self.assertEqual(FirmwareStore(None).list(), [])


class Best(unittest.TestCase):
    def setUp(self):
        mk = lambda v, flash, size, up: dict(inspect(image(version=v, flash=flash, size=size, seed=hash(v + str(flash)) % 1000)), uploaded=up)
        self.imgs = [mk("0.2.0", 0x20, 300_000, 1), mk("0.2.3", 0x20, 300_000, 2), mk("0.2.10", 0x20, 300_000, 3),
                     mk("0.3.0", 0x20, 470_000, 4), mk("0.2.5", 0x40, 300_000, 5)]

    def pick(self, **t):
        img, why = best_for(t, self.imgs)
        return (img["version"] if img else None), why

    def test_newest_that_fits_the_same_flash_size(self):
        self.assertEqual(self.pick(ota_space=460_000, flash_size=MB, bridge_fw="0.2.0")[0], "0.2.10",
                         "0.2.10 > 0.2.3 (numeric, not text); 0.3.0 is too big; the 4 MB image is not for this chip")
        self.assertEqual(self.pick(ota_space=900_000, flash_size=MB, bridge_fw="0.2.0")[0], "0.3.0")
        self.assertEqual(self.pick(ota_space=900_000, flash_size=4 * MB, bridge_fw="0.2.0")[0], "0.2.5")

    def test_reasons(self):
        self.assertIn("cannot update from the Pi", self.pick(ota_space=0, bridge_fw="0.1.16")[1])
        self.assertIn("no image for a 2 MB build", self.pick(ota_space=900_000, flash_size=2 * MB, bridge_fw="0.2.0")[1])
        self.assertIn("larger than its 200000 bytes", self.pick(ota_space=200_000, flash_size=MB, bridge_fw="0.2.0")[1])
        v, why = self.pick(ota_space=460_000, flash_size=MB, bridge_fw="0.2.10")
        self.assertEqual(v, None)
        self.assertIn("already up to date", why)
        self.assertEqual(self.pick(ota_space=460_000, flash_size=MB, bridge_fw="0.9.0")[0], None, "never a downgrade")
        self.assertEqual(self.pick(ota_space=460_000, flash_size=MB, bridge_fw="garbage")[0], "0.2.10")
        self.assertIn("no image", best_for({"ota_space": 1, "flash_size": MB}, [])[1])


class Queue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = Core(Database(os.path.join(tmp.name, "t.db")))
        self.img = inspect(image())

    def hb(self, did, **kw):
        self.core.handle_heartbeat({"device_id": did, "boot_count": 1, "uptime_ms": 1000, "bridge_fw": kw.pop("fw", "0.2.0"), **kw})

    def test_rules(self):
        self.hb("old", fw="0.1.16")
        with self.assertRaises(BadRequest) as cm:
            self.core.queue_firmware("old", self.img)
        self.assertIn("cannot update from the Pi", str(cm.exception))
        self.hb("small", ota_space=100_000, flash_size=MB)
        with self.assertRaises(BadRequest) as cm:
            self.core.queue_firmware("small", self.img)
        self.assertIn("room for 100000", str(cm.exception))
        self.hb("big", ota_space=900_000, flash_size=4 * MB)
        with self.assertRaises(BadRequest) as cm:
            self.core.queue_firmware("big", self.img)
        self.assertIn("4 MB build; this image is a 1 MB build", str(cm.exception))
        self.hb("ok", ota_space=460_000, flash_size=MB)
        cid = self.core.queue_firmware("ok", self.img)
        with self.assertRaises(BadRequest):
            self.core.queue_firmware("ok", self.img)              # one at a time
        r = self.core.handle_heartbeat({"device_id": "ok", "boot_count": 1, "uptime_ms": 2000, "ota_space": 460_000, "flash_size": MB})
        self.assertEqual(r["commands"], [{"id": cid, "type": "fw_update", "line": f"{self.img['sha256']} 200000"}])
        job = self.core.firmware_jobs()["ok"]
        self.assertEqual((job["status"], job["sha256"], job["size"]), ("sent", self.img["sha256"], 200000))
        self.core.handle_heartbeat({"device_id": "ok", "boot_count": 1, "uptime_ms": 3000, "ota_space": 460_000, "flash_size": MB,
                                    "cmd_results": [{"id": cid, "status": "done", "reply": "updated"}]})
        self.assertEqual(self.core.firmware_jobs()["ok"]["status"], "done")
        self.core.queue_firmware("ok", self.img)                 # done: another may follow
        with self.assertRaises(NotFound):
            self.core.queue_firmware("nope", self.img)

    def test_downgrade_clears_capability(self):
        self.hb("t", ota_space=460_000, flash_size=MB)
        self.hb("t", fw="0.1.16")                                 # reflashed by USB with old firmware: no fields
        with self.assertRaises(BadRequest):
            self.core.queue_firmware("t", self.img)
        for junk in (-5, "460000", True, 2 ** 40):
            self.hb("t", ota_space=junk)
            with self.assertRaises(BadRequest):
                self.core.queue_firmware("t", self.img)


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.core = Core(Database(str(self.dir / "t.db")), heartbeat_interval_ms=5000)
        self.store = FirmwareStore(self.dir)
        self.server = make_server(self.core, TOKEN, "127.0.0.1", 0, admin=Admin(self.dir), firmware=self.store)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.cookie = None

    def req(self, method, path, body=None, raw=None, ctype=None, admin=True, token=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {}
        if admin:
            h["X-FET-Admin"] = "1"
        if self.cookie:
            h["Cookie"] = self.cookie
        if token:
            h["X-FET-Token"] = token
        data = raw
        if body is not None:
            data, ctype = json.dumps(body).encode(), "application/json"
        if ctype:
            h["Content-Type"] = ctype
        try:
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            payload = r.read()
        except (BrokenPipeError, ConnectionResetError):
            return 0, None, {}
        finally:
            c.close()
        return r.status, payload, dict(r.getheaders())

    def login(self):
        write_password(self.dir, "range-pass")
        st, payload, headers = self.req("POST", "/api/v1/admin/login", {"password": "range-pass"})
        self.assertEqual(st, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]


class FirmwareHttp(ServerCase):
    def test_upload_rules_and_device_download(self):
        img = image()
        st, _, _ = self.req("POST", "/api/v1/admin/firmware", raw=img, ctype="application/octet-stream")
        self.assertIn(st, (0, 401), "not logged in")
        self.login()
        self.assertEqual(self.req("POST", "/api/v1/admin/firmware", raw=img, ctype="multipart/form-data")[0], 415)
        self.assertEqual(self.req("POST", "/api/v1/admin/firmware", raw=img, ctype="application/octet-stream", admin=False)[0], 403)
        st, payload, _ = self.req("POST", "/api/v1/admin/firmware", raw=image(mode=2), ctype="application/octet-stream")
        self.assertEqual(st, 400)
        self.assertIn("DOUT", json.loads(payload)["error"])
        st, payload, _ = self.req("POST", "/api/v1/admin/firmware", raw=img, ctype="application/octet-stream")
        self.assertEqual(st, 200, payload)
        meta = json.loads(payload)
        st, payload, _ = self.req("GET", "/api/v1/admin/firmware")
        self.assertEqual([m["sha256"] for m in json.loads(payload)["images"]], [meta["sha256"]])
        # the device side
        self.assertEqual(self.req("GET", f"/api/v1/firmware/{meta['sha256']}", admin=False)[0], 401)
        self.assertEqual(self.req("GET", f"/api/v1/firmware/{meta['sha256']}", admin=False, token="wrong")[0], 401)
        self.assertEqual(self.req("GET", "/api/v1/firmware/" + "0" * 64, admin=False, token=TOKEN)[0], 404)
        self.assertEqual(self.req("GET", "/api/v1/firmware/../../t.db", admin=False, token=TOKEN)[0], 404)
        st, payload, headers = self.req("GET", f"/api/v1/firmware/{meta['sha256']}", admin=False, token=TOKEN)
        self.assertEqual((st, payload, headers["Content-Length"]), (200, img, str(len(img))))
        # queue for a target over HTTP, then delete the image
        self.core.handle_heartbeat({"device_id": "t1", "boot_count": 1, "uptime_ms": 1, "bridge_fw": "0.2.0", "ota_space": 460000, "flash_size": MB})
        st, payload, _ = self.req("POST", "/api/v1/admin/targets/t1/firmware", {"sha256": meta["sha256"]})
        self.assertEqual(st, 200, payload)
        st, payload, _ = self.req("POST", "/api/v1/admin/targets/t1/firmware", {"sha256": meta["sha256"]})
        self.assertEqual(st, 400, "already waiting")
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/t1/firmware", {"sha256": "f" * 64})[0], 404)
        view = json.loads(self.req("GET", "/api/v1/admin/targets")[1])
        self.assertEqual(view["targets"][0]["fw_job"]["status"], "queued")
        # "best": refused while one is waiting; the targets view names each target's best image (or why none)
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/t1/firmware", {"sha256": "best"})[0], 400)
        self.core.handle_heartbeat({"device_id": "t2", "boot_count": 1, "uptime_ms": 1, "bridge_fw": "0.1.9", "ota_space": 460000, "flash_size": MB})
        self.core.handle_heartbeat({"device_id": "t3", "boot_count": 1, "uptime_ms": 1, "bridge_fw": "0.2.0", "ota_space": 900000, "flash_size": 4 * MB})
        rows = {t["device_id"]: t for t in json.loads(self.req("GET", "/api/v1/admin/targets")[1])["targets"]}
        self.assertEqual(rows["t2"]["fw_best"]["version"], "0.2.0")
        self.assertIsNone(rows["t3"]["fw_best"])
        self.assertIn("no image for a 4 MB build", rows["t3"]["fw_best_reason"])
        st, payload, _ = self.req("POST", "/api/v1/admin/targets/t3/firmware", {"sha256": "best"})
        self.assertEqual(st, 400)
        self.assertIn("no image for a 4 MB build", json.loads(payload)["error"], "refused on the Pi, never sent to the ESP")
        st, payload, _ = self.req("POST", "/api/v1/admin/targets/t2/firmware", {"sha256": "best"})
        self.assertEqual(st, 200, payload)
        self.assertEqual(self.core.firmware_jobs()["t2"]["sha256"], meta["sha256"])
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/ghost/firmware", {"sha256": "best"})[0], 404)
        self.assertEqual(self.req("POST", f"/api/v1/admin/firmware/{meta['sha256']}/delete", {})[0], 200)
        self.assertEqual(self.req("POST", f"/api/v1/admin/firmware/{meta['sha256']}/delete", {})[0], 404)

    def test_factory_reset_over_http(self):
        self.login()
        self.core.handle_heartbeat({"device_id": "old", "boot_count": 1, "uptime_ms": 1, "bridge_fw": "0.2.1", "ota_space": 1, "flash_size": MB})
        self.core.handle_heartbeat({"device_id": "new", "boot_count": 1, "uptime_ms": 1, "bridge_fw": "0.2.2", "ota_space": 1, "flash_size": MB})
        st, payload, _ = self.req("POST", "/api/v1/admin/targets/old/command", {"type": "factory_reset"})
        self.assertEqual(st, 400)
        self.assertIn("needs 0.2.2", json.loads(payload)["error"])
        st, payload, _ = self.req("POST", "/api/v1/admin/targets/new/command", {"type": "factory_reset"})
        self.assertEqual(st, 200, payload)
        line = self.core.db.conn().execute("SELECT line FROM commands WHERE device_id='new'").fetchone()[0]
        self.assertEqual(line, "erase")
        self.cookie = None
        self.assertEqual(self.req("POST", "/api/v1/admin/targets/new/command", {"type": "factory_reset"})[0], 401)

    def test_too_big_is_refused_before_reading(self):
        self.login()
        st, _, _ = self.req("POST", "/api/v1/admin/firmware", raw=b"\xe9" * (MB + 1), ctype="application/octet-stream")
        self.assertIn(st, (0, 413))


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class UpdateRun(ServerCase):
    """The real bridge core downloads, checks and stages the image (the harness's 'flash' is memory)."""
    def setUp(self):
        super().setUp()
        self.h = Harness(build())
        self.addCleanup(self.h.close)
        self.h.run(f"cfg {self.port} {TOKEN}")
        self.h.run("otaspace 460000")
        self.h.run("boot")
        self.h.run("tick 200")
        self.meta = self.store.add(image(size=250_001, seed=7))

    def queue(self):
        return self.core.queue_firmware("fet-test01", self.meta)

    def status(self, cid):
        return self.core.db.conn().execute("SELECT status, result FROM commands WHERE id=?", (cid,)).fetchone()

    def test_good_image_is_staged_then_reboot(self):
        cid = self.queue()
        self.h.run("tick 12000")
        state, size, sha = self.h.run("fwstate").split()
        self.assertEqual((state, int(size), sha), ("staged", 250_001, self.meta["sha256"]))
        st, result = self.status(cid)
        self.assertEqual(st, "done")
        self.assertIn("updated", result)
        self.assertEqual(self.h.flags()["reboots"], 1, "restarts only after the server has the result")

    def test_damaged_download_is_never_complete(self):
        self.h.run("fwcorrupt 100000")
        cid = self.queue()
        self.h.run("tick 12000")
        state, size, _ = self.h.run("fwstate").split()
        self.assertEqual(state, "none")
        self.assertEqual(int(size), 250_000, "everything but the last byte reached the updater; the image never completes")
        self.assertEqual(tuple(self.status(cid)), ("failed", "update failed: checksum mismatch"))
        self.assertEqual(self.h.flags()["reboots"], 0)

    def test_old_firmware_refuses(self):
        self.h.run("otaspace 0")
        self.h.run("tick 6000")
        with self.assertRaises(BadRequest):
            self.queue()                              # the server refuses first
        with self.core.db.write() as c:                # and if a command got there anyway, the bridge refuses
            cid = c.execute("INSERT INTO commands (device_id, type, line, created_ms) VALUES (?,?,?,?)",
                            ("fet-test01", "fw_update", f"{self.meta['sha256']} {self.meta['size']}", 1)).lastrowid
        self.h.run("tick 12000")
        st, result = self.status(cid)
        self.assertEqual(st, "failed")
        self.assertIn("does not fit", result)
        self.assertEqual(self.h.run("fwstate").split()[0], "none")

    def test_bad_command_lines_are_refused(self):
        for line in ("nothex " + "1" * 3, self.meta["sha256"].upper() + " 5", self.meta["sha256"] + " -5",
                     self.meta["sha256"] + " 99999999999", self.meta["sha256"], self.meta["sha256"] + " 12x"):
            with self.core.db.write() as c:
                cid = c.execute("INSERT INTO commands (device_id, type, line, created_ms) VALUES (?,?,?,?)",
                                ("fet-test01", "fw_update", line, 1)).lastrowid
            self.h.run("tick 6000")
            self.assertEqual(tuple(self.status(cid)), ("failed", "refused: bad firmware command"), line)
        self.assertEqual(self.h.run("fwstate").split()[0], "none")

    def test_factory_reset_erases_after_reporting_and_leaves_the_list(self):
        self.h.run("tick 6000")
        self.assertEqual([t["device_id"] for t in self.core.list_targets()], ["fet-test01"])
        cid = self.core.enqueue_command("fet-test01", "factory_reset")
        self.h.run("tick 12000")
        st, result = self.status(cid)
        self.assertEqual(st, "done")
        self.assertIn("erasing all settings", result)
        self.assertEqual(self.h.run("erases"), "1", "erased only after the server had the result")
        self.assertEqual(self.core.list_targets(), [], "taken off the lists; history stays")

    def test_factory_reset_needs_the_word(self):
        with self.core.db.write() as c:
            cid = c.execute("INSERT INTO commands (device_id, type, line, created_ms) VALUES (?,?,?,?)",
                            ("fet-test01", "factory_reset", None, 1)).lastrowid
        self.h.run("tick 8000")
        self.assertEqual(tuple(self.status(cid)), ("failed", 'refused: factory reset needs "erase"'))
        self.assertEqual(self.h.run("erases"), "0")

    def test_fwcheck_holds_back_the_last_byte_and_refuses_extra(self):
        self.assertEqual(self.h.run("fwcheck"), "6 0 3 1 1 10", "6 taken, 6 more refused (too many), 4 -> 3 + held byte")
        self.assertEqual(self.h.run("fwcheck " + "0" * 64), "6 0 3 1 0 10", "complete but not verified")

    def test_sha256_matches_hashlib(self):
        rnd = random.Random(3)
        for n in (0, 1, 55, 56, 63, 64, 65, 119, 120, 1000):
            data = bytes(rnd.getrandbits(8) for _ in range(n))
            arg = "".join("\\x%02x" % b for b in data)
            self.assertEqual(self.h.run("sha256 " + arg), hashlib.sha256(data).hexdigest(), n)


if __name__ == "__main__":
    unittest.main()
