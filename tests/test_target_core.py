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


class ServerSide(unittest.TestCase):
    """fetserver/targetproto.py on its own: building commands and checking replies."""
    def test_make_command(self):
        from fetserver.targetproto import make_command
        line = make_command(7, 3, "set", {"led_pct": 80}, KEY)
        head, _, tag = line.rstrip("\n").rpartition(" h=")
        self.assertEqual(head, "FT1 C b=7 i=3 set led_pct=80")
        self.assertEqual(tag, mac(KEY, head.encode()))
        for verb, settings in (("Paper", None), ("pa per", None), ("set", {"led pct": 1}), ("set", {"led_pct": True}),
                               ("set", {"led_pct": "80"}), ("set", {"led_pct": 1.5})):
            with self.assertRaises(ProtoError, msg=(verb, settings)):
                make_command(7, 1, verb, settings, KEY)

    def test_parse_reply(self):
        from fetserver.targetproto import parse_reply

        def signed(head, key=KEY):
            return f"{head} h={mac(key, head.encode())}"
        r = parse_reply(signed("FT1 R b=7 i=3 r=ok k=0a1b2c3d led_pct=80"), KEY)
        self.assertEqual((r["ok"], r["reason"], r["values"], r["authenticated"]),
                         (True, "", {"k": "0a1b2c3d", "led_pct": 80}, True))
        r = parse_reply(signed("FT1 R b=7 i=4 r=out_of_range:wait_ms"), KEY)
        self.assertEqual((r["ok"], r["reason"]), (False, "out_of_range:wait_ms"))
        self.assertFalse(parse_reply("FT1 R b=7 i=0 r=not_provisioned h=-", KEY)["authenticated"])
        self.assertFalse(parse_reply(signed("FT1 R b=7 i=3 r=ok"))["authenticated"], "no key given: not checked")
        for bad in (signed("FT1 C b=7 i=3 r=ok"), signed("FT1 R i=3 b=7 r=ok"), signed("FT1 R b=7 i=3"),
                    signed("FT1 R b=7 i=3 r=ok", bytes(32)), signed("FT1 R b=7 i=3 r=ok").replace("i=3", "i=4"),
                    "FT1 R b=7 i=3 r=ok"):
            with self.assertRaises(ProtoError, msg=bad):
                parse_reply(bad, KEY)


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


DEFAULTS = [0, 0, 0, 0, 50, 2, 50, 0, 5, 0]
KEYS = ["paper_ms", "step_count", "step_ms", "paper_eco_mm", "led_pct", "wait_ms", "ring_ms", "face", "face_alone_ms", "vref_mv"]
BASE, SLOT = 64, 8 + 4 * 10 + 4


def slot_bytes(values, gen, version=1, count=10):
    """A settings slot as firmware/target/core/src/settings.cpp writes it."""
    import struct
    import zlib
    b = struct.pack("<HBBHxx", 0x4654, version, count, gen) + struct.pack("<%di" % len(values), *values)
    return b + struct.pack("<I", zlib.crc32(b))


def settings_id(values):
    import struct
    import zlib
    return "%08x" % zlib.crc32(struct.pack("<%di" % len(values), *values))


