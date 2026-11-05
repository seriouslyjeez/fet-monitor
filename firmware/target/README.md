# FeT target firmware

New firmware for freETarget-compatible target boards (Arduino Mega board v3.x, the ESP32-S3 board 6.2), written for
FeT Monitor: the target sends raw microphone times with every shot, signed with its own key, and FeT Monitor computes
the position (fetserver/shotsolve.py). Plan and review of the upstream code: docs/PLAN-target-firmware.md (local).

- `core/` platform-independent C++ (no STL, no heap), an Arduino library: shot capture (`capture.h`), signed shot
  lines (`shotline.h`), SHA-256/HMAC, settings (two slots, CRC, per-board defaults), signed commands (`commands.h`:
  get, set, paper, status, update), identity (key + boot count, USB setup lines, `identity.h`), non-blocking paper
  drive (`paper.h`). Tested on the PC by tests/test_target_core.py.
- `mega/fet_target_mega` the Arduino Mega 2560 layer for boards v3.x (built for 3.1): counters (8 MHz), 1 kHz
  Timer1, face sensor on D19, LM75, LEDs, paper, switches A (paper) and B (lights); lines to USB and to FeT Bridge
  (0.4.0+) on Serial3. Installed by the Pi's flash station (USB). Build:
  `arduino-cli compile -b arduino:avr:mega --library firmware/target/core firmware/target/mega/fet_target_mega`
- `esp32/fet_target_esp32` the ESP32-S3 board 6.2 (upstream revision 620): PCNT counters at 10 MHz, the trip point
  by the MCP4725 DAC (with read-back), TMP1075, paper, lights, three status LEDs (V12, COM, RDY), buttons 1 (paper)
  and 2 (lights). It talks to FeT Monitor itself: the FeT Bridge core (firmware/fet_bridge/src/core, as the
  fet-bridge-core library) runs in its own task, with the setup portal and USB provisioning. Signed updates over
  WiFi from the Pi with rollback (a new image confirms itself once FeT Monitor answers). First install by the
  flash station (USB, 230400 baud: the board's FTDI garbles faster). Build:
  `arduino-cli compile -b esp32:esp32:esp32s3:FlashSize=8M,PartitionScheme=default_8MB,FlashMode=dio
   --library firmware/target/core --library firmware/fet_bridge/src/core firmware/target/esp32/fet_target_esp32`
  then copy `fet_target_esp32.ino.merged.bin` to `dist/target-firmware/fet-target-esp32-<version>.bin`.
- `test/` the PC harness and a Mega compile check.

**Power: the 12 V supply must always be connected in normal operation.** USB is for flashing and setup. On USB power
alone the ESP32 board starts and joins the WiFi, but its WiFi works badly: on B59 (2026-11-05), 9 of 10 reports to
FeT Monitor could not connect, the board answered ARP 2.8 s late and pings in 0.06-5 s with 69 % lost, while power
save was off and both cores were 95 % idle. With the 12 V connected it worked normally. The first status LED (V12) is
red without it.

On hardware (2026-11): the Mega board 3.1 and the ESP32 board B59 (WiFi, commands, OTA, rollback, a tampered image
refused). Open: phones (iPhone) get no address from the ESP32's setup network (also with upstream's own); set the
board up with the flash station instead. The Mega's watchdog stays off until its bootloader is known to handle it.

## Credits

The hardware design, the acquisition method and much of the knowledge in this firmware come from freETarget
(https://github.com/ten-point-nine/freETarget), MIT License, "Copyright 2020, ten-point-nine project". This firmware
is a separate rewrite and is not part of, affiliated with or endorsed by that project.
