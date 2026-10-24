#!/usr/bin/env python3
"""Bench test without the target: plays the Arduino's serial output into the ESP-01 through a USB-serial adapter.

Wire the adapter's TX -> ESP RX, RX -> ESP TX, GND -> GND (3.3 V logic!), power the ESP from 3.3 V, and run:
    pip install pyserial
    python3 inject.py /dev/cu.usbserial-XXXX            # a banner, 3 shots, a miss and an ECHO reply, 1 s apart
    python3 inject.py /dev/cu.usbserial-XXXX --count 20 # 20 shots
Everything the ESP sends back is printed (it should print nothing: it never answers the Arduino's AT probe).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from fetserver.simtarget import ECHO_SAMPLE, miss_line, shot_line  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--gap", type=float, default=1.0)
    a = ap.parse_args()
    import serial  # pyserial

    s = serial.Serial(a.port, a.baud, timeout=0.05)

    def send(text):
        s.write(text.encode() + b"\n")
        s.flush()
        end = time.time() + a.gap
        while time.time() < end:
            back = s.read(256)
            if back:
                print("ESP SAID:", back)

    send('freETarget "4.3.0 December 21, 2023"')
    send("+++")
    send("AT")
    for n in range(1, a.count + 1):
        send(shot_line(n, 1.0 * n % 7 - 3, 2.0 - n % 5, (100 + n, 200, 300, 400), 1.5 + n))
    send(miss_line(a.count + 1))
    send(ECHO_SAMPLE)
    print("sent %d shots, 1 miss, a banner and an ECHO reply" % a.count)


if __name__ == "__main__":
    main()
