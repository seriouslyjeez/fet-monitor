#!/usr/bin/env python3
"""Watch and set the microphone trip point of a freETarget Arduino Mega target (firmware 4.x) over USB.

    python3 tools/trippoint.py [port] [--log trippoint.csv]

The target's own trip-point test ({"TEST":11}) prints, five times a second, the trip voltage (V_Ref) and which
microphones have tripped since it was armed (the latch). This shows it live while you turn the trip-point trimmer
(RV1 on the 3.1 board), re-arms the latch on request, and logs every reading.

Keys (then Enter):  r  re-arm the latch (all four clear)     q  quit
Ending restarts the Mega (the firmware's own exit from the test leaves its shot timer off until a restart).

Procedure: set the lowest V_Ref at which NOTHING trips on its own for a quiet minute (re-arm, then wait), then add a
margin of about 0.2 V; check that a click near the target trips all four (r, click, watch the latch).
Port: /dev/cu.usbserial-* or /dev/cu.usbmodem* on a Mac, /dev/ttyUSB* or /dev/ttyACM* on the Pi; found by itself
when there is just one.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetserver.seriallink import SerialLink    # noqa: E402

STATUS_RE = re.compile(r"Latch:([NESW.]{4}).*?V_Ref:\s*(-?\d+(?:\.\d+)?)")
BANNER_RE = re.compile(r"freETarget")


def find_port() -> str:
    ports = sorted(set(glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyUSB*") +
                       glob.glob("/dev/ttyACM*")))
    if len(ports) != 1:
        raise SystemExit("plug in just the target's USB cable, or name the port: " + (", ".join(ports) or "none found"))
    return ports[0]


def parse(line: str):
    """(latch, volts) from a status line of the trip-point test, else None."""
    m = STATUS_RE.search(line)
    return (m.group(1), float(m.group(2))) if m else None


class Session:
    def __init__(self, link, out=sys.stdout, log=None, clock=time.monotonic):
        self.link, self.out, self.log, self.clock = link, out, log, clock
        self.armed_at = clock()
        self.last_latch = "...."
        self.vmin = self.vmax = None

    def start(self, wait_banner: float = 4.0) -> None:
        end = self.clock() + wait_banner              # opening the port restarts the Mega: let it start
        while self.clock() < end:
            line = self.link.readline(0.2)
            if line and BANNER_RE.search(line):
                self.say("target: " + line.strip())
        self.link.write(b'{"TEST":11}\n')
        self.rearm()

    def rearm(self) -> None:
        self.link.write(b"R")
        self.armed_at = self.clock()
        self.last_latch = "...."
        self.say("re-armed: all four clear (r to re-arm again, q to quit)")

    def say(self, text: str) -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def handle(self, line: str) -> None:
        got = parse(line)
        if not got:
            return
        latch, volts = got
        self.vmin = volts if self.vmin is None else min(self.vmin, volts)
        self.vmax = volts if self.vmax is None else max(self.vmax, volts)
        if self.log:
            self.log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{volts:.2f},{latch}\n")
            self.log.flush()
        if latch != self.last_latch and latch != "....":
            self.say(f"  TRIPPED {latch}  {self.clock() - self.armed_at:5.1f} s after arming  (V_Ref {volts:.2f} V)")
        self.last_latch = latch
        self.out.write(f"\rV_Ref {volts:4.2f} V   latch {latch}   armed {self.clock() - self.armed_at:5.0f} s   ")
        self.out.flush()

    def stop(self) -> None:
        self.link.write(b"!")
        self.say("\nleaving the trip-point test; V_Ref seen %s to %s V" % (
            "?" if self.vmin is None else f"{self.vmin:.2f}", "?" if self.vmax is None else f"{self.vmax:.2f}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?")
    ap.add_argument("--log", default="trippoint.csv", help="CSV of every reading (time, volts, latch)")
    a = ap.parse_args(argv)
    port = a.port or find_port()
    print(f"opening {port} (the target restarts)")
    with open(a.log, "a") as log:
        link = SerialLink(port)
        s = Session(link, log=log)
        try:
            s.start()
            while True:
                r, _, _ = select.select([sys.stdin], [], [], 0)
                if r:
                    cmd = sys.stdin.readline().strip().lower()
                    if cmd == "q":
                        break
                    if cmd in ("", "r"):
                        s.rearm()
                line = link.readline(0.1)
                if line:
                    s.handle(line)
        except KeyboardInterrupt:
            pass
        finally:
            s.stop()
            link.close()
            time.sleep(0.3)
            SerialLink(port).close()                  # opening the port again restarts the Mega: shot timer back on
            print("the target restarted; readings logged in", os.path.abspath(a.log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
