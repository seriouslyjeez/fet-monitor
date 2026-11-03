"""The first-time setup portal: an open WiFi network "FeTMonitor-Setup-XXXX" and this page on port 80.

Flow (one person, one phone):
  form       the page; nearby WiFi networks were scanned before the setup network started (one radio). What is typed
             is kept here (draft) until the setup ends or DRAFT_TTL_S without a change: the phone's captive-portal
             window often closes while the WiFi is tested. Passwords are kept but never sent back (open network).
  testing    club WiFi only, after everything is filled in: the setup network goes down for up to a minute, the Pi
             joins the club WiFi, notes its address and router, and the setup network comes back (the phone rejoins
             it): saved if it worked, else the form again, filled in, with the reason
  saved      every setting except the network is applied; the page shows the address and device token to write down
  switching  the setup network goes away and the chosen network takes over; done (setup.done written) when it works,
             otherwise the setup network comes back with the reason
Every phone's captive-portal check (any other host name) is redirected here, so the page opens by itself.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from . import config
from .system import ETH, SETUP_IP, System, SystemError_

log = logging.getLogger("fetsetup")
PAGE = Path(__file__).resolve().parent / "web" / "setup.html"
MAX_BODY = 16 * 1024
SERVER_PORT = 8080
DRAFT_TTL_S = 30 * 60                     # what was typed is forgotten after this long without a change
SECRETS = ("range_password", "range_password2", "director_password", "admin_password", "admin_password2")
TEXT = ("hostname", "country", "timezone", "director", "admin")


class Portal:
    def __init__(self, system: System, done_file: Path, sleep: Callable[[float], None] = time.sleep, background: bool = True,
                 reopen: bool = False, clock: Callable[[], float] = time.monotonic):
        """reopen: the Pi is set up but found no network at boot: only its country and network can be set here."""
        self.sys, self.done_file, self.sleep, self.background, self.reopen = system, Path(done_file), sleep, background, reopen
        self.lock = threading.Lock()
        self.phase, self.error, self.test, self.saved, self.summary = "form", None, None, None, None
        self.ssid = "FeTMonitor-Setup-" + system.serial_suffix()
        self.networks, self.stopped = [], threading.Event()
        self.clock, self.draft, self.draft_at = clock, {}, 0.0

    def start(self) -> None:
        """Scan first (the radio is free), then open the setup network."""
        self.networks = self.sys.scan()
        self.sys.hotspot_on(self.ssid)

    def _bg(self, fn) -> None:
        if self.background:
            threading.Thread(target=fn, daemon=True).start()
        else:
            fn()

    # ------------------------------------------------------------------------------------- what the page shows
    def info(self) -> dict:
        with self.lock:
            countries = self.sys.countries()
            test = {k: v for k, v in self.test.items() if k != "psk"} if self.test else None     # never sent back: the network is open
            return {"phase": self.phase, "error": self.error, "test": test, "summary": self.summary, "setup_ssid": self.ssid, "reopen": self.reopen,
                    "networks": self.networks, "cable": self.sys.lease(ETH), "countries": [{"code": c, "name": n} for c, n in countries],
                    "zones": self.sys.zones(), "own_ip": config.OWN_IP, "draft": self._draft_view(),
                    "min_password": config.MIN_PASSWORD, "min_admin_password": config.MIN_ADMIN_PASSWORD}

    # ------------------------------------------------------------------------------------- what was typed (draft)
    def _draft_now(self) -> dict:
        """The draft, or {} once it is DRAFT_TTL_S old (lock held)."""
        if self.draft and self.clock() - self.draft_at > DRAFT_TTL_S:
            self.draft = {}
        return self.draft

    def _draft_view(self) -> Optional[dict]:
        """The draft for the page: everything but the passwords, which are only marked as kept (lock held)."""
        d = self._draft_now()
        if not d:
            return None
        out = {k: d[k] for k in TEXT + ("ssh",) if k in d}
        n = d.get("network")
        if isinstance(n, dict):
            out["network"] = {k: v for k, v in n.items() if k != "psk"}
        out["kept"] = [k for k in SECRETS if d.get(k)] + (["psk"] if isinstance(n, dict) and n.get("psk") else [])
        return out

    @staticmethod
    def _clean(raw) -> dict:
        """Only the form's own fields, of plausible size (anyone on the open setup network can send anything)."""
        if not isinstance(raw, dict):
            return {}
        out = {k: raw[k][:200] for k in TEXT + SECRETS if isinstance(raw.get(k), str)}
        if isinstance(raw.get("ssh"), bool):
            out["ssh"] = raw["ssh"]
        if isinstance(raw.get("clock_ms"), int):
            out["clock_ms"] = raw["clock_ms"]
        n = raw.get("network")
        if isinstance(n, dict):
            net = {k: n[k][:100] for k in ("mode", "ssid", "psk") if isinstance(n.get(k), str)}
            if isinstance(n.get("channel"), int):
                net["channel"] = n["channel"]
            a = n.get("address")
            if a in ("dhcp", "keep"):
                net["address"] = a
            elif isinstance(a, dict):
                net["address"] = {k: (a[k][:40] if isinstance(a[k], str) else a[k]) for k in ("ip", "prefix", "gateway")
                                  if isinstance(a.get(k), (str, int)) and not isinstance(a.get(k), bool)}
            out["network"] = net
        return out

    def remember(self, raw, secrets: bool = False) -> dict:
        """Keep what the page sent; an empty password keeps the one kept before (the page never has it back). Returns
        the full settings to check. Without secrets (the page saving as it goes) passwords are not taken."""
        new = self._clean(raw)
        with self.lock:
            old = self._draft_now()
            if not secrets:
                for k in SECRETS:
                    new.pop(k, None)
                if isinstance(new.get("network"), dict):
                    new["network"].pop("psk", None)
            for k in SECRETS:
                if not new.get(k) and old.get(k):
                    new[k] = old[k]
            on, nn = old.get("network") or {}, new.get("network")
            if isinstance(nn, dict) and not nn.get("psk") and on.get("psk") and on.get("mode") == nn.get("mode") \
                    and on.get("ssid") == nn.get("ssid"):
                nn["psk"] = on["psk"]                  # the same network: its kept password
            self.draft, self.draft_at = new, self.clock()
            return dict(new, network=dict(nn)) if isinstance(nn, dict) else dict(new)

    # ------------------------------------------------------------------------------------- test the club WiFi
    def test_wifi(self, ssid: str, psk: str, then: Optional[dict] = None) -> None:
        """Join the club WiFi for a moment (one radio: the setup network goes down meanwhile). then: the checked
        settings to apply if it works (the page goes straight to the address to write down); if not, the form again."""
        if not config.ssid_ok(ssid if isinstance(ssid, str) else "") or not config.wpa_psk_ok(psk if isinstance(psk, str) else ""):
            raise config.SetupError("choose the club WiFi and enter its password (8 to 63 characters)")
        with self.lock:
            if self.phase not in ("form", "saved"):
                raise config.SetupError("busy: wait a moment")
            self.phase, self.error, self.test = "testing", None, None

        def run():
            result = {"ok": False, "why": "the test did not finish"}
            try:
                self.sys.hotspot_off()
                self.networks = self.sys.scan() or self.networks
                result = self.sys.try_wifi(ssid, psk)
            except SystemError_ as exc:
                result = {"ok": False, "why": str(exc)}
            finally:
                self._reopen()
                with self.lock:
                    self.test = dict(result, ssid=ssid, psk=psk)
                    if then is None or not result.get("ok"):  # worked, with settings to apply: straight on (no form)
                        self.phase = "form"
                    if then is not None and not result.get("ok"):
                        self.error = f"Could not connect to {ssid}: {result.get('why')}. Nothing was changed; correct it and try again."
            if then is not None and result.get("ok"):
                try:
                    self._apply(then, result.get("lease"))
                except config.SetupError as exc:
                    with self.lock:
                        self.phase, self.error = "form", str(exc)
        self._bg(run)

    def _reopen(self) -> None:
        for attempt in range(3):                  # the setup network must come back, or nobody reaches the Pi
            try:
                self.sys.hotspot_on(self.ssid)
                return
            except SystemError_ as exc:
                log.warning("setup network did not start (%s), retrying", exc)
                self.sleep(5)

    # ------------------------------------------------------------------------------------- save, then switch
    def finish(self, raw: dict) -> Optional[dict]:
        """Check everything; the club WiFi is tested first (then applied, in the background: None is returned and the
        page follows the phase). Otherwise apply everything but the network and return the summary to write down."""
        with self.lock:
            if self.phase not in ("form", "saved"):
                raise config.SetupError("busy: wait a moment")
        raw = self.remember(raw, secrets=True)
        codes = sorted(c for c, _ in self.sys.countries())
        cfg = config.check_reopen(raw, codes) if self.reopen else config.check(raw, codes, self.sys.zones())
        net = cfg["network"]
        if net["mode"] == "wifi":
            t = self.test
            if t and t["ok"] and t["ssid"] == net["ssid"] and t["psk"] == net["psk"]:
                return self._apply(cfg, t["lease"])
            with self.lock:
                self.error = None
            self.test_wifi(net["ssid"], net["psk"], then=cfg)
            return None
        lease = self.sys.lease(ETH) if net["mode"] == "cable" else None
        if net["mode"] == "cable" and net["address"] in ("dhcp", "keep") and not lease:
            raise config.SetupError("the cable has no address: is it plugged in to the club router?")
        return self._apply(cfg, lease)

    def _apply(self, cfg: dict, lease: Optional[dict]) -> dict:
        cfg = dict(cfg, network=config.resolve_keep(cfg["network"], lease))
        net = cfg["network"]
        live = lease["ip"] if lease and net["address"] == "dhcp" else None
        with self.lock:
            if self.phase not in ("form", "saved", "testing"):    # testing: the WiFi test that just worked
                raise config.SetupError("busy: wait a moment")
            self.phase = "applying"
        try:
            for step, arg in config.plan(cfg):
                self.sys.apply(step, arg)
        except SystemError_ as exc:
            with self.lock:
                self.phase = "form"
            raise config.SetupError(f"could not apply the settings: {exc}")
        addr = config.server_address(cfg, live)
        summary = {"hostname": cfg.get("hostname"), "address": addr, "url": f"http://{addr}:{SERVER_PORT}/" if addr else None,
                   "token": self.sys.device_token(), "mode": net["mode"], "ssid": net.get("ssid"),
                   "fixed": isinstance(net["address"], dict), "ssh": cfg.get("ssh", False), "admin": cfg.get("admin")}
        with self.lock:
            self.saved, self.summary, self.phase, self.error = cfg, summary, "saved", None
        return summary

    def switch(self) -> None:
        with self.lock:
            if self.phase != "saved" or not self.saved:
                raise config.SetupError("save the settings first")
            self.phase = "switching"
        net = self.saved["network"]

        def run():
            self.sleep(3)                         # the page has its answer before the network goes away
            try:
                self.sys.hotspot_off()
                self.sys.use_network(net)
                ok = False
                for _ in range(30):
                    if self.sys.has_network():
                        ok = True
                        break
                    self.sleep(2)
                if not ok:
                    raise SystemError_("the chosen network did not come up")
            except SystemError_ as exc:
                self._reopen()
                with self.lock:
                    self.phase, self.error = "saved", f"{exc}: the setup network is back; check the network settings"
                return
            from .sysreq import save_last_good
            save_last_good(net, self.done_file.parent / "network.json")   # Range Control falls back to it
            self.done_file.parent.mkdir(parents=True, exist_ok=True)
            self.done_file.write_text(json.dumps({"at": int(time.time()), "mode": net["mode"], "reopened": self.reopen}))
            with self.lock:
                self.phase, self.draft = "done", {}          # set up: what was typed is not kept any longer
            self.stopped.set()
        self._bg(run)


    def watch(self, every_s: float = 10.0) -> None:
        """A reopened setup closes by itself when a network appears without anyone using the page (the cable was
        plugged back in): the Pi just goes on with its old settings."""
        while not self.stopped.is_set():
            self.sleep(every_s)
            with self.lock:
                idle = self.phase == "form" and self.test is None
            if idle and self.sys.has_network():
                log.info("a network came back: closing the setup network")
                self.sys.hotspot_off()
                self.stopped.set()


