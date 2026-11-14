# FeT Display: calibrate the touch screen (a new display does it on its first start; the buttons need it). Tap the
# cross at 4 points near the corners; a 5th in the middle checks the fit and is retried if it is off by more than
# MAX_ERR_PX. Saves /touch.json (xpt2046.CAL_FILE).
import time

from ili9341 import H, W
from text import big
from ui import BG, FG, GOOD, WARN, BAD

WAIT_MS = 120_000
MAX_ERR_PX = 12


def _say(s, text, colour=FG):
    s.fill(0, 96, W, 48, BG)
    big(s, text, max(0, (W - 16 * len(text)) // 2), 104, 2, colour, BG)


def _cross(s, x, y, colour):
    s.fill(max(0, x - 12), y - 1, min(W, x + 13) - max(0, x - 12), 3, colour)
    s.fill(x - 1, max(0, y - 12), 3, min(H, y + 13) - max(0, y - 12), colour)


def _tap(t, tick):
    """The raw reading of one tap (the median while pressed), or None after WAIT_MS."""
    t0 = time.ticks_ms()
    while t.pressed():
        tick()
        time.sleep_ms(20)
    while True:
        while not t.pressed():
            tick()
            if time.ticks_diff(time.ticks_ms(), t0) > WAIT_MS:
                return None
            time.sleep_ms(20)
        xs, ys = [], []
        while t.pressed():
            r = t.raw()
            if r:
                xs.append(r[0])
                ys.append(r[1])
            time.sleep_ms(10)
        if len(xs) >= 3:
            xs.sort()
            ys.sort()
            return xs[len(xs) // 2], ys[len(ys) // 2]


def calibrate(s, t, tick=lambda: None):
    """True when calibrated and saved; False when nobody tapped."""
    while True:
        s.fill(0, 0, W, H, BG)
        pairs = []
        for i, (x, y) in enumerate(((30, 30), (W - 30, 30), (30, H - 30), (W - 30, H - 30))):
            _say(s, "TAP THE CROSS %d/4" % (i + 1))
            _cross(s, x, y, BAD)
            r = _tap(t, tick)
            if r is None:
                return False
            pairs.append((r, (x, y)))
            _cross(s, x, y, GOOD)
        try:
            m = t.fit(pairs)
        except ValueError:
            continue
        s.fill(0, 0, W, H, BG)
        _say(s, "TAP THE MIDDLE")
        cx, cy = W // 2, H // 2 + 50
        _cross(s, cx, cy, BAD)
        r = _tap(t, tick)
        if r is None:
            return False
        px, py = m[0] * r[0] + m[1] * r[1] + m[2], m[3] * r[0] + m[4] * r[1] + m[5]
        if ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 <= MAX_ERR_PX:
            t.save(m)
            return True
        _say(s, "AGAIN, PLEASE", WARN)
        time.sleep_ms(1500)
