"""Runs the real firmware core (C++) on this machine against the real server.
Skipped if g++ is not installed. Faults (power cuts, torn flash writes, lost replies, outages) are injected by the harness."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

from fetserver.core import Core
from fetserver.db import Database
from fetserver.server import make_server
from fetserver.simtarget import ECHO_SAMPLE, miss_line, shot_line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
TOKEN = "fw-token"
_BIN = {}


def build():
    if "path" in _BIN:
        return _BIN["path"]
    gpp = shutil.which("g++")
    if not gpp:
        _BIN["path"] = None
        return None
    out = os.path.join(tempfile.gettempdir(), "fet_host_harness_%d" % os.getpid())
    src = [os.path.join(FW, "test", "host_harness.cpp")] + [
        os.path.join(FW, "fet_bridge", "src", "core", f)
        for f in sorted(os.listdir(os.path.join(FW, "fet_bridge", "src", "core"))) if f.endswith(".cpp")]
    subprocess.run([gpp, "-std=c++14", "-O1", "-Wall", "-Wextra", "-Werror", "-o", out] + src, check=True)
    _BIN["path"] = out
    return out


def esc(s):
    return s.replace("\\", "\\\\").replace("\n", "\\n")


class Harness:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def run(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        out = []
        while True:
            l = self.p.stdout.readline()
            if l == "":
                raise RuntimeError("harness died on: " + line)
            l = l.rstrip("\n")
            if l == ".":
                return "\n".join(out)
            out.append(l)

    def close(self):
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
        except Exception:
            pass
        self.p.wait(timeout=5)
        for f in (self.p.stdin, self.p.stdout):
            try:
                f.close()
            except Exception:
                pass

    def flags(self):
        r, p, req, boot, q = map(int, self.run("flags").split())
        return dict(reboots=r, portals=p, requests=req, boot=boot, queued=q)


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class FirmwareHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = Core(Database(os.path.join(self.tmp.name, "t.db")), heartbeat_interval_ms=5000)
        self.server = make_server(self.core, TOKEN, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        self.h = Harness(build())
        self.addCleanup(self.h.close)
        self.h.run("cfg %d %s" % (self.port, TOKEN))
        self.h.run("boot")
        self.tick(100)
        self.core.handle_heartbeat({"device_id": "fet-test01", "boot_count": 0, "uptime_ms": 0})

    # helpers
    def feed(self, text):
        self.h.run("uart " + esc(text) + "\\n")

    def tick(self, ms):
        self.h.run("tick %d" % ms)

    def events(self, etype=None):
        c = self.core.db.conn()
        if True:
            q = "SELECT boot_count, seq, type, raw FROM events WHERE device_id='fet-test01'"
            if etype:
                q += " AND type=?"
                return [tuple(r) for r in c.execute(q + " ORDER BY id", (etype,)).fetchall()]
            return [tuple(r) for r in c.execute(q + " ORDER BY id").fetchall()]

    def shots(self):
        return [e for e in self.events() if e[2] == "shot"]

    def shot(self, n, x=1.0, y=2.0):
        return shot_line(n, x, y, (100 + n, 200, 300, 400), 1.5)

    # ---- basics
    def test_shot_miss_echo_banner_noise(self):
        self.feed("+++")
        self.feed("AT")
        self.feed('freETarget "4.3.0 December 21, 2023"')
        self.feed(self.shot(1))
        self.feed(miss_line(2))
        self.feed(ECHO_SAMPLE)
        self.feed("garbage line")
        self.tick(3000)
        types = [e[2] for e in self.events()]
        self.assertEqual(types, ["arduino_boot", "shot", "shot", "config"])
        self.assertEqual(self.h.flags()["queued"], 0)
        c = self.core.db.conn()
        if True:
            n = c.execute("SELECT COUNT(*) FROM shots").fetchone()[0]
            ver = c.execute("SELECT arduino_version FROM targets").fetchone()[0]
        self.assertEqual(n, 2)
        self.assertTrue(ver and "4.3.0" in ver, ver)

    def test_never_answers_at(self):
        self.feed("+++")
        self.feed("AT")
        self.tick(2000)
        self.assertEqual(self.h.run("uartlog"), "")

    def test_each_event_once_in_order(self):
        for i in range(1, 31):
            self.feed(self.shot(i))
        self.tick(5000)
        s = self.shots()
        self.assertEqual(len(s), 30)
        self.assertEqual([(e[0], e[1]) for e in s], sorted(set((e[0], e[1]) for e in s)))
        self.assertEqual(self.h.flags()["queued"], 0)

    # ---- outages and power cycles
    def test_outage_then_recovery_delivers_everything(self):
        self.h.run("net down")
        for i in range(1, 21):
            self.feed(self.shot(i))
            self.tick(500)
        self.assertEqual(self.shots(), [])
        self.assertEqual(self.h.flags()["queued"], 20)
        self.h.run("net up")
        self.tick(60000)
        self.assertEqual(len(self.shots()), 20)
        self.assertEqual(self.h.flags()["queued"], 0)

    def test_power_cycle_with_unsent_shots(self):
        first = self.h.flags()["boot"]
        self.h.run("net down")
        for i in range(1, 6):
            self.feed(self.shot(i))
        self.h.run("boot")                       # power cycle: RAM lost, flash kept
        self.assertEqual(self.h.flags()["queued"], 5)
        self.assertEqual(self.h.flags()["boot"], first + 1)
        for i in range(6, 9):
            self.feed(self.shot(i))
        self.h.run("boot")
        for i in range(9, 12):
            self.feed(self.shot(i))
        self.h.run("net up")
        self.tick(60000)
        s = self.shots()
        self.assertEqual(len(s), 11)
        self.assertEqual(sorted({e[0] for e in s}), [first, first + 1, first + 2])        # three distinct boot counts
        self.assertEqual(len({(e[0], e[1]) for e in s}), 11)

    def test_ack_loss_never_duplicates(self):
        self.h.run("drop 60")
        for i in range(1, 41):
            self.feed(self.shot(i))
            self.tick(300)
        self.h.run("drop 0")
        self.tick(120000)
        s = self.shots()
        self.assertEqual(len(s), 40)
        self.assertEqual(len({(e[0], e[1]) for e in s}), 40)
        self.assertEqual(self.h.flags()["queued"], 0)

    def test_power_cut_between_send_and_ack(self):
        self.h.run("drop 100")                   # server stores, device never hears
        for i in range(1, 6):
            self.feed(self.shot(i))
        self.tick(3000)
        self.assertEqual(len(self.shots()), 5)   # server has them
        self.assertEqual(self.h.flags()["queued"], 5)
        self.h.run("boot")                       # power cut; they are resent
        self.h.run("drop 0")
        self.tick(30000)
        self.assertEqual(len(self.shots()), 5)   # still exactly 5
        self.assertEqual(self.h.flags()["queued"], 0)

    # ---- flash faults
    def test_torn_write_loses_at_most_that_event(self):
        self.h.run("net down")
        for i in range(1, 4):
            self.feed(self.shot(i))
        self.h.run("tear 20")                    # power cut part-way through writing shot 4
        self.feed(self.shot(4))
        self.h.run("boot")
        self.feed(self.shot(5))
        self.h.run("net up")
        self.tick(60000)
        got = self.shots()
        self.assertEqual(len(got), 4)            # shots 1,2,3 and 5; the torn one is gone, nothing else
        self.assertEqual(self.h.flags()["queued"], 0)

    def test_flash_write_failure_keeps_events_in_ram(self):
        self.h.run("failappends 1")
        for i in range(1, 6):
            self.feed(self.shot(i))
        self.tick(5000)
        self.assertEqual(len(self.shots()), 5)   # still delivered
        self.h.run("failappends 0")

    def test_overflow_drops_oldest_and_reports(self):
        self.h.run("cap 6000")
        self.h.run("boot")
        self.h.run("net down")
        for i in range(1, 61):
            self.feed(self.shot(i))
        flags = self.h.flags()
        self.assertLess(flags["queued"], 60)
        self.h.run("net up")
        self.tick(120000)
        got = len(self.shots())
        self.assertEqual(got, flags["queued"])
        c = self.core.db.conn()
        if True:
            ov = c.execute("SELECT overflow_count FROM targets").fetchone()[0]
        self.assertEqual(got + ov, 60)

    # ---- server answers
    def test_auth_error_is_reported_and_not_dropped(self):
        self.h.run("token wrong")
        self.feed(self.shot(1))
        self.tick(5000)
        self.assertEqual(self.shots(), [])
        self.assertEqual(self.h.flags()["queued"], 1)
        self.h.run("token " + TOKEN)
        self.tick(60000)
        self.assertEqual(len(self.shots()), 1)

    def test_poison_event_is_dropped_but_others_survive(self):
        self.h.run("fake 400")
        for i in range(1, 4):
            self.feed(self.shot(i))
        self.tick(60000)
        self.assertEqual(self.h.flags()["queued"], 0)      # all rejected one by one
        self.h.run("fake 0")
        self.feed(self.shot(9))
        self.tick(10000)
        self.assertEqual(len(self.shots()), 1)

    # ---- commands
    def test_serial_command_reaches_arduino_once(self):
        self.core.enqueue_command("fet-test01", "serial", '{"ECHO":0}')
        self.core.enqueue_command("fet-test01", "serial", '{"NEW":5}')
        self.tick(20000)
        self.assertEqual(self.h.run("uartlog"), '{"ECHO":0}\\n{"NEW":5}\\n')
        self.h.run("boot")                       # a reboot must not replay them
        self.tick(20000)
        self.assertEqual(self.h.run("uartlog"), '{"ECHO":0}\\n{"NEW":5}\\n')
        c = self.core.db.conn()
        if True:
            st = [r[0] for r in c.execute("SELECT status FROM commands ORDER BY id")]
        self.assertEqual(st, ["done", "done"])

    def test_command_runs_once_even_if_power_cut_before_result_is_reported(self):
        self.core.enqueue_command("fet-test01", "serial", '{"NEW":5}')
        for _ in range(400):                       # step until the Arduino has been sent the line
            self.tick(50)
            if self.h.run("uartlog"):
                break
        self.assertEqual(self.h.run("uartlog"), '{"NEW":5}\\n')
        self.h.run("net down")                     # the result never reaches the server...
        self.h.run("boot")                         # ...and power is cut: RAM (including the pending result) is gone
        self.h.run("net up")
        with self.core.db.write() as c:
            c.execute("UPDATE commands SET status='sent'")     # server still thinks it is outstanding
        self.tick(30000)
        self.assertEqual(self.h.run("uartlog"), '{"NEW":5}\\n')    # the UART log spans the reboot, so a resend would show twice
        with self.core.db.write() as c:
            st = [(r[0], r[1]) for r in c.execute("SELECT status, result FROM commands")]
        self.assertEqual(st, [("done", "already done")])

    def test_bad_serial_command_is_refused(self):
        # the server's allowlist refuses this, so plant it straight in the database: the device must refuse it too
        with self.core.db.write() as c:
            for line in ('{"EVIL":1}', '{"NEW":5}\n{"NEW":6}', 'x' * 100, '[1]'):
                c.execute("INSERT INTO commands(device_id,type,line,created_ms) VALUES('fet-test01','serial',?,0)", (line,))
        self.tick(20000)
        self.assertEqual(self.h.run("uartlog"), "")
        with self.core.db.write() as c:
            st = [r[0] for r in c.execute("SELECT status FROM commands")]
        self.assertEqual(st, ["failed"] * 4)

    def test_reboot_and_portal_commands(self):
        self.core.enqueue_command("fet-test01", "wifi_portal")
        self.tick(8000)
        self.assertEqual(self.h.flags()["portals"], 1)
        self.core.enqueue_command("fet-test01", "reboot")
        self.tick(8000)
        self.assertEqual(self.h.flags()["reboots"], 1)
        c = self.core.db.conn()
        if True:
            st = [r[0] for r in c.execute("SELECT status FROM commands ORDER BY id")]
        self.assertEqual(st, ["done", "done"])

    # ---- assembler / helpers
    def test_multiline_echo_split_across_chunks(self):
        for part in ECHO_SAMPLE.split("\n"):
            self.feed(part)
        self.tick(3000)
        self.assertEqual([e[2] for e in self.events()], ["config"])

    def test_oversize_and_unterminated_objects_do_not_wedge(self):
        self.h.run("uart {" + "a" * 6000)
        self.feed("}")
        self.feed(self.shot(1))
        self.tick(5000)
        self.assertEqual(len(self.shots()), 1)

    def test_status_json_is_valid_and_truthful(self):
        self.h.run("net down")
        self.feed(self.shot(1))
        self.tick(3000)
        st = json.loads(self.h.run("status"))
        self.assertEqual((st["device_id"], st["queued"], st["fs_ok"], st["boot_count"]), ("fet-test01", 1, True, self.h.flags()["boot"]))
        self.h.run("net up")
        self.tick(60000)
        st = json.loads(self.h.run("status"))
        self.assertEqual((st["queued"], st["acked"], st["uptime_s"] >= 60), (0, 1, True))

    def test_http_response_parsing(self):
        def p(raw):
            return self.h.run("httpparse " + raw.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n"))
        ok = "HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}"
        self.assertEqual(p(ok), 'OK 200 [{"ok":true}]')
        self.assertEqual(p(ok.replace("Content-Length", "content-length")), 'OK 200 [{"ok":true}]')
        self.assertEqual(p(ok[:-3]), "BAD")                                    # body cut short
        self.assertEqual(p("HTTP/1.1 200 OK\r\nContent-Len"), "BAD")           # headers cut short
        self.assertEqual(p("HTTP/1.1 401 Unauthorized\r\nContent-Length: 2\r\n\r\n{}"), "OK 401 [{}]")
        self.assertEqual(p("HTTP/1.0 200 OK\r\n\r\nabc"), "OK 200 [abc]")      # no length: read-to-close body
        self.assertEqual(p("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n"), "BAD")
        for bad in ("", "garbage", "HTTP/1.1 abc OK\r\n\r\n", "HTTP/1.1 900 X\r\n\r\n", "HTTP/1.1 200 OK\r\nContent-Length: x\r\n\r\n"):
            self.assertEqual(p(bad), "BAD", bad)

    def test_parses_a_real_server_response(self):
        import socket
        body = json.dumps({"device_id": "fet-test01", "boot_count": 1, "uptime_ms": 5, "events": []}).encode()
        s = socket.create_connection(("127.0.0.1", self.port))
        s.sendall(b"POST /api/v1/events HTTP/1.1\r\nHost: x\r\nX-FET-Token: " + TOKEN.encode() +
                  b"\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        raw = b""
        while True:
            d = s.recv(4096)
            if not d:
                break
            raw += d
        s.close()
        text = raw.decode()
        out = self.h.run("httpparse " + text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n"))
        self.assertTrue(out.startswith("OK 200 ["), out)

    def test_esp_layer_type_checks(self):
        r = subprocess.run([os.path.join(FW, "test", "syntax_check.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_boot_number_survives_normal_restarts_and_is_reseeded_when_lost(self):
        first = self.h.flags()["boot"]                       # setUp booted once
        self.assertGreaterEqual(first, 1000)                 # a fresh device starts from a random number, not 1
        self.h.run("boot")
        self.assertEqual(self.h.flags()["boot"], first + 1)  # normal restarts count up by one
        self.h.run("boot")
        self.assertEqual(self.h.flags()["boot"], first + 2)
        self.h.run("wipe")                                   # flash erased: the counter is gone
        self.h.run("boot")
        after = self.h.flags()["boot"]
        self.assertGreaterEqual(after, 1000)
        self.assertNotEqual(after, first + 3)                # not a continuation, not 1: a new random start
        self.assertNotEqual(after, 1)

    def test_events_of_a_wiped_device_do_not_collide_with_its_earlier_run(self):
        self.feed(self.shot(1))
        self.tick(3000)
        self.h.run("wipe")
        self.h.run("boot")
        self.feed(self.shot(2))
        self.tick(3000)
        s = self.shots()
        self.assertEqual(len(s), 2)                          # both stored; the second was not taken for a duplicate of the first
        self.assertEqual(len({e[0] for e in s}), 2)
        with self.core.db.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM event_conflicts").fetchone()[0]
        self.assertEqual(n, 0)

    def test_jparse(self):
        self.assertEqual(self.h.run('jparse {"a":1,"b":[1,2,{"c":"x"}]}'), "OK")
        self.assertEqual(self.h.run("jparse {nope"), "ERR")

    def test_config_validation(self):
        base = {"ssid": "club", "pass": "password1", "host": "10.0.0.5", "port": 8080, "token": "abc", "name": "Lane-1"}
        def check(**kw):
            d = dict(base); d.update(kw)
            return self.h.run("cfgcheck " + esc(json.dumps(d)))
        self.assertEqual(check(), "VALID")
        self.assertEqual(check(**{"pass": ""}), "VALID")
        for bad in ({"ssid": ""}, {"pass": "short"}, {"host": "http://x"}, {"host": "a/b"}, {"port": 0},
                    {"port": 70000}, {"token": "has space"}, {"name": "bad name"}, {"name": ""}):
            self.assertNotEqual(check(**bad), "VALID", bad)

    def test_config_fixed_ip(self):
        base = {"ssid": "club", "pass": "password1", "host": "10.0.0.5", "port": 8080, "token": "abc", "name": "Lane-1"}
        def check(**kw):
            d = dict(base); d.update(kw)
            return self.h.run("cfgcheck " + esc(json.dumps(d)))
        self.assertEqual(check(ip="192.168.1.60", gw="192.168.1.1"), "VALID")
        self.assertEqual(check(ip="192.168.1.60", gw="192.168.1.1", mask="255.255.255.0"), "VALID")
        self.assertEqual(check(ip="10.1.2.3", gw="10.1.0.1", mask="255.255.0.0"), "VALID")
        for bad in ({"ip": "192.168.1.60"},                                   # no gateway
                    {"ip": "192.168.1.60", "gw": "192.168.2.1"},              # different network (/24)
                    {"ip": "192.168.1.256", "gw": "192.168.1.1"},
                    {"ip": "192.168.001.60", "gw": "192.168.1.1"},             # leading zero
                    {"ip": "192.168.1", "gw": "192.168.1.1"},
                    {"ip": "192.168.1.60.1", "gw": "192.168.1.1"},
                    {"ip": "192.168.1.60", "gw": "192.168.1.1", "mask": "255.0.255.0"},   # not contiguous
                    {"ip": "192.168.1.60", "gw": "192.168.1.1", "mask": "0.0.0.0"},
                    {"gw": "192.168.1.1"}):                                    # gateway without ip
            self.assertNotEqual(check(**bad), "VALID", bad)

    def test_quick_boot_three_power_cycles(self):
        self.assertTrue(self.h.run("qboot").startswith("normal 1"))
        self.assertTrue(self.h.run("qboot").startswith("normal 2"))
        self.assertTrue(self.h.run("qboot").startswith("PORTAL"))
        self.assertTrue(self.h.run("qboot").startswith("normal 1"))      # counter was reset
        self.assertEqual(self.h.run("qtick 9000"), "0")                   # stable for 8 s clears it
        self.assertEqual(self.h.run("qtick 2000"), "1")                   # too soon: stays


if __name__ == "__main__":
    unittest.main()
