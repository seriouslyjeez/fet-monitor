# FeT Display as a portable kit's server (user 2026-11-14, docs/PLAN-cyd.md step 5): the target reports to the display
# itself, as it would to FeT Monitor (docs/PROTOCOL.md: POST /api/v1/events and /api/v1/heartbeat, X-FET-Token), on the
# display's own network or any network both are on. Every line is stored on flash before it is acknowledged, a repeat
# is acknowledged again and stored once (at-least-once delivery), and the display forwards the lines to deepTENS when
# it reaches the internet (forward.py). No hardware here: it also runs under MicroPython on a computer, where
# tools/protocol_check.py checks it (tests/test_display_kit.py).
#
# Memory: the identities seen are kept per (device, boot) as ranges of seq numbers ([[1, 523]]), not one entry each
# (a target sends in order), so thousands of shots take a few bytes.
# Time: each line keeps the display's own uptime when it arrived (rx), the display's start (cb) and the request's
# uptime (ru): with the display's clock set later (NTP) in the same start, a shot's real time follows (the same rule
# as FeT Monitor's: arrival - request uptime + event uptime). A display started again before it was online cannot
# tell: those shots go to deepTENS with no time (practice only, user 2026-11-14).
import json
import os

DIR = "/kit-data"                    # not "/kit": a folder in / is imported before /lib/kit.py (2026-11-14 on the CYD)
LOG = DIR + "/events.jsonl"
MAX_EVENTS = 50
MAX_BODY = 16 * 1024                 # the protocol allows 256 KB; our targets send at most ~5 KB a request (BATCH_BYTES in
                                     # firmware/fet_bridge/src/core/bridge.cpp) and the CYD's memory is short
MAX_HEAD = 2048
MAX_RAW = 16384
TYPES = ("shot", "config", "arduino_boot", "log", "target_shot", "target_hello", "target_reply")
_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
HEARTBEAT_MS = 5000


def _device_ok(d):
    return isinstance(d, str) and 1 <= len(d) <= 64 and all(c in _ID_CHARS for c in d)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _same(a, b):
    """Constant-time comparison of two strings (the token)."""
    a, b = a.encode(), b.encode()
    diff = len(a) ^ len(b)
    for x, y in zip(a, b if len(a) == len(b) else a):
        diff |= x ^ y
    return diff == 0


class Ranges:
    """A set of non-negative integers as sorted [lo, hi] ranges."""
    def __init__(self):
        self.r = []

    def has(self, n):
        for lo, hi in self.r:
            if lo <= n <= hi:
                return True
            if n < lo:
                return False
        return False

    def add(self, n):
        r = self.r
        i = 0
        while i < len(r) and r[i][1] < n - 1:
            i += 1
        if i < len(r) and r[i][0] - 1 <= n <= r[i][1] + 1:
            r[i][0], r[i][1] = min(r[i][0], n), max(r[i][1], n)
            if i + 1 < len(r) and r[i + 1][0] <= r[i][1] + 1:      # it now touches the next range
                r[i][1] = max(r[i][1], r[i + 1][1])
                del r[i + 1]
        else:
            r.insert(i, [n, n])


class Store:
    """The kit's lines, append-only on flash (one JSON object a line), and what has been seen."""
    def __init__(self, path=LOG, cyd_boot=0, ticks=None, epoch=None):
        self.path, self.cyd_boot = path, cyd_boot
        self.ticks = ticks or (lambda: 0)            # the display's uptime, ms
        self.epoch = epoch or (lambda: None)         # the real time, ms, when the clock is set, else None
        self.seen = {}                               # (device, boot) -> Ranges
        self.count = 0
        self.devices = {}                            # device -> {"last_ms", "name", "rssi", ...} (memory only)
        d = path.rsplit("/", 1)[0]
        try:
            os.mkdir(d)
        except OSError:
            pass
        try:
            with open(path) as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue                     # a line cut short by a power cut: never acknowledged
                    self._mark(e["d"], e["b"], e["q"])
                    self.count += 1
        except OSError:
            pass

    def _mark(self, device, boot, seq):
        k = (device, boot)
        if k not in self.seen:
            self.seen[k] = Ranges()
        self.seen[k].add(seq)

    def has(self, device, boot, seq):
        k = (device, boot)
        return k in self.seen and self.seen[k].has(seq)

    def put(self, device, boot, req_uptime, events, face=None):
        """(acked, rejected, new lines) for one events request (already checked as a whole). face: the face on the
        target now (each line keeps it: deepTENS scores it on that face)."""
        acked, rejected, new = [], [], []
        rx, ep = self.ticks(), self.epoch()
        batch = set()
        for ev in events:
            if not isinstance(ev, dict):
                rejected.append({"boot_count": boot, "seq": None, "reason": "not an object"})
                continue
            b = ev.get("boot_count", boot)
            seq = ev.get("seq")
            why = None
            if not _int(b) or not _int(seq):
                why = "boot_count and seq: numbers"
            elif ev.get("type") not in TYPES:
                why = "unknown type"
            elif not _int(ev.get("uptime_ms")):
                why = "uptime_ms: a number"
            elif "raw" in ev and not (isinstance(ev["raw"], str) and len(ev["raw"]) <= MAX_RAW):
                why = "raw: text up to %d characters" % MAX_RAW
            elif "raw" not in ev and not isinstance(ev.get("data"), dict):
                why = "raw or data needed"
            if why:
                rejected.append({"boot_count": b, "seq": seq, "reason": why})
                continue
            if self.has(device, b, seq) or (b, seq) in batch:
                acked.append([b, seq])               # had it already: acknowledged again, stored once
                continue
            rec = {"d": device, "b": b, "q": seq, "t": ev["type"], "u": ev["uptime_ms"], "ru": req_uptime if b == boot else None,
                   "rx": rx, "cb": self.cyd_boot, "ep": ep, "f": face}
            if "raw" in ev:
                rec["r"] = ev["raw"]
            else:
                rec["j"] = ev["data"]
            new.append(rec)
            batch.add((b, seq))
            acked.append([b, seq])
        if new:
            with open(self.path, "a") as fh:          # stored before anything is acknowledged
                for rec in new:
                    fh.write(json.dumps(rec) + "\n")
                fh.flush()
            for rec in new:
                self._mark(rec["d"], rec["b"], rec["q"])
            self.count += len(new)
        return acked, rejected, new


