# FeT Display: a test pattern for the CYD's screen (is it the right way round, are the colours right?), and how long
# the 10 m air rifle face takes to draw in strips. Run with: mpremote run firmware/display/screen_test.py
import time

from ili9341 import H, W, Screen, Strip, rgb

BG = rgb(30, 30, 30)
BLACK, WHITE = rgb(0, 0, 0), rgb(255, 255, 255)
RED, GREEN, BLUE, YELLOW = rgb(255, 0, 0), rgb(0, 200, 0), rgb(0, 0, 255), rgb(255, 220, 0)

s = Screen()
s.fill(0, 0, W, H, BG)
s.light(True)


def label(text, x, y, colour=WHITE):
    st = Strip(8 * len(text), 8)
    st.fill(BG)
    st.text(text, 0, 0, colour)
    s.show(st, x, y)


# the 10 m air rifle face (ISSF): rings 45.5 mm (1) to 0.5 mm (10), black from 30.5 mm (ring 4), 224 px square
RINGS = (45.5, 40.5, 35.5, 30.5, 25.5, 20.5, 15.5, 10.5, 5.5, 0.5)
SIZE, STRIP_H, X0, Y0 = 224, 16, 8, 8
scale = SIZE / 47.0                        # mm to pixels: the 1 ring and a little margin
cx = cy = SIZE // 2
t0 = time.ticks_ms()
st = Strip(SIZE, STRIP_H)
for y0 in range(0, SIZE, STRIP_H):         # 224 / 16 = 14 strips, the last one ends at the face's edge
    st.fill(WHITE)
    for d in RINGS:
        r = int(d / 2 * scale + 0.5)
        black = d <= 30.5
        if d == 0.5:
            st.ellipse(cx, cy - y0, r, r, WHITE, True)        # the ten is a white dot in the black
        else:
            st.ellipse(cx, cy - y0, r, r, BLACK if black else WHITE, True)
            st.ellipse(cx, cy - y0, r, r, WHITE if black else BLACK, False)
    s.show(st, X0, Y0 + y0)
ms = time.ticks_diff(time.ticks_ms(), t0)

# colour bars on the right, each with its name under it
for i, (name, c) in enumerate((("RED", RED), ("GREEN", GREEN), ("BLUE", BLUE), ("YELLOW", YELLOW), ("WHITE", WHITE))):
    s.fill(244, 22 + i * 40, 68, 26, c)
    label(name, 244, 22 + i * 40 + 28)

# the corners last, so nothing covers them
label("TOP LEFT", 0, 0)
label("TOP RIGHT", W - 72, 0)
label("BOT LEFT", 0, H - 8)
label("BOT RIGHT", W - 72, H - 8)
label("%d ms" % ms, 244, 10, YELLOW)
print("face drawn in", ms, "ms")