class SettingsStore(Case):
    def load(self):
        r = self.h.run("sload").split()
        return r[0], int(r[1]), [int(v) for v in r[2:]]

    def test_defaults_on_a_blank_chip(self):
        self.assertEqual(self.load(), ("defaults", 0, DEFAULTS))
        self.assertEqual(self.h.run("sid"), settings_id(DEFAULTS), "the id is the CRC-32 of the values")

    def test_ranges_and_names(self):
        self.load()
        self.assertEqual(self.h.run("sset led_pct 101"), "out of range")
        self.assertEqual(self.h.run("sset wait_ms 0"), "out of range")
        self.assertEqual(self.h.run("sset nonsense 1"), "unknown setting")
        self.assertEqual(self.h.run("sseti 10 1"), "unknown setting", "index past the end")
        self.assertEqual(self.h.run("sseti -1 1"), "unknown setting")
        self.assertEqual(self.h.run("sget led_pct"), "50", "nothing changed")
        self.assertEqual(self.h.run("sset led_pct 100"), "ok")
        self.assertEqual(self.h.run("sset wait_ms 1"), "ok")
        self.assertEqual(self.h.run("sget led_pct"), "100")

    def test_saved_only_when_changed_and_survives_a_restart(self):
        self.load()
        self.assertEqual(self.h.run("ssave"), "saved 1 1", "first save on a blank chip")
        self.assertEqual(self.h.run("ssave"), "saved 1 1", "nothing changed: no write")
        self.h.run("sset led_pct 50")
        self.assertEqual(self.h.run("sdirty"), "0", "same value: not a change")
        self.h.run("sset led_pct 80")
        self.assertEqual(self.h.run("ssave"), "saved 2 2")
        self.h.run("reboot")
        state, gen, vals = self.load()
        self.assertEqual((state, gen, vals[4]), ("loaded", 2, 80))
        self.assertEqual(self.h.run("ssave"), "saved 2 2", "loaded and unchanged: no write")
        self.assertEqual(self.h.run("sid"), settings_id(vals))

    def test_reloading_discards_unsaved_changes(self):
        self.load()
        self.h.run("ssave")
        self.h.run("sset led_pct 90")
        self.assertEqual(self.load()[2][4], 50)
        self.assertEqual(self.h.run("ssave"), "saved 1 1", "nothing to write after reloading")

    def test_alternate_slots(self):
        self.load()
        for led in (10, 20, 30):
            self.h.run("sset led_pct %d" % led)
            self.h.run("ssave")
        self.h.run("reboot")
        self.assertEqual(self.load()[2][4], 30)
        self.h.run("nvmset %d %s" % (BASE, "00" * SLOT))           # wipe slot 0: slot 1 still has a good copy
        self.h.run("reboot")
        state, gen, vals = self.load()
        self.assertEqual(state, "loaded")
        self.assertIn(vals[4], (20, 30))

    def test_a_save_torn_by_a_power_cut_keeps_the_previous_settings(self):
        self.load()
        self.h.run("sset led_pct 70")
        self.h.run("ssave")
        for cut in (0, 1, 10, SLOT - 1):
            self.h.run("sset led_pct %d" % (71 + cut % 20))
            self.h.run("nvmtear %d" % cut)
            self.assertTrue(self.h.run("ssave").startswith("failed"))
            self.h.run("reboot")
            self.assertEqual(self.load()[2][4], 70, cut)

    def test_a_failed_write_is_reported_and_retried(self):
        self.load()
        self.h.run("sset led_pct 60")
        self.h.run("nvmfail 1")
        self.assertTrue(self.h.run("ssave").startswith("failed"))
        self.assertEqual(self.h.run("sdirty"), "1", "still to be saved")
        self.assertTrue(self.h.run("ssave").startswith("saved"))
        self.h.run("reboot")
        self.assertEqual(self.load()[2][4], 60)

    def test_damaged_or_foreign_slots_are_not_used(self):
        good = list(DEFAULTS)
        good[4] = 33
        self.h.run("nvmset %d %s" % (BASE, slot_bytes(good, 5).hex()))
        newer = list(DEFAULTS)
        newer[4] = 44
        for bad in (slot_bytes(newer, 6)[:-1] + b"\x00",               # CRC wrong
                    slot_bytes(newer, 6, version=2),                    # another layout
                    slot_bytes(newer[:-1], 6, count=9),                 # fewer settings
                    slot_bytes(newer, 6, count=9),                      # says 9, has 10: not this layout
                    slot_bytes(newer[:4] + [101] + newer[5:], 6)):      # a value this firmware does not accept
            self.h.run("nvmset %d %s" % (BASE + SLOT, bad.hex().ljust(2 * SLOT, "f")))
            self.h.run("reboot")
            self.assertEqual(self.load(), ("loaded", 5, good), bad.hex()[:20])
        self.h.run("nvmset %d %s" % (BASE + SLOT, slot_bytes(newer, 6).hex()))
        self.h.run("reboot")
        self.assertEqual(self.load()[2][4], 44, "a good newer slot wins")

    def test_generation_wraps(self):
        a = list(DEFAULTS)
        a[4] = 1
        b = list(DEFAULTS)
        b[4] = 2
        self.h.run("nvmset %d %s" % (BASE, slot_bytes(a, 0xFFFF).hex()))
        self.h.run("nvmset %d %s" % (BASE + SLOT, slot_bytes(b, 0x0001).hex()))
        self.h.run("reboot")
        self.assertEqual(self.load()[1:], (1, b), "0x0001 is newer than 0xFFFF")
        self.h.run("sset led_pct 3")
        self.h.run("ssave")
        self.h.run("reboot")
        self.assertEqual(self.load()[2][4], 3, "and the next save goes to the older slot")

    def test_names_match_the_list(self):
        for k, d in zip(KEYS, DEFAULTS):
            self.assertEqual(self.h.run("sget " + k), str(d), k)


