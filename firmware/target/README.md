# FeT target firmware (in progress)

New firmware for freETarget-compatible target boards (Arduino Mega board v3.x, ESP32 boards v6.x), written for FeT
Monitor: the target sends raw microphone times with every shot, signed with its own key, and FeT Monitor computes
the position (fetserver/shotsolve.py). Plan and review of the upstream code: docs/PLAN-target-firmware.md (local).

- `core/` platform-independent C++ (no STL, no heap), an Arduino library: shot capture (`capture.h`), signed shot
  lines (`shotline.h`), SHA-256/HMAC. Tested on the PC by tests/test_target_core.py.
- `test/` the PC harness and a Mega compile check (`arduino-cli compile -b arduino:avr:mega --library core test/avr_compile`).
- Not yet: the Mega and ESP32 layers, settings, commands. Nothing here has run on a target yet.

## Credits

The hardware design, the acquisition method and much of the knowledge in this firmware come from freETarget
(https://github.com/ten-point-nine/freETarget), MIT License, "Copyright 2020, ten-point-nine project". This firmware
is a separate rewrite and is not part of, affiliated with or endorsed by that project.
