# The CYD's resistive touch (XPT2046) for FeT Display, in MicroPython. Its own pins (CYD: clock 25, MOSI 32, MISO 39,
# CS 33, touch signal 36, which goes low while pressed), on a software SPI so the second hardware bus stays free for the
# microSD card.
#
# raw() gives the chip's 12-bit readings (x, y) or None: several samples, the median of each, and only when pressed
# firmly enough (z). Calibration maps raw readings to screen pixels with an affine fit from taps on known points (any
# rotation or mirroring of the axes comes out of the fit); it is kept in /touch.json.
import json
from machine import Pin, SoftSPI

CAL_FILE = "/touch.json"
Z_MIN = 300                  # pressure needed for a tap (z = z1 + 4095 - z2 rises with pressure)
SAMPLES = 7


def _median(v):
    v = sorted(v)
    return v[len(v) // 2]


class Touch:
    def __init__(self):
        self.spi = SoftSPI(baudrate=1_000_000, sck=Pin(25), mosi=Pin(32), miso=Pin(39))
        self.cs = Pin(33, Pin.OUT, value=1)
        self.irq = Pin(36, Pin.IN)
        self._tx = bytearray(3)
        self._rx = bytearray(3)
        self.cal = None
        try:
            with open(CAL_FILE) as fh:
                self.cal = json.load(fh)["m"]
        except (OSError, ValueError, KeyError):
            pass

    def _read(self, cmd):
        self._tx[0] = cmd
        self.cs(0)
        self.spi.write_readinto(self._tx, self._rx)
        self.cs(1)
        return ((self._rx[1] << 8) | self._rx[2]) >> 3

    def pressed(self):
        return self.irq() == 0

    def raw(self):
        """(x, y) as the chip reads them, or None when not pressed firmly enough."""
        if not self.pressed():
            return None
        xs, ys, zs = [], [], []
        for _ in range(SAMPLES):
            z1, z2 = self._read(0xB1), self._read(0xC1)
            zs.append(z1 + 4095 - z2)
            xs.append(self._read(0xD1))
            ys.append(self._read(0x91))
        self._read(0x90)                              # power down between readings, touch signal on
        if _median(zs) < Z_MIN or not self.pressed():
            return None
        return _median(xs), _median(ys)

    def point(self):
        """(x, y) in screen pixels, or None (not pressed, or not calibrated)."""
        r = self.raw()
        if r is None or self.cal is None:
            return None
        m, (rx, ry) = self.cal, r
        return int(m[0] * rx + m[1] * ry + m[2] + 0.5), int(m[3] * rx + m[4] * ry + m[5] + 0.5)

    @staticmethod
    def fit(pairs):
        """The affine map raw -> screen from [((rx, ry), (sx, sy)), ...] (3 or more), least squares."""
        ata = [[0.0] * 3 for _ in range(3)]
        atx, aty = [0.0] * 3, [0.0] * 3
        for (rx, ry), (sx, sy) in pairs:
            row = (rx, ry, 1.0)
            for i in range(3):
                for j in range(3):
                    ata[i][j] += row[i] * row[j]
                atx[i] += row[i] * sx
                aty[i] += row[i] * sy
        return Touch._solve(ata, atx) + Touch._solve(ata, aty)

    @staticmethod
    def _solve(a, b):
        m = [a[i][:] + [b[i]] for i in range(3)]
        for c in range(3):
            p = max(range(c, 3), key=lambda r: abs(m[r][c]))
            if abs(m[p][c]) < 1e-12:
                raise ValueError("the taps do not span the screen: calibrate again")
            m[c], m[p] = m[p], m[c]
            for r in range(3):
                if r != c:
                    f = m[r][c] / m[c][c]
                    for k in range(c, 4):
                        m[r][k] -= f * m[c][k]
        return [m[i][3] / m[i][i] for i in range(3)]

    def save(self, m):
        self.cal = m
        with open(CAL_FILE, "w") as fh:
            json.dump({"m": m}, fh)
