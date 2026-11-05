# SPDX-License-Identifier: GPL-2.0-or-later
"""esptool with the stub flashers Debian leaves out. The flash station (fetserver/flashstation.py) runs this file as
its own process: `python3 .../esptool_shim/run.py <esptool arguments>`.

Debian's esptool package (Trixie: 4.7.0) has no stub flasher for the Xtensa chips ESP32, ESP32-S2 and ESP32-S3
(Debian builds the stubs from source and has no Xtensa compiler for them), so every esptool command that loads the
stub on the ESP32-S3 board ends in FileNotFoundError: .../stub_flasher/stub_flasher_32s3.json. This folder carries
those three files exactly as esptool 4.7.0 ships them (PyPI esptool-4.7.0.tar.gz, sha256 01454e69e1ef3601215db8
3ff2cb1fc79ece67d24b0e5d43d451b410447c4893; their source: github.com/espressif/esptool, tag v4.7.0, flasher_stub/).
They are used only when esptool lacks the file, only with esptool 4.7.0 (the stub must match its esptool), and only
if the file's hash is the one below. Everything else is esptool itself, unchanged.

This folder (run.py and the stub files) is under esptool's license, GPL-2.0-or-later (LICENSE here), not FeT
Monitor's MIT license; FeT Monitor only runs it as a separate program.
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FOR_VERSION = "4.7.0"
STUBS = {
    "stub_flasher_32.json": "8a672b647d6e9034cb223f2c756fffbc234af628cf09679b74027bc47c4ff5e4",
    "stub_flasher_32s2.json": "f1de232fd1298ea85485bd378e1d10810267c2dd2a9a19deda599f5377cd2342",
    "stub_flasher_32s3.json": "8fca153b59e52fe24957b2387938e9186659d1311dcb7f2a1ea4c77001bf9a01",
}


def resolve(path, version, here=HERE):
    """esptool's own stub file if it has it; else ours, if esptool is the version ours came from and ours is intact;
    else esptool's path unchanged (it then fails as before)."""
    if os.path.exists(path):
        return path
    name = os.path.basename(path)
    if version != FOR_VERSION or name not in STUBS:
        return path
    ours = os.path.join(here, name)
    try:
        with open(ours, "rb") as fh:
            ok = hashlib.sha256(fh.read()).hexdigest() == STUBS[name]
    except OSError:
        return path
    return ours if ok else path


def patch():
    """Point esptool's stub lookup (loader.get_stub_json_path, used by run_stub) at resolve()."""
    import esptool
    import esptool.loader as loader
    original = loader.get_stub_json_path
    loader.get_stub_json_path = lambda chip_name: resolve(original(chip_name), esptool.__version__)
    return esptool


if __name__ == "__main__":
    esptool = patch()
    sys.argv[0] = "esptool"
    (getattr(esptool, "_main", None) or esptool.main)()
