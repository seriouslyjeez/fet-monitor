# Larger text for FeT Display: MicroPython's built-in 8x8 font, scaled up by whole pixels. Good enough for setup
# screens; the real screens get proper fonts later.
import framebuf


def big(screen, text, x, y, scale, fg, bg):
    """text at (x, y), each font pixel scale x scale screen pixels, fg on bg (colours from ili9341.rgb)."""
    if not text:
        return 0, 8 * scale
    w = 8 * len(text)
    mono = bytearray((w + 7) // 8 * 8)
    fb = framebuf.FrameBuffer(mono, w, 8, framebuf.MONO_HLSB)
    fb.text(text, 0, 0, 1)
    sw = w * scale
    line = bytearray(sw * 2)
    fg_b, bg_b = (fg & 0xFF, fg >> 8), (bg & 0xFF, bg >> 8)
    rows = bytearray(sw * 2 * scale)
    for fy in range(8):
        i = 0
        for fx in range(w):
            b = fg_b if fb.pixel(fx, fy) else bg_b
            for _ in range(scale):
                line[i], line[i + 1] = b
                i += 2
        for k in range(scale):
            rows[k * sw * 2:(k + 1) * sw * 2] = line
        screen.blit(x, y + fy * scale, sw, scale, rows)
    return sw, 8 * scale
