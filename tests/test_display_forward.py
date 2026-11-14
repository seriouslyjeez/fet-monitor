"""A portable kit sends its stored lines on to deepTENS (firmware/display/lib/forward.py; deepTENS devices/ingest.py
handle_kit_lines): what it sends (the line as the target sent it, its face, the target boot's start when the real time
was known), where it stands (an offset kept across restarts, moved only once deepTENS answered for a batch), and the
whole loop under MicroPython against a stand-in deepTENS: pairing pending, then sending, a refused target counted and
not sent again, a dropped connection retried. deepTENS's side is tested there (devices/test_kit.py)."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    """A display module by its path: the display's lib never goes on this process's sys.path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("display_" + name, ROOT / "firmware/display/lib" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


kit, forward = _load("kit"), _load("forward")
MP = shutil.which("micropython")
SHOT = '{"shot":%d, "miss":0, "x":1.00, "y":2.00}'


def ev(seq, t="shot", up=None):
    return {"seq": seq, "type": t, "uptime_ms": 1000 * seq if up is None else up, "raw": SHOT % seq}


class Lines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "kit-data", "events.jsonl")
        self.state = os.path.join(self.tmp.name, "kit-data", "sent")
        self.clock = {"up": 50_000, "ep": None}
        self.store = kit.Store(self.log, cyd_boot=9, ticks=lambda: self.clock["up"], epoch=lambda: self.clock["ep"])

    def fw(self, cyd_boot=9):
        return forward.Forwarder(self.log, self.state, cyd_boot, lambda: self.clock["up"], lambda: self.clock["ep"])

    def test_what_is_sent_and_the_time(self):
        self.store.put("fet-a", 3, 20_000, [ev(1), ev(2, "config"), dict(ev(3), boot_count=2)], face="nra-a17")
        self.clock.update(up=60_000, ep=1_800_000_060_000)
        self.store.put("fet-a", 3, 30_000, [ev(4)], face="issf-ar10")
        lines, end = self.fw().batch()
        self.assertEqual([(x["seq"], x["face_id"], x.get("boot_start_ms")) for x in lines],
                         [(1, "nra-a17", 1_800_000_000_000 + 50_000 - 20_000),    # the clock set later in this start
                          (3, "nra-a17", None),                                   # another boot than the request's
                          (4, "issf-ar10", 1_800_000_060_000 - 30_000)], "config lines are not sent")
        self.assertEqual(lines[0], {"device_id": "fet-a", "boot_count": 3, "seq": 1, "type": "shot", "uptime_ms": 1000,
                                    "raw": SHOT % 1, "face_id": "nra-a17", "boot_start_ms": 1_800_000_030_000})
        self.assertEqual(end, os.path.getsize(self.log))
        self.clock["ep"] = None
        self.assertNotIn("boot_start_ms", self.fw().batch()[0][0], "no clock: no start")
        self.clock["ep"] = 1_800_000_060_000
        self.assertNotIn("boot_start_ms", self.fw(cyd_boot=10).batch()[0][0], "stored in an earlier start, before the clock")

    def test_where_it_stands_across_restarts(self):
        self.store.put("fet-a", 3, 1, [ev(i) for i in range(1, 26)])
        f = self.fw()
        lines, end = f.batch()
        self.assertEqual(len(lines), forward.BATCH)
        self.assertEqual(f.batch()[0], lines, "nothing moves until deepTENS answers")
        f.done(end, {"acked": [["fet-a", 3, i] for i in range(1, 20)], "rejected": [{"seq": 20, "why": "not one of your targets"}]})
        g = self.fw()
        self.assertEqual((g.offset, g.sent, g.refused), (end, 19, 1), "kept across a restart")
        self.assertEqual([x["seq"] for x in g.batch()[0]], [21, 22, 23, 24, 25])
        with open(self.log, "ab") as fh:
            fh.write(b'{"d": "fet-a", "b": 3, "q": 26, "t": "shot", "u"')    # a line cut short by a power cut
        lines, end2 = g.batch()
        self.assertEqual(([x["seq"] for x in lines], end2 < os.path.getsize(self.log)), ([21, 22, 23, 24, 25], True))
        g.done(end2, {"acked": [], "rejected": []})
        self.assertEqual(g.batch(), ([], end2))
        self.assertEqual(self.fw().waiting(), os.path.getsize(self.log) - end2)


class FakeDeepTENS(BaseHTTPRequestHandler):
    calls = []
    state = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeDeepTENS.calls.append((self.path, self.headers.get("Authorization"), body))
        st = FakeDeepTENS.state
        if self.path == "/api/v1/display/pair":
            out, code = {"code": "K7M2QX", "token": "fetd_kit", "expires_in_s": 600, "kit": body.get("kit")}, 200
        elif st["removed"] > 0:
            st["removed"] -= 1                            # removed on deepTENS: the kit pairs again
            out, code = {"error": "not assigned", "pairing": "none"}, 401
        elif st["approved_after"] > 0:
            st["approved_after"] -= 1
            out, code = {"error": "not assigned", "pairing": "pending"}, 401
        elif st["drop"] > 0 and body["lines"]:
            st["drop"] -= 1
            self.connection.close()                       # no answer: the kit must send the batch again
            return
        else:
            ok = [ln for ln in body["lines"] if ln["device_id"] != "stranger"]
            out = {"acked": [[ln["device_id"], ln["boot_count"], ln["seq"]] for ln in ok],
                   "rejected": [{"device_id": ln["device_id"], "seq": ln["seq"], "why": "not one of your targets on deepTENS"}
                                for ln in body["lines"] if ln["device_id"] == "stranger"]}
            code = 200
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