def boot(system: System, done_file: Path, wait_s: int = 180, sleep: Callable[[float], None] = time.sleep) -> Optional[Portal]:
    """At every start: not set up yet -> the full setup; set up and a network comes up within wait_s -> nothing;
    set up but no network -> the setup reopens for the network only. A stale catch-all DNS file (a crash during setup)
    is removed first, so it never captures the Pi's own range network."""
    system.hotspot_off()
    if not Path(done_file).exists():
        return Portal(system, done_file, sleep=sleep)
    waited = 0
    while waited < wait_s:
        if system.has_network():
            return None
        sleep(5)
        waited += 5
    log.warning("no network %d s after start: reopening the setup network (network settings only)", wait_s)
    return Portal(system, done_file, sleep=sleep, reopen=True)


# ------------------------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "fet-setup"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def _send(self, status: int, body: bytes, ctype: str, headers=()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _ours(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in (SETUP_IP, "localhost", "127.0.0.1", self.server.extra_host)

    def do_GET(self):
        if not self._ours():                     # a phone's captive-portal check, or any other site: here instead
            return self._send(302, b"", "text/plain", [("Location", f"http://{SETUP_IP}/")])
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/info":
            return self._json(200, self.server.portal.info())
        self._send(302, b"", "text/plain", [("Location", "/")])

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not self._ours() or self.headers.get("X-FET-Setup") != "1" or not 0 <= length <= MAX_BODY:
            self.close_connection = True
            return self._json(403, {"error": "setup requests must come from the setup page"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad request"})
        p = self.server.portal
        try:
            if self.path == "/api/test":
                p.test_wifi(body.get("ssid"), body.get("psk"))
                return self._json(200, {"phase": "testing"})
            if self.path == "/api/finish":
                summary = p.finish(body)
                return self._json(200, {"summary": summary, "phase": "saved" if summary else "testing"})
            if self.path == "/api/draft":                # the page keeps what is typed here as it goes (no passwords)
                p.remember(body)
                return self._json(200, {"ok": True})
            if self.path == "/api/switch":
                p.switch()
                return self._json(200, {"phase": "switching"})
        except config.SetupError as exc:
            return self._json(400, {"error": str(exc)})
        self._json(404, {"error": "not found"})


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, portal: Portal, extra_host: str = ""):
        super().__init__(addr, Handler)
        self.portal, self.extra_host = portal, extra_host


def serve(portal: Portal, host: str = "0.0.0.0", port: int = 80) -> None:
    """Run until the setup is done."""
    portal.start()
    srv = SetupServer((host, port), portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if portal.reopen:
        threading.Thread(target=portal.watch, daemon=True).start()
    portal.stopped.wait()
    time.sleep(1)
    srv.shutdown()
