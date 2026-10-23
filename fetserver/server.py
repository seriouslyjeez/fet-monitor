"""HTTP/JSON API (standard library only)."""
from __future__ import annotations

import hmac
import json
import logging
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .core import BadRequest, Core, NotFound, iso

log = logging.getLogger("fetserver.http")
MAX_BODY = 256 * 1024
SHOTS_RE = re.compile(r"^/api/v1/targets/([A-Za-z0-9_-]{1,64})/shots$")
COMMANDS_RE = re.compile(r"^/api/v1/targets/([A-Za-z0-9_-]{1,64})/commands$")


class Handler(BaseHTTPRequestHandler):
    server_version = "freETarget/" + __version__
    protocol_version = "HTTP/1.1"
    timeout = 30

    # -- plumbing --
    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
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

    def _dispatch(self, fn):
        try:
            fn()
        except _TooLarge:
            self._send(413, {"error": "body too large"})
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
        except NotFound as exc:
            self._send(404, {"error": str(exc)})
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

    def _get(self):
        url = urlparse(self.path)
        core: Core = self.server.core
        if url.path == "/api/v1/health":
            return self._send(200, {"ok": True, "version": __version__, "time": iso(core.now_ms())})
        if url.path == "/api/v1/targets":
            return self._send(200, {"targets": core.list_targets()})
        m = SHOTS_RE.match(url.path)
        if m:
            q = parse_qs(url.query)
            try:
                since = int(q.get("since_id", ["0"])[0])
                limit = int(q.get("limit", ["200"])[0])
            except ValueError:
                raise BadRequest("since_id and limit must be integers")
            return self._send(200, {"shots": core.list_shots(m.group(1), since, limit)})
        self._send(404, {"error": "not found"})

    def _post(self):
        url = urlparse(self.path)
        core: Core = self.server.core
        ip = self.client_address[0]
        if url.path in ("/api/v1/events", "/api/v1/heartbeat"):
            if not self._device_auth():
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), MAX_BODY))
                return self._send(401, {"error": "bad token"})
            payload = self._read_json()
            if url.path.endswith("/events"):
                return self._send(200, core.handle_events(payload, ip))
            return self._send(200, core.handle_heartbeat(payload, ip))
        m = COMMANDS_RE.match(url.path)
        if m:
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise BadRequest("body must be an object")
            cid = core.enqueue_command(m.group(1), payload.get("type"), payload.get("line"))
            return self._send(200, {"id": cid})
        self._send(404, {"error": "not found"})


class _TooLarge(Exception):
    pass


class FetHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, core: Core, token: str):
        super().__init__(addr, Handler)
        self.core = core
        self.token = token


def make_server(core: Core, token: str, host: str = "0.0.0.0", port: int = 8080) -> FetHTTPServer:
    return FetHTTPServer((host, port), core, token)
