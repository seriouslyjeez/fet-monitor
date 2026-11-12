# FeT Monitor quick start

Three short guides for a club volunteer. Each fits on a page; the full details are in [INSTALL.md](INSTALL.md).

1. [FeT Monitor on the range in 30 minutes](#1-fet-monitor-on-the-range-in-30-minutes)
2. [Your first target](#2-your-first-target)
3. [Linking the range to deepTENS](#3-linking-the-range-to-deeptens)

---

## 1. FeT Monitor on the range in 30 minutes

**You need:** a Raspberry Pi 3, 4 or 5 with its power supply, a microSD card (16 GB or more), a computer with an SD
card reader, and a phone.

1. **Write the card.** Download `fet-monitor-<version>-pi.img.xz` from the
   [latest release](https://github.com/seriouslyjeez/fet-monitor/releases/latest) and write it with
   Raspberry Pi Imager ("Use custom"). Details: [INSTALL.md, steps 1-2](INSTALL.md#1-download-the-fet-monitor-image).
2. **Start the Pi.** Card in, a network cable if you use one, then power. Wait about 2 minutes.
3. **Set it up from your phone.** Join the open WiFi network `FeTMonitor-Setup-XXXX`; the setup page opens by itself
   (or browse to `http://192.168.4.1/`). Choose a **Range Control password**, the time zone, and the network: a cable,
   the club WiFi, or the Pi's own range network (no router needed). **Save settings**, and write down the
   **address** it shows.
4. **Open FeT Monitor.** Put the phone back on the range network and open the address, for example
   `http://192.168.1.20:8080/`. Use the IP address: names ending in `.local` do not work on most club networks.
5. **Sign in to Range Control** (the menu) with the password from step 3. From here you set up targets
   ([guide 2](#2-your-first-target)), firmware, TV displays, staff logins and the Pi itself.

**Shut the Pi down from Range Control → This Pi** before you unplug it: pulling the power can damage the SD card.

## 2. Your first target

The Pi has a **flash station**: plug a target (or its WiFi module) into one of the Pi's USB ports, and the Pi
installs the firmware, gives the target its own key, and connects it to the range WiFi. No computer needed.

1. In **Range Control → Flash station**, choose your target under **Set up a target step by step**:
   - **Arduino Mega target:** a blue Arduino Mega board with a small WiFi module on top (most freETarget-compatible
     targets). You also need a USB programmer for the WiFi module (ESP-01); one with automatic download mode needs
     no hands, one with a PROG / UART switch works too.
   - **ESP32 target:** one board with a USB-C socket and a chip marked ESP32-S3.
2. Follow the steps. Each step shows only what it needs, and the progress of what the Pi is doing:
   - *Connect the target:* switch it on (12 V) and plug its USB cable into the Pi; press **Identify**.
   - *Install:* press **Install**. A first install saves the board's original firmware first (it can be put back),
     then writes and checks the new firmware and gives the target its key.
   - *WiFi:* fill in the range WiFi's name and password (a 2.4 GHz network) once; Install sends them.
   - *Mega targets only:* the WiFi module comes out (target **off**), goes into the programmer for its own install,
     then back in (target still off, the same way round).
3. **Test:** switch the target on and fire a shot. The last step lists the targets reporting now. A yellow dot
   (*No 12 V*) means an ESP32 target is running from USB alone: connect its 12 V supply.
4. **Put it on a lane:** Range Control → Targets.

A target set up at home on deepTENS, or at another range, comes to your range the same way: run the steps (it gets
your range's WiFi and a new key). Its owner sets it up again at home afterwards.

## 3. Linking the range to deepTENS

Linking is optional: FeT Monitor runs a range on its own. Linked, your range's matches and results go to
deeptens.com, athletes sign in at a lane with their deepTENS account, and the range can host virtual matches.
Shooting never waits for the internet: what is for deepTENS waits on the Pi while the internet is down.

1. On deeptens.com, the **Range Manager** signs up, with a passkey or two-step sign-in.
2. In FeT Monitor, open **Range Control → This Pi → deepTENS** and press **Link to deepTENS**. It shows a short
   **code** and a QR code.
3. On a phone or computer with internet, open the address it shows (or scan the code), sign in as the Range Manager,
   choose your range (or create it), and enter the code.
4. FeT Monitor says **Linked**. On deeptens.com, give your members their roles: Match Director, Range Officer, Coach.
