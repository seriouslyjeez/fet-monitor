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
- `esp32/fet_target_esp32` the ESP32-S3 board 6.2 (upstream revision 620): the microphones' RUN edges stamped by the
  ESP32-S3's capture timers (MCPWM, 80 MHz; 0.3.0, the board's 10 MHz clock stays off; `FET_TIMING_PCNT 1` builds the
  PCNT counters of that clock instead, as upstream), times sent as 10 MHz counts (`core/src/stamps.h`), the trip point
  by the MCP4725 DAC (with read-back), TMP1075, paper, lights, three status LEDs (V12, COM, RDY), buttons 1 (paper)
  and 2 (lights). It talks to FeT Monitor itself: the FeT Bridge core (firmware/fet_bridge/src/core, as the
  fet-bridge-core library) runs in its own task, with the setup portal and USB provisioning. Signed updates over
  WiFi from the Pi with rollback (a new image confirms itself once FeT Monitor answers). First install by the
  flash station (USB, 230400 baud: the board's FTDI garbles faster). Build:
  `arduino-cli compile -b esp32:esp32:esp32s3:FlashSize=8M,PartitionScheme=default_8MB,FlashMode=dio
   --library firmware/target/core --library firmware/fet_bridge/src/core firmware/target/esp32/fet_target_esp32`
  then copy `fet_target_esp32.ino.merged.bin` to `dist/target-firmware/fet-target-esp32-<version>.bin`.
- `test/` the PC harness and a Mega compile check.

**Power: the 12 V supply must always be connected in normal operation.** USB is for flashing and setup; on USB alone
the lights, paper drive and microphone circuits are not powered as designed. The first status LED (V12) is red
without it.

**The 10 MHz clock disturbs the ESP32 board's WiFi (B59, 2026-11-05).** With the clock running (whenever the board is
armed, as in upstream's firmware), B59's reports to FeT Monitor mostly failed ("could not connect"), it answered ARP
2.8 s late and pings in 0.06-5 s with 69 % lost, with power save off and both cores 95 % idle; an ESP-01 beside it
answered in 7-45 ms. Neither 802.11b (test build 0.2.4) nor the 12 V changed that. Asleep (0.2.4: clock off) and in
test build 0.2.5 (awake, everything running except the clock) it answered normally (0 % loss, 7-99 ms). The clock's
harmonics lie every 10 MHz across the 2.4 GHz band (2430 and 2440 MHz inside channel 6). The clock must run while the
board waited for a shot. The iPhone's missing address on the board's setup network had the same cause (it worked with
the clock off). Firmware 0.3.0 times shots without the clock: the ESP32-S3's capture timers stamp the RUN edges, which
latch without it (0.2.5: shots registered). Group 0 stamps North and East, group 1 South and West, both the re-arm
edge (STOP_N, via io_loop_back) for their offset. To check on hardware: accuracy against 0.2.x (cards), and whether
other 6.2 boards show the WiFi problem with the clock running (B59 only, or the design).

On hardware (2026-11): the Mega board 3.1 and the ESP32 board B59 (WiFi, commands, OTA, rollback, a tampered image
refused). Open: phones (iPhone) get no address from the ESP32's setup network (also with upstream's own); set the
board up with the flash station instead. The Mega's watchdog stays off until its bootloader is known to handle it.

## Credits

The hardware design, the acquisition method and much of the knowledge in this firmware come from freETarget
(https://github.com/ten-point-nine/freETarget), MIT License, "Copyright 2020, ten-point-nine project". This firmware
is a separate rewrite and is not part of, affiliated with or endorsed by that project.
