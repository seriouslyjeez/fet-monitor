"""Copy FeT Display to a CYD over USB: its libraries and the parts of fetcore that place and score a shot to /lib,
main.py to /. Compiled to .mpy when mpy-cross is found (less memory on the board). Never touches the display's
settings (/config.json) or its touch calibration (/touch.json).

    python3 firmware/display/deploy.py /dev/cu.usbserial-1130

Needs mpremote (pip install mpremote) or MPREMOTE=<path>; mpy-cross on PATH or MPY_CROSS=<path> (optional; it must be
the same MicroPython version as the board)."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PORTABLE = ("__init__", "exact", "faces", "fmath", "lineparse", "scoring", "shotsolve", "targetproto")


def main(port):
    mp = os.environ.get("MPREMOTE") or shutil.which("mpremote")
    if not mp:
        sys.exit("mpremote not found: pip install mpremote, or MPREMOTE=<path>")
    cross = os.environ.get("MPY_CROSS") or shutil.which("mpy-cross")
    with tempfile.TemporaryDirectory() as tmp:
        lib = Path(tmp, "lib")
        (lib / "fetcore").mkdir(parents=True)
        srcs = [(p, lib / p.name) for p in sorted((HERE / "lib").glob("*.py"))]
        srcs += [(ROOT / "fetcore" / (m + ".py"), lib / "fetcore" / (m + ".py")) for m in PORTABLE]
        for src, dst in srcs:
            if cross and src.name not in ("__future__.py",):
                out = dst.with_suffix(".mpy")
                subprocess.run([cross, "-march=xtensa", "-o", str(out), str(src)], check=True)
            else:
                shutil.copy(src, dst)
        shutil.copy(HERE / "main.py", Path(tmp, "main.py"))
        cmd = [mp, "connect", port, "exec", "import os\ntry:\n    os.mkdir('/lib')\nexcept OSError:\n    pass\ntry:\n    os.mkdir('/lib/fetcore')\nexcept OSError:\n    pass",
               "+", "cp", "-r", str(lib), ":/", "+", "cp", str(Path(tmp, "main.py")), ":/main.py", "+", "reset"]
        subprocess.run(cmd, check=True)
    print("deployed to", port, "(compiled)" if cross else "(source)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
