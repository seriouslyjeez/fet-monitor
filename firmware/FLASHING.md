# Flashing the ready-made FeT Bridge firmware (no compiling)

Download the FeT Bridge firmware `fet-bridge-<version>-<1MB|4MB>.bin` (0.1.15: `freetarget-bridge-0.1.15-1MB.bin`) from the GitHub Releases page. The same file works on the original
ESP-01 (1 MB) and the ESP-01S (1 MB or 4 MB): it is built with the 1 MB layout (128 KB for queued shots, about 438 KB
for over-the-air updates). On a 4 MB chip it uses the first 1 MB only.

| Version | File | SHA-256 |
|---------|------|---------|
| 0.1.15 | `freetarget-bridge-0.1.15-1MB.bin` (428,544 bytes) | `b3085a6ecfebcef9441ca6e805bedba2d37e9a28a1f9828ea872abf0b500638f` |

Check the download: `shasum -a 256 freetarget-bridge-0.1.15-1MB.bin` (macOS/Linux) or
`certutil -hashfile freetarget-bridge-0.1.15-1MB.bin SHA256` (Windows) must print the value above.

## You need

- A USB programmer for the ESP-01 (the kind with a PROG/UART switch, or one where GPIO0 is held to GND at power-up).
- esptool: `pip install esptool` (or `brew install esptool` on macOS).
- The serial port name: `/dev/cu.usbserial-…` (macOS), `/dev/ttyUSB0` (Linux), `COM3` etc. (Windows).

Take the ESP out of the target first. Seat it in the programmer the right way round: an offset plug does not
work and can look like line noise.

## First install (or moving from a different build)

The first install needs a FULL erase. Without it the chip can crash at start (`wifi_softap_start`, exccause 29).
The erase also removes any saved WiFi settings, queued shots and the boot counter.

```
esptool --chip esp8266 --port <port> erase_flash
esptool --chip esp8266 --port <port> --baud 115200 write_flash -fm dout 0x0 freetarget-bridge-0.1.15-1MB.bin
```

(Older esptool versions are called `esptool.py`.) Put the programmer back in PROG mode, or re-plug it, between the
two commands if the second one cannot connect. Do not pass `--flash_size detect`; the default keeps the 1 MB setting.

Optional: keep a backup of the old firmware before erasing:
`esptool --chip esp8266 --port <port> read_flash 0 ALL old-esp-backup.bin`. That backup contains the old WiFi
password, so do not share it.

## Updating later (keeps settings and queued shots)

- Browser: open the setup portal (3 quick power cycles of the target, then join the open WiFi
  `FeTMonitor-<last6>`; firmware 0.1.15 and older: `freETarget-<last6>`), go to `/update` (user `fet`, password = the server token from the setup page) and upload
  the new `.bin`.
- Or with esptool as above, without `erase_flash`.
- Firmware 0.2.0 and newer: from Range Control on the FeT Monitor page (upload the `.bin`, select targets, Update).
  The image must be built for the target's flash size.

## After flashing

Put the ESP back in the target **with the target powered off**. The ESP is not hot-pluggable: power the target up
with the ESP already in place. On first start it opens the setup portal on the open WiFi `FeTMonitor-<last6>` (0.1.15 and older: `freETarget-<last6>`).
Enter the club WiFi (2.4 GHz, WPA2), the server's IP address (`.local` names are not reliable) and the server
token. Then fire a shot
and check that it appears on the dashboard.
