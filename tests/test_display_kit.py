"""FeT Display as a portable kit's server (firmware/display/lib/kit.py): the seq ranges, the store across a restart,
and tools/protocol_check.py (FeT Monitor's own check of the target protocol) against it under MicroPython."""
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    """A display module by its path: the display's lib never goes on this process's sys.path (its http.py would hide
    the standard library's http for every test after this one)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("display_" + name, ROOT / "firmware/display/lib" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


kit = _load("kit")

MP = shutil.which("micropython")
SERVER = r"""
import sys, asyncio
sys.path.insert(0, %r)
import kit
st = kit.Store(%r, cyd_boot=7, ticks=lambda: 1234)
k = kit.Kit(st, %r)
async def main():
    await kit.serve(k, %d, "127.0.0.1")
    print("READY")
    while True:
        await asyncio.sleep(1)
asyncio.run(main())
"""


class RangesAndStore(unittest.TestCase):
    def test_the_data_folder_can_never_be_imported(self):
        for name in (kit.DIR.strip("/"), kit.LOG.rsplit("/", 1)[0].strip("/")):
            self.assertFalse(name.isidentifier(), "a folder %s in / hides a module of that name in /lib" % name)

    def test_ranges_are_a_set(self):
        rng = random.Random(14)
        for _ in range(300):
            r, ref = kit.Ranges(), set()
            for _ in range(rng.randint(0, 60)):
                n = rng.choice([rng.randint(0, 40), rng.randint(0, 2000)])
                r.add(n)
                ref.add(n)
                self.assertTrue(all(r.has(x) for x in ref))
            self.assertFalse(any(r.has(x) for x in range(-1, 2100) if x not in ref and x >= 0))
            self.assertEqual(sum(hi - lo + 1 for lo, hi in r.r), len(ref), "ranges never overlap")
            self.assertEqual(r.r, sorted(r.r))
        r = kit.Ranges()
        for n in range(1, 524):
            r.add(n)
        self.assertEqual(r.r, [[1, 523]], "a target sending in order: one range")

    def test_stored_before_acknowledged_and_kept_across_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "kit", "events.jsonl")
            st = kit.Store(path, cyd_boot=3, ticks=lambda: 500, epoch=lambda: None)
            ev = lambda q: {"seq": q, "type": "shot", "uptime_ms": 100 + q, "raw": '{"shot":%d,"x":1.0,"y":2.0}' % q}
            acked, rej, new = st.put("fet-1", 9, 200, [ev(1), ev(2), ev(2), {"seq": 3, "type": "nope", "uptime_ms": 1}])
            self.assertEqual((acked, [r["seq"] for r in rej], len(new)), ([[9, 1], [9, 2], [9, 2]], [3], 2), "a repeat in one request: stored once")
            with open(path) as fh:
                lines = [json.loads(x) for x in fh]
            self.assertEqual([(x["d"], x["b"], x["q"], x["ru"], x["rx"], x["cb"], x["ep"]) for x in lines],
                             [("fet-1", 9, 1, 200, 500, 3, None), ("fet-1", 9, 2, 200, 500, 3, None)])
            with open(path, "a") as fh:
                fh.write('{"d": "fet-1", "b": 9, "q"')         # a line cut short by a power cut
            st2 = kit.Store(path)
            self.assertEqual(st2.count, 2)
            acked, _, new = st2.put("fet-1", 9, 300, [ev(2), ev(3)])
            self.assertEqual((acked, [x["q"] for x in new]), ([[9, 2], [9, 3]], [3]), "after a restart: the repeat is known")
            acked, _, new = st2.put("fet-1", 8, 300, [dict(ev(5), boot_count=10)])
            self.assertEqual((acked, new[0]["ru"]), ([[10, 5]], None), "an event of another boot: no request uptime for it")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(MP, "micropython not installed (brew install micropython)")
class ProtocolCheck(unittest.TestCase):
    def test_fet_monitors_protocol_check_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = free_port()
            Path(tmp, "s.py").write_text(SERVER % (str(ROOT / "firmware/display/lib"), os.path.join(tmp, "kit", "events.jsonl"), "kit-token-1", port))
            p = subprocess.Popen([MP, str(Path(tmp, "s.py"))], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "READY")
                r = subprocess.run([sys.executable, str(ROOT / "tools/protocol_check.py"), "http://127.0.0.1:%d" % port, "kit-token-1",
                                    "--device", "kit-check"], capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("All checks passed", r.stdout)
                with open(os.path.join(tmp, "kit", "events.jsonl")) as fh:
                    self.assertEqual(len(fh.readlines()), 3, "two shots and one more: each stored once")
            finally:
                p.kill()
                p.communicate()

    @unittest.skipUnless(MP, "micropython not installed")
    def test_bodies_in_pieces_and_too_large(self):
        import socket
        import time as _t

        def send(port, parts, pause=0.0):
            c = socket.create_connection(("127.0.0.1", port), timeout=10)
            for part in parts:
                c.sendall(part)
                _t.sleep(pause)
            out = b""
            while True:
                d = c.recv(4096)
                if not d:
                    break
                out += d
            c.close()
            return int(out.split(b" ", 2)[1]), json.loads(out.partition(b"\r\n\r\n")[2])

        def head(n, token="kit-token-1"):
            return ("POST /api/v1/events HTTP/1.1\r\nHost: x\r\nX-FET-Token: %s\r\nContent-Length: %d\r\n\r\n" % (token, n)).encode()

        with tempfile.TemporaryDirectory() as tmp:
            port = free_port()
            Path(tmp, "s.py").write_text(SERVER % (str(ROOT / "firmware/display/lib"), os.path.join(tmp, "kit-data", "events.jsonl"), "kit-token-1", port))
            p = subprocess.Popen([MP, str(Path(tmp, "s.py"))], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "READY")
                body = json.dumps({"device_id": "kit-a", "boot_count": 3, "uptime_ms": 900, "events": [
                    {"seq": i, "type": "shot", "uptime_ms": 100 * i, "raw": '{"shot":%d, "miss":0, "x":1.00, "y":2.00}' % i} for i in range(1, 41)]}).encode()
                self.assertGreater(len(body), 3000)
                h = head(len(body))
                pieces = [h[:20], h[20:], body[:7], body[7:2000], body[2000:]]
                st, ans = send(port, pieces, 0.05)
                self.assertEqual((st, len(ans["acked"])), (200, 40), "head and body in pieces, with pauses")
                st, ans = send(port, [head(kit.MAX_BODY + 1)])
                self.assertEqual(st, 413, "refused from its declared length, before it is read")
                st, ans = send(port, [head(len(body) + 10) + body])
                self.assertEqual(st, 400, "a body shorter than its length: refused once the target stops sending (5 s)")
                st, ans = send(port, [head(len(body), "wrong") + body])
                self.assertEqual(st, 401)
                st, ans = send(port, [b"GARBAGE\r\n\r\n"])
                self.assertEqual(st, 400)
            finally:
                p.kill()
                p.communicate()


if __name__ == "__main__":
    unittest.main()
