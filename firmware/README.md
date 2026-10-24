# freETarget WiFi bridge firmware (ESP-01 / ESP-01S)

Replaces the stock AT firmware on the target's ESP-01. It reads the Arduino's serial output, writes every shot to the
ESP's flash *first*, and then delivers it to the club server (Raspberry Pi) until the server acknowledges it.
A WiFi drop, a server restart, or a power cut on the target loses nothing that was already written.

**Status: v0.1.0. The logic is tested on a PC (dozens of tests, including power cuts, torn flash writes, lost replies and
outages, against the real server). The ESP8266-specific parts (flash, WiFi, web pages, OTA) could not be compiled or
run where this was written, so the first build and the first boot on real hardware are the real test. See
"What is not verified yet" below.**

## What it does

* **Durable queue.** Each record (shot, miss, ECHO reply, Arduino boot banner) is appended to a checksummed log in
  flash before anything else happens. Delivery is at-least-once with a (boot, sequence) number on every event; the
  server drops duplicates, so a lost acknowledgement never double-counts a shot.
* **Never answers the Arduino's `+++` / `AT` probe**, so the Arduino stays in raw-serial mode.
* **Heartbeat** every few seconds (interval set by the server) carrying signal strength, queue depth, firmware
  versions, and the server's commands (`ECHO`, `VERSION`, `NEW`, open the WiFi portal, reboot). A command runs at most once.
* **Setup portal.** Open WiFi "freETarget-xxxxxx"; a phone pops up the setup page (WiFi, server address and port,
  token, target name). Opens automatically when there are no settings, after **3 quick power cycles** (switch off
  within 8 s of switching on, three times), or on a command from the server. Closes after 15 minutes unused. No extra hardware.
* **Finds the server by itself** (mDNS `_freetarget._tcp`) if the server address is left empty. The Pi side is the
  avahi file in `deploy/`.
* **Status page** at `http://<target-name>.local/` or its IP: queue depth, last server status, errors, heap.
  (There is no debug output on the serial port; that port belongs to the Arduino.)
* **Over-the-air update**, token as password: Arduino IDE / `arduino-cli` network upload, or a browser upload at
  `/update` while the portal is open. The queue and settings survive an update.

## Build

Needs the **ESP8266 Arduino core 3.1.2 or newer** (Arduino IDE → Boards Manager → "esp8266 by ESP8266 Community").
Open `fet_bridge/fet_bridge.ino`. Settings (Tools menu):

| Setting | Value |
|---|---|
| Board | Generic ESP8266 Module |
| Flash Mode | **DOUT** (ESP-01 modules need it) |
| Flash Size | the chip's size: the spare **ESP-01S is 4 MB** → `4MB (FS:2MB OTA:~1019KB)`; the target's original ESP-01 is 1 MB → `1MB (FS:128KB OTA:~438KB)` |
| CPU Frequency | 80 MHz |
| Upload Speed | 115200 |
| Erase Flash (first flash) | All Flash Contents |

`arduino-cli` equivalent (check the option names with `arduino-cli board details -b esp8266:esp8266:generic`):

    arduino-cli compile -b esp8266:esp8266:generic:FlashMode=dout,eesz=4M2M,baud=115200 fet_bridge
    arduino-cli upload  -b esp8266:esp8266:generic:FlashMode=dout,eesz=4M2M,baud=115200 -p /dev/cu.usbserial-XXXX fet_bridge

**The Flash Size menu must match the chip, and you must erase flash on the first upload.** A build made for a smaller
flash layout than the chip really has can crash as soon as the WiFi radio starts (the radio keeps calibration data at the
end of flash). With `FET_DEBUG` set to 1 the start-up output shows `flash: configured=… real=…`; the two must be equal.

**Check the size.** The compiler prints "Sketch uses N bytes". On the 1 MB layout over-the-air updates need
N below about 430 KB. If it is bigger, edit `src/platform/build_config.h`: set `FET_ENABLE_WEB_UPDATE` to 0 first, then
`FET_ENABLE_MDNS` to 0 (then the server address must be typed in the portal). The 4 MB chip has room for everything.

## Flashing, in this order

1. **Spare ESP-01S first** (MAC ends 41:31). Programmer: GPIO0 to GND while powering up, then flash. After flashing,
   power-cycle without GPIO0 grounded. If upload fails with "Invalid head of packet", use `--baud 115200` on a
   direct USB port.
2. Bench test it (below). Only when it works, swap it into the target. The original ESP-01 is untouched and can be
   put back at any time. If you ever do overwrite the original, first save it:
   `esptool --port /dev/cu.usbserial-XXXX read-flash 0 0x100000 esp01-original.bin`.
3. The ESP-01S module has the same pins as the ESP-01; it is 3.3 V, like the socket on the target.

## First setup

1. Power the unit. With no settings it opens the portal: join WiFi **freETarget-xxxxxx** on a phone (the page opens by
   itself; otherwise go to `http://192.168.4.1/`).
2. Pick the club WiFi, enter its password, the Pi's address (or leave empty to auto-find), port 8080, and the token:
   `sudo cat /var/lib/freetarget/device_token` on the Pi. Name the target (e.g. `Lane-1`). **Save**. It restarts and joins.
3. The target appears on the dashboard within a few seconds.

To change settings later: switch the target off and on **three times quickly**, or send the `wifi_portal` command from
the server. "Forget all settings" in the portal clears WiFi and server settings but keeps stored shots.

## Bench test without the target

Wire a 3.3 V USB-serial adapter to the ESP (adapter TX → ESP RX, RX → TX, GND), power the ESP, then:

    pip install pyserial
    python3 firmware/tools/inject.py /dev/cu.usbserial-XXXX --count 5

Expect: the shots appear on the dashboard; the script prints nothing from the ESP. Then the durability checks:

* Stop the server (`sudo systemctl stop freetarget-server`), inject 5 shots, power-cycle the ESP, start the server:
  all 5 appear, once each.
* Inject while the router is off, then switch it on.
* Switch the ESP off and on three times quickly: the portal opens.
* Status page shows `queued` falling to 0 after delivery.

## Wiring on the target

ESP-01 TX/RX connect to the Mega's Serial3 (pins 14/15) at 115200 on the target board; nothing changes there.

## What is not verified yet

Verified on a PC with the real server: the queue, ordering, exactly-once delivery, recovery from power cuts and torn
writes, flash full / failing, outages, lost replies, commands, parsing of your real Arduino lines, config validation,
the quick-boot counter, HTTP response parsing. The ESP code is type-checked against stand-in headers only.

Not yet run on hardware, so expect small fixes on first contact:

* the build itself (a mistake in how I remember an ESP8266 library call would show up as a compile error);
* LittleFS behaviour (rename over an existing file, how many segments fit in the 1 MB chip's 128 KB);
* mDNS discovery (`queryService`) and the status page's `.local` name;
* the portal on a phone (captive-page pop-up differs between iOS and Android);
* serial buffering while a WiFi connect blocks (the FIFO holds 2 KB, plenty for shot lines, but unproven);
* over-the-air update.

The portal is an **open** WiFi network for the minutes it is up (to avoid a password on a sticker); it only opens after a
physical power-cycle action, a server command, or when unconfigured, and saving settings requires the server token to be
entered. Anyone on the club WiFi can read the status page (no secrets on it).
