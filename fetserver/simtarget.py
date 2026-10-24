"""A simulated target that speaks the same protocol as the ESP-01 bridge will.

Used by the tests and for trying the server without hardware:
    python -m fetserver.simtarget --url http://127.0.0.1:8080 --token TOKEN --shots 20
Behaves like the planned firmware: events go into a queue that survives reboots,
batches are POSTed, and an event is dropped from the queue only when acknowledged.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional

VERSION = "4.3.0 December 21, 2023"
ECHO_SAMPLE = ('{\n"NAME":"TARGET",\n"ANGLE": 45,\n"CALIBREx10": 45,\n"SENSOR": 230.000000,\n"SN": 302,\n'
               '"VERSION": "4.3.0 December 21, 2023",\n"BD_REV": 3.20\n}')


def shot_line(n: int, x: float, y: float, counts, time_s: float, vref=4.94, temp=19.0) -> str:
    """Exactly the 4.3.0 firmware format, including its doubled commas."""
    return ('{"shot":%d, "miss":0, "name":"TARGET", "time":%.2f ,"x":%.2f, "y":%.2f , '
            '"N":%d, "E":%d, "S":%d, "W":%d , "V_REF":%.2f, , "T":%.2f, , "VERSION":"%s" }'
            % (n, time_s, x, y, counts[0], counts[1], counts[2], counts[3], vref, temp, VERSION))


def miss_line(n: int, counts=(0, 5596, 13199, 0)) -> str:
    return ('{"shot":%d, "miss":1, "name":"TARGET", "x":0, "y":0 , "N":%d, "E":%d, "S":%d, "W":%d , "face":0 }'
            % (n, *counts))


class SimTarget:
    def __init__(self, url: str, token: str, device_id: str = "sim-01", name: str = "Sim target",
                 clock_ms: Optional[Callable[[], int]] = None, seed: int = 1, ack_loss: float = 0.0,
                 batch_size: int = 10):
        self.url, self.token, self.device_id, self.name = url.rstrip("/"), token, device_id, name
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.rng = random.Random(seed)
        self.ack_loss = ack_loss
        self.batch_size = batch_size
        self.boot_count = 1
        self.seq = 0
        self.queue: List[dict] = []        # stands in for the flash queue: survives reboot()
        self.shot_no = 0
        self.last_cmd_id = 0               # persisted: each command id runs at most once
        self.executed: List[dict] = []
        self._pending_results: List[dict] = []
        self._boot_t0 = self.clock_ms()

    # -- device side --
    def uptime_ms(self) -> int:
        return self.clock_ms() - self._boot_t0

    def reboot(self) -> None:
        self.boot_count += 1
        self.seq = 0
        self.shot_no = 0
        self._boot_t0 = self.clock_ms()

    def _enqueue(self, etype: str, raw: str) -> None:
        self.seq += 1
        self.queue.append({"boot_count": self.boot_count, "seq": self.seq, "type": etype,
                           "uptime_ms": self.uptime_ms(), "raw": raw})

    def fire(self, x_mm: Optional[float] = None, y_mm: Optional[float] = None) -> None:
        self.shot_no += 1
        if x_mm is None:
            x_mm, y_mm = self.rng.gauss(0, 8), self.rng.gauss(0, 8)
        counts = [self.rng.randint(0, 600) for _ in range(4)]
        counts[self.rng.randrange(4)] = 0
        self._enqueue("shot", shot_line(self.shot_no, x_mm, y_mm, counts, self.uptime_ms() / 1000.0))

    def fire_miss(self) -> None:
        self.shot_no += 1
        self._enqueue("shot", miss_line(self.shot_no))

    def send_config(self) -> None:
        self._enqueue("config", ECHO_SAMPLE)

    # -- network side --
    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-FET-Token": self.token})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())

    def deliver(self, max_batches: int = 1000) -> bool:
        """Try to send the queue. True when it is empty, False if the server could not be reached."""
        for _ in range(max_batches):
            if not self.queue:
                return True
            batch = self.queue[: self.batch_size]
            try:
                reply = self._post("/api/v1/events", {"device_id": self.device_id, "boot_count": self.boot_count,
                                                      "uptime_ms": self.uptime_ms(), "events": batch})
                if self.rng.random() < self.ack_loss:
                    raise ConnectionError("simulated lost response")   # server stored it; we never hear
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                return False
            done = {(b, s) for b, s in reply["acked"]}
            self.queue = [e for e in self.queue if (e["boot_count"], e["seq"]) not in done]
            if not done:
                return False        # nothing acknowledged: do not spin
        return not self.queue

    def heartbeat(self) -> Optional[dict]:
        try:
            reply = self._post("/api/v1/heartbeat", {
                "device_id": self.device_id, "boot_count": self.boot_count, "uptime_ms": self.uptime_ms(),
                "name": self.name, "bridge_fw": "sim-0.1", "arduino_version": VERSION, "rssi": -55,
                "queue_depth": len(self.queue), "overflow_count": 0, "cmd_results": self._pending_results})
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            return None
        self._pending_results = []
        for cmd in reply.get("commands", []):
            if cmd["id"] > self.last_cmd_id:           # execute each id at most once
                self.last_cmd_id = cmd["id"]
                self.executed.append(cmd)
            self._pending_results.append({"id": cmd["id"], "status": "done", "reply": "simulated"})
        return reply


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="simulated freETarget")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--token", required=True)
    ap.add_argument("--device-id", default="sim-01")
    ap.add_argument("--name", default="Sim target")
    ap.add_argument("--shots", type=int, default=20)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--miss-rate", type=float, default=0.05)
    ap.add_argument("--boot-count", type=int, default=None, help="default: current time, so every run is a new boot")
    args = ap.parse_args(argv)
    t = SimTarget(args.url, args.token, args.device_id, args.name)
    t.boot_count = args.boot_count if args.boot_count is not None else int(time.time())
    t.send_config()
    for i in range(args.shots):
        t.fire_miss() if t.rng.random() < args.miss_rate else t.fire()
        t.heartbeat()
        print("delivered" if t.deliver() else "server unreachable, %d queued" % len(t.queue), flush=True)
        time.sleep(args.interval)
    while not t.deliver():
        time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
