# The CYD's screen (ESP32-2432S028R: ILI9341, 320x240, landscape) for FeT Display, in MicroPython.
#
# Pins and the start-up sequence come from mrjerkface/freetarget-display (MIT), which took the sequence from Bodmer's
# TFT_eSPI ("ILI9341_2_DRIVER", as the ESP32 Cheap Yellow Display community reference chooses it): the classic
# ILI9341 sequence leaves this panel washed-out grey. Credits in NOTICE.
# Some CYDs (seen 2026-11-14: a board marked TPM408-2.8) show every colour inverted with that sequence: they need the
# panel's inversion on (0x21), Screen(invert=True), the default here. A board that shows inverted colours with it: False.
#
# Drawing goes through small framebuf strips (a whole screen would be 150 KB, more than the heap has next to WiFi):
# Strip(w, h) is a framebuf.FrameBuffer in RGB565 whose colours are stored high byte first, as the panel wants them,
# so a strip goes to the screen as it is (no byte swapping in Python).
import framebuf
import time
from machine import Pin, SPI

W, H = 320, 240
_SEQ = (
    (0xCF, b"\x00\xC1\x30"),
    (0xED, b"\x64\x03\x12\x81"),
    (0xE8, b"\x85\x00\x78"),
    (0xCB, b"\x39\x2C\x00\x34\x02"),
    (0xF7, b"\x20"),
    (0xEA, b"\x00\x00"),
    (0xC0, b"\x10"),                     # power control 1
    (0xC1, b"\x00"),                     # power control 2
    (0xC5, b"\x30\x30"),                 # VCOM control 1
    (0xC7, b"\xB7"),                     # VCOM control 2
    (0x3A, b"\x55"),                     # 16-bit pixels
    (0x36, b"\x28"),                     # MADCTL: landscape, BGR
    (0xB1, b"\x00\x1A"),                 # frame rate
    (0xB6, b"\x08\x82\x27"),             # display function control
    (0xF2, b"\x00"),                     # 3-gamma off
    (0x26, b"\x01"),                     # gamma curve 1
    (0xE0, b"\x0F\x2A\x28\x08\x0E\x08\x54\xA9\x43\x0A\x0F\x00\x00\x00\x00"),
    (0xE1, b"\x00\x15\x17\x07\x11\x06\x2B\x56\x3C\x05\x10\x0F\x3F\x3F\x0F"),
)


def rgb(r, g, b):
    """A colour for a Strip: RGB565, stored high byte first (the panel's order)."""
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)


class Strip(framebuf.FrameBuffer):
    """A w x h piece of the screen to draw into, then show(x, y)."""
    def __init__(self, w, h):
        self.buf = bytearray(w * h * 2)
        self.w, self.h = w, h
        super().__init__(self.buf, w, h, framebuf.RGB565)


class Screen:
    def __init__(self, baud=40_000_000, invert=True):
        self.dc = Pin(2, Pin.OUT, value=1)
        self.cs = Pin(15, Pin.OUT, value=1)
        self.bl = Pin(21, Pin.OUT, value=0)
        self.spi = SPI(1, baudrate=baud, sck=Pin(14), mosi=Pin(13), miso=Pin(12))   # HSPI
        self._win = bytearray(4)
        self._cmd(0x01)                      # software reset (the CYD has no reset pin)
        time.sleep_ms(150)
        for cmd, data in _SEQ:
            self._cmd(cmd, data)
        self._cmd(0x21 if invert else 0x20)  # display inversion on / off
        self._cmd(0x11)                      # sleep out
        time.sleep_ms(120)
        self._cmd(0x29)                      # display on

    def _cmd(self, cmd, data=None):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes((cmd,)))
        if data:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def _window(self, x0, y0, x1, y1):
        w = self._win
        w[0], w[1], w[2], w[3] = x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF
        self._cmd(0x2A, w)
        w[0], w[1], w[2], w[3] = y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF
        self._cmd(0x2B, w)

    def blit(self, x, y, w, h, buf):
        """w x h pixels from buf (2 bytes each, panel order) at (x, y). Only inside the screen: the panel would wrap
        pixels past its edge onto the next lines."""
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > W or y + h > H:
            raise ValueError("off the screen: %dx%d at %d,%d" % (w, h, x, y))
        self._window(x, y, x + w - 1, y + h - 1)
        self.cs(0)
        self.dc(0)
        self.spi.write(b"\x2C")
        self.dc(1)
        self.spi.write(buf)
        self.cs(1)

    def show(self, strip, x, y):
        self.blit(x, y, strip.w, strip.h, strip.buf)

    def fill(self, x, y, w, h, colour):
        """A plain rectangle without a strip of its size: one line's worth of pixels, sent h times."""
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > W or y + h > H:
            raise ValueError("off the screen: %dx%d at %d,%d" % (w, h, x, y))
        line = bytearray(w * 2)
        hi, lo = colour & 0xFF, colour >> 8          # rgb() stored the bytes swapped: first byte = its low byte
        for i in range(0, w * 2, 2):
            line[i], line[i + 1] = hi, lo
        self._window(x, y, x + w - 1, y + h - 1)
        self.cs(0)
        self.dc(0)
        self.spi.write(b"\x2C")
        self.dc(1)
        for _ in range(h):
            self.spi.write(line)
        self.cs(1)

    def light(self, on=True):
        self.bl(1 if on else 0)
