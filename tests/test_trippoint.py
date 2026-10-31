"""tools/trippoint.py: reading freETarget's trip-point test ({"TEST":11}) over a serial line (a pty plays the Mega)."""
import importlib.util
import io
import os
import pty
import threading
import time
import unittest
from pathlib import Path

from fetserver.seriallink import SerialLink

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("trippoint", ROOT / "tools/trippoint.py")
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)
# what firmware 4.3.x prints (diag_tools.ino show_sensor_status), \n\r line ends
STATUS = " Latch:%s  Face Strike:0  V_Ref:%s  Temperature:20.50  WiFi:1  Switch:----\n\r"


class Parse(unittest.TestCase):
    def test_status_lines(self):
        self.assertEqual(tp.parse(STATUS % ("N.S.", "4.94")), ("N.S.", 4.94))
        self.assertEqual(tp.parse(STATUS % ("....", "1.07")), ("....", 1.07))
        self.assertIsNone(tp.parse('freETarget "4.3.0 December 21, 2023"'))
        self.assertIsNone(tp.parse("Setting trip point. Type ! of cycle power to exit"))


class OverAPty(unittest.TestCase):
    def test_session(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        link = SerialLink(os.ttyname(slave))
        self.addCleanup(link.close)
        os.close(slave)
        got = []

        def mega():                                     # the target: banner, then status lines once tested
            os.write(master, b'freETarget "4.3.0 December 21, 2023"\r\n')
            buf = b""
            while b'{"TEST":11}' not in buf:
                buf += os.read(master, 64)
            got.append(buf)
            for latch, v in (("....", "1.20"), ("....", "1.10"), (".E..", "1.10"), (".E..", "1.10"), ("NESW", "1.00")):
                os.write(master, (STATUS % (latch, v)).encode())
                time.sleep(0.02)
            while True:
                b = os.read(master, 64)
                got.append(b)
                if b"!" in b:
                    break
        t = threading.Thread(target=mega, daemon=True)
        t.start()
        out, log = io.StringIO(), io.StringIO()
        s = tp.Session(link, out=out, log=log)
        s.start(wait_banner=0.3)
        end = time.monotonic() + 2
        while time.monotonic() < end and s.vmin != 1.0:
            line = link.readline(0.1)
            if line:
                s.handle(line)
        s.stop()
        t.join(2)
        text = out.getvalue()
        self.assertIn('target: freETarget "4.3.0', text)
        self.assertEqual(text.count("TRIPPED"), 2, "each new latch state once")
        self.assertIn("TRIPPED .E..", text)
        self.assertIn("TRIPPED NESW", text)
        self.assertEqual((s.vmin, s.vmax), (1.0, 1.2))
        self.assertEqual(len(log.getvalue().strip().splitlines()), 5)
        self.assertIn("V_Ref seen 1.00 to 1.20 V", text)
        self.assertTrue(any(b"!" in b for b in got), "the test is left with !")
        sent = b"".join(got)
        self.assertLess(sent.index(b'{"TEST":11}'), sent.index(b"R"), "re-armed once in the test")


if __name__ == "__main__":
    unittest.main()
