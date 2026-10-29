#!/usr/bin/env python3
"""Build the FeT Bridge firmware for both flash layouts without the Arduino IDE.

    python3 tools/build_firmware.py            # 1 MB and 4 MB
    python3 tools/build_firmware.py 4MB        # just one

Uses arduino-cli (brew install arduino-cli) with the ESP8266 core 3.1.2 the IDE installed, and exactly the IDE
settings in firmware/README.md (Generic ESP8266, DOUT, 80 MHz, 115200; Flash Size per layout; everything else default).
Checked 2026-10-26: for the 0.2.1 source this gives byte-for-byte the same .bin as the IDE's Export Compiled Binary.
Each image is then checked and kept by tools/collect_firmware.py as dist/firmware/fet-bridge-<version>-<flash>.bin.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKETCH = ROOT / "firmware" / "fet_bridge"
CORE = "esp8266:esp8266"
CORE_VERSION = "3.1.2"
LAYOUTS = {"1MB": "1M128", "4MB": "4M2M"}       # Flash Size: 1MB (FS:128KB OTA:~438KB) / 4MB (FS:2MB OTA:~1019KB)
FQBN = "esp8266:esp8266:generic:eesz={eesz},FlashMode=dout,xtal=80,baud=115200"
# Over-the-air updates (core 3.1.2 Updater/getFreeSketchSpace): the running image and the new one, each rounded up to
# 4 KB sectors, must both fit below the file system, which starts at 0xDB000 in the 1 MB layout (eagle.flash.1m128.ld).
SKETCH_AREA_1MB = 0xDB000
SECTOR = 4096


def sectors(n: int) -> int:
    return (n + SECTOR - 1) // SECTOR * SECTOR


def ota_check_1mb(size: int) -> tuple:
    """(ok, text): can a 1 MB module running this image take an update of the same size, and how large may the next be."""
    room = SKETCH_AREA_1MB - sectors(size)
    return sectors(size) <= room, (f"1 MB over-the-air room: a module running this image can take an update of up to "
                                   f"{room} bytes ({room - sectors(size)} bytes more than this image)")


def main() -> int:
    if not shutil.which("arduino-cli"):
        print("arduino-cli is not installed: brew install arduino-cli", file=sys.stderr)
        return 1
    cores = subprocess.run(["arduino-cli", "core", "list"], capture_output=True, text=True).stdout
    if not any(line.split()[:2] == [CORE, CORE_VERSION] for line in cores.splitlines()):
        print(f"{CORE} {CORE_VERSION} is not installed (arduino-cli core install {CORE}@{CORE_VERSION}, with the "
              "ESP8266 board manager URL); other versions build different firmware", file=sys.stderr)
        return 1
    wanted = sys.argv[1:] or list(LAYOUTS)
    for name in wanted:
        if name not in LAYOUTS:
            print(f"unknown layout {name}: choose from {', '.join(LAYOUTS)}", file=sys.stderr)
            return 1
    for name in wanted:
        with tempfile.TemporaryDirectory() as out:
            print(f"== building {name} ...", flush=True)
            r = subprocess.run(["arduino-cli", "compile", "-b", FQBN.format(eesz=LAYOUTS[name]), "--output-dir", out, str(SKETCH)],
                               capture_output=True, text=True)
            if r.returncode:
                print(r.stdout[-3000:] + r.stderr[-3000:], file=sys.stderr)
                return 1
            binary = Path(out) / "fet_bridge.ino.bin"
            if name == "1MB":
                ok, text = ota_check_1mb(binary.stat().st_size)
                print("  " + text, flush=True)
                if not ok:
                    print("  too large: a 1 MB module could not update itself to a build of this size; make it smaller", file=sys.stderr)
                    return 1
            c = subprocess.run([sys.executable, str(ROOT / "tools/collect_firmware.py"), str(binary)])
            if c.returncode:
                return c.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
