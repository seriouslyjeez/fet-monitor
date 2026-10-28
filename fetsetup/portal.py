"""The first-time setup portal: an open WiFi network "FeTMonitor-Setup-XXXX" and this page on port 80.

Flow (one person, one phone):
  form       the page; nearby WiFi networks were scanned before the setup network started (one radio)
  testing    club WiFi only: the setup network goes down for up to a minute, the Pi joins the club WiFi, notes its
             address and router, and the setup network comes back with the result (the phone rejoins it)
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


class Portal:
    def __init__(self, system: System, done_file: Path, sleep: Callable[[float], None] = time.sleep, background: bool = True):
        self.sys, self.done_file, self.sleep, self.background = system, Path(done_file), sleep, background
        self.lock = threading.Lock()
        self.phase, self.error, self.test, self.saved, self.summary = "form", None, None, None, None
        self.ssid = "FeTMonitor-Setup-" + system.serial_suffix()
        self.networks, self.stopped = [], threading.Event()

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
            return {"phase": self.phase, "error": self.error, "test": test, "summary": self.summary, "setup_ssid": self.ssid,
                    "networks": self.networks, "cable": self.sys.lease(ETH), "countries": [{"code": c, "name": n} for c, n in countries],
                    "zones": self.sys.zones(), "own_ip": config.OWN_IP}

    # ------------------------------------------------------------------------------------- test the club WiFi
    def test_wifi(self, ssid: str, psk: str) -> None:
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
                    self.phase = "form"
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
    def finish(self, raw: dict) -> dict:
        """Check and apply everything but the network; returns the summary to write down."""
        info = {c for c, _ in self.sys.countries()}
        cfg = config.check(raw, sorted(info), self.sys.zones())
        net = cfg["network"]
        live = None
        if net["mode"] == "wifi":
            t = self.test
            if not t or not t["ok"] or t["ssid"] != net["ssid"] or t["psk"] != net["psk"]:
                raise config.SetupError("test the club WiFi connection first (with this network and password)")
            if net["address"] == "dhcp":
                live = t["lease"]["ip"]
        elif net["mode"] == "cable":
            lease = self.sys.lease(ETH)
            if net["address"] == "dhcp":
                if not lease:
                    raise config.SetupError("the cable has no address: is it plugged in to the club router?")
                live = lease["ip"]
        with self.lock:
            if self.phase not in ("form", "saved"):
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
        summary = {"hostname": cfg["hostname"], "address": addr, "url": f"http://{addr}:{SERVER_PORT}/" if addr else None,
                   "token": self.sys.device_token(), "mode": net["mode"], "ssid": net.get("ssid"),
                   "fixed": isinstance(net["address"], dict), "ssh": cfg["ssh"], "admin": cfg["admin"]}
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
            self.done_file.parent.mkdir(parents=True, exist_ok=True)
            self.done_file.write_text(json.dumps({"at": int(time.time()), "hostname": self.saved["hostname"], "mode": net["mode"]}))
            with self.lock:
                self.phase = "done"
            self.stopped.set()
        self._bg(run)


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
                return self._json(200, {"summary": p.finish(body)})
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
    portal.stopped.wait()
    time.sleep(1)
    srv.shutdown()
