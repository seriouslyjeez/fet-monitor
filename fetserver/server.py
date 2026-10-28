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
from .admin import ADMIN_MAX_S, COOKIE, COOKIE_PATH, MATCH_COOKIE, MATCH_COOKIE_PATH, Admin
from .corrections import OFFICER_KINDS
from .matches import MatchError
from .core import BadRequest, Core, Forbidden, NotFound, iso
from .firmware import MAX_IMAGE, BadImage, FirmwareStore, best_for, version_tuple
from .releases import ReleaseError, Updates, fetch_firmware

log = logging.getLogger("fetserver.http")
MAX_BODY = 256 * 1024
TARGET_RE = re.compile(r"^/api/v1/targets/([A-Za-z0-9_-]{1,64})/(shots|segments|series|control|control/release|mode|settings|start-record)$")
MATCH_RE = re.compile(r"^/api/v1/match/matches/(\d{1,9})(?:/(start|command|restart|move|lane|corrections|official|later-relay|late|shoot-off|redraw)|/flags/(\d{1,12}))?$")
CORR_RE = re.compile(r"^/api/v1/match/matches/(\d{1,9})/(?:corrections/(\d{1,12})/(settle|void)|shots/(\d{1,4})|(incidents))$")
COMP_RE = re.compile(r"^/api/v1/match/competitions/(\d{1,9})(?:/(relays|competitors|finals)(?:/(\d{1,12})(/delete|/move)?)?)?$")
COMP_IMPORT_RE = re.compile(r"^/api/v1/match/competitions/(\d{1,9})/competitors/import$")
MAX_CSV = 256 * 1024
RESULTS_RE = re.compile(r"^/api/v1/results/([cma]?\d{1,9})(\.csv|-teams\.csv)?$")   # c competition, m match, a frozen copy
MATCH_FORMAT_DELETE_RE = re.compile(r"^/api/v1/match/formats/([a-z0-9-]{2,41})/delete$")
MATCH_USER_DELETE_RE = re.compile(r"^/api/v1/admin/match-users/([A-Za-z0-9._-]{2,32})/delete$")
SERIES_RE = re.compile(r"^/api/v1/series/(\d{1,12})(/sight)?$")
ADMIN_TARGET_RE = re.compile(r"^/api/v1/admin/targets/([A-Za-z0-9_-]{1,64})/(mode|remove|ban|unban|firmware|command)$")
ADMIN_FW_DELETE_RE = re.compile(r"^/api/v1/admin/firmware/([0-9a-f]{64})/delete$")
DEVICE_FW_RE = re.compile(r"^/api/v1/firmware/([0-9a-f]{64})$")
FACTORY_RESET_MIN = (0, 2, 2)
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
    def _send_csv(self, text: str, filename: str) -> None:
        body = text.encode("utf-8-sig")                 # with a BOM so spreadsheet programs read it as UTF-8
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _admin_request_ok(self) -> bool:
        """Only our own page may call the admin API: a custom header (a cross-site page cannot send one without a
        CORS preflight, which we never answer) and, when the browser sends one, an Origin naming this server."""
        if self.headers.get("X-FET-Admin") != "1":
            return False
        origin = self.headers.get("Origin")
        if origin:
            return urlparse(origin).netloc.lower() == (self.headers.get("Host") or "").lower()
        return True

    def _match_token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        return c[MATCH_COOKIE].value if MATCH_COOKIE in c else None

    def _match(self, path: str, method: str) -> None:
        """Match Control. Directors (and the Range Control admin) create formats and matches and decide flagged
        shots; range officers run the clock. Same cross-site guard as Range Control."""
        admin: Admin = self.server.admin
        core: Core = self.server.core
        mc = core.matches
        if not self._admin_request_ok():
            if method == "POST":
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), MAX_BODY))
            return self._send(403, {"error": "Match Control requests must come from the FeT Monitor page"})
        sess = admin.match_session(self._match_token(), mc.user)
        imp = COMP_IMPORT_RE.match(path)
        if imp and method == "POST":                   # a CSV file of competitors: read in memory, never stored
            if not sess or sess["role"] not in ("director", "admin"):
                self.close_connection = True
                return self._send(401 if not sess else 403, {"error": "only a match director can do that"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise BadRequest("Content-Length required")
            if not 0 < length <= MAX_CSV:
                self.close_connection = True
                return self._send(413, {"error": f"a competitor file is at most {MAX_CSV // 1024} KB"})
            raw = self.rfile.read(length)
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")   # files saved by older spreadsheet programs
            try:
                return self._send(200, {**core.competitions.import_csv(int(imp.group(1)), text), **core.competitions.detail(int(imp.group(1)))})
            except MatchError as exc:
                return self._send(400, {"error": str(exc)})
        payload = {}
        if method == "POST":
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise BadRequest("body must be an object")
        if path == "/api/v1/match/status" and method == "GET":
            return self._send(200, {"logged_in": bool(sess), **(sess or {}), "users_defined": bool(mc.users()),
                                    "admin_password_set": admin.password_set()})
        if path == "/api/v1/match/login" and method == "POST":
            status, value = admin.login_match(payload.get("username"), payload.get("password"), self.client_address[0], mc.user)
            if status == "ok":
                cookie = f"{MATCH_COOKIE}={value}; Path={MATCH_COOKIE_PATH}; HttpOnly; SameSite=Strict; Max-Age={ADMIN_MAX_S}"
                return self._send(200, {"logged_in": True}, headers=[("Set-Cookie", cookie)])
            if status == "locked":
                return self._send(429, {"error": "too many wrong passwords", "retry_after": value}, headers=[("Retry-After", str(value))])
            log.warning("Match Control: failed login for %r from %s", str(payload.get("username"))[:32], self.client_address[0])
            return self._send(401, {"error": "wrong username or password"})
        if path == "/api/v1/match/logout" and method == "POST":
            admin.logout(self._match_token())
            return self._send(200, {"logged_in": False},
                              headers=[("Set-Cookie", f"{MATCH_COOKIE}=; Path={MATCH_COOKIE_PATH}; HttpOnly; SameSite=Strict; Max-Age=0")])
        if not sess:
            return self._send(401, {"error": "log in to Match Control"})
        who, role = sess["user"], sess["role"]
        director = role in ("director", "admin")

        def need_director():
            if not director:
                raise Forbidden("only a match director can do that")
        try:
            if path == "/api/v1/match/formats" and method == "GET":
                return self._send(200, {"formats": mc.formats()})
            if path == "/api/v1/match/formats" and method == "POST":
                need_director()
                return self._send(200, mc.save_format(payload.get("format"), who))
            fd = MATCH_FORMAT_DELETE_RE.match(path)
            if fd and method == "POST":
                need_director()
                if not mc.delete_format(fd.group(1)):
                    raise NotFound("no such custom format")
                return self._send(200, {"ok": True})
            if path == "/api/v1/match/targets" and method == "GET":
                return self._send(200, {"targets": [{"device_id": t["device_id"], "name": t["name"], "online": t["online"],
                                                     "in_match": bool(t["match"]), "lease": t["lease"]}
                                                    for t in core.list_targets(None)]})
            if path == "/api/v1/match/competitions" and method == "GET":
                return self._send(200, {"competitions": core.competitions.list()})
            if path == "/api/v1/match/competitions" and method == "POST":
                need_director()
                cid = core.competitions.create(payload.get("name"), payload.get("format_id"), who, payload.get("override"),
                                               payload.get("team_size"), bool(payload.get("best_count")), payload.get("paced") or "spoken")
                return self._send(200, core.competitions.detail(cid))
            cm = COMP_RE.match(path)
            if cm:
                cid, sub, pid, delete = int(cm.group(1)), cm.group(2), cm.group(3), cm.group(4)
                cc = core.competitions
                if method == "GET" and not sub:
                    return self._send(200, cc.detail(cid))
                need_director()
                if method == "POST" and not sub:
                    cc.update(cid, payload.get("name"), payload.get("team_size"), payload.get("best_count"), payload.get("paced"),
                              payload.get("challenge_min"))
                elif method == "POST" and sub == "competitors" and delete == "/move":
                    cc.move_competitor(cid, int(pid), payload.get("match_id"), payload.get("device_id"), who)
                    log.info("Match Control: %s moved competitor %s to relay match %s, %s", who, pid, payload.get("match_id"), payload.get("device_id"))
                elif method == "POST" and sub == "competitors" and delete:
                    cc.remove_competitor(cid, int(pid))
                elif method == "POST" and sub == "competitors":
                    cc.save_competitor(cid, payload, int(pid) if pid else None)
                elif method == "POST" and sub == "finals" and not pid:     # CMP 10: a final for the top of a class, or for a relay
                    fid = cc.create_final(cid, str(payload.get("kind", "")), payload.get("devices"), who, payload.get("cls"), payload.get("n"),
                                          payload.get("for_relay"), payload.get("paced") or "officer")
                    log.info("Match Control: %s created final match %d in competition %d", who, fid, cid)
                elif method == "POST" and sub == "relays" and not pid:
                    mid = cc.add_relay(cid, payload.get("lanes"), who)
                    log.info("Match Control: %s added relay match %d to competition %d", who, mid, cid)
                else:
                    raise NotFound("not found")
                return self._send(200, cc.detail(cid))
            if path == "/api/v1/match/results" and method == "GET":
                return self._send(200, {"results": core.results.listing(hidden_too=True)})
            if path in ("/api/v1/match/results/delete", "/api/v1/match/results/restore") and method == "POST":
                need_director()
                fn = core.results.delete_results if path.endswith("delete") else core.results.restore_results
                n = fn(payload.get("keys"), who)
                log.warning("Match Control: %s %s results %s", who, "deleted" if path.endswith("delete") else "restored", payload.get("keys"))
                return self._send(200, {"count": n, "results": core.results.listing(hidden_too=True)})
            if path == "/api/v1/match/delete" and method == "POST":
                need_director()
                out = core.results.delete_matches(payload.get("matches"), payload.get("competitions"), who)
                log.warning("Match Control: %s deleted matches %s and competitions %s (%d frozen results)", who,
                            payload.get("matches"), payload.get("competitions"), out["frozen"])
                return self._send(200, out)
            if path == "/api/v1/match/matches" and method == "GET":
                return self._send(200, {"matches": mc.list()})
            if path == "/api/v1/match/matches" and method == "POST":
                need_director()
                mid = mc.create(payload.get("name"), payload.get("format_id"), payload.get("lanes"), who, payload.get("override"),
                                payload.get("paced") or "spoken")
                log.info("Match Control: %s created match %d", who, mid)
                return self._send(200, mc.detail(mid))
            cr = CORR_RE.match(path)
            if cr:
                mid = int(cr.group(1))
                if cr.group(4) and method == "GET":
                    return self._send(200, core.corrections.lane_shots(mid, int(cr.group(4))))
                if cr.group(5) and method == "GET":                 # the Range Incident Report
                    return self._send(200, mc.incidents(mid))
                if cr.group(2) and method == "POST":
                    need_director()
                    if cr.group(3) == "settle":
                        core.corrections.settle(mid, int(cr.group(2)), str(payload.get("outcome", "")), who, payload.get("penalty"), payload.get("note"),
                                                payload.get("value"))
                    else:
                        core.corrections.void(mid, int(cr.group(2)), who)
                    log.warning("Match Control: %s %s correction %s of match %d", who, cr.group(3), cr.group(2), mid)
                    return self._send(200, mc.detail(mid))
            mm = MATCH_RE.match(path)
            if mm:
                mid = int(mm.group(1))
                if method == "GET" and not mm.group(2) and not mm.group(3):
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "start":
                    mc.start(mid, who)
                    log.info("Match Control: %s started match %d", who, mid)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "command":
                    mc.command(mid, str(payload.get("action", "")), who, payload.get("seconds"), payload.get("device_id"),
                               payload.get("paced"))
                    log.info("Match Control: %s %s match %d", who, payload.get("action"), mid)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "move":     # a shooter to another target (a target failed)
                    if mc.detail(mid)["state"] == "ready":
                        need_director()                    # before the start it is setting up: the match director's
                    mc.move(mid, payload.get("from"), payload.get("to"), who)
                    log.warning("Match Control: %s moved %s to %s in match %d", who, payload.get("from"), payload.get("to"), mid)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "shoot-off":     # a tie-breaking shot in a final (CMP 10.2.9 n)
                    need_director()
                    mc.shoot_off(mid, payload.get("lanes"), who)
                    log.warning("Match Control: %s started a shoot-off for %s in match %d", who, payload.get("lanes"), mid)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "redraw":        # a new draw of a final's firing points (Air Gun 10.1.2)
                    need_director()
                    core.competitions.redraw_final(mid, who)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "late":          # a late arrival joins (ISSF 6.11.4)
                    if payload.get("competitor_id") is not None:
                        core.competitions.add_late(mid, payload.get("competitor_id"), payload.get("device_id"), who)
                    else:
                        mc.add_late(mid, payload.get("device_id"), payload.get("competitor"), who)
                    log.warning("Match Control: %s added a late arrival on %s to match %d", who, payload.get("device_id"), mid)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "later-relay":   # CMP 7.18: finish the match on a later relay
                    to = core.competitions.finish_on_later_relay(mid, payload.get("from"), payload.get("to_match"), payload.get("to"), who)
                    log.warning("Match Control: %s sent %s of match %d to relay match %d (CMP 7.18)", who, payload.get("from"), mid, to)
                    return self._send(200, {"to_match": to, **mc.detail(mid)})
                if method == "POST" and mm.group(2) == "lane":     # one shooter: ready after a move, extra time, start record
                    mc.lane_command(mid, payload.get("device_id"), str(payload.get("action", "")), who, payload.get("seconds"))
                    log.info("Match Control: %s %s for %s in match %d", who, payload.get("action"), payload.get("device_id"), mid)
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "corrections":   # range officer: review/protest; the rest: director
                    if payload.get("kind") not in OFFICER_KINDS:
                        need_director()
                    cid = core.corrections.add(mid, str(payload.get("kind", "")), payload.get("lane"), who, director,
                                               payload.get("shot_id"), payload.get("data"))
                    log.warning("Match Control: %s recorded %s on firing point %s of match %d (correction %d)", who, payload.get("kind"),
                                payload.get("lane"), mid, cid)
                    return self._send(200, {"id": cid, **mc.detail(mid)})
                if method == "POST" and mm.group(2) == "official":     # make the results official, or reopen them
                    need_director()
                    action = payload.get("action")
                    if action == "reopen":
                        mc.reopen(mid, who)
                    elif action == "post":
                        mc.post_results(mid, who)
                    else:
                        mc.make_official(mid, who, payload.get("override") is True)
                    log.warning("Match Control: %s %s the results of match %d%s", who, {"reopen": "reopened", "post": "posted"}.get(action, "made official"),
                                mid, " (protest time overridden)" if payload.get("override") is True else "")
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(2) == "restart":
                    need_director()                        # mistakes happen: the match director can go back
                    mc.restart(mid, payload.get("phase"), who)
                    log.warning("Match Control: %s restarted match %d at phase %s", who, mid, payload.get("phase"))
                    return self._send(200, mc.detail(mid))
                if method == "POST" and mm.group(3):
                    need_director()
                    mc.decide(mid, int(mm.group(3)), str(payload.get("decision", "")), who)
                    return self._send(200, mc.detail(mid))
        except MatchError as exc:
            return self._send(400, {"error": str(exc)})
        self._send(404, {"error": "not found"})

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
        if path == "/api/v1/admin/match-users" and method == "GET":
            return self._send(200, {"users": core.matches.users()})
        if path == "/api/v1/admin/match-users" and method == "POST":
            name = payload.get("username")
            try:
                core.matches.set_user(name, payload.get("role"), payload.get("password") or None)
            except MatchError as exc:
                return self._send(400, {"error": str(exc)})
            admin.drop_user(name)                      # a new role or password ends that user's sessions
            log.info("Range Control from %s: Match Control user %s set (%s)", self.client_address[0], name, payload.get("role"))
            return self._send(200, {"users": core.matches.users()})
        mu = MATCH_USER_DELETE_RE.match(path)
        if mu and method == "POST":
            if not core.matches.remove_user(mu.group(1)):
                raise NotFound("no such user")
            admin.drop_user(mu.group(1))
            return self._send(200, {"users": core.matches.users()})
        if path == "/api/v1/admin/updates" and method == "GET":
            return self._send(200, {"current": __version__, **self.server.updates.status()})
        if path == "/api/v1/admin/updates/check" and method == "POST":
            try:
                return self._send(200, {"current": __version__, **self.server.updates.check(__version__)})
            except ReleaseError as exc:
                return self._send(502, {"error": str(exc)})
        if path == "/api/v1/admin/updates/install" and method == "POST":
            try:
                meta = self.server.updates.install(str(payload.get("version", "")))
            except ReleaseError as exc:
                return self._send(400, {"error": str(exc)})
            log.warning("Range Control from %s: server update to %s requested", self.client_address[0], meta["version"])
            return self._send(202, meta)
        if path == "/api/v1/admin/firmware/github" and method == "POST":
            store = self.server.firmware
            try:
                res = fetch_firmware(self.server.updates.gh, lambda sha: store.get(sha) is not None,
                                     lambda data: store.add(data, uploaded_by="GitHub"), self.server.updates.signers)
            except (ReleaseError, BadImage) as exc:
                return self._send(502, {"error": str(exc)})
            log.info("Range Control from %s: FeT Bridge firmware from GitHub: %s", self.client_address[0], res["message"])
            return self._send(200, res)
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
                if payload.get("type") == "factory_reset":
                    fw = core.firmware_info(did)["bridge_fw"]
                    if (version_tuple(fw) or (0, 0, 0)) < FACTORY_RESET_MIN:
                        raise BadRequest(f"{did} runs FeT Bridge {fw or '?'}; factory reset needs 0.2.2 or newer "
                                         "(or use its setup portal: Forget settings)")
                    log.warning("Range Control from %s: FACTORY RESET of %s", self.client_address[0], did)
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
        if url.path.startswith("/api/v1/match/"):
            return self._match(url.path, "GET")
        dm = DEVICE_FW_RE.match(url.path)
        if dm:
            return self._device_firmware(dm.group(1))
        if url.path == "/api/v1/stream":
            return self._stream()
        if url.path == "/api/v1/health":
            return self._send(200, {"ok": True, "version": __version__, "time": iso(core.now_ms()),
                                    "event_conflicts": core.conflict_count()})
        if url.path == "/api/v1/board":                # public scoreboard (read only, like the target pages)
            try:
                ids = [int(x) for x in q.get("m", [""])[0].split(",") if x.strip()]
                cids = [int(x) for x in q.get("c", [""])[0].split(",") if x.strip()]
            except ValueError:
                raise BadRequest("m and c must be numbers, e.g. m=3,4 or c=2")
            comps = []
            for cid in cids[:20]:
                try:
                    comps.append(core.results.get(f"c{cid}"))     # deleted results are not shown
                except MatchError:
                    continue
            return self._send(200, {"matches": core.matches.board(ids) if ids or not cids else [], "competitions": comps,
                                    "server_time_ms": core.now_ms()})
        if url.path == "/api/v1/competitions":         # public: the competition list (TV set-up)
            return self._send(200, {"competitions": [x for x in core.competitions.list() if not x["results_hidden"]]})
        if url.path == "/api/v1/results":              # public: everything on the Results page
            return self._send(200, {"results": core.results.listing()})
        rm = RESULTS_RE.match(url.path)
        if rm:
            try:
                res = core.results.get(rm.group(1), with_shots=q.get("shots", ["0"])[0] == "1")
            except MatchError as exc:
                raise NotFound(str(exc))
            if not rm.group(2):
                return self._send(200, res)
            return self._send_csv(results_csv(res, teams=rm.group(2) == "-teams.csv"),
                                  f"results-{res['key']}{'-teams' if rm.group(2) == '-teams.csv' else ''}.csv")
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
        if url.path.startswith("/api/v1/match/"):
            return self._match(url.path, "POST")
        if url.path in ("/api/v1/events", "/api/v1/heartbeat"):
            if not self._device_auth():
                self.rfile.read(min(int(self.headers.get("Content-Length", "0") or 0), MAX_BODY))
                return self._send(401, {"error": "bad token"})
            payload = self._read_json()
            if url.path.endswith("/events"):
                return self._send(200, core.handle_events(payload, ip))
            return self._send(200, core.handle_heartbeat(payload, ip))
        m = TARGET_RE.match(url.path)
        if m and m.group(2) in ("control", "control/release", "mode", "settings", "start-record"):
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise BadRequest("body must be an object")
            did, what = m.group(1), m.group(2)
            if what == "start-record":                 # NRA-style stage: the shooter (holding the lane) starts record
                try:
                    return self._send(200, {"match": core.shooter_start_record(did, payload.get("client_id"))})
                except MatchError as exc:
                    return self._send(400, {"error": str(exc)})
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


def results_csv(res: dict, teams: bool = False) -> str:
    """Results as CSV: one row per competitor (or team), every class, in place order. Values are plain numbers."""
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out)
    dec = res["scoring"] == "decimal"
    num = (lambda v: f"{v:.1f}") if dec else (lambda v: f"{v:.0f}")

    def txt(v):                                      # a cell a spreadsheet would run as a formula is kept as text
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    xs = "Inner tens" if dec else "X"
    if teams:
        w.writerow(["Competition", "Class", "Place", "Team", "Total", xs, "Counting members", "All members", "Note"])
        for cl in res["classes"]:
            for t in cl["teams"]:
                w.writerow([txt(res["name"]), txt(cl["name"]), t["place"] or "", txt(t["name"]), num(t["total"]) if t["total"] is not None else "",
                            t.get("x", ""), txt("; ".join(t["counting"])), txt("; ".join(t["members"])), t.get("reason", "")])
        return out.getvalue()
    n = max(1, -(-res["record_shots"] // 10))
    w.writerow(["Competition", "Class", "Place", "Family name", "Given name", "Team", "Relay", "Firing point", "Total", xs, "Shots"]
               + [f"Series {i + 1}" for i in range(n)] + ["Tie", "Penalties"])
    for cl in res["classes"]:
        for e in cl["individuals"]:
            dq = e.get("dq")
            pens = "; ".join(f"−{p['points']} {p['rule']}" for p in e.get("penalties") or [])
            if dq:
                pens = "; ".join(x for x in (f"DQ {dq['rule']}", pens) if x)
            w.writerow([txt(res["name"]), txt(cl["name"]), e["place"] if e["place"] is not None else "", txt(e["family"]), txt(e["given"]), txt(e["team"]),
                        e["relay"] or "", e["lane"] or "", "DQ" if dq else num(e["total"]), "" if dq else e["x"], "" if dq else e["shots"]]
                       + ["" if dq else (num(e["series"][i]) if i < len(e["series"]) else "") for i in range(n)]
                       + ["tied" if e["tied"] else "", txt(pens)])
    return out.getvalue()


class _TooLarge(Exception):
    pass


class FetHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256      # the default of 5 drops connections when many targets (up to 100) call in at once

    def __init__(self, addr, core: Core, token: str, admin: Admin = None, firmware: FirmwareStore = None, updates: Updates = None):
        super().__init__(addr, Handler)
        self.core = core
        self.token = token
        self.firmware = firmware if firmware is not None else FirmwareStore(None)
        self.updates = updates if updates is not None else Updates(None)
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
                firmware: FirmwareStore = None, updates: Updates = None) -> FetHTTPServer:
    return FetHTTPServer((host, port), core, token, admin, firmware, updates)