class CommandChannel(Case):
    def setUp(self):
        super().setUp()
        self.h.run("sload")

    def send(self, line):
        return self.h.run("cmd " + line.rstrip("\n"))

    def cmd(self, cmd_id, verb, settings=None, boot=7, key=KEY):
        from fetserver.targetproto import make_command, parse_reply
        r = self.send(make_command(boot, cmd_id, verb, settings, key))
        return parse_reply(r, KEY)

    def test_refused_until_a_key_is_set(self):
        from fetserver.targetproto import make_command, parse_reply
        r = parse_reply(self.send(make_command(7, 1, "paper", key=KEY)), KEY)
        self.assertEqual((r["ok"], r["reason"], r["authenticated"]), (False, "not_provisioned", False))
        self.assertEqual(self.h.run("acts"), "0 0")

    def test_get_status_paper(self):
        self.h.run("ckey " + KEY.hex())
        r = self.cmd(1, "get")
        self.assertTrue(r["ok"] and r["authenticated"])
        self.assertEqual([r["values"][k] for k in KEYS], DEFAULTS)
        self.assertEqual(r["values"]["k"], settings_id(DEFAULTS))
        s = self.cmd(2, "status")
        self.assertEqual((s["values"]["up"], s["values"]["d"]), (123456, 2))
        self.assertTrue(self.cmd(3, "paper")["ok"])
        self.assertEqual(self.h.run("acts"), "1 0")

    def test_set_is_checked_saved_and_survives_a_restart(self):
        self.h.run("ckey " + KEY.hex())
        r = self.cmd(1, "set", {"led_pct": 80, "paper_ms": 400})
        self.assertTrue(r["ok"])
        vals = list(DEFAULTS)
        vals[4], vals[0] = 80, 400
        self.assertEqual(r["values"]["k"], settings_id(vals), "the reply names the new settings id")
        self.assertEqual(self.h.run("acts"), "0 1")
        self.h.run("reboot")
        self.assertEqual(self.h.run("sget led_pct"), "80")

    def test_set_is_all_or_nothing(self):
        self.h.run("ckey " + KEY.hex())
        r = self.cmd(1, "set", {"led_pct": 80, "nonsense": 1})
        self.assertEqual(r["reason"], "unknown_setting:nonsense")
        r = self.cmd(2, "set", {"led_pct": 80, "wait_ms": 99})
        self.assertEqual(r["reason"], "out_of_range:wait_ms")
        self.assertEqual(self.h.run("sget led_pct"), "50", "nothing applied")
        self.assertEqual(self.h.run("acts"), "0 0")

    def test_a_failed_save_is_reported(self):
        self.h.run("ckey " + KEY.hex())
        self.h.run("nvmfail 1")
        r = self.cmd(1, "set", {"led_pct": 81})
        self.assertEqual(r["reason"], "save_failed")
        self.assertEqual(self.h.run("sget led_pct"), "81", "in force now, saved at the next successful save")

    def test_forged_commands(self):
        from fetserver.targetproto import make_command, parse_reply
        self.h.run("ckey " + KEY.hex())
        good = make_command(7, 5, "set", {"led_pct": 90}, KEY)
        g = good.rstrip("\n")
        last = "0" if g[-1] != "0" else "1"
        for bad in (good.replace("led_pct=90", "led_pct=10"), make_command(7, 5, "set", {"led_pct": 90}, bytes(32)),
                    g + "0", g[:-1] + last,
                    good.rstrip("\n").rsplit(" h=", 1)[0] + " h=-", good.rstrip("\n")[:-1],
                    good.rstrip("\n").rsplit(" h=", 1)[0]):
            r = parse_reply(self.send(bad), KEY)
            self.assertEqual(r["reason"], "bad_signature", bad)
            self.assertEqual(r["id"], 0, "nothing of a forged line is believed")
        self.assertEqual(self.h.run("sget led_pct"), "50")
        self.assertTrue(parse_reply(self.send(good), KEY)["ok"], "a forgery uses up no ids")

    def test_replays(self):
        from fetserver.targetproto import make_command, parse_reply
        self.h.run("ckey " + KEY.hex())
        line = make_command(7, 5, "paper", key=KEY)
        self.assertTrue(parse_reply(self.send(line), KEY)["ok"])
        self.assertEqual(parse_reply(self.send(line), KEY)["reason"], "old_command")
        self.assertEqual(self.cmd(4, "paper")["reason"], "old_command", "a lower id")
        self.assertEqual(self.cmd(6, "set", {"wait_ms": 99})["reason"], "out_of_range:wait_ms")
        self.assertEqual(self.cmd(6, "paper")["reason"], "old_command", "a failed command used its id too")
        self.h.run("reboot")
        self.assertEqual(parse_reply(self.send(make_command(7, 50, "paper", key=KEY)), KEY)["reason"], "wrong_boot")
        self.assertTrue(self.cmd(1, "paper", boot=8)["ok"], "the new boot starts over")
        self.assertEqual(self.h.run("acts"), "2 0")

    def test_malformed_and_unknown(self):
        from fetserver.targetproto import mac, parse_reply
        self.h.run("ckey " + KEY.hex())

        def signed(head):
            return parse_reply(self.send(f"{head} h={mac(KEY, head.encode())}"), KEY)["reason"]
        self.assertEqual(signed("FT1 C b=7 i=1 dance"), "unknown_verb")
        for head in ("FT1 C i=2 b=7 get", "FT1 C b=x i=3 get", "FT1 C b=7 i=4", "FT1 C b=7  i=5 get",
                     "FT1 C b=7 i=99999999999 get", "FT1 C b=-7 i=6 get", "FT1 C b=7 i=4294967296 get",
                     "FT1 C b=7 i=20 get "):
            self.assertEqual(signed(head), "malformed", head)
        for head in ("FT1 C b=7 i=10 set", "FT1 C b=7 i=11 get extra", "FT1 C b=7 i=12 paper now"):
            self.assertEqual(signed(head), "unknown_verb", head)
        for head in ("FT1 C b=7 i=13 set led_pct", "FT1 C b=7 i=14 set =5", "FT1 C b=7 i=15 set led_pct=5x",
                     "FT1 C b=7 i=16 set led_pct=99999999999", "FT1 C b=7 i=18 set led_pct=",
                     "FT1 C b=7 i=19 set led_pct=-4294967246", "FT1 C b=7 i=21 set led_pct=--5"):
            self.assertEqual(signed(head), "malformed", head)
        self.assertEqual(signed("FT1 C b=7 i=30 set wait_ms=-1"), "out_of_range:wait_ms")
        self.assertEqual(self.h.run("sget led_pct"), "50")

    def test_overlong_line_handed_in_directly(self):
        self.h.run("ckey " + KEY.hex())
        from fetserver.targetproto import parse_reply
        r = parse_reply(self.send("FT1 C b=7 i=1 get " + "x" * 250 + " h=0123456789abcdef"), KEY)
        self.assertEqual(r["reason"], "malformed")

    def test_line_reader_edges(self):
        self.assertEqual(self.h.run("rfeed \\n"), "0 []", "an empty line is no line")
        self.assertEqual(self.h.run("rfeed abc\\n"), "1 [abc]")
        self.assertEqual(self.h.run("rfeed def\\n"), "1 [abc]", "not taken yet: kept, nothing appended")
        self.h.run("rnext")
        self.assertEqual(self.h.run("rfeed def\\n"), "1 [def]")

    def test_not_ours(self):
        for line in ("hello", "FT1 S b=1", "FT1 R b=7 i=1 r=ok h=-", "FT1 C"):
            self.assertEqual(self.send(line), "none", line)

    def test_line_reader(self):
        from fetserver.targetproto import make_command, parse_reply
        self.h.run("ckey " + KEY.hex())
        a = make_command(7, 1, "paper", key=KEY).rstrip("\n")
        b = make_command(7, 2, "status", key=KEY).rstrip("\n")
        out = self.h.run("feed " + a + "\\r\\n" + b + "\\n").split("\n")
        self.assertEqual([parse_reply(r, KEY)["id"] for r in out], [1, 2])
        long = make_command(7, 3, "paper", key=KEY).rstrip("\n") + " " + "x" * 200
        self.assertEqual(self.h.run("feed " + long + "\\n"), "none", "an overlong line is dropped whole")
        c = make_command(7, 4, "paper", key=KEY).rstrip("\n")
        self.assertEqual(parse_reply(self.h.run("feed " + c + "\\n"), KEY)["id"], 4, "and the next one works")
        self.assertEqual(self.h.run("acts"), "2 0")

    def test_reply_buffer_too_small(self):
        self.h.run("ckey " + KEY.hex())
        self.h.run("replycap 60")
        from fetserver.targetproto import make_command
        self.assertEqual(self.send(make_command(7, 1, "get", key=KEY)), "none", "never a cut-off reply")
