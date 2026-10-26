# Releasing (maintainer)

Every release is signed with the FeT Monitor release key (`~/.ssh/fet-monitor-release`, ed25519, passphrase
protected). The Pi installs only releases whose `SHA256SUMS` carries a valid signature by a key in
`fetserver/release_signers`, and only files listed in it with a matching hash.

Fingerprint: `SHA256:X50vi9/xw7zvNVO6SyaLsVb7yWCg/Uoe2Yy0am0J6B8`.

## Server release (tag `v<version>`)

1. Bump `__version__` in `fetserver/__init__.py`, run the tests, commit and push.
2. Write the release notes (Markdown; Range Control shows them before anyone installs), e.g. `notes.md`.
3. `python3 tools/release.py server --notes notes.md` builds `dist/server-<version>/` (archive, `SHA256SUMS`,
   `SHA256SUMS.sig`), asks for the key's passphrase, and checks the signature with `fetserver/release_signers`.
4. Publish: add `--publish` (asks before running `gh release create`), or run the printed command.

## FeT Bridge firmware release (tag `fw-<version>`)

1. Bump `FET_FW_VERSION` in `firmware/fet_bridge/src/platform/build_config.h`, commit.
2. In Arduino: Flash Size 1MB (FS:128KB OTA:~438KB), DOUT, Export Compiled Binary, then
   `python3 tools/collect_firmware.py`. Repeat with Flash Size 4MB (FS:2MB OTA:~1019KB).
3. `python3 tools/release.py firmware <version> --notes notes.md [--publish]`.

Range Control's *Check GitHub for FeT Bridge firmware* downloads the newest `fw-` release's images (after the same
signature and image checks as a manual upload).

## Rules

- Never publish an unsigned release, and never re-use a version number (the Pi compares versions).
- The repository must be public for the Pi to see releases (GitHub answers 404 to anonymous requests otherwise).
- Changing the key: add the new key to `fetserver/release_signers`, release that with the OLD key, and only then
  sign with the new one. Pis that missed that release need a manual install. If the private key is lost, every Pi
  needs a manual install of a release that trusts a new key.
- If the key may be compromised: remove it from `release_signers` in a release signed by it as soon as possible,
  and tell the clubs to reinstall by hand after checking the new fingerprint from a second source.
