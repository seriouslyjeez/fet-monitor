# Installing FeT Monitor on a Raspberry Pi

This guide is for a club volunteer; no Linux experience is needed. Allow about 45 minutes, most of it waiting.

**You need:** a Raspberry Pi (Zero 2 W, 3, 4 or 5) with its power supply, a microSD card (16 GB or larger, a good
brand), a computer with an SD card reader, and access to the club router's admin page. A wired network cable is
best for the Pi; WiFi works too.

Commands are shown in grey boxes. Type them exactly (or copy and paste), then press Enter. Where you see
`<Pi IP>`, type the Pi's address instead, for example `192.168.1.20`.

---

## 1. Prepare the SD card

1. On your computer, install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/> and start it.
2. **Choose Device:** your Pi model.
3. **Choose OS:** *Raspberry Pi OS (other)* → **Raspberry Pi OS (Legacy, 32-bit) Lite**.
   (On a Pi 4 or 5 the 64-bit *Raspberry Pi OS Lite (Legacy)* also works. Pick a Bookworm-based image; FeT Monitor
   is tested on Bookworm.)
4. **Choose Storage:** the SD card. Double-check it is the card, not another drive: it will be erased.
5. Click **Next**. When asked *"Would you like to apply OS customisation settings?"* choose **Edit settings**.
   (Newer Imager versions show these as steps of a wizard; the settings are the same.)

## 2. Imager settings

On the **General** tab:

- **Hostname:** `range` (any short name; it is only a label).
- **Username and password:** choose them and write them down. You need them to log in.
- **Wireless LAN:** only if the Pi will not use a cable. Enter the network name and password, and set
  **Wireless LAN country** to your country.
- **Locale settings:** your **time zone** (important: shot times use it) and keyboard layout.

On the **Services** tab:

- Tick **Enable SSH** and choose **Use password authentication**.

Click **Save**, then **Yes** to apply the settings, and **Yes** to write the card. Wait until Imager says it is done,
then remove the card.

## 3. First boot

Put the card in the Pi, connect the network cable (if used) and then the power. Wait **2 to 3 minutes**: the first
start sets everything up and may restart once on its own.

## 4. Find the Pi's address and fix it

1. Open the router's admin page and find the list of connected devices (often called *Clients*, *DHCP leases* or
   *Attached devices*). Look for the hostname you chose (`range`). Note its IP address, for example `192.168.1.20`.
2. In the router, add a **DHCP reservation** (also called *address reservation* or *manual assignment*) for that
   device, so the Pi always gets the same address.

Why this matters: each target is told the Pi's **IP address** and sends its shots there. Names like `range.local`
did not work reliably on club networks in testing, so the targets need a number that never changes.

## 5. Log in to the Pi

On your computer open a terminal (Windows: *PowerShell*; Mac: *Terminal*) and type:

    ssh <your username>@<Pi IP>

The first time, answer `yes` to the question about the key fingerprint, then type the Pi password (nothing is shown
while you type; that is normal).

## 6. Update the Pi

    sudo apt update && sudo apt full-upgrade -y
    sudo reboot

Wait a minute, then log in again as in step 5.

## 7. Download and check FeT Monitor

