"""For development: copy FeT Display's modules (or some of them) to a CYD's /lib over USB, where they override the
ones frozen into the firmware (micropython/build.py freezes lib/ and fetcore's portable modules: they run from flash).
A module in /lib is loaded into memory: with everything copied, a portable kit runs out of memory, so copy only what
you are changing, or build and flash the firmware. Compiled to .mpy when mpy-cross is found. Never touches the
display's settings (/config.json), its touch calibration (/touch.json) or a kit's shots (/kit-data).

    python3 firmware/display/deploy.py /dev/cu.usbserial-1130 app kit     # lib/app.py and lib/kit.py only
    python3 firmware/display/deploy.py /dev/cu.usbserial-1130 --all       # every module (and fetcore's)
    python3 firmware/display/deploy.py /dev/cu.usbserial-1130 --clear     # back to the frozen code only

Needs mpremote (pip install mpremote) or MPREMOTE=<path>; mpy-cross on PATH or MPY_CROSS=<path> (optional; it must be
the same MicroPython version as the board: dist/display/mpy-cross from build.py)."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PORTABLE = ("__init__", "exact", "faces", "fmath", "lineparse", "scoring", "shotsolve", "targetproto")
BOOT = '''# FeT Display, development (deploy.py): modules in /lib override the frozen ones. deploy.py --clear removes this.
import sys
sys.path[:] = ["", "/lib", ".frozen"]
'''
CLEAR = '''import os
def rm(p):
    try:
        if os.stat(p)[0] & 0x4000:
            for f in os.listdir(p):
                rm(p + "/" + f)
            os.rmdir(p)
        else:
            os.remove(p)
    except OSError:
        pass
for p in ("/lib", "/boot.py", "/main.py"):
    rm(p)
with open("/boot.py", "w") as fh:
    fh.write("# boot.py -- run on boot-up\\n")
'''


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("modules", nargs="*", help="modules of lib/ to copy (e.g. app kit), or fetcore.<name>")
    ap.add_argument("--all", action="store_true", help="every module of lib/ and fetcore's portable ones")
    ap.add_argument("--clear", action="store_true", help="remove every copy: the frozen code runs")
    a = ap.parse_args(argv)
    mp = os.environ.get("MPREMOTE") or shutil.which("mpremote")
    if not mp:
        sys.exit("mpremote not found: pip install mpremote, or MPREMOTE=<path>")
    if a.clear:
        subprocess.run([mp, "connect", a.port, "exec", CLEAR, "+", "reset"], check=True)
        print("cleared", a.port, "(the frozen code runs)")
        return
    if a.all:
        srcs = [(p, "lib/" + p.name) for p in sorted((HERE / "lib").glob("*.py"))]
        srcs += [(ROOT / "fetcore" / (m + ".py"), "lib/fetcore/" + m + ".py") for m in PORTABLE]
    elif a.modules:
        srcs = []
        for m in a.modules:
            if m.startswith("fetcore."):
                src, dst = ROOT / "fetcore" / (m[8:] + ".py"), "lib/fetcore/" + m[8:] + ".py"
            else:
                src, dst = HERE / "lib" / (m + ".py"), "lib/" + m + ".py"
            if not src.exists():
                sys.exit("no such module: %s" % src)
            srcs.append((src, dst))
    else:
        sys.exit("which modules? (e.g. app kit), --all or --clear")
    cross = os.environ.get("MPY_CROSS") or shutil.which("mpy-cross")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "lib" / "fetcore").mkdir(parents=True)
        for src, dst in srcs:
            out = Path(tmp, dst)
            if cross and src.name != "__future__.py":
                subprocess.run([cross, "-march=xtensa", "-o", str(out.with_suffix(".mpy")), str(src)], check=True)
            else:
                shutil.copy(src, out)
        Path(tmp, "boot.py").write_text(BOOT)
        mk = "import os\nfor d in ('/lib', '/lib/fetcore'):\n    try:\n        os.mkdir(d)\n    except OSError:\n        pass"
        if not any(d.startswith("lib/fetcore/") for _, d in srcs):
            os.rmdir(Path(tmp, "lib", "fetcore"))
        subprocess.run([mp, "connect", a.port, "exec", mk, "+", "cp", "-r", str(Path(tmp, "lib")), ":/",
                        "+", "cp", str(Path(tmp, "boot.py")), ":/boot.py", "+", "reset"], check=True)
    print("copied %d module(s) to %s %s; they override the frozen ones" % (len(srcs), a.port, "(compiled)" if cross else "(source)"))


if __name__ == "__main__":
    main()
