# FeT Display

A shot monitor for one firing point: the ESP32 "Cheap Yellow Display" (ESP32-2432S028R, 2.8", 320x240, resistive
touch), paired with one target by FeT Monitor's Range Control. It shows what a phone on that lane shows: the face and
the current bull's shots, the last score, the total, the mode, the match clock and the shooter's own match buttons,
and lets an athlete sign in (the lane's QR code, or athlete number and PIN). Nothing is scored on it: the scores come
from FeT Monitor. The protocol: docs/PROTOCOL.md, "Lane displays".

Status (2026-11-14): tried on one CYD against FeT Monitor on a Mac with simulated targets and a fake deepTENS. Not yet
tried at a range, with real targets, or with a phone scanning its QR code. Home targets (deepTENS) and a portable mode
(no FeT Monitor) are planned.

## Hardware

- ESP32-2432S028R ("CYD"), 4 MB flash, the version with one USB port (CH340). Some boards (seen: one marked
  TPM408-2.8) show every colour inverted unless the panel's inversion is on; FeT Display turns it on
  (`lib/ili9341.py`, `Screen(invert=True)`). A board whose colours then look inverted wants `invert=False`.
- Buttons on the back: **BOOT** cycles the zoom (Full, Bull, Auto). Do not hold BOOT while switching on: that starts
  the ESP32's flashing mode (the screen stays dark until the next power cycle). **RST** restarts it.
- The display only joins 2.4 GHz WiFi.

## Putting it on a CYD

1. **MicroPython, once.** FeT Display needs MicroPython built with double-precision floats (fetcore gives the same
   bits as on the Pi and deepTENS: tests/test_micropython.py). Build it with ESP-IDF v5.5.2:
   `python3 firmware/display/micropython/build.py` (it says what it needs). Then flash, erasing the whole chip the
   first time:

       esptool --port /dev/cu.usbserial-XXXX erase-flash
       esptool --port /dev/cu.usbserial-XXXX --baud 460800 write-flash 0x1000 dist/display/micropython-cyd-double-1.29.0.bin

2. **FeT Display.** `pip install mpremote`, then

       MPY_CROSS=dist/display/mpy-cross python3 firmware/display/deploy.py /dev/cu.usbserial-XXXX

   It copies `main.py` and `lib/` (and the parts of fetcore that place and score a shot) and restarts the display. It
   never touches the display's settings (`/config.json`) or its touch calibration (`/touch.json`).

## On the display

- **First start:** tap the 4 crosses (touch calibration), then the 5th to check it. To calibrate again, switch it on
  with a finger on the screen.
- **Setup:** with nothing set up, after 3 quick power cycles, or from *Set up* when it cannot reach the WiFi or FeT
  Monitor, it opens an open WiFi network `FeT-Display-<last 6 of its MAC>`. Join it with a phone: the setup page opens
  (or open http://192.168.4.1). Enter the range WiFi, its password and FeT Monitor's address with its port, e.g.
  `192.168.1.20:8080` (an IP address: `.local` names do not work on every network).
- **Pairing:** it shows a 6-character code. In FeT Monitor, Range Control → Lane displays: type the code and choose
  the target. One display per target: pairing another replaces it. Range Control can move, rename or remove it.

## The code

`main.py` starts it: setup, WiFi, pairing, then two tasks (asyncio): the network (a long poll: FeT Monitor answers as
soon as anything on the target changes) and the screen (drawing, touch, BOOT, clocks). `lib/`: `ili9341.py` (screen),
`xpt2046.py` and `calibrate.py` (touch), `config.py` and `portal.py` (settings, setup), `http.py` and `client.py`
(FeT Monitor), `target.py` and `ui.py` (screens), `qr.py` (QR codes, as FeT Monitor's pages draw them), `text.py`
(scaled font). Only what changed is drawn again, character by character. `screen_test.py`: a test pattern.

Tests (under MicroPython on a computer: `brew install micropython`): tests/test_display_portal.py,
test_display_client.py, test_display_qr.py, test_display_screens.py, test_micropython.py.

Credits: NOTICE.
