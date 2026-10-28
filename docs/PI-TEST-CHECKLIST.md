# Installer: manual test on real Raspberry Pi hardware

Nothing below has been run on a real Pi yet. The automated tests (`tests/test_install.py`) run `install.sh` against a
fake root folder on a Mac with stand-ins for `systemctl`, `useradd`, `chown`, `runuser` and `id`; they prove the
installer's logic, not its behaviour on Raspberry Pi OS. Tick each box and note anything odd.

Build the archive on the Mac first: commit, then `python3 tools/make_release.py`; copy
`dist/fet-monitor-<version>.tar.gz` and `dist/SHA256SUMS` to the Pi with `scp` (the repo is private, so `wget` from
GitHub will not work yet).

## A. Clean install (Pi Zero 2 W and, if available, a Pi 4/5)

Fresh SD card, Raspberry Pi OS (Legacy, 32-bit) Lite, Bookworm, set up as in `docs/INSTALL.md` steps 1-6.

- [ ] `sha256sum -c SHA256SUMS` prints `OK`.
- [ ] `tar -xzf ...` then `ls -l fet-monitor-*/install.sh` shows it executable (`-rwxr-xr-x`).
- [ ] `sudo ./install.sh` runs every step; note how long the whole run takes: ____ s (Zero 2 W).
- [ ] The password prompt does not echo what you type, asks twice, and rejects a 5-character password.
- [ ] The final box shows the Pi's real IP (compare with `hostname -I`) and a token.
- [ ] `systemctl status fet-monitor` is `active (running)`; `systemctl list-timers fet-monitor-backup.timer` shows the next 03:30 run.
- [ ] `id fet-monitor` exists; `getent passwd fet-monitor` shows shell `/usr/sbin/nologin`; `ls -d /home/fet-monitor` does NOT exist.
- [ ] `sudo ls -l /var/lib/fet-monitor`: `device_token` and `admin_password` are `-rw-------` owned by `fet-monitor`; `fetserver.db` exists (made by the service).
- [ ] `ls -ld /opt/fet-monitor /var/lib/fet-monitor /var/backups/fet-monitor`: root 755, fet-monitor 750, fet-monitor 750.
- [ ] `http://<Pi IP>:8080/` opens from a phone on the same network.
- [ ] A target with FeT Bridge firmware 0.1.16 or newer, set up with that IP and token, reports and a shot appears.
- [ ] `sudo reboot`; after it, the page works again without doing anything (service enabled at boot).
- [ ] `sudo systemctl start fet-monitor-backup.service` then `ls /var/backups/fet-monitor` shows a new `fet-*.db`.

## B. Re-run / upgrade

- [ ] Fire a few shots. Run `sudo ./install.sh` again: no password prompt ("already set (kept)"), same token
      printed, "stopping the running server for the upgrade" shown, the shots are still on the page.
- [ ] Bump `__version__` on the Mac, rebuild the archive, copy, unpack, run its `install.sh`: the health check
      (`curl -s http://127.0.0.1:8080/api/v1/health`) reports the new version.
- [ ] `sudo ./install.sh --reset-password` asks for a password; the new one works in Range Control (once built).
- [ ] `sudo ./install.sh --no-password` on a box without a password prints the set-password command; that command,
      run exactly as printed, works.

## C. Migration from the old `freetarget-server` install

Use the Pi that runs the old install today (or recreate it: old README steps with the previous release).

- [ ] Before: note the number of shots/series on the page, and `sudo cat /var/lib/freetarget/device_token`.
- [ ] `sudo ./install.sh` prints "Found an older install" and a backup path
      `/var/backups/freetarget-full-before-install-*.tar.gz`; `tar -tzf` on it lists the old database, code and units.
- [ ] The migration script's own archive `/var/backups/freetarget-before-migration-*.tar.gz` also exists.
- [ ] After: same shots/series on the page; `/var/lib/fet-monitor/device_token` equals the old token; targets keep
      reporting without being reconfigured.
- [ ] `systemctl is-enabled freetarget-server` says `disabled`; `systemctl is-active freetarget-server` says `inactive`.
- [ ] Rollback works: run the two rollback commands the migration printed; the old service serves the old data.
      Then re-enable the new one (`sudo systemctl disable --now freetarget-server; sudo systemctl enable --now fet-monitor`).
