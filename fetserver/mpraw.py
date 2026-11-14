"""MicroPython's raw REPL over a serial link (as mpremote does), for FeT Display on a CYD: the flash station stops what
the display runs (Ctrl-C until a prompt), enters raw mode (Ctrl-A), sends a short script, runs it (Ctrl-D) and reads
"OK", its output, \\x04, any error, \\x04, ">". The same bytes deepTENS's "Program a display" page sends
(core/static/core/display.js), tried on the user's CYD (2026-11-14): 1.6 s right after a flash, 3.4 s while it ran.
A link: write(bytes), read(timeout) -> bytes or None (seriallink.SerialLink)."""
from __future__ import annotations

import json
import time
from typing import Optional, Tuple


class ReplError(OSError):
    pass


def _until(link, buf: bytearray, done, timeout: float, poke: Optional[bytes] = None) -> bool:
    end, nxt = time.monotonic() + timeout, 0.0
    while time.monotonic() < end:
        if poke is not None and time.monotonic() >= nxt:
            link.write(poke)
            nxt = time.monotonic() + 0.3
        got = link.read(0.1)
        if got:
            buf += got
        if done(bytes(buf)):
            return True
    return False


def run(link, code: str, timeout: float = 20.0) -> Tuple[str, str]:
    """(output, error) of code run on the display; ReplError when it does not answer as MicroPython."""
    buf = bytearray()
    # Ctrl-C stops a program; Ctrl-B leaves raw mode if an earlier exchange left it there: either way a ">>> " prompt
    if not _until(link, buf, lambda b: b">>> " in b, timeout, b"\r\x03\x02"):
        raise ReplError("the display did not answer over USB as MicroPython")
    buf = bytearray()
    link.write(b"\x01")
    if not _until(link, buf, lambda b: b"raw REPL; CTRL-B to exit\r\n>" in b, 5):
        raise ReplError("the display did not enter its raw mode")
    buf = bytearray()
    data = code.encode()
    for i in range(0, len(data), 256):
        link.write(data[i:i + 256])
        time.sleep(0.01)
    link.write(b"\x04")
    if not _until(link, buf, lambda b: b.count(b"\x04") >= 2 and b.rstrip().endswith(b">"), 15):
        raise ReplError("the display did not finish running the script")
    link.write(b"\x02")                                       # back to the normal prompt
    text = bytes(buf).decode("utf-8", "replace")
    if not text.startswith("OK"):
        raise ReplError("the display refused the script")
    out, err = (text[2:].split("\x04") + ["", ""])[:2]
    return out.strip(), err.strip()


def reset(link) -> None:
    """Restart the display (it starts FeT Display with what was written)."""
    link.write(b"\x01import machine\nmachine.reset()\n\x04")
    time.sleep(0.3)


PROBE = ("import os\ntry:\n    import fetdisplay\n    v = fetdisplay.VERSION\nexcept ImportError:\n    v = '-'\n"
         "print('FETD-PROBE', v, 'config.json' in os.listdir('/'))\n")


def probe(link, timeout: float = 8.0) -> Optional[dict]:
    """What a display runs: {"version": "0.2.0" or None (FeT Display before 0.2.0, or other MicroPython), "configured"}.
    None when it does not answer as MicroPython (another program, or nothing)."""
    try:
        out, err = run(link, PROBE, timeout=timeout)
    except ReplError:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "FETD-PROBE":
            return {"version": None if parts[1] == "-" else parts[1], "configured": parts[2] == "True"}
    return None


def settings_script(cfg: dict) -> str:
    """The Python the display runs to keep its settings: written whole, then renamed (a power cut leaves the old or the
    new); the "3 quick power cycles" count cleared (these restarts are not a person asking for setup)."""
    text = json.dumps(cfg)
    return ("import json, os\ncfg = %s\njson.loads(cfg)\n" % json.dumps(text) +
            "f = open('/config.json.new', 'w')\nf.write(cfg)\nf.close()\nos.rename('/config.json.new', '/config.json')\n"
            "try:\n    os.remove('/quickboot')\nexcept OSError:\n    pass\nprint('FETDISPLAY-SAVED', len(cfg))\n")


def write_settings(link, cfg: dict, timeout: float = 20.0) -> None:
    out, err = run(link, settings_script(cfg), timeout)
    if err or "FETDISPLAY-SAVED" not in out:
        raise ReplError("the display could not keep its settings: " + ((err or out).splitlines() or ["no answer"])[-1])
    reset(link)
