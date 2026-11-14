# Releasing (maintainer)

Every release is signed with the FeT Monitor release key (`~/.ssh/fet-monitor-release`, ed25519, passphrase
protected). The Pi installs only releases whose `SHA256SUMS` carries a valid signature by a key in
`fetserver/release_signers`, and only files listed in it with a matching hash.

Fingerprint: `SHA256:X50vi9/xw7zvNVO6SyaLsVb7yWCg/Uoe2Yy0am0J6B8`.

## Before any release: what is out

`python3 tools/release_status.py` shows, per product (FeT Monitor, FeT Bridge, the Mega and ESP32 target firmware, FeT
Display): the newest release on GitHub, the version in the code, and the commits since. "ready to release" means the
code has a new version; "bump the version" means the code changed after its published version. Commits that were
checked and need no release (the built image is the same, or nothing that product uses changed) are recorded in
`tools/release_reviewed.json` with why; a later commit shows again. It also says whether the newest server release's SD
card image is signed. `tools/release.py` refuses, before asking for the passphrase, any version already tagged here or
on GitHub, or already published.

## Server release (tag `v<version>`)

1. Bump `__version__` in `fetserver/__init__.py`, run the tests, commit and push.
2. Write the release notes (Markdown; Range Control shows them before anyone installs), e.g. `notes.md`.
3. `python3 tools/release.py server --notes notes.md` builds `dist/server-<version>/` (archive, `SHA256SUMS`,
   `SHA256SUMS.sig`), asks for the key's passphrase, and checks the signature with `fetserver/release_signers`.
4. Publish: add `--publish` (asks before running `gh release create`), or run the printed command.
5. The SD card image: publishing the release starts the GitHub Actions workflow *SD card image*
   (`.github/workflows/image.yml`, about an hour). It downloads the signed release, checks its signature with
   `fetserver/release_signers` and refuses to build otherwise, builds Raspberry Pi OS Lite (Trixie, 64-bit) with
   FeT Monitor (`image/stage-fet`, `install.sh --image`) and attaches `fet-monitor-<version>-pi.img.xz` and
   `IMAGE-SHA256SUMS` to the release. Then sign it: `python3 tools/release.py image <version> --publish` downloads the
   image, checks its hash on your computer, signs `IMAGE-SHA256SUMS` and uploads `IMAGE-SHA256SUMS.sig`. To rebuild
   an image by hand: Actions → *SD card image* → *Run workflow* with the tag.

## FeT Bridge firmware release (tag `fw-<version>`)

1. Bump `FET_FW_VERSION` in `firmware/fet_bridge/src/platform/build_config.h`, commit.
2. `python3 tools/build_firmware.py` builds the 1 MB and 4 MB images with arduino-cli and collects them (same bytes as
   the IDE). With the IDE instead: Flash Size 1MB (FS:128KB OTA:~438KB), DOUT, Export Compiled Binary, then
   `python3 tools/collect_firmware.py`; repeat with Flash Size 4MB (FS:2MB OTA:~1019KB).
3. `python3 tools/release.py firmware <version> --notes notes.md [--publish]`.

Range Control's *Check GitHub for FeT Bridge firmware* downloads the newest `fw-` release's images (after the same
signature and image checks as a manual upload).

## FeT target firmware release (tag `tgt-<version>`)

1. Bump `FW_VERSION` in `firmware/target/mega/fet_target_mega/fet_target_mega.ino` (and `version=` in
   `firmware/target/core/library.properties`), commit.
2. Build: `arduino-cli compile -b arduino:avr:mega --library firmware/target/core --output-dir <dir>
   firmware/target/mega/fet_target_mega`, then copy `<dir>/fet_target_mega.ino.hex` (not the one "with_bootloader") to
   `dist/target-firmware/fet-target-mega-<version>.hex`.
3. `python3 tools/release.py target <version> --notes notes.md [--publish]` (checks the .hex like the Pi does).

Range Control's *Check GitHub for FeT target firmware* (flash station) downloads the newest `tgt-` release's image.
Firmware releases are never marked GitHub's "Latest" (that is always the newest FeT Monitor release, which
docs/INSTALL.md links to).

## FeT Display (tag `display-<version>`)

1. Bump `VERSION` and `MARKER` in `firmware/display/lib/fetdisplay.py`, commit.
2. `python3 firmware/display/micropython/build.py` builds `dist/display/fet-display-<version>.bin` (MicroPython with the
   display and fetcore frozen in, written at 0x1000).
3. `python3 tools/release.py display <version> --notes notes.md [--publish]`.

deepTENS's daily firmware fetch picks it up for "Program a display" (or on the server:
`sudo deeptens-compose exec app python manage.py fetch_firmware`); a Pi gets it with Firmware → Check GitHub for FeT
Display, for the flash station.

## FeT target firmware for the ESP32 board (tag `esp32-<version>`)

1. Bump `FW_VERSION` in `firmware/target/esp32/fet_target_esp32/fet_target_esp32.ino`, commit.
2. Build (see firmware/target/README.md) and copy `fet_target_esp32.ino.merged.bin` (the full 8 MB image) to
   `dist/target-firmware/fet-target-esp32-<version>.bin`.
3. `python3 tools/release.py esp32 <version> --notes notes.md [--publish]` (checks the image like the Pi does).

Range Control's *Check GitHub for ESP32 firmware* downloads it; targets then update over WiFi (Firmware →
Update), signed by the Pi with each target's own key.

## Rules

- Never publish an unsigned release, and never re-use a version number (the Pi compares versions). Never rebuild a
  published version either: a change after a release is a new version (2026-11-14: a FeT Display 0.2.0 image was
  rebuilt with other contents after 0.2.0 was published; it became 0.2.1).
- The repository must be public for the Pi to see releases (GitHub answers 404 to anonymous requests otherwise).
- Changing the key: add the new key to `fetserver/release_signers`, release that with the OLD key, and only then
  sign with the new one. Pis that missed that release need a manual install. If the private key is lost, every Pi
  needs a manual install of a release that trusts a new key.
- If the key may be compromised: remove it from `release_signers` in a release signed by it as soon as possible,
  and tell the clubs to reinstall by hand after checking the new fingerprint from a second source.
