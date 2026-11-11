# FeT Bridge: the WiFi module of a target

FeT Bridge is the firmware for the small ESP-01 WiFi module on a freETarget-compatible target's Arduino Mega board. It
carries the target's shots to FeT Monitor (the range server on a Raspberry Pi) and keeps every shot until FeT Monitor
has it. Targets on the ESP32 board have WiFi built in and need no ESP-01.

## Which ESP-01 to buy

| Module | Memory | With FeT Monitor | With deepTENS directly (coming) |
|---|---|---|---|
| **ESP-01S, 4 MB** (recommended) | 4 MB | yes | yes (HTTPS) |
| ESP-01, 1 MB (the original) | 1 MB | yes, while the firmware fits | no: no room for HTTPS |

Buy 4 MB modules for new targets: they cost a few dollars more and leave room for the features to come. Range Control
→ Flash station → **Identify** shows a module's memory.

## How long the 1 MB ESP-01 is supported

As long as FeT Bridge fits in its 1 MB. Each FeT Bridge release is built for both sizes; the 1 MB build is checked to
leave room for updates over WiFi. If a future version no longer fits, FeT Monitor shows on that target:

> This ESP-01 is no longer compatible with FeT Monitor and deepTENS. Please purchase and flash a new ESP-01 with 4 MB
> of memory to continue.

with a link back to this page.

## Installing FeT Bridge

From FeT Monitor: put the module on a USB programmer, plug it into the Raspberry Pi and use **Range Control → Flash a
device** (docs/INSTALL.md, section 7a). An auto-reset programmer (CH340 with automatic download mode) is easiest.
Later updates go over WiFi from **Range Control → Firmware**.

## Power

A target runs from its 12 V supply. USB is for flashing and setting up only (docs/INSTALL.md, section 7b).