On the [releases page](https://github.com/seriouslyjeez/fet-monitor/releases) find the newest **server** release
(named `v<version>`, for example `v0.6.24`; releases named `fw-...` are FeT Bridge firmware). Download its three
files on the Pi, replacing `0.6.24` with that version:

    cd ~
    V=0.6.24
    wget https://github.com/seriouslyjeez/fet-monitor/releases/download/v$V/fet-monitor-$V.tar.gz
    wget https://github.com/seriouslyjeez/fet-monitor/releases/download/v$V/SHA256SUMS
    wget https://github.com/seriouslyjeez/fet-monitor/releases/download/v$V/SHA256SUMS.sig

**Check that the release really comes from the FeT Monitor author** (the signature), then that the download is
complete (the checksum). First save the author's public key in a file named `signers` (one line; copy it exactly):

    echo 'release@fet-monitor namespaces="fet-monitor-release" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICZo2o3OYW8M4gMSQXrY0/dBfgHDD5kQykvSefZcm5te' > signers

Then:

    ssh-keygen -Y verify -f signers -I release@fet-monitor -n fet-monitor-release -s SHA256SUMS.sig < SHA256SUMS
    sha256sum -c SHA256SUMS

The first command must print `Good "fet-monitor-release" signature for release@fet-monitor with ED25519 key
SHA256:X50vi9/xw7zvNVO6SyaLsVb7yWCg/Uoe2Yy0am0J6B8`; the second `fet-monitor-0.6.24.tar.gz: OK` (with your version). **If either prints anything else,
stop** and do not install: delete the files and ask for help.

Where the key comes from matters: it is printed here, in the repository's `fetserver/release_signers`, and every
installed FeT Monitor shows the same file. If you can, compare the fingerprint above with one from a second source
(for example a printout from the club or the author).

> **While the repository is private** the download links above only work for its owner. Copy the three files
> from your computer instead: `scp fet-monitor-$V.tar.gz SHA256SUMS SHA256SUMS.sig <your username>@<Pi IP>:`

Do **not** install from a command that pipes a download straight into `sudo bash`. Always download, check, then run.

## 8. Install

    tar -xzf fet-monitor-$V.tar.gz
    cd fet-monitor-$V
    sudo ./install.sh

The installer checks the system, installs FeT Monitor and starts it. Along the way:

- It asks for a **Range Control password** (twice). Range Control is the page for removing, banning and updating
  targets. Choose a password you do not use anywhere else: the club network is not encrypted, so it could be
  overheard there. Write it down.
- At the end it prints a box with the **web address** and the **device token**. **Write the token down**: every
  target needs it.

If anything fails, the installer stops with a message saying which step failed. See *Troubleshooting* below.

## 9. Open FeT Monitor

On any phone, tablet or computer on the same network, open a browser at:

    http://<Pi IP>:8080/

You should see the FeT Monitor page with *All targets* (empty until a target reports).

## 10. Set up each target

For each target (its WiFi bridge must already have FeT Monitor firmware; see `firmware/FLASHING.md`):

1. With the target off, make sure the WiFi module is plugged in. Switch the target **off and on three times
   quickly** (within a few seconds). This opens its setup portal.
2. On a phone, join the open WiFi network **`FeTMonitor-xxxxxx`** (older firmware: `freETarget-xxxxxx`). The setup page
   usually opens by itself; if not, browse to `http://192.168.4.1/`.
3. Enter:
   - **WiFi network** and **password** of the club network (2.4 GHz, WPA2; the module cannot use 5 GHz),
   - **Server address:** the Pi's IP address (not `range.local`),
   - **Server token:** the device token from step 8.
4. Save. The target restarts, joins the club WiFi and appears on the FeT Monitor page within a minute.
   Fire a shot to check.

## 11. Optional: automatic security updates

    sudo apt install -y unattended-upgrades

This keeps the Pi's own software patched (`openssh-client`, which checks release signatures, is part of it).
FeT Monitor itself is updated separately:

- **From Range Control**: *Check for updates* shows the newest signed release and its notes; *Install* backs up the
  database, installs it and restarts. If the new version does not start, the previous one comes back by itself.
- **By hand:** download, check and unpack a newer release as in steps 7 and 8, and run its `sudo ./install.sh`.
  Re-running the installer is the upgrade: it keeps the database, the device token and the password.

---

## Troubleshooting

**Is it running? What does the log say?**

    systemctl status fet-monitor
    journalctl -u fet-monitor -n 50 --no-pager
    journalctl -u fet-monitor -f              # follow live; Ctrl+C to stop

`active (running)` is good. To restart it: `sudo systemctl restart fet-monitor`.

**The page at port 8080 does not open.**

- Check the address: `hostname -I` on the Pi prints it. Use `http://`, not `https://`, and add `:8080`.
- Check the server answers on the Pi itself: `curl http://127.0.0.1:8080/api/v1/health` should print `"ok": true`.
  If it does, the problem is the network: is your phone on the same network (not a guest network, not mobile data)?
  Some routers isolate WiFi clients ("AP isolation" / "client isolation"); turn that off for the club network.
- If it does not, look at the log (above).

**Lost the device token.** It is stored on the Pi:

    sudo cat /var/lib/fet-monitor/device_token

**Lost the Range Control password.** Set a new one (everyone logged in is logged out):

    cd ~/fet-monitor-$V && sudo ./install.sh --reset-password

or, without the release folder:

    sudo -u fet-monitor python3 -m fetserver set-password --data-dir /var/lib/fet-monitor

(run the second form from `/opt/fet-monitor`: `cd /opt/fet-monitor` first).

**Restore the database from a backup.** A backup is made every night at 03:30 in `/var/backups/fet-monitor`
(the newest 14 are kept). To go back to one:

    ls -l /var/backups/fet-monitor                     # pick one, e.g. fet-20261025-033000.db
    sudo systemctl stop fet-monitor
    sudo mv /var/lib/fet-monitor/fetserver.db /var/lib/fet-monitor/fetserver.db.broken
    sudo rm -f /var/lib/fet-monitor/fetserver.db-wal /var/lib/fet-monitor/fetserver.db-shm
    sudo cp /var/backups/fet-monitor/fet-20261025-033000.db /var/lib/fet-monitor/fetserver.db
    sudo chown fet-monitor:fet-monitor /var/lib/fet-monitor/fetserver.db
    sudo systemctl start fet-monitor

Shots fired after that backup are lost from the database unless the targets still have them queued.
The server refuses to start if its database fails its integrity check; the log then says so, and restoring a
backup as above is the fix.

**Remove FeT Monitor.** `sudo ./install.sh --uninstall` removes the program and service but keeps the database,
backups, token and password (re-running the installer brings everything back).
`sudo ./install.sh --uninstall --purge` also deletes all data, after asking you to type `DELETE`.
