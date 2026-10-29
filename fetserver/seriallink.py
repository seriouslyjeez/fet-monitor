"""A USB serial port, stdlib only (termios): raw 8N1 at 115200, lines in, bytes out, and the modem lines an auto-reset
programmer uses to restart an ESP. Used by the flash station (flashstation.py) to talk to a freshly flashed FeT Bridge
and to read what an Arduino Mega says at start-up."""
from __future__ import annotations

import fcntl
import os
import select
import struct
import termios
import time
from typing import Optional

BAUDS = {115200: termios.B115200, 9600: termios.B9600}
# Linux values where the termios module lacks them; macOS has its own in the module
TIOCMBIS = getattr(termios, "TIOCMBIS", 0x5416)
TIOCMBIC = getattr(termios, "TIOCMBIC", 0x5417)
TIOCM_DTR = getattr(termios, "TIOCM_DTR", 0x002)
TIOCM_RTS = getattr(termios, "TIOCM_RTS", 0x004)
MAX_PARTIAL = 8192                     # a "line" this long without an end is noise (e.g. the ESP ROM at 74880 baud)


class SerialLink:
    def __init__(self, path: str, baud: int = 115200):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            a = termios.tcgetattr(self.fd)
            a[0] = 0                                                  # iflag: no translation, no flow control
            a[1] = 0                                                  # oflag: bytes as they are
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL       # 8N1, ignore the modem's carrier
            a[3] = 0                                                  # lflag: raw, no echo
            a[4] = a[5] = BAUDS[baud]
            a[6][termios.VMIN] = 0
            a[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, a)
            termios.tcflush(self.fd, termios.TCIFLUSH)
        except Exception:
            os.close(self.fd)
            raise
        self.buf = b""

    def write(self, data: bytes, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while data:
            left = deadline - time.monotonic()
            if left <= 0:
                raise OSError("the serial port does not take data")
            _, w, _ = select.select([], [self.fd], [], left)
            if w:
                data = data[os.write(self.fd, data):]

    def readline(self, timeout: float) -> Optional[str]:
        """The next line (without \\r\\n), or None when none is complete within timeout seconds. OSError when the device
        is gone."""
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], left)
            if r:
                chunk = os.read(self.fd, 512)
                if not chunk:
                    raise OSError("the device is gone")
                self.buf += chunk
                if len(self.buf) > MAX_PARTIAL and b"\n" not in self.buf:
                    self.buf = self.buf[-MAX_PARTIAL // 2:]
        line, self.buf = self.buf.split(b"\n", 1)
        return line.rstrip(b"\r").decode("latin-1")

    def _lines(self, which: int, on: bool) -> None:
        fcntl.ioctl(self.fd, TIOCMBIS if on else TIOCMBIC, struct.pack("I", which))

    def restart_esp(self) -> bool:
        """Auto-reset programmers (the NodeMCU circuit: RTS -> EN, DTR -> GPIO0): EN low with GPIO0 high, then release,
        so the ESP starts its program. A programmer with a PROG/UART switch has neither line wired: nothing happens.
        False when the port has no modem lines at all."""
        try:
            self._lines(TIOCM_DTR, False)
            self._lines(TIOCM_RTS, True)
            time.sleep(0.12)
            self._lines(TIOCM_RTS, False)
        except OSError:
            return False
        return True

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
