# FeT target firmware (in progress)

New firmware for freETarget-compatible target boards (Arduino Mega board v3.x, ESP32 boards v6.x), written for FeT
Monitor: the target sends raw microphone times with every shot, signed with its own key, and FeT Monitor computes
the position (fetserver/shotsolve.py). Plan and review of the upstream code: docs/PLAN-target-firmware.md (local).

- `core/` platform-independent C++ (no STL, no heap), an Arduino library: shot capture (`capture.h`), signed shot
  lines (`shotline.h`), SHA-256/HMAC. Tested on the PC by tests/test_target_core.py.
  Also: settings (two slots, CRC), signed commands (`commands.h`), identity (key + boot count, USB setup lines,
  `identity.h`), non-blocking paper drive (`paper.h`).
- `mega/fet_target_mega` the Arduino Mega 2560 layer for boards v3.x (built for 3.1): counters, 1 kHz Timer1,
  face sensor on D19, LM75, LEDs, paper, switches A (paper) and B (lights). Build:
  `arduino-cli compile -b arduino:avr:mega --library firmware/target/core firmware/target/mega/fet_target_mega`
- `test/` the PC harness and a Mega compile check.
- Not yet: the ESP32 layer, FeT Bridge forwarding these lines, the server side. **Nothing here has run on a board
  yet.** Before the first flash: back up the Mega's flash AND EEPROM (its calibration) with avrdude; the watchdog
  stays off until the board's bootloader is known to handle it.

## Credits

The hardware design, the acquisition method and much of the knowledge in this firmware come from freETarget
(https://github.com/ten-point-nine/freETarget), MIT License, "Copyright 2020, ten-point-nine project". This firmware
is a separate rewrite and is not part of, affiliated with or endorsed by that project.
