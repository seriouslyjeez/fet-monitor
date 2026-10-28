# FeT Bridge firmware (ESP-01 / ESP-01S): build and flash

## Arduino IDE settings (ESP8266 core 3.1.2)

| Setting (Tools menu) | 4 MB ESP-01S | Original 1 MB ESP-01 |
|---|---|---|
| Board | Generic ESP8266 Module | Generic ESP8266 Module |
| Flash Size | `4MB (FS:2MB OTA:~1019KB)` | `1MB (FS:128KB OTA:~438KB)` |
| Flash Mode | DOUT | DOUT |
| CPU Frequency | 80 MHz | 80 MHz |
| Upload Speed | 115200 | 115200 |
| Erase Flash | All Flash Contents on the FIRST upload only | same |
| lwIP Variant, Debug Port, others | leave at default | leave at default |

After the first upload, set Erase Flash back to "Only Sketch" so the saved settings and queued shots survive updates.
The first upload on a chip needs the full erase; without it the radio can crash at start-up (exccause 29).
To program: hold the programmer's button while plugging in (GPIO0 low), upload, then replug without the button.
Serial Monitor: 115200 baud. Open it before replugging to see the start-up messages.

Without the IDE: `python3 tools/build_firmware.py` builds both layouts with arduino-cli (`brew install arduino-cli`;
it uses the ESP8266 core 3.1.2 the IDE installed) and keeps them as `dist/firmware/fet-bridge-<version>-<1MB|4MB>.bin`.
Its settings are the ones above (1 MB: `eesz=1M128`, 4 MB: `eesz=4M2M`); for the 0.2.1 source its output was
byte-for-byte identical to the IDE's Export Compiled Binary. By hand:

    arduino-cli compile -b esp8266:esp8266:generic:FlashMode=dout,eesz=4M2M,xtal=80,baud=115200 firmware/fet_bridge

Before overwriting a chip that is in service, back it up with esptool (`esptool.py read_flash 0 0x400000 backup.bin`
for 4 MB). The 1 MB original also needs a full erase the first time it gets this firmware.

Ready-made firmware, no compiling: see [FLASHING.md](FLASHING.md).

## Fixed IP address (skip DHCP)

If the router is slow to hand out an address (the serial monitor shows "associated ... waiting for an IP address"
for a long time), open the setup page and fill in **Fixed IP address** (an unused address on the same network,
outside the router's DHCP pool, e.g. 192.168.1.60) and **Router address** (e.g. 192.168.1.1). The subnet mask
defaults to 255.255.255.0. Leave all three empty to use DHCP. With a fixed IP the target is "connected" as soon as
it joins the WiFi. Firmware 0.1.8 also waits 3 minutes (not 45 s) for DHCP before restarting a connection attempt,
since a restart only delays a slow-but-working lease.

## Slow IP address (DHCP) experiments, firmware 0.1.9

The serial monitor shows `WiFi options: ...` at start and `Connected to network: X (ip), after N s` for the first
connection. Switches in `src/platform/build_config.h`; change ONE per test and compare N over several power cycles:

| Test | FET_WIFI_NO_SLEEP | FET_WIFI_PHY_G | Also |
|------|-------------------|----------------|------|
| A (0.1.9 default) | 1 | 0 | |
| B | 0 | 0 | baseline = old behaviour |
| C | 1 | 1 | forces 802.11g |
| D | 1 | 0 | Arduino IDE Tools > lwIP Variant: other choice |

Router side: DHCP reservation for the ESP's MAC; a separate 2.4 GHz SSID with WPA2 only, protected management frames
off, fixed channel, 20 MHz; roaming assistant / Smart Connect off. A fixed IP (setup page) skips DHCP entirely.

### Firmware 0.1.10: DHCP state and fast DHCP restart

- `FET_DHCP_DIAG 1`: the "Not connected" line adds `[DHCP state S, tries T]` while associated. lwIP states:
  1 requesting (an offer was received, waiting for the ACK), 2 init, 6 selecting (discover sent, no offer yet),
  8 checking, 10 bound (has an address), 12 backing off. "Stays 6" = no offer reaches the ESP; "reaches 1 and stays" =
  the offer arrived but the final ACK did not.
- `FET_DHCP_RESTART_MS 6000`: while associated without an IP, the DHCP request is restarted every 6 s (the WiFi
  association is not dropped), because lwIP's own retry back-off grows 4, 8, 16 s. Prints `Restarting DHCP request on X`.
  Set to 0 to get the old behaviour back. Compare "Connected ... after N s" with it on and off.
- If the build fails on `lwip/netif.h` or `netif_dhcp_data`, set `FET_DHCP_DIAG 0` and tell me the error.

### Firmware 0.1.11

- The "Not connected" line now ends with `[netif <name><num> up|down dhcp STATE/TRIES; ... default=N]` for EVERY network
  interface (0.1.10 looked at only one and printed nothing). States: 1 requesting, 2 init, 6 selecting, 10 bound, 12 backing off.
- `FET_REASSOC_MS` (default 0 = off): set to 20000 to drop and re-join the WiFi when associated for 20 s without an IP
  (prints `Re-joining X`). Test it on its own, with `FET_DHCP_RESTART_MS` left at 6000, then again with it at 0.

### Firmware 0.1.12

0.1.11 hardware log: with `FET_DHCP_RESTART_MS 6000` every status line showed `st0 up dhcp 0/0` (lwIP DHCP client OFF, no
tries), so the restart through `wifi_station_dhcpc_stop/start` appears to leave the client off. It is now off by default (0).
Next: read the natural DHCP state with the restart off.

### Firmware 0.1.13

Hardware logs (0.1.9: 1267 s, 0.1.11: 1625 s to get an IP) both ended with success on the first status after a
"Retrying connection" (a fresh association), after many failed attempts. Hypothesis: each association to the ASUS
BQ16 Pro either gets DHCP answers or never does. So `FET_REASSOC_MS 15000` re-joins the WiFi after 15 s associated without
an IP (every 4th attempt waits `FET_REASSOC_LONG_MS 45000`). The restart of the DHCP request stays off
(`FET_DHCP_RESTART_MS 0`). Output: `Re-joining X (attempt N)`, then `Connected ... after N s`. Workaround, not a root-cause fix.

### Firmware 0.1.14: 802.11g by default

esp8266/Arduino issue #8299 (core > 2.5.2, ASUS routers incl. XT8 mesh: associates, no DHCP answer; the router replied
only when the ESP used 802.11g). `FET_WIFI_PHY_G` is now 1. The PHY mode can persist in the chip's flash, so after
removing it again, power-cycle and check the "WiFi options" line. Router-side alternatives from the thread: turn off
"Wi-Fi Agile Multiband" on 2.4 GHz, or disable 802.11n/"legacy" mode. The 15 s re-join (0.1.13) stays as a safety net.

