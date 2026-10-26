"""HTTP/JSON API (standard library only)."""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import re
import sqlite3
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .admin import ADMIN_MAX_S, COOKIE, COOKIE_PATH, Admin
from .core import BadRequest, Core, Forbidden, NotFound, iso
from .firmware import MAX_IMAGE, BadImage, FirmwareStore, best_for

log = logging.getLogger("fetserver.http")
MAX_BODY = 256 * 1024
TARGET_RE = re.compile(r"^/api/v1/targets/([A-Za-z0-9_-]{1,64})/(shots|segments|series|control|control/release|mode|settings)$")
SERIES_RE = re.compile(r"^/api/v1/series/(\d{1,12})(/sight)?$")
ADMIN_TARGET_RE = re.compile(r"^/api/v1/admin/targets/([A-Za-z0-9_-]{1,64})/(mode|remove|ban|unban|firmware|command)$")
ADMIN_FW_DELETE_RE = re.compile(r"^/api/v1/admin/firmware/([0-9a-f]{64})/delete$")
DEVICE_FW_RE = re.compile(r"^/api/v1/firmware/([0-9a-f]{64})$")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class Handler(BaseHTTPRequestHandler):
    server_version = "FeTMonitor/" + __version__
    protocol_version = "HTTP/1.1"
    timeout = 30

    # -- plumbing --
    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, obj, headers=()) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise BadRequest("Content-Length required")
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            raise _TooLarge()
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            raise BadRequest("body is not valid JSON")

    def _device_auth(self) -> bool:
        given = self.headers.get("X-FET-Token", "")
        return hmac.compare_digest(given.encode(), self.server.token.encode())

    # -- Range Control --
    def _admin_request_ok(self) -> bool:
        """Only our own page may call the admin API: a custom header (a cross-site page cannot send one without a
        CORS preflight, which we never answer) and, when the browser sends one, an Origin naming this server."""
        if self.headers.get("X-FET-Admin") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin:
            return urlparse(origin).netloc.lower() == (self.headers.get("Host") or "").lower()
        return True

    def _firmware_upload(self) -> None:
        """Raw image upload (application/octet-stream). Refused before reading the body when not logged in or too big."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.close_connection = True
            raise BadRequest("Content-Length required")
        if not self.server.admin.check(self._admin_token()):
            self.close_connection = True
            return self._send(401, {"error": "log in to Range Control"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            self.close_connection = True
            return self._send(415, {"error": "upload the .bin file itself (application/octet-stream)"})
        if length <= 0 or length > MAX_IMAGE:
            self.close_connection = True
            return self._send(413, {"error": f"a firmware image is at most {MAX_IMAGE // 1024} KB"})
        data = self.rfile.read(length)
        if len(data) != length:
            self.close_connection = True
            raise BadRequest("upload cut off")
        try:
            meta = self.server.firmware.add(data, uploaded_by=self.client_address[0])
        except BadImage as exc:
            return self._send(400, {"error": str(exc)})
        log.info("Range Control from %s: firmware %s (%s, %d bytes) uploaded", self.client_address[0], meta["version"],
                 meta["sha256"][:12], meta["size"])
        return self._send(200, meta)

    def _device_firmware(self, sha: str) -> None:
        """A target fetching an image it was told to install. Device token required; only stored images by SHA-256."""
        if not self._device_auth():
            return self._send(401, {"error": "bad token"})
        path = self.server.firmware.path(sha)
        if path is None:
            return self._send(404, {"error": "no such firmware image"})
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _admin_token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        return c[COOKIE].value if COOKIE in c else None

    def _admin(self, path: str, method: str) -> None:
        admin: Admin = self.server.admin
        core: Core = self.server.core
        if not self._admin_request_ok():
            if method == "POST":
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), MAX_BODY))
            return self._send(403, {"error": "admin requests must come from the FeT Monitor page"})
        if path == "/api/v1/admin/status" and method == "GET":
            return self._send(200, {"password_set": admin.password_set(), "logged_in": admin.check(self._admin_token())})
        if path == "/api/v1/admin/firmware" and method == "POST":
            return self._firmware_upload()
        if method == "POST":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise BadRequest("body must be an object")
        if path == "/api/v1/admin/login" and method == "POST":
            status, value = admin.login(payload.get("password"), self.client_address[0])
            if status == "ok":
                cookie = f"{COOKIE}={value}; Path={COOKIE_PATH}; HttpOnly; SameSite=Strict; Max-Age={ADMIN_MAX_S}"
                return self._send(200, {"logged_in": True}, headers=[("Set-Cookie", cookie)])
            if status == "locked":
                return self._send(429, {"error": "too many wrong passwords", "retry_after": value},
                                  headers=[("Retry-After", str(value))])
            if status == "unset":
                return self._send(409, {"error": "no Range Control password is set; set it on the Pi"})
            log.warning("Range Control: wrong password from %s", self.client_address[0])
            return self._send(401, {"error": "wrong password"})
        if path == "/api/v1/admin/logout" and method == "POST":
            admin.logout(self._admin_token())
            return self._send(200, {"logged_in": False},
                              headers=[("Set-Cookie", f"{COOKIE}=; Path={COOKIE_PATH}; HttpOnly; SameSite=Strict; Max-Age=0")])
        if not admin.check(self._admin_token()):
            return self._send(401, {"error": "log in to Range Control"})
        if path == "/api/v1/admin/targets" and method == "GET":
            view = core.admin_view()
            images = self.server.firmware.list()
            for t in view["targets"]:
                best, why = best_for(t, images)
                t["fw_best"] = {"sha256": best["sha256"], "version": best["version"]} if best else None
                t["fw_best_reason"] = why
            return self._send(200, view)
        if path == "/api/v1/admin/firmware" and method == "GET":
            return self._send(200, {"images": self.server.firmware.list(), "max_size": MAX_IMAGE})
        fd = ADMIN_FW_DELETE_RE.match(path)
        if fd and method == "POST":
            if not self.server.firmware.delete(fd.group(1)):
                raise NotFound("no such firmware image")
            log.info("Range Control from %s: deleted firmware %s", self.client_address[0], fd.group(1)[:12])
            return self._send(200, {"ok": True})
        m = ADMIN_TARGET_RE.match(path)
        if m and method == "POST":
            did, what = m.groups()
            log.info("Range Control from %s: %s %s %s", self.client_address[0], what, did, payload if what == "mode" else "")
            if what == "mode":
                return self._send(200, core.set_mode(did, None, payload.get("mode"), payload.get("session"),
                                                     bool(payload.get("new_series")), payload.get("label"), admin=True))
            if what == "command":                     # restart, setup portal, allowlisted serial lines: Range Control only
                return self._send(200, {"id": core.enqueue_command(did, payload.get("type"), payload.get("line"))})
            if what == "firmware":
                if payload.get("sha256") == "best":       # the newest image built for this target's flash size that fits
                    image, why = best_for(core.firmware_info(did), self.server.firmware.list())
                    if image is None:
                        raise BadRequest(f"{did}: {why}")
                else:
                    image = self.server.firmware.get(payload.get("sha256"))
                if image is None:
                    raise NotFound("no such firmware image")
                return self._send(200, {"command_id": core.queue_firmware(did, image)})
            if what == "remove":
                core.remove_target(did)
            else:
                core.set_banned(did, what == "ban")
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def _dispatch(self, fn):
        try:
            fn()
        except _TooLarge:
            self._send(413, {"error": "body too large"})
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
        except NotFound as exc:
            self._send(404, {"error": str(exc)})
        except Forbidden as exc:
            self._send(403, {"error": str(exc)})
        except sqlite3.Error:
            # Nothing was committed, so nothing is acknowledged; the target will retry.
            log.exception("database error")
            self._send(500, {"error": "database error"})
        except Exception:
            log.exception("unhandled error")
            self._send(500, {"error": "internal error"})

    # -- routes --
    def do_GET(self):
        self._dispatch(self._get)

    def do_POST(self):
        self._dispatch(self._post)

    def _page(self):
        try:
            with open(os.path.join(WEB_DIR, "dashboard.html"), "rb") as fh:
                body = fh.read()
        except OSError:
            return self._send(404, {"error": "dashboard not installed"})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        """Server-sent events: a tiny 'something changed' hint; the page re-reads what it shows."""
        hub = self.server.core.hub
        q = hub.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not self.server.stopping:
                try:
                    msg = q.get(timeout=min(15, self.server.stream_keepalive_s))
                    self.wfile.write(b"data: " + json.dumps(msg).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except OSError:
            pass                      # the browser went away
        finally:
            hub.unsubscribe(q)

    def _get(self):
        url = urlparse(self.path)
        core: Core = self.server.core
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._page()
        if url.path.startswith("/api/v1/admin/"):
            return self._admin(url.path, "GET")
        dm = DEVICE_FW_RE.match(url.path)
        if dm:
            return self._device_firmware(dm.group(1))
        if url.path == "/api/v1/stream":
            return self._stream()
        if url.path == "/api/v1/health":
            return self._send(200, {"ok": True, "version": __version__, "time": iso(core.now_ms()),
                                    "event_conflicts": core.conflict_count()})
        if url.path == "/api/v1/targets":
            return self._send(200, {"targets": core.list_targets(q.get("client_id", [None])[0])})
        m = TARGET_RE.match(url.path)
        if m and m.group(2) == "shots":
            try:
                since = int(q.get("since_id", ["0"])[0])
                limit = int(q.get("limit", ["200"])[0])
                seg = int(q["segment_id"][0]) if "segment_id" in q else None
            except ValueError:
                raise BadRequest("since_id, limit and segment_id must be integers")
            return self._send(200, {"shots": core.list_shots(m.group(1), since, limit, seg)})
        sm = SERIES_RE.match(url.path)
        if sm and sm.group(2):
            try:
                after = int(q.get("after", ["0"])[0])
            except ValueError:
                raise BadRequest("after must be an integer")
            return self._send(200, core.sight_suggestion(int(sm.group(1)), after))
        if sm:
            try:
                bull = int(q["bull"][0]) if "bull" in q else None
                upto = int(q["upto"][0]) if "upto" in q else None
            except ValueError:
                raise BadRequest("bull and upto must be integers")
            return self._send(200, core.series_view(int(sm.group(1)), q.get("stream", ["scored"])[0], bull, upto))
        if m and m.group(2) == "series":
            try:
                limit = int(q.get("limit", ["50"])[0])
            except ValueError:
                raise BadRequest("limit must be an integer")
            return self._send(200, {"series": core.list_series(m.group(1), limit)})
        if m and m.group(2) == "segments":
            try:
                limit = int(q.get("limit", ["50"])[0])
            except ValueError:
                raise BadRequest("limit must be an integer")
            return self._send(200, {"segments": core.list_segments(m.group(1), limit)})
        self._send(404, {"error": "not found"})

    def _post(self):
        url = urlparse(self.path)
        core: Core = self.server.core
        ip = self.client_address[0]
        if url.path.startswith("/api/v1/admin/"):
            return self._admin(url.path, "POST")
        if url.path in ("/api/v1/events", "/api/v1/heartbeat"):
            if not self._device_auth():
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), MAX_BODY))
                return self._send(401, {"error": "bad token"})
            payload = self._read_json()
            if url.path.endswith("/events"):
                return self._send(200, core.handle_events(payload, ip))
            return self._send(200, core.handle_heartbeat(payload, ip))
        m = TARGET_RE.match(url.path)
        if m and m.group(2) in ("control", "control/release", "mode", "settings"):
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise BadRequest("body must be an object")
            did, what = m.group(1), m.group(2)
            if what == "control":
                res = core.acquire_control(did, payload.get("client_id"), payload.get("name"), bool(payload.get("takeover")))
                return self._send(200 if res["granted"] else 409, res)
            if what == "control/release":
                return self._send(200, {"released": core.release_control(did, payload.get("client_id"))})
            if what == "settings":
                return self._send(200, core.set_settings(did, payload.get("client_id"),
                                                         **{k: payload.get(k) for k in core.SETTING_KEYS}))
            return self._send(200, core.set_mode(did, payload.get("client_id"), payload.get("mode"),
                                                 payload.get("shooter"), bool(payload.get("new_series")), payload.get("label")))
        self._send(404, {"error": "not found"})


class _TooLarge(Exception):
    pass


class FetHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256      # the default of 5 drops connections when many targets (up to 100) call in at once

    def __init__(self, addr, core: Core, token: str, admin: Admin = None, firmware: FirmwareStore = None):
        super().__init__(addr, Handler)
        self.core = core
        self.token = token
        self.firmware = firmware if firmware is not None else FirmwareStore(None)
        self.admin = admin if admin is not None else Admin(None)     # no data dir: no password, Range Control refuses
        self.stopping = False
        self.stream_keepalive_s = 15

    def shutdown(self):
        self.stopping = True
        super().shutdown()

    def handle_error(self, request, client_address):
        """A client that disconnects mid-request is normal (closed browser tab, WiFi drop): one quiet line, no stack trace.
        Anything else is a real bug and keeps its full traceback."""
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            logging.getLogger("fetserver").debug("client %s dropped the connection: %s", client_address[0], exc)
            return
        super().handle_error(request, client_address)


def make_server(core: Core, token: str, host: str = "0.0.0.0", port: int = 8080, admin: Admin = None,
                firmware: FirmwareStore = None) -> FetHTTPServer:
    return FetHTTPServer((host, port), core, token, admin, firmware)