- [ ] Running `sudo ./install.sh` again does NOT migrate again ("already migrated").

## D. Failure handling

- [ ] Run without sudo: clear "run with sudo" message, nothing changed.
- [ ] Trixie (if you have a Trixie card): installs, with the "has not been tested" warning.
- [ ] Block the port (`sudo systemctl stop fet-monitor; sudo python3 -m http.server 8080 &` then `sudo ./install.sh`):
      the installer reports the server did not answer and shows the log; kill the http.server afterwards.

## E. Uninstall

- [ ] `sudo ./install.sh --uninstall`: service gone (`systemctl status fet-monitor` says not found), `/opt/fet-monitor`
      gone, `/var/lib/fet-monitor` still there. Re-running `install.sh` brings back the same data and token.
- [ ] `sudo ./install.sh --uninstall --purge`, type something other than DELETE: data kept. Type DELETE: data, backups
      and the `fet-monitor` user are gone.

## Could not be verified off the Pi

- Real `systemctl`/`journalctl`/`runuser`/`useradd` behaviour, the systemd sandboxing in the units
  (`ProtectSystem=strict` with `ReadWritePaths`) and file ownership (the tests cannot change owners on a Mac).
- `getpass` on a real terminal (the tests feed the password through stdin without a terminal).
- `ip -4 route get` output parsing on Raspberry Pi OS, and start-up time on a Pi Zero 2 W (the installer waits up to
  45 s for the server to answer).
- Raspberry Pi Imager screen names in `docs/INSTALL.md` (they change between Imager versions).

## The Pi as an appliance (setup network, System page, SD card image)

Upgrade first (an existing Pi, through Range Control):
- [ ] After the update the Pi does NOT show the setup network (it has a network); FeT Monitor runs as before.
- [ ] *Range Control → This Pi* shows the name, time zone, clock and network; *Turn SSH off/on* works (`ssh` refused /
      accepted); *Set* the admin password and log in with it over SSH; *Restart* comes back in about a minute.
- [ ] *Change network* to a club WiFi with a WRONG password: "nothing changed", the Pi stays reachable.
- [ ] *Shut down*: the green light stops blinking; unplug, plug in: it starts normally.

The image (a spare SD card):
- [ ] The *SD card image* workflow attached `fet-monitor-<version>-pi.img.xz`; `release.py image` signed it.
- [ ] Raspberry Pi Imager → *Use custom* → the image, customisation **No**. First start: within ~2 minutes the open
      WiFi `FeTMonitor-Setup-XXXX` appears (WiFi not blocked on a new card).
- [ ] iPhone and Android: joining it opens the setup page by itself (captive portal). If not, `http://192.168.4.1/`
      works.
- [ ] Cable: the page shows the address; *Keep as fixed*; Save shows address + device token; *Switch now*; FeT Monitor
      opens at the address; the token works in a target.
- [ ] Club WiFi: *Test connection* with a wrong password -> the setup network comes back and the page says why; with
      the right one -> the address; *Switch now* -> FeT Monitor on the club WiFi.
- [ ] Own range network: a target (ESP-01) joins it with server address 10.42.0.1; a phone joins it and opens
      `http://10.42.0.1:8080/`; with no internet the clock is right (from the phone).
- [ ] Fallback: unplug the cable (cable setup) and restart: after ~3 minutes the setup network appears with the
      network settings only; plug the cable back in: it closes by itself.
- [ ] Two cards from the same image get different device tokens; SSH is off until switched on.

Keeping the Pi's software up to date:
- [ ] *This Pi → Update the Pi now* (with internet): "N packages updated" (several minutes on a Zero 2 W); FeT
      Monitor keeps answering meanwhile; during a running match it is refused.
- [ ] Without internet (own range network): "no internet: could not reach the update servers".
- [ ] After a kernel update the page says *Restart needed*; *Restart the Pi* clears it.
- [ ] Nightly: `systemctl list-timers fet-os-update.timer` shows ~04:00; the next morning *This Pi* shows last night's
      update (or "skipped tonight: ..." if a match ran); `journalctl -u fet-os-update` has the details; the Pi
      restarted only if an update needed it.
