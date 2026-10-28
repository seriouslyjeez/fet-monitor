#!/usr/bin/env python3
"""Check a server against the FeT Bridge protocol (docs/PROTOCOL.md) by playing a target.

    python3 tools/protocol_check.py http://192.168.1.20:8080 <device token> [--device check-target-1]

It sends heartbeats and events as FeT Bridge does, then checks the answers: a wrong token is refused, events are
acknowledged by [boot_count, seq], a repeat is acknowledged again (and not stored twice, as far as can be seen),
a malformed event is rejected and not acknowledged, a batch of 51 is refused. It creates a target with the given
device id and two shots on the server: use a test server, or remove that target afterwards (Range Control).
Standard library only. Exit code 0 when every check passed.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request

SHOT = '{"shot":%d, "miss":0, "name":"CHECK", "time":1.00 ,"x":%s, "y":0.50 , "N":1, "E":2, "S":3, "W":0 , "VERSION":"4.3.0" }'


class Check:
    def __init__(self, base: str, token: str, device: str):
        self.base, self.token, self.device = base.rstrip("/"), token, device
        self.boot = random.randint(1000, 3_000_000)
        self.failures = 0

    def post(self, path: str, body, token: str = None):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "X-FET-Token": self.token if token is None else token})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}

    def ok(self, cond: bool, what: str) -> None:
        print(("ok    " if cond else "FAIL  ") + what)
        self.failures += 0 if cond else 1

    def run(self) -> int:
        hb = {"device_id": self.device, "boot_count": self.boot, "uptime_ms": 5000, "name": "protocol check", "bridge_fw": "check"}
        st, body = self.post("/api/v1/heartbeat", hb, token="wrong-" + self.token)
        self.ok(st == 401, f"a wrong token is refused (got {st})")
        st, body = self.post("/api/v1/heartbeat", hb)
        self.ok(st == 200 and isinstance(body.get("server_time_ms"), int) and isinstance(body.get("commands"), list),
                f"heartbeat answered with server_time_ms and commands (got {st} {body})")
        ev = lambda seq, x: {"seq": seq, "type": "shot", "uptime_ms": 4000 + seq, "raw": SHOT % (seq, x)}
        batch = {"device_id": self.device, "boot_count": self.boot, "uptime_ms": 6000, "events": [ev(1, "1.00"), ev(2, "2.00")]}
        st, body = self.post("/api/v1/events", batch)
        self.ok(st == 200 and body.get("acked") == [[self.boot, 1], [self.boot, 2]], f"two shots acknowledged as [boot, seq] (got {st} {body})")
        st, body = self.post("/api/v1/events", batch)
        self.ok(st == 200 and body.get("acked") == [[self.boot, 1], [self.boot, 2]], "a repeat is acknowledged again (at-least-once delivery)")
        bad = {"device_id": self.device, "boot_count": self.boot, "uptime_ms": 7000,
               "events": [{"seq": 3, "type": "nonsense", "uptime_ms": 6500}, ev(4, "0.50")]}
        st, body = self.post("/api/v1/events", bad)
        self.ok(st == 200 and body.get("acked") == [[self.boot, 4]] and [r.get("seq") for r in body.get("rejected", [])] == [3],
                f"a malformed event is rejected, not acknowledged; the good one is (got {body})")
        big = {"device_id": self.device, "boot_count": self.boot, "uptime_ms": 8000, "events": [ev(100 + i, "1.00") for i in range(51)]}
        st, body = self.post("/api/v1/events", big)
        self.ok(st == 400, f"more than 50 events in one request are refused (got {st})")
        st, body = self.post("/api/v1/events", {"device_id": "bad id!", "boot_count": self.boot, "uptime_ms": 1, "events": []})
        self.ok(st == 400, f"an invalid device_id is refused (got {st})")
        print(f"\n{'All checks passed' if not self.failures else str(self.failures) + ' check(s) failed'}; "
              f"test target '{self.device}' (boot {self.boot}) now has 3 shots on the server.")
        return 1 if self.failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", help="the server's base URL, e.g. http://192.168.1.20:8080")
    ap.add_argument("token", help="the device token")
    ap.add_argument("--device", default="protocol-check", help="device id to use (default protocol-check)")
    a = ap.parse_args(argv)
    return Check(a.base, a.token, a.device).run()


if __name__ == "__main__":
    sys.exit(main())
