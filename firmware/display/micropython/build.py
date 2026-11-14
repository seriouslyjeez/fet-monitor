"""Build MicroPython for FeT Display (the CYD with double-precision floats), once per MicroPython version.

    python3 firmware/display/micropython/build.py            # -> dist/display/micropython-cyd-double-1.29.0.bin

Needs ESP-IDF v5.5.2 (MicroPython 1.29's recommended version) installed, e.g. in ~/esp/esp-idf (IDF_PATH or
--idf), with cmake and ninja (brew install cmake ninja). Clones MicroPython v1.29.0 into ~/esp/micropython-fet (or
--mp), applies float-impl.patch (a board may choose its float precision: ESP32's mpconfigport.h fixes single
precision) and mpy-tool-float.patch (frozen float constants exact), builds mpy-cross and the firmware with
boards/CYD_DOUBLE, with FeT Display (lib/, main.py) and fetcore's portable modules frozen in (they run from flash:
boards/CYD_DOUBLE/manifest.py), and merges bootloader, partition table and firmware into one image. Flash it (the first
time: erase the whole chip first):

    esptool --port /dev/cu.usbserial-XXXX erase-flash
    esptool --port /dev/cu.usbserial-XXXX --baud 460800 write-flash 0x1000 dist/display/micropython-cyd-double-1.29.0.bin

That is all a display needs. A change to the display's code or fetcore needs this build again (deploy.py copies
changed modules over the frozen ones while developing).
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MP_TAG = "v1.29.0"
PATCHES = ("float-impl.patch",           # a board may choose its float precision (ESP32's mpconfigport.h fixes single)
           "mpy-tool-float.patch")       # frozen float constants: 17 digits (%.16g lost a double's last bit) and inf/nan


def run(cmd, **kw):
    print("+", cmd if isinstance(cmd, str) else " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--idf", default=os.environ.get("IDF_PATH") or str(Path.home() / "esp/esp-idf"))
    ap.add_argument("--mp", default=str(Path.home() / "esp/micropython-fet"))
    a = ap.parse_args()
    idf, mp = Path(a.idf), Path(a.mp)
    if not (idf / "export.sh").exists():
        sys.exit("ESP-IDF not found at %s: install v5.5.2 (git clone -b v5.5.2 --recursive https://github.com/espressif/esp-idf.git; ./install.sh esp32)" % idf)
    for tool in ("cmake", "ninja"):
        if not shutil.which(tool):
            sys.exit("%s not found: brew install cmake ninja" % tool)
    if not mp.exists():
        run(["git", "clone", "--depth", "1", "--branch", MP_TAG, "https://github.com/micropython/micropython.git", str(mp)])
    tag = subprocess.run(["git", "-C", str(mp), "describe", "--tags", "--always"], capture_output=True, text=True).stdout.strip()
    if not tag.startswith(MP_TAG):
        sys.exit("%s is MicroPython %s, not %s: use another --mp" % (mp, tag, MP_TAG))
    for patch in PATCHES:
        if subprocess.run(["git", "-C", str(mp), "apply", "--check", "--reverse", str(HERE / patch)], capture_output=True).returncode != 0:
            run(["git", "-C", str(mp), "apply", str(HERE / patch)])          # not applied yet
    run(["make", "-C", str(mp / "mpy-cross"), "-j8"])
    board = HERE / "boards" / "CYD_DOUBLE"
    port = mp / "ports" / "esp32"
    stale = port / "build-CYD_DOUBLE" / "frozen_content.c"
    if stale.exists():
        stale.unlink()                   # made again every time: make does not see a change to the freezer itself
    run("bash -c '. \"%s/export.sh\" >/dev/null && make -C \"%s\" BOARD_DIR=\"%s\" submodules && make -C \"%s\" BOARD_DIR=\"%s\" -j8'"
        % (idf, port, board, port, board), shell=True)
    out = port / "build-CYD_DOUBLE"
    dist = ROOT / "dist" / "display"
    dist.mkdir(parents=True, exist_ok=True)
    image = dist / ("micropython-cyd-double-%s.bin" % MP_TAG.lstrip("v"))
    esptool = shutil.which("esptool") or shutil.which("esptool.py")
    if not esptool:
        sys.exit("esptool not found (pip install esptool): the parts are in %s" % out)
    run([esptool, "--chip", "esp32", "merge-bin", "-o", str(image), "--target-offset", "0x1000", "--flash-mode", "dio", "--flash-size", "4MB",
         "0x1000", str(out / "bootloader" / "bootloader.bin"), "0x8000", str(out / "partition_table" / "partition-table.bin"),
         "0x10000", str(out / "micropython.bin")])
    shutil.copy(mp / "mpy-cross" / "build" / "mpy-cross", dist / "mpy-cross")
    print("\nbuilt", image, "(flash at 0x1000) and", dist / "mpy-cross", "(for deploy.py: MPY_CROSS=%s)" % (dist / "mpy-cross"))


if __name__ == "__main__":
    main()