LOOP = r"""
import sys, asyncio, json
sys.path.insert(0, %r)
import kit, forward, client
store = kit.Store(%r, cyd_boot=1, ticks=lambda: 5000, epoch=lambda: None)
store.put("fet-a", 3, 100, [{"seq": i, "type": "shot", "uptime_ms": i, "raw": "x%%d" %% i} for i in range(1, 41)])
store.put("stranger", 1, 100, [{"seq": 1, "type": "shot", "uptime_ms": 1, "raw": "y"}])     # a batch of its own, refused
fw = forward.Forwarder(%r, %r, 1)
cl = client.Client("127.0.0.1:%d", "cyd-test", "0.2.0")
seen = {"codes": [], "states": [], "tokens": []}
wake = asyncio.Event()
async def main():
    t = asyncio.create_task(forward.run(fw, cl, lambda: True, seen["codes"].append, seen["states"].append, wake, seen["tokens"].append))
    for _ in range(400):
        await asyncio.sleep_ms(50)
        if seen["states"] and seen["states"][-1] == "deepTENS: all sent" and fw.refused >= 1:
            break
    store.put("fet-a", 3, 100, [{"seq": 41, "type": "shot", "uptime_ms": 41, "raw": "x41"}])
    wake.set()                                         # new lines: sent at once, not after a minute
    for _ in range(100):
        await asyncio.sleep_ms(50)
        if fw.sent >= 41:
            break
    t.cancel()
asyncio.run(main())
print("RESULT " + json.dumps({"sent": fw.sent, "refused": fw.refused, "why": fw.last_refusal, "codes": seen["codes"],
                               "tokens": seen["tokens"][-1:], "forgot": None in seen["tokens"], "states": seen["states"]}))
"""


@unittest.skipUnless(MP, "micropython not installed (brew install micropython)")
class TheLoop(unittest.TestCase):
    def loop(self, **state):
        FakeDeepTENS.calls.clear()
        FakeDeepTENS.state = dict({"approved_after": 0, "drop": 0, "removed": 0}, **state)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeDeepTENS)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            log, state = os.path.join(tmp, "kit-data", "events.jsonl"), os.path.join(tmp, "kit-data", "sent")
            Path(tmp, "run.py").write_text(LOOP % (str(ROOT / "firmware/display/lib"), log, log, state, srv.server_address[1]))
            r = subprocess.run([MP, str(Path(tmp, "run.py"))], capture_output=True, text=True, timeout=120)
            self.assertIn("RESULT ", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])
            res = json.loads(r.stdout.split("RESULT ", 1)[1])
            self.assertEqual(json.loads(Path(state).read_text())["sent"], 41)
        return res

    def test_pairs_then_sends_everything_once(self):
        res = self.loop(approved_after=2, drop=1)
        self.assertEqual((res["sent"], res["refused"]), (41, 1), res)
        self.assertEqual(res["why"], "not one of your targets on deepTENS")
        self.assertEqual(res["codes"][:2], ["K7M2QX", None], "the code shown, then gone once typed")
        self.assertEqual(res["tokens"], ["fetd_kit"], "the token kept")
        self.assertIn("deepTENS: type the code", res["states"])
        self.assertIn("deepTENS: no answer", res["states"], "a dropped connection, then sent again")
        pair = [c for c in FakeDeepTENS.calls if c[0].endswith("/pair")]
        self.assertEqual((len(pair), pair[0][2].get("kit")), (1, True), "paired once, as a kit")
        sent = [(ln["device_id"], ln["seq"]) for c in FakeDeepTENS.calls if c[0].endswith("/lines") for ln in c[2]["lines"]]
        self.assertTrue(all(c[1] == "Bearer fetd_kit" for c in FakeDeepTENS.calls if c[0].endswith("/lines")))
        self.assertEqual(sorted(set(sent)), sorted([("fet-a", i) for i in range(1, 42)] + [("stranger", 1)]))
        self.assertEqual(sent.count(("stranger", 1)), 1, "a refused line is not sent again")

    def test_removed_on_deeptens_pairs_again(self):
        res = self.loop(removed=1)
        self.assertEqual(res["sent"], 41)
        self.assertTrue(res["forgot"], "the old token forgotten")
        pair = [c for c in FakeDeepTENS.calls if c[0].endswith("/pair")]
        self.assertEqual(len(pair), 2, "a new code after the kit was removed")


if __name__ == "__main__":
    unittest.main()
