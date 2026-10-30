"""firmware/target/core (the new target firmware's platform-independent core), compiled for the PC: SHA-256/HMAC
against Python, the capture state machine with a fake counter board, and signed shot lines checked by
fetserver/targetproto.py (the server's side). Built as C++11 with warnings as errors, like the Mega's toolchain.
Mutation checks (2026-10-29): all killed except one equivalent: face() ignoring edges while ringing (re-arming
clears the face flag anyway; kept in case interrupts can preempt each other on the ESP32)."""
import hashlib
import hmac
import os
import random
import shutil
import subprocess
import tempfile
import unittest

from fetserver.targetproto import ProtoError, mac, parse_shot

HERE = os.path.dirname(os.path.abspath(__file__))
TGT = os.path.join(HERE, "..", "firmware", "target")
_BIN = {}
KEY = bytes(range(1, 33))


def build():
    if "path" not in _BIN:
        gpp = shutil.which("g++")
        if not gpp:
            _BIN["path"] = None
        else:
            out = os.path.join(tempfile.gettempdir(), "fet_target_harness_%d" % os.getpid())
            core = os.path.join(TGT, "core", "src")
            src = [os.path.join(TGT, "test", "host_harness.cpp")] + [
                os.path.join(core, f) for f in sorted(os.listdir(core)) if f.endswith(".cpp")]
            subprocess.run([gpp, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", "-o", out] + src, check=True)
            _BIN["path"] = out
    return _BIN["path"]


class Harness:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def run(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        out = []
        while True:
            line = self.p.stdout.readline()
            if line == "":
                raise RuntimeError("harness died")
            line = line.rstrip("\n")
            if line == ".":
                return "\n".join(out)
            out.append(line)

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)
        self.p.stdout.close()


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class Case(unittest.TestCase):
    def setUp(self):
        self.h = Harness(build())
        self.addCleanup(self.h.close)

    def tick(self, ms, us=None):
        self.h.run("tick %d %d" % (ms, ms * 1000 if us is None else us))

    def hw(self):
        return tuple(int(v) for v in self.h.run("hw").split())   # arms, clears, reads, run bits

    def pop(self):
        line = self.h.run("pop")
        return None if line == "none" else line


class Crypto(Case):
    def test_sha256(self):
        rng = random.Random(1)
        for n in (0, 3, 55, 56, 63, 64, 65, 119, 120, 1000):
            data = bytes(rng.randrange(256) for _ in range(n))
            self.assertEqual(self.h.run("sha " + data.hex()), hashlib.sha256(data).hexdigest(), n)

    def test_hmac(self):
        rng = random.Random(2)
        for klen, mlen in ((32, 0), (32, 100), (64, 63), (65, 64), (100, 200), (1, 1)):
            k = bytes(rng.randrange(256) for _ in range(klen))
            m = bytes(rng.randrange(256) for _ in range(mlen))
            self.assertEqual(self.h.run("hmac %s %s" % (k.hex(), m.hex() or "")),
                             hmac.new(k, m, hashlib.sha256).hexdigest(), (klen, mlen))


class Capture(Case):
    def test_all_four_at_once(self):
        self.h.run("counts 5123 4987 5401 5290")
        self.tick(9)
        self.assertIsNone(self.pop())
        self.h.run("run f")
        self.tick(10)
        s = parse_shot(self.pop())
        self.assertEqual((s["seq"], s["ms"], s["mask"], s["counts"]), (1, 10, 15, [5123, 4987, 5401, 5290]))
        self.assertEqual(self.hw()[2], 1, "read once")
        self.assertEqual(self.hw()[3], 0, "the RUN latches are cleared at once")

    def test_waits_for_the_others(self):
        self.h.run("counts 100 200 300 400")
        self.h.run("run 1")
        self.tick(10)
        self.h.run("run 3")
        self.tick(11)
        self.assertIsNone(self.pop(), "within the wait time, not all four yet")
        self.h.run("run f")
        self.tick(12)
        s = parse_shot(self.pop())
        self.assertEqual((s["ms"], s["mask"]), (10, 15), "the time of the first microphone")

    def test_a_missing_microphone(self):
        self.h.run("counts 100 200 300 0")
        self.h.run("run 7")
        self.tick(10)
        self.tick(11)
        self.assertIsNone(self.pop())
        self.tick(12)                                              # waitMs = 2
        s = parse_shot(self.pop())
        self.assertEqual((s["mask"], s["counts"], s["raw_counts"]), (7, [100, 200, 300, None], [100, 200, 300, 0]))

    def test_ringing_delays_rearming(self):
        self.h.run("run f")
        self.tick(10)
        self.assertIsNotNone(self.pop())
        arms = self.hw()[0]
        self.tick(30)
        self.h.run("run 2")                                        # an echo at 40 ms
        self.tick(40)
        self.assertEqual(self.hw()[3], 0, "echo cleared")
        self.tick(85)
        self.assertEqual(self.hw()[0], arms, "not re-armed: 50 ms of quiet not yet over")
        self.tick(90)
        self.assertEqual(self.hw()[0], arms + 1)
        self.assertIsNone(self.pop(), "the echo is not a shot")

    def test_queue_full_drops_and_counts(self):
        for i in range(10):
            self.h.run("counts %d 1 1 1" % (i + 1))
            self.h.run("run f")
            self.tick(100 * i + 10)
            self.tick(100 * i + 70)                                # quiet: re-armed
        got = []
        while True:
            line = self.pop()
            if line is None:
                break
            got.append(parse_shot(line))
        self.assertEqual([s["seq"] for s in got], list(range(1, 8)), "the first 7 kept, in order")
        self.assertEqual(self.h.run("dropped"), "3")
        self.assertEqual(got[-1]["dropped"], 3)

    def test_face_before_the_microphones(self):
        self.h.run("face 9300")
        self.h.run("run f")
        self.tick(10, 10000)
        s = parse_shot(self.pop())
        self.assertEqual(s["face_us"], -700)

    def test_first_face_edge_counts(self):
        self.h.run("face 9300")
        self.h.run("face 9800")                                    # the plate keeps ringing: later edges ignored
        self.h.run("run f")
        self.tick(10, 10000)
        self.assertEqual(parse_shot(self.pop())["face_us"], -700)

    def test_face_does_not_carry_over(self):
        self.h.run("face 9300")
        self.h.run("run f")
        self.tick(10, 10000)
        self.assertIsNotNone(parse_shot(self.pop())["face_us"])
        self.tick(70)                                              # quiet: re-armed
        self.h.run("run f")
        self.tick(100)
        self.assertIsNone(parse_shot(self.pop())["face_us"], "the next shot has no face edge of its own")

    def test_face_alone_is_a_shot(self):
        self.tick(20)
        self.h.run("face 20500")
        for ms in range(21, 25):
            self.tick(ms)
        self.assertIsNone(self.pop())
        before = self.hw()[0]
        self.tick(25, 25000)                                       # faceAloneMs = 5
        self.assertEqual(self.hw()[0], before + 1, "the counters are re-armed")
        s = parse_shot(self.pop())
        self.assertEqual((s["mask"], s["counts"], s["face_us"], s["ms"]), (0, [None] * 4, -4500, 20))
        self.h.run("run f")
        self.tick(30)
        self.assertIsNone(parse_shot(self.pop())["face_us"], "the face edge was used up")

    def test_face_while_ringing_is_ignored(self):
        self.h.run("run f")
        self.tick(10)
        self.pop()
        self.h.run("face 20000")
        for ms in range(20, 80, 5):
            self.tick(ms)
        self.assertIsNone(self.pop())
        self.h.run("run f")
        self.tick(100)
        self.assertIsNone(parse_shot(self.pop())["face_us"], "and does not stick to the next shot")


class Lines(Case):
    def shot(self):
        self.h.run("counts 5123 4987 5401 5290")
        self.h.run("run f")
        self.tick(10)
        return self.pop()

    def test_signed_and_checked(self):
        self.h.run("key " + KEY.hex())
        self.h.run("temp 196")
        line = self.shot()
        self.assertTrue(line.startswith("FT1 S b=7 q=1 t=10 n=5123 e=4987 s=5401 w=5290 m=f f=- c=196 k=1234abcd d=0 h="))
        s = parse_shot(line, KEY)
        self.assertEqual((s["authenticated"], s["temp_c"], s["settings_id"], s["boot"]), (True, 19.6, "1234abcd", 7))
        self.assertNotIn("{", line)
        self.assertEqual(parse_shot(line)["authenticated"], False, "no key here: not authenticated, not an error")

    def test_tampering_and_wrong_key(self):
        self.h.run("key " + KEY.hex())
        line = self.shot()
        with self.assertRaisesRegex(ProtoError, "signature"):
            parse_shot(line.replace("n=5123", "n=5124"), KEY)
        with self.assertRaisesRegex(ProtoError, "signature"):
            parse_shot(line, bytes(32))

    def test_no_key_yet(self):
        line = self.shot()
        self.assertTrue(line.endswith(" h=-"))
        self.assertIsNone(parse_shot(line)["temp_c"], "no temperature reading: c=-")
        self.assertIn(" c=- ", line)
        self.assertEqual(parse_shot(line, KEY)["authenticated"], False)

    def test_negative_numbers(self):
        self.h.run("temp -55")
        self.h.run("face 9000")
        self.h.run("run f")
        self.tick(10, 10000)
        s = parse_shot(self.pop())
        self.assertEqual((s["temp_c"], s["face_us"]), (-5.5, -1000))

    def test_longest_line_fits(self):
        self.h.run("key " + KEY.hex())
        self.h.run("temp -32767")
        self.h.run("counts 4294967295 4294967295 4294967295 4294967295")
        self.h.run("face 0")
        self.h.run("run f")
        self.tick(4294967, 2147483649)                             # face - stop = -2147483649 -> wraps to int32 max
        line = self.pop()
        self.assertLess(len(line) + 1, 160)
        self.assertTrue(parse_shot(line, KEY)["authenticated"])
        t = 4294967
        lines = []
        for cap in (500, None, None):                              # a reference line, then exactly at the edge
            if cap is None:
                cap = len(lines[0]) + 1 + (1 if len(lines) == 1 else 0)   # +1 newline; room for the zero, or not
            t += 100
            self.tick(t)                                           # quiet: re-armed
            self.h.run("linecap %d" % cap)
            self.h.run("run f")
            self.tick(t + 1)
            lines.append(self.pop())
        self.assertNotEqual(lines[1], "TOO LONG", "length + newline + zero fits")
        self.assertEqual(lines[2], "TOO LONG", "one byte less does not")
        self.h.run("linecap 40")
        self.tick(t + 200)
        self.h.run("counts 1 1 1 1")
        self.h.run("run f")
        self.tick(t + 300)
        self.assertEqual(self.pop(), "TOO LONG")

    def test_parser_rejects_malformed_lines(self):
        line = self.shot()
        for bad in ("FT1 X b=1 h=-", "", line.replace("FT1 S ", "FT1 X "), line.replace("FT1 S ", "FT2 S "), "FT1 S b=7 q=1 h=-", line.replace(" m=f", " m=10"),
                    line.replace(" k=1234abcd", " k=1234abc"), line.replace(" t=10", " t=1 0"),
                    line.replace(" q=1 t=10", " t=10 q=1"), line + " x=1", line.replace("h=-", "h=12"),
                    line.replace("n=5123", "n=five")):
            with self.assertRaises(ProtoError, msg=bad):
                parse_shot(bad, KEY)

    def test_python_mac_matches(self):
        self.h.run("key " + KEY.hex())
        line = self.shot()
        head, _, tag = line.rpartition(" h=")
        self.assertEqual(tag, mac(KEY, head.encode()))


if __name__ == "__main__":
    unittest.main()


def avr_core_installed():
    if not shutil.which("arduino-cli"):
        return False
    out = subprocess.run(["arduino-cli", "core", "list"], capture_output=True, text=True).stdout
    return any(line.split()[:1] == ["arduino:avr"] for line in out.splitlines())


@unittest.skipUnless(avr_core_installed(), "arduino-cli with the arduino:avr core not installed")
class Mega(unittest.TestCase):
    def test_core_builds_for_the_mega_and_fits(self):
        with tempfile.TemporaryDirectory() as out:
            r = subprocess.run(["arduino-cli", "compile", "-b", "arduino:avr:mega", "--library", os.path.join(TGT, "core"),
                                "--output-dir", out, os.path.join(TGT, "test", "avr_compile")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        ram = int(r.stdout.split("Global variables use ")[1].split()[0])
        self.assertLess(ram, 2048, "the core leaves the Mega most of its 8 KB")
