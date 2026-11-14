"""FeT Display's setup portal (firmware/display/lib/portal.py, config.py) run under MicroPython on this computer, on
local ports: DNS answers every name with the display's address; the setup page; captive-portal checks redirected;
settings checked before saving; percent-encoded passwords decoded. Skipped without `micropython`. The real WiFi
network on the CYD is checked by hand (docs/PLAN-cyd.md)."""
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MP = shutil.which("micropython")
RUNNER = r"""
import sys, json
sys.path.insert(0, %r)
import portal
p = portal.Portal("FeT-Display-7e08f8", current=%s, scan=lambda: ["Club WiFi", "Home <b>"])
got = p.serve(http_port=%d, dns_port=%d, bind="127.0.0.1")
print("SAVED " + json.dumps(got))
"""


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@unittest.skipUnless(MP, "micropython not installed (brew install micropython)")
class Portal(unittest.TestCase):
    def start(self, current=None):
        self.http, self.dns = free_port(), free_port(socket.SOCK_DGRAM)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = os.path.join(tmp.name, "run.py")
        Path(script).write_text(RUNNER % (str(ROOT / "firmware/display/lib"), repr(current), self.http, self.dns))
        self.proc = subprocess.Popen([MP, script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.http), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        self.fail("the portal did not start: " + self.proc.stderr.read())

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.communicate()                     # closes its pipes

    def req(self, method, path, body=None, host="192.168.4.1"):
        s = socket.create_connection(("127.0.0.1", self.http), timeout=5)
        data = body.encode() if body else b""
        s.sendall(("%s %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: %d\r\n\r\n"
                   % (method, path, host, len(data))).encode() + data)
        out = b""
        while True:
            c = s.recv(4096)
            if not c:
                break
            out += c
        s.close()
        head, _, body = out.partition(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        return int(lines[0].split()[1]), {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}, body.decode()

    def dns_query(self, name, qtype=1):
        q = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0) + b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack(">HH", qtype, 1)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.sendto(q, ("127.0.0.1", self.dns))
        a, _ = s.recvfrom(512)
        s.close()
        return a

    def test_dns_sends_every_name_to_the_display(self):
        self.start()
        a = self.dns_query("connectivitycheck.gstatic.com")
        self.assertEqual(a[:2], b"\x12\x34", "the query's id")
        self.assertEqual(struct.unpack(">H", a[6:8])[0], 1, "one answer")
        self.assertEqual(a[-4:], bytes([192, 168, 4, 1]))
        a6 = self.dns_query("captive.apple.com", qtype=28)
        self.assertEqual(struct.unpack(">H", a6[6:8])[0], 0, "no IPv6 address")

    def test_the_page_and_captive_checks(self):
        self.start()
        st, _, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("FeT Display setup", body)
        self.assertIn('<option value="Club WiFi">', body)
        self.assertIn("Home &lt;b&gt;", body, "network names escaped")
        self.assertNotIn("Home <b>", body)
        for path, host in (("/generate_204", "192.168.4.1"), ("/hotspot-detect.html", "captive.apple.com"), ("/", "example.com"), ("/x", "192.168.4.1")):
            st, hdr, _ = self.req("GET", path, host=host)
            self.assertEqual((st, hdr.get("location")), (302, "http://192.168.4.1/"), (path, host))

    def test_settings_checked_then_saved(self):
        self.start()
        bad = {"ssid": "Club", "password": "short", "server": "192.168.1.20:8080"}
        for f, why in ((bad, "8 to 63"), (dict(bad, password="longenough", server="range.local"), "IP address"),
                       (dict(bad, password="longenough", server="192.168.1.20:99999"), "1 to 65535"),
                       (dict(bad, ssid="", password="longenough"), "1 to 32")):
            st, _, body = self.req("POST", "/save", urllib.parse.urlencode(f))
            self.assertEqual(st, 200)
            self.assertIn(why, body)
            self.assertIn("class=err", body)
        pw = "p@ss w&rd+%é"
        st, _, body = self.req("POST", "/save", urllib.parse.urlencode({"ssid": "Club WiFi", "password": pw, "server": "192.168.1.20"}))
        self.assertEqual(st, 200)
        self.assertIn("Saved", body)
        out, err = self.proc.communicate(timeout=5)
        saved = json.loads(out.split("SAVED ", 1)[1])
        self.assertEqual(saved, {"ssid": "Club WiFi", "password": pw, "server": "192.168.1.20:8080"}, err)

    def test_an_open_network_and_keeping_the_pairing(self):
        self.start({"ssid": "Club WiFi", "password": "longenough", "server": "192.168.1.20:8080", "token": "fetd_x"})
        st, _, body = self.req("GET", "/")
        self.assertIn('value="Club WiFi"', body)
        self.assertIn('placeholder="unchanged"', body)
        self.assertNotIn("longenough", body, "the password is never shown")
        self.req("POST", "/save", urllib.parse.urlencode({"ssid": "Club WiFi", "password": "", "server": "192.168.1.20:8080"}))
        out, _ = self.proc.communicate(timeout=5)
        saved = json.loads(out.split("SAVED ", 1)[1])
        self.assertEqual((saved["password"], saved.get("token")), ("longenough", "fetd_x"), "empty: unchanged; same FeT Monitor: still paired")

    def test_a_new_fet_monitor_forgets_the_pairing(self):
        self.start({"ssid": "Club WiFi", "password": "longenough", "server": "192.168.1.20:8080", "token": "fetd_x"})
        self.req("POST", "/save", urllib.parse.urlencode({"ssid": "Guest", "open": "1", "server": "10.0.0.5:8080"}))
        out, _ = self.proc.communicate(timeout=5)
        saved = json.loads(out.split("SAVED ", 1)[1])
        self.assertEqual(saved, {"ssid": "Guest", "password": "", "server": "10.0.0.5:8080"})


if __name__ == "__main__":
    unittest.main()
