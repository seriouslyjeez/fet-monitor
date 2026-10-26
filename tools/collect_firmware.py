#!/usr/bin/env python3
"""Keep each Arduino export of the FeT Bridge firmware under its own name.

Arduino's Sketch > Export Compiled Binary always writes the same file, so a 1 MB and a 4 MB build overwrite each other.
Run this after each export:

    python3 tools/collect_firmware.py

It checks the export with the same rules the Pi uses for uploads and copies it to
dist/firmware/fet-bridge-<version>-<flash>.bin (plus a .sha256 file). It never overwrites a different image with the
same name unless --force is given.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fetserver.firmware import BadImage, flash_label, inspect  # noqa: E402

EXPORT = ROOT / "firmware/fet_bridge/build/esp8266.esp8266.generic/fet_bridge.ino.bin"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bin", nargs="?", default=str(EXPORT), help="the exported .bin (default: Arduino's export path)")
    ap.add_argument("--out", default=str(ROOT / "dist/firmware"))
    ap.add_argument("--force", action="store_true", help="replace a different image that has the same name")
    a = ap.parse_args()
    src = Path(a.bin)
    if not src.is_file():
        print(f"no export at {src}: in Arduino use Sketch > Export Compiled Binary first", file=sys.stderr)
        return 1
    data = src.read_bytes()
    try:
        meta = inspect(data)
    except BadImage as exc:
        print(f"not usable: {exc}", file=sys.stderr)
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"fet-bridge-{meta['version']}-{flash_label(meta['flash_size']).replace(' ', '')}.bin"
    if dest.exists() and dest.read_bytes() != data and not a.force:
        print(f"{dest.name} already exists with different contents (rebuilt without a version bump?). "
              "Bump FET_FW_VERSION, or pass --force to replace it.", file=sys.stderr)
        return 1
    shutil.copyfile(src, dest)
    digest = hashlib.sha256(data).hexdigest()
    dest.with_name(dest.name + ".sha256").write_text(f"{digest}  {dest.name}\n")
    print(f"{dest}\n  version {meta['version']}, {flash_label(meta['flash_size'])} flash, {meta['size']} bytes, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
