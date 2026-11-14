# FeT Display's screen pieces: a page of lines of text and tap-able buttons. Colours from ili9341.rgb.
import time

from ili9341 import H, W, rgb
from text import big

BG, FG, DIM, GOOD, WARN, BAD, BTN = (rgb(18, 20, 24), rgb(240, 240, 240), rgb(150, 155, 165), rgb(60, 200, 90),
                                     rgb(255, 210, 60), rgb(255, 90, 80), rgb(55, 60, 70))


class Page:
    """A whole screen of text lines and buttons. lines: (text, scale, colour); buttons: (label, key)."""

    def __init__(self, screen, touch):
        self.s, self.t = screen, touch
        self.buttons = []

    def show(self, lines, buttons=(), footer=None):
        s = self.s
        s.fill(0, 0, W, H, BG)
        y = 12
        for text, scale, colour in lines:
            for part in wrap(text, W // (8 * scale)):
                big(s, part, max(0, (W - 8 * scale * len(part)) // 2), y, scale, colour, BG)
                y += 8 * scale + 6
            y += 4
        self.buttons = []
        if buttons:
            n = len(buttons)
            bw, bh, gap = (W - 16 - (n - 1) * 10) // n, 48, 10           # 48 px: easy to hit with a finger
            for i, (label, key) in enumerate(buttons):
                x, by = 8 + i * (bw + gap), H - bh - 8 - (10 if footer else 0)
                s.fill(x, by, bw, bh, BTN)
                sc = 2 if 16 * len(label) <= bw - 8 else 1
                big(s, label, x + (bw - 8 * sc * len(label)) // 2, by + (bh - 8 * sc) // 2, sc, FG, BTN)
                self.buttons.append((x, by, bw, bh, key))
        if footer:
            big(s, footer[: W // 8], 0, H - 8, 1, DIM, BG)

    def tapped(self):
        """The key of the button being tapped now (after it is let go), or None."""
        p = self.t.point()
        if not p:
            return None
        for x, y, w, h, key in self.buttons:
            if x - 4 <= p[0] < x + w + 4 and y - 4 <= p[1] < y + h + 4:
                while self.t.pressed():
                    time.sleep_ms(20)
                return key
        return None

    def wait(self, ms, tick=lambda: None):
        """A button's key, or None after ms (tick() is called meanwhile)."""
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < ms:
            tick()
            k = self.tapped()
            if k:
                return k
            time.sleep_ms(30)
        return None


def wrap(text, width):
    """Lines of at most width characters, broken at spaces where possible."""
    out, line = [], ""
    for word in text.split(" "):
        while len(word) > width:
            if line:
                out.append(line)
                line = ""
            out.append(word[:width])
            word = word[width:]
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= width:
            line += " " + word
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out or [""]