### Firmware 0.1.15: diagnostics off

The 802.11g fix holds, so the experiment output is off: `FET_DHCP_DIAG 0` (no `[netif ...]` on the "Not connected"
line) and the `WiFi options: ...` line only prints with `FET_DEBUG 1`. The ordinary status lines (connecting, connected
after N s, re-joining, first report, setup portal) stay; `FET_STATUS_MESSAGES 0` removes them all. The WiFi settings
themselves are unchanged (802.11g, sleep off, 15 s re-join). The DHCP-restart experiment (`FET_DHCP_RESTART_MS`,
off since 0.1.12 because it switched lwIP's DHCP client off) is removed from the code. For a slow-connect investigation, set `FET_DHCP_DIAG 1`
and `FET_DEBUG 1` again.

### Firmware 0.1.16: FeT Monitor name

The setup WiFi is now `FeTMonitor-<last6>` (was `freETarget-<last6>`), the setup and status pages say FeT Monitor, and
mDNS looks for `_fetmonitor._tcp` (install `deploy/fet-monitor.avahi.service` on the Pi). Nothing else changed; saved
settings, queued shots and the boot counter are kept across the update.

### Firmware 0.2.0: updates from the Pi

- New server command `fw_update` (line `<sha256> <size>`): the bridge downloads `GET /api/v1/firmware/<sha256>` from the
  server it reports to (device token), feeds every byte through `FwCheck` (SHA-256, `src/core/sha256.h`) into the
  ESP8266 updater, and writes the image's last byte only after the whole image matched, so a damaged or wrong image never
  completes and the running firmware stays. On success it reports the result, then restarts into the new image.
- The heartbeat now reports `ota_space` (free room for a new image) and `flash_size` (the layout this build uses); the
  server refuses images that do not fit or were built for another flash size.
- HTTP requests carry `User-Agent: fet-monitor-bridge/<version>`; the server finds the same text inside uploaded images
  to recognise our firmware and its version.
- One-time step: a target on 0.1.x must get 0.2.0 by USB (esptool) or its `/update` page; after that, Range Control.
- Export one image per flash layout and update each target with the image matching its layout (ESP-01S boards here use
  4 MB, the original ESP-01 1 MB). Unverified on hardware: the download/flash path in `platform.cpp` (the host harness
  runs the same core logic against the real server, with memory standing in for flash).

### FeT Bridge firmware 0.2.1

The bridge firmware is now called FeT Bridge (its setup and status pages say so). No other change: a first real
0.2.0 -> 0.2.1 update from Range Control. After each Arduino export run `python3 tools/collect_firmware.py`: it checks the
export with the Pi's upload rules and keeps it as `dist/firmware/fet-bridge-<version>-<flash>.bin` (exports overwrite
each other otherwise).

### FeT Bridge firmware 0.2.2: factory reset

New server command `factory_reset` (Range Control, one target at a time; the command line must be `erase`). The
bridge reports the result first, then formats LittleFS (settings, server token, queued shots, boot counter), erases
the SDK's WiFi area (`ESP.eraseConfig()`) and restarts unconfigured, i.e. into the open setup portal. The server
refuses it for targets older than 0.2.2. Unverified on hardware (the host harness runs the same core logic).


### FeT Bridge firmware 0.3.0: settings over USB (flash station)

For the first 60 s after start the bridge listens on its serial port for one-line settings from the Pi's flash
station, so a freshly flashed module can be configured without its setup WiFi:

    FETCFG HELLO                                  ->  FETCFG HELLO <device id> <firmware version>
    FETCFG <settings JSON as hex> <crc32, 8 hex>  ->  FETCFG OK <device id>   (then it restarts with them)
                                                      FETCFG ERROR <reason>   (nothing is changed)

The settings are the same JSON as the setup page saves (`ssid`, `pass`, `host`, `port`, `token`, `name`, optional
`ip`/`gw`/`mask`) and pass the same checks. A provisioning line never reaches the shot log; every other byte (the
Arduino's output) passes on unchanged and in order. Hex keeps braces out of the line and replies never contain any, so
nothing here can look like a command to the Arduino. The settings restart does not count towards the "3 quick power
cycles" portal trigger (opening a serial port can reset a module on an auto-reset programmer). Tested in the host
harness only; unverified on hardware.