class Kit:
    """The protocol's two requests. token: the kit's device token (in the target's setup). on_new(lines): called with
    the newly stored lines (the screen scores and shows them)."""
    def __init__(self, store, token, on_new=lambda lines: None, face=lambda: None):
        self.store, self.token, self.on_new, self.face = store, token, on_new, face

    def handle(self, method, path, headers, body):
        """(status, answer) for one request."""
        if method != "POST" or path not in ("/api/v1/events", "/api/v1/heartbeat"):
            return 404, {"error": "not found"}
        if not _same(headers.get("x-fet-token", ""), self.token):
            return 401, {"error": "bad token"}
        try:
            declared = int(headers.get("content-length", "0") or 0)
        except ValueError:
            declared = 0
        if len(body) > MAX_BODY or declared > MAX_BODY:
            return 413, {"error": "body too large"}
        try:
            req = json.loads(body)
        except ValueError:
            return 400, {"error": "body is not valid JSON"}
        if not isinstance(req, dict) or not _device_ok(req.get("device_id")):
            return 400, {"error": "device_id: 1 to 64 of A-Z a-z 0-9 _ -"}
        if not _int(req.get("boot_count")) or not _int(req.get("uptime_ms")):
            return 400, {"error": "boot_count and uptime_ms: numbers"}
        device, boot = req["device_id"], req["boot_count"]
        st = self.store
        info = st.devices.setdefault(device, {})
        info["last_ms"] = st.ticks()
        now = st.epoch() or 0
        if path == "/api/v1/heartbeat":
            for k in ("name", "rssi", "bridge_fw", "queue_depth"):
                if k in req:
                    info[k] = req[k]
            return 200, {"server_time_ms": now, "heartbeat_interval_ms": HEARTBEAT_MS, "commands": []}
        events = req.get("events")
        if not isinstance(events, list):
            return 400, {"error": "events: a list"}
        if len(events) > MAX_EVENTS:
            return 400, {"error": "at most %d events a request" % MAX_EVENTS}
        acked, rejected, new = st.put(device, boot, req["uptime_ms"], events, self.face())
        if new:
            self.on_new(new)
        return 200, {"acked": acked, "rejected": rejected, "server_time_ms": now}


def _head(data):
    """(method, path, headers) of a request's head (the bytes before the blank line)."""
    lines = data.decode("utf-8", "replace").split("\r\n")
    parts = lines[0].split(" ")
    hdr = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdr[k.strip().lower()] = v.strip()
    return (parts[0], parts[1].split("?")[0], hdr) if len(parts) >= 2 else None


async def serve(kit, port=8080, host="0.0.0.0"):
    """The kit's web server (asyncio: the screen keeps running). One request per connection. Memory: the head is read
    in small pieces, the body once into one buffer of its declared size (a body built up piece by piece left the CYD's
    memory in pieces too small for the screen, 2026-11-14)."""
    import asyncio
    import gc

    def answer(w, status, ans):
        body = json.dumps(ans).encode()
        w.write(("HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                 % (status, "OK" if status == 200 else "Error", len(body))).encode() + body)

    async def client(r, w):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = await asyncio.wait_for(r.read(256), 5)
                if not chunk or len(data) > MAX_HEAD:
                    break
                data += chunk
            head, sep, rest = data.partition(b"\r\n\r\n")
            req = _head(head) if sep else None
            if req is None:
                answer(w, 400, {"error": "not HTTP"})
            else:
                try:
                    n = int(req[2].get("content-length", "0") or 0)
                except ValueError:
                    n = -1
                if n > MAX_BODY:
                    answer(w, 413, {"error": "body too large"})
                elif n < 0:
                    answer(w, 400, {"error": "Content-Length: a number"})
                else:
                    gc.collect()
                    buf = bytearray(n)
                    got = min(len(rest), n)
                    buf[:got] = rest[:got]
                    mv = memoryview(buf)
                    try:
                        while got < n:
                            k = await asyncio.wait_for(r.readinto(mv[got:]), 5)
                            if not k:
                                break
                            got += k
                    except asyncio.TimeoutError:
                        pass                         # answered: cut short
                    del mv
                    if got < n:
                        answer(w, 400, {"error": "body cut short"})
                    else:
                        status, ans = kit.handle(req[0], req[1], req[2], buf)
                        answer(w, status, ans)
                    buf = None
            await w.drain()
        except Exception as exc:                     # never the server's end: printed to USB
            import sys
            sys.print_exception(exc)
        finally:
            w.close()
            await w.wait_closed()
            gc.collect()

    return await asyncio.start_server(client, host, port)
