"""The first-time setup portal (fetsetup): the settings it accepts, the commands it gives the Pi (recorded, never
run), and its flow over HTTP. The WiFi network itself, the phones' captive-portal pop-up and nmcli on a real Pi are
verified on hardware only."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from fetsetup import config
from fetsetup.config import SetupError
from fetsetup.portal import Portal, SetupServer
from fetsetup.system import CATCH_ALL, System, SystemError_, split_terse, why

ZONES = ["America/Chicago", "Europe/Berlin", "UTC"]
COUNTRIES = ["DE", "US"]


def good(**over):
    raw = {"hostname": "Range", "country": "US", "timezone": "America/Chicago", "clock_ms": 1_800_000_000_000,
           "range_password": "range-pass", "range_password2": "range-pass", "director": "", "admin": "clubadmin",
           "admin_password": "admin-pass", "admin_password2": "admin-pass", "ssh": False,
           "network": {"mode": "cable", "address": "dhcp"}}
    raw.update(over)
    return raw


class Settings(unittest.TestCase):
    def test_a_good_setup(self):
        cfg = config.check(good(), COUNTRIES, ZONES)
        self.assertEqual((cfg["hostname"], cfg["ssh"], cfg["director"], cfg["network"]), ("range", False, None, {"mode": "cable", "address": "dhcp"}))
        self.assertTrue(config.check(good(ssh=True), COUNTRIES, ZONES)["ssh"])
        self.assertFalse(config.check(good(ssh="yes"), COUNTRIES, ZONES)["ssh"], "only a real true turns SSH on")

    def test_what_is_refused(self):
        bad = [({"hostname": "-range"}, "Pi name"), ({"hostname": "a" * 64}, "Pi name"), ({"hostname": "my range"}, "Pi name"),
               ({"country": "XX"}, "country"), ({"country": "us"}, "country"), ({"timezone": "Mars/Base"}, "time zone"),
               ({"clock_ms": 5}, "clock"), ({"clock_ms": True}, "clock"),
               ({"range_password": "short", "range_password2": "short"}, "at least 8"), ({"range_password2": "other-pass"}, "differ"),
               ({"director": "x"}, "match director"), ({"director": "md", "director_password": "short"}, "director password"),
               ({"admin": "root"}, "admin login"), ({"admin": "pi"}, "admin login"), ({"admin": "Admin"}, "admin login"),
               ({"admin": "fet-monitor"}, "admin login"), ({"admin_password": "short", "admin_password2": "short"}, "admin password"),
               ({"admin_password2": "nope-nope"}, "differ"), ({"network": {"mode": "lte"}}, "how the Pi connects"), ({"network": None}, "how the Pi connects")]
        for over, want in bad:
            with self.assertRaises(SetupError, msg=over) as cm:
                config.check(good(**over), COUNTRIES, ZONES)
            self.assertIn(want, str(cm.exception), over)
        with self.assertRaises(SetupError):
            config.check(None, COUNTRIES, ZONES)

    def test_networks(self):
        ok = lambda n: config.check(good(network=n), COUNTRIES, ZONES)["network"]
        self.assertEqual(ok({"mode": "wifi", "ssid": "Club", "psk": "12345678"}), {"mode": "wifi", "ssid": "Club", "psk": "12345678", "address": "dhcp"})
        own = ok({"mode": "own", "ssid": "Range", "psk": "range-wifi", "channel": 11})
        self.assertEqual((own["address"]["ip"], own["channel"]), (config.OWN_IP, 11))
        self.assertEqual(ok({"mode": "own", "ssid": "R", "psk": "range-wifi"})["channel"], 6)
        fixed = ok({"mode": "cable", "address": {"ip": "192.168.1.20", "prefix": 24, "gateway": "192.168.1.1", "dns": []}})
        self.assertEqual(fixed["address"], {"ip": "192.168.1.20", "prefix": 24, "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}, "DNS: the router")
        for n, want in (({"mode": "wifi", "ssid": "", "psk": "12345678"}, "choose the network"), ({"mode": "wifi", "ssid": "C", "psk": "1234567"}, "8 to 63"),
                        ({"mode": "wifi", "ssid": "x" * 33, "psk": "12345678"}, "choose the network"), ({"mode": "own", "ssid": "R", "psk": "pässwörd1"}, "no accents"),
                        ({"mode": "own", "ssid": "R", "psk": "range-wifi", "channel": 3}, "channel"), ({"mode": "own", "ssid": " R", "psk": "range-wifi"}, "1 to 32"),
                        ({"mode": "cable", "address": {"ip": "192.168.1.0", "prefix": 24, "gateway": "192.168.1.1"}}, "network's own address"),
                        ({"mode": "cable", "address": {"ip": "192.168.1.255", "prefix": 24, "gateway": "192.168.1.1"}}, "network's own address"),
                        ({"mode": "cable", "address": {"ip": "192.168.1.20", "prefix": 24, "gateway": "10.0.0.1"}}, "router"),
                        ({"mode": "cable", "address": {"ip": "192.168.1.20", "prefix": 24, "gateway": "192.168.1.20"}}, "router"),
                        ({"mode": "cable", "address": {"ip": "192.168.1.20", "prefix": 31, "gateway": "192.168.1.21"}}, "prefix"),
                        ({"mode": "cable", "address": {"ip": "nope", "prefix": 24, "gateway": "192.168.1.1"}}, "valid IPv4"),
                        ({"mode": "cable", "address": "static"}, "valid IPv4")):
            with self.assertRaises(SetupError, msg=n) as cm:
                ok(n)
            self.assertIn(want, str(cm.exception), n)
        self.assertEqual([config.wpa_psk_ok(p) for p in ("ab" * 32, "x" * 63, "g" * 64, "x" * 64, "1234567", "12345678")],
                         [True, True, False, False, False, True], "64 hex digits, or 8 to 63 characters")

    def test_plan_keeps_the_network_last_and_never_in_it(self):
        cfg = config.check(good(director="md", director_password="director-pass", ssh=True), COUNTRIES, ZONES)
        steps = [s for s, _ in config.plan(cfg)]
        self.assertEqual(steps, ["country", "timezone", "clock", "hostname", "admin", "range_password", "director", "ssh"])
        self.assertNotIn("clock", [s for s, _ in config.plan(dict(cfg, clock_ms=None))])
        self.assertEqual(config.server_address(cfg, "192.168.1.7"), "192.168.1.7")
        own = config.check(good(network={"mode": "own", "ssid": "R", "psk": "range-wifi"}), COUNTRIES, ZONES)
        self.assertEqual(config.server_address(own, "1.2.3.4"), config.OWN_IP, "own network: always its fixed address")


class Recorder:
    """Stands in for the programs: records every argv, answers from a table of (argv prefix -> (code, output))."""
    def __init__(self, answers=None):
        self.calls, self.answers = [], dict(answers or {})

    def __call__(self, argv, stdin=None, timeout=60.0):
        self.calls.append((list(argv), stdin))
        for prefix, ans in self.answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return ans(argv) if callable(ans) else ans
        return 0, ""

    def argvs(self, *prefix):
        return [a for a, _ in self.calls if tuple(a[:len(prefix)]) == prefix]


SHOW_UP = "GENERAL.STATE:100 (connected)\nIP4.ADDRESS[1]:192.168.1.57/24\nIP4.GATEWAY:192.168.1.1\nIP4.DNS[1]:192.168.1.1\n"


class Commands(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def sys(self, answers=None):
        self.rec = Recorder(answers)
        return System(self.rec, str(self.root))

    def test_terse_output_and_reasons(self):
        self.assertEqual(split_terse(r"My\:Net:72:WPA2"), ["My:Net", "72", "WPA2"])
        self.assertEqual(split_terse(r"a\\b::x"), ["a\\b", "", "x"])
        self.assertEqual(why("Error: Connection activation failed: (7) Secrets were required, but not provided."), "wrong WiFi password")
        self.assertIn("not found", why("Error: No network with SSID 'x' found."))
        self.assertIn("no address", why("Error: Connection activation failed: IP configuration could not be reserved"))

    def test_scan(self):
        out = "Club:70:WPA2\nClub:40:WPA2\nOpen\\:Cafe:90:\n:55:WPA2\nOther:12:WPA1 WPA2\n"
        s = self.sys({("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY"): (0, out)})
        self.assertEqual(s.scan(), [{"ssid": "Open:Cafe", "signal": 90, "secure": False}, {"ssid": "Club", "signal": 70, "secure": True},
                                    {"ssid": "Other", "signal": 12, "secure": True}], "strongest first, one per name, hidden ones dropped")

    def test_setup_network_is_open_and_answers_every_name(self):
        s = self.sys()
        s.hotspot_on("FeTMonitor-Setup-AB12")
        add = self.rec.argvs("nmcli", "connection", "add")[0]
        self.assertIn("ap", add)
        self.assertNotIn("wifi-sec.key-mgmt", add, "open")
        self.assertEqual(add[add.index("ipv4.addresses") + 1], "192.168.4.1/24")
        self.assertEqual((self.root / CATCH_ALL.lstrip("/")).read_text(), "address=/#/192.168.4.1\n")
        s.hotspot_off()
        self.assertFalse((self.root / CATCH_ALL.lstrip("/")).exists(), "the catch-all goes with the setup network")
        self.assertIn(["nmcli", "connection", "delete", "fet-setup"], self.rec.argvs("nmcli", "connection", "delete"))

    def test_trying_the_club_wifi(self):
        s = self.sys({("nmcli", "--wait"): (0, ""), ("nmcli", "-t", "-f", "GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS"): (0, SHOW_UP)})
        self.assertEqual(s.try_wifi("Club", "pass-word"), {"ok": True, "lease": {"ip": "192.168.1.57", "prefix": 24, "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}})
        self.assertEqual(self.rec.argvs("nmcli", "connection", "delete")[-1], ["nmcli", "connection", "delete", "fet-test"], "the trial is removed")
        add = self.rec.argvs("nmcli", "connection", "add")[0]
        self.assertEqual((add[add.index("ssid") + 1], add[add.index("wifi-sec.psk") + 1], add[add.index("autoconnect") + 1]), ("Club", "pass-word", "no"))
        s = self.sys({("nmcli", "--wait"): (4, "Error: Connection activation failed: (7) Secrets were required, but not provided.")})
        self.assertEqual(s.try_wifi("Club", "wrong-pw"), {"ok": False, "why": "wrong WiFi password"})
        s = self.sys({("nmcli", "--wait"): (0, ""), ("nmcli", "-t", "-f", "GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS"):
                      (0, "GENERAL.STATE:70 (connecting)\nIP4.ADDRESS[1]:192.168.1.57/24\n")})
        self.assertFalse(s.try_wifi("Club", "pass-word")["ok"], "an address while still connecting is not a connection")
        self.assertIn(["nmcli", "connection", "down", "fet-test"], self.rec.argvs("nmcli", "connection", "down"), "the trial is taken down")
        self.assertEqual(self.rec.argvs("nmcli", "connection", "delete").count(["nmcli", "connection", "delete", "fet-test"]), 2, "and removed, also after a failure")

    def test_the_final_network(self):
        listing = "Wired connection 1:802-3-ethernet\nHome:802-11-wireless\nfet-setup:802-11-wireless\n"
        s = self.sys({("nmcli", "-t", "-f", "NAME,TYPE"): (0, listing)})
        s.use_network({"mode": "own", "ssid": "Range", "psk": "range-wifi", "channel": 11, "address": {"ip": "10.42.0.1", "prefix": 24}})
        add = self.rec.argvs("nmcli", "connection", "add")[0]
        val = lambda k: add[add.index(k) + 1]
        self.assertEqual((val("con-name"), val("802-11-wireless.mode"), val("802-11-wireless.band"), val("802-11-wireless.channel"), val("wifi-sec.proto"),
                          val("wifi-sec.pairwise"), val("ipv4.method"), val("ipv4.addresses")),
                         ("fet-range", "ap", "bg", "11", "rsn", "ccmp", "shared", "10.42.0.1/24"), "WPA2-CCMP on 2.4 GHz for the ESP-01")
        self.assertEqual(self.rec.argvs("nmcli", "connection", "modify"), [["nmcli", "connection", "modify", "Home", "connection.autoconnect", "no"]],
                         "other saved WiFi networks stop taking the radio; ours and the cable are left alone")
        s = self.sys()
        s.use_network({"mode": "wifi", "ssid": "Club", "psk": "pass-word", "address": {"ip": "192.168.1.20", "prefix": 24, "gateway": "192.168.1.1", "dns": ["1.1.1.1", "8.8.8.8"]}})
        add = self.rec.argvs("nmcli", "connection", "add")[0]
        self.assertEqual((add[add.index("ipv4.method") + 1], add[add.index("ipv4.addresses") + 1], add[add.index("ipv4.dns") + 1]),
                         ("manual", "192.168.1.20/24", "1.1.1.1,8.8.8.8"))
        s = self.sys({("nmcli", "-t", "-f", "NAME,TYPE"): (0, listing)})
        s.use_network({"mode": "cable", "address": "dhcp"})
        add = self.rec.argvs("nmcli", "connection", "add")[0]
        self.assertEqual((add[add.index("con-name") + 1], add[add.index("ipv4.method") + 1]), ("fet-eth", "auto"))
        self.assertEqual(self.rec.argvs("nmcli", "connection", "modify"), [], "cable: WiFi settings untouched")

    def test_the_rest(self):
        (self.root / "etc").mkdir()
        (self.root / "etc/hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi\n")
        s = self.sys({("id", "-u"): (1, "id: 'clubadmin': no such user"), ("timedatectl", "show"): (0, "no\n")})
        s.set_hostname("range")
        self.assertEqual((self.root / "etc/hosts").read_text(), "127.0.0.1\tlocalhost\n127.0.1.1\trange\n")
        s.set_admin("clubadmin", "admin-pass")
        self.assertEqual(self.rec.argvs("useradd")[0][-1], "clubadmin")
        self.assertEqual(self.rec.calls[-1], (["chpasswd"], "clubadmin:admin-pass\n"), "the password on stdin, never on a command line")
        s.set_clock(1_800_000_000_500)
        self.assertEqual(self.rec.argvs("date")[0], ["date", "-u", "-s", "@1800000000"])
        s.set_ssh(False)
        self.assertEqual(self.rec.argvs("systemctl")[0], ["systemctl", "disable", "--now", "ssh"])
        s.set_range_password("range-pass")
        argv, stdin = self.rec.calls[-1]
        self.assertEqual((argv[:4], argv[-4:], stdin), (["runuser", "-u", "fet-monitor", "--"], ["set-password", "--password-stdin", "--data-dir", "/var/lib/fet-monitor"],
                                                         "range-pass\n"), "as the service user, the password on stdin")
        s.add_director("md", "director-pass")
        argv, stdin = self.rec.calls[-1]
        i = argv.index("match-user")
        self.assertEqual((argv[i:i + 5], stdin), (["match-user", "add", "md", "--role", "director"], "director-pass\n"))
        s = self.sys({("id", "-u"): (0, "1001\n"), ("timedatectl", "show"): (0, "yes\n")})
        s.set_admin("clubadmin", "admin-pass")
        self.assertEqual(self.rec.argvs("useradd"), [], "an existing login: only its password")
        s.set_clock(1_800_000_000_500)
        self.assertEqual(self.rec.argvs("date"), [], "a Pi with network time keeps it")
        s = self.sys({("raspi-config",): (127, "not installed")})
        s.set_country("DE")
        self.assertEqual(self.rec.argvs("iw")[0], ["iw", "reg", "set", "DE"])
        with self.assertRaises(SystemError_):
            self.sys({("timedatectl", "set-timezone"): (1, "Failed to set time zone")}).set_timezone("Mars/Base")


class FakeSystem:
    """The Pi, in memory: what the portal asks for and what it changed."""
    def __init__(self, cable=None, wifi=None, network_comes_up=True):
        self.cable, self.wifi, self.comes_up = cable, wifi or {}, network_comes_up
        self.log, self.ap, self.net = [], False, None

    def serial_suffix(self): return "AB12"
    def countries(self): return [("DE", "Germany"), ("US", "United States")]
    def zones(self): return ZONES
    def scan(self): return [{"ssid": s, "signal": 60, "secure": True} for s in self.wifi]
    def lease(self, ifname): return self.cable
    def hotspot_on(self, ssid): self.ap = True; self.log.append(("ap", True))
    def hotspot_off(self): self.ap = False; self.log.append(("ap", False))
    def device_token(self): return "tok-123"

    def try_wifi(self, ssid, psk):
        self.log.append(("try", ssid, self.ap))
        if self.wifi.get(ssid) == psk:
            return {"ok": True, "lease": {"ip": "192.168.1.57", "prefix": 24, "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}}
        return {"ok": False, "why": "wrong WiFi password"}

    def apply(self, step, arg):
        if step == "hostname" and arg == "boom":
            raise SystemError_("hostnamectl: failed")
        self.log.append(("apply", step))

    def use_network(self, net): self.net = net; self.log.append(("use", net["mode"], self.ap))
    def has_network(self): return self.comes_up


class Flow(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.done = Path(tmp.name) / "setup.done"

    def portal(self, **kw):
        p = Portal(FakeSystem(**kw), self.done, sleep=lambda s: None, background=False)
        p.start()
        return p

    def test_cable(self):
        p = self.portal(cable={"ip": "192.168.1.30", "prefix": 24, "gateway": "192.168.1.1", "dns": []})
        self.assertEqual((p.ssid, p.sys.ap), ("FeTMonitor-Setup-AB12", True))
        s = p.finish(good())
        self.assertEqual((s["address"], s["url"], s["token"], s["fixed"]), ("192.168.1.30", "http://192.168.1.30:8080/", "tok-123", False))
        self.assertEqual([x[1] for x in p.sys.log if x[0] == "apply"], ["country", "timezone", "clock", "hostname", "admin", "range_password", "ssh"])
        self.assertTrue(p.sys.ap, "the setup network stays until Switch")
        p.switch()
        self.assertEqual((p.phase, p.sys.net["mode"], p.stopped.is_set(), json.loads(self.done.read_text())["hostname"]), ("done", "cable", True, "range"))
        self.assertIn(("use", "cable", False), p.sys.log, "the setup network went first")
        with self.assertRaisesRegex(SetupError, "save the settings first"):
            p.switch()                                       # done: not again

    def test_cable_not_plugged_in(self):
        p = self.portal(cable=None)
        with self.assertRaisesRegex(SetupError, "plugged in"):
            p.finish(good())
        fixed = {"mode": "cable", "address": {"ip": "192.168.1.20", "prefix": 24, "gateway": "192.168.1.1"}}
        self.assertEqual(p.finish(good(network=fixed))["address"], "192.168.1.20", "a fixed address needs no live one")

    def test_club_wifi_must_be_tested_first(self):
        p = self.portal(wifi={"Club": "pass-word"})
        net = {"mode": "wifi", "ssid": "Club", "psk": "pass-word", "address": "dhcp"}
        with self.assertRaisesRegex(SetupError, "test the club WiFi"):
            p.finish(good(network=net))
        p.test_wifi("Club", "wrong-pw")
        self.assertEqual((p.phase, p.test["ok"], p.sys.ap), ("form", False, True), "a failed test: the setup network is back")
        self.assertEqual(p.info()["test"], {"ok": False, "why": "wrong WiFi password", "ssid": "Club"}, "the password is never sent back")
        with self.assertRaisesRegex(SetupError, "test the club WiFi"):
            p.finish(good(network=net))
        p.test_wifi("Club", "pass-word")
        self.assertIn(("try", "Club", False), p.sys.log, "the trial runs with the setup network down (one radio)")
        self.assertTrue(p.test["ok"] and p.sys.ap)
        with self.assertRaisesRegex(SetupError, "test the club WiFi"):
            p.finish(good(network=dict(net, psk="other-pw!")))
        self.assertEqual(p.finish(good(network=net))["address"], "192.168.1.57")
        with self.assertRaisesRegex(SetupError, "choose the club WiFi"):
            p.test_wifi("Club", "short")

    def test_own_network(self):
        p = self.portal()
        s = p.finish(good(network={"mode": "own", "ssid": "Range", "psk": "range-wifi"}))
        self.assertEqual((s["address"], s["ssid"], s["mode"]), ("10.42.0.1", "Range", "own"))

    def test_a_network_that_does_not_come_up(self):
        p = self.portal(cable={"ip": "192.168.1.30", "prefix": 24, "gateway": "192.168.1.1", "dns": []}, network_comes_up=False)
        p.finish(good())
        p.switch()
        self.assertEqual((p.phase, p.sys.ap, self.done.exists(), p.stopped.is_set()), ("saved", True, False, False))
        self.assertIn("setup network is back", p.error)

    def test_a_failing_step_is_reported_and_nothing_switches(self):
        p = self.portal(cable={"ip": "192.168.1.30", "prefix": 24, "gateway": "192.168.1.1", "dns": []})
        p.finish(good())
        p.saved = None
        with self.assertRaisesRegex(SetupError, "save the settings first"):
            p.switch()
        p.phase = "form"
        with self.assertRaisesRegex(SetupError, "could not apply"):
            p.finish(good(hostname="boom"))
        self.assertEqual(p.phase, "form")


class OverHttp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.portal = Portal(FakeSystem(cable={"ip": "192.168.1.30", "prefix": 24, "gateway": "192.168.1.1", "dns": []}),
                             Path(tmp.name) / "done", sleep=lambda s: None, background=False)
        self.portal.start()
        self.srv = SetupServer(("127.0.0.1", 0), self.portal)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, host="192.168.4.1", header=True):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        h = {"Host": host, "Content-Type": "application/json"}
        if header:
            h["X-FET-Setup"] = "1"
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        out = r.status, r.getheader("Location"), r.read()
        c.close()
        return out

    def test_captive_portal_checks_land_on_the_page(self):
        for host, path in (("captive.apple.com", "/hotspot-detect.html"), ("connectivitycheck.gstatic.com", "/generate_204"),
                           ("www.msftconnecttest.com", "/connecttest.txt"), ("example.com", "/")):
            st, loc, _ = self.req("GET", path, host=host)
            self.assertEqual((st, loc), (302, "http://192.168.4.1/"), host)
        st, _, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b'<main id="app">', body)
        st, _, body = self.req("GET", "/api/info")
        info = json.loads(body)
        self.assertEqual((st, info["phase"], info["setup_ssid"], info["cable"]["ip"]), (200, "form", "FeTMonitor-Setup-AB12", "192.168.1.30"))

    def test_saving_over_http(self):
        st, _, body = self.req("POST", "/api/finish", good(), header=False)
        self.assertEqual(st, 403, "only the setup page")
        st, _, body = self.req("POST", "/api/finish", good(), host="evil.example")
        self.assertEqual(st, 403)
        st, _, body = self.req("POST", "/api/finish", good(admin="root"))
        self.assertEqual((st, "admin login" in json.loads(body)["error"]), (400, True))
        st, _, body = self.req("POST", "/api/finish", good())
        self.assertEqual((st, json.loads(body)["summary"]["token"]), (200, "tok-123"))
        st, _, body = self.req("POST", "/api/switch", {})
        self.assertEqual((st, self.portal.phase), (200, "done"))


if __name__ == "__main__":
    unittest.main()
