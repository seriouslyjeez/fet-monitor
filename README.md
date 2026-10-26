# FeT Monitor (v0.6.22: firmware updates from the Pi; Range Control; series, zoom, scoring screens, ammo test, sight adjustment; ESP-01 firmware included)

FeT Monitor is an independent, standalone companion project for electronic targets running the
[freETarget](https://github.com/ten-point-nine/freETarget) Arduino firmware. It is not part of, affiliated with or
endorsed by the freETarget project; "freETarget" is used here only to say what it works with.

Receives shots and status from freETarget-compatible targets over the club LAN, stores them
durably in SQLite, scores them (ISSF 10 m air rifle, exact arithmetic) and serves a JSON API
and a web dashboard. Standard library only: Python 3.9 or newer, nothing to install.

Open `http://<pi-address>:8080/` in any browser (phone, tablet, PC).

**All targets (coach view).** One card per target: name and online dot, a target picture that
auto-zooms, the shooter, then a score box. The big number on the left is the total in decimal with
the integer score and X count under it. To its right, the top row lists the aggregate of each group
of 10 shots and the bottom row shows the shots on the current target picture. Click a card to open it.

**Single target (shooter view).** The target is the focus. Above it is the zoom bar:
*Off* (whole target), *1-9* (zoom to that ring and stay there), *Auto* (starts on the 8 ring, then
widens one ring at a time so the worst shot is always fully in view). Your choice is remembered per
browser. On the right: target name and info, the shots on the current picture (filled from the top),
then Last shot with the decimal and integer aggregate of the current group and of the whole series.
Inner tens (10.2 and above) show an **X** and are totalled.

**Practice and sighter shots are not scored**, so the target's top-right corner is red (black vanished when zoomed in).

**Counting.** Shots are numbered within the series. The picture and the shot list reset when
shot 11 arrives (not before, so you can study shot 10) while the number keeps counting.
A practice series is 20 shots; the 21st shot starts a new series at 1. A practice series left idle for 30
minutes also starts afresh. In a match, sighters count up with no limit (the picture still resets every
10) and record shots count to 60, picking up where they left off if you go back to sighters and return.

**Adjust sights** (practice only, for whoever is in control): the button appears once 3 shots have been fired since
the last adjustment. The overlay says which way and how many whole clicks to turn the sight (Up/Down, Left/Right =
the way the shots will move). It aims the group's best-scoring centre (the geometric median: decimal scores fall
by 0.1 for every 0.25 mm, so the point with the least total distance gives the highest total) at the bullseye and
ignores flyers: shots more than 3x the group's median distance from its centre (never closer than 1.5 mm), always
keeping at least 3 shots; flyers are drawn dashed and misses are left out. *Clicks per ring* (default 4.0, decimals
such as 3.5 or 3,5 accepted; one ring = 2.5 mm) is remembered by that browser. On **Done** it offers to **clear the
target** (a new practice series) or **keep the shots**, after which only newer shots count; a new series (including the
automatic one after 20 shots) starts the count afresh.

**Range Control** (header link; needs the Range Control password, set on the Pi by the installer or with
`cd /opt/fet-monitor && sudo -u fet-monitor python3 -m fetserver set-password --data-dir /var/lib/fet-monitor`):
select targets and set their mode (Practice / Match / Ammo test) or start a new series without taking control
(whoever holds control keeps it); **Remove** a dead target (hidden, history kept, back automatically when it reports
again); **Ban** a rogue one (everything it sends is refused with 403 and not stored; it keeps its queue) and unban it.
**Bridge firmware updates** (Range Control, bridge firmware 0.2.0 or newer): upload the `.bin` exported from Arduino,
pick it, select targets and press *Update*. The Pi accepts only FeT Monitor bridge images (ESP8266 image, flash mode
DOUT, carrying `fet-monitor-bridge/<version>`), at most 1 MB, and queues one only for a target that reported it has
room and runs a build for the same flash size (a 1 MB image on a 4 MB-layout ESP-01S would lose its settings and
queued shots). The target downloads it from the Pi with its device token, checks the SHA-256 before the last byte
reaches the updater (a damaged download is abandoned, the running firmware stays), reports, and restarts.
Each row shows the update's progress. Targets on 0.1.x need one update to 0.2.0 by USB or their `/update` page first.

The login lasts 2 hours idle, 12 hours at most, and ends when the password changes or the server restarts; five wrong
passwords from one address lock it for 10 minutes. The LAN is plain HTTP, so the password can be overheard there:
use one that is not used anywhere else.

**The gear icon** holds everything that is not scoring:
* *Control*: take, take over or release. Viewing never needs it. The claim lasts 60 s, renews itself while the
  page is open, and frees itself when the page closes.
* *Mode*: Practice, Match or Ammo test. In Match the **Sighter** and **Record** buttons appear on the main screen
  (usable only by whoever is in control); a match always starts in Sighter. Everyone else sees the current mode.
* *Shooter* name, and a *New series* button. A new shooter starts a new series.
* *Advanced*: shots per target picture, shots per aggregate group, total shots for practice and for match
  (defaults 10 / 10 / 20 / 60). Saved per target and applied to the live series and new ones.
* *Review*: any past series, starting at shot 1. **<<** back one target, **<** back one shot, **>** forward one shot,
  **>>** forward one target (the arrow keys do the same: left/right = one shot, up/down or PageUp/PageDown = one
  target). The picture, the last-shot panel, the groups and the total are exactly as they stood after the shot shown.
  >> goes to the end of the current target picture, or of the next one if you are already there; << goes to the end of
  the previous one (or shot 1). In an ammo test << and >> move ten shots and the group figures, oval and centre grow shot by shot.
  In a match, switch between record and sighters.

A shot takes the mode that was active when it was fired, not when it arrived, so a delayed upload still lands in
the right series. Misses are listed as M, count as 0 and take a shot number.

**Ammo test** (rifle or pistol in a vise, to compare ammunition):
* Pick *Ammo test* under Mode, then the projectile: **4.5 mm pellet** (default), **.22 LR (5.7 mm)** or any size from
  1 to 10 mm typed in (stored in 0.02 mm steps). .22 LR bullets are 0.222-0.2255 in (5.64-5.73 mm) depending on
  the maker; 5.7 mm is the figure SAAMI/Wikipedia round to. Type your own if you measure yours.
* The test takes **any number of shots** (no 10/20/60 limit, no picture reset). The **first shot is always 10.9**
  wherever it lands, and every later shot is placed and scored **relative to it** on the normal rings using the
  projectile's size. A miss has no position: it counts 0, is listed, and is never the reference.
* The target shows every shot, the **oval** (smallest-area ellipse around all shot centres, widened by one
  projectile diameter so it is the oval a pellet-sized pen would draw) and a **cross at the group centre**
  (the mean point of impact). Only the latest shot is numbered.
* Live figures: **group size** (extreme spread, centre to centre, plus one projectile diameter = outside edge to
  outside edge), spread centre to centre, oval long x short and its direction, width x height, mean radius, and
  the group centre relative to the first shot.
* An ammo test has two names. The **shooter** is whoever was in control when the test started; it is shown
  read-only and does not change, even if someone else takes over. The **test label** (ammo, lot) can be edited at
  any time without starting over, and a new test starts with no label. Review lists show both.
* *New test* (in the gear) starts over: the next shot becomes the first shot. Finished tests are in Review with
  their group size. Changing the projectile size re-scores the live test.
* The raw coordinates (`x_mm`, `y_mm` in /shots) stay in the target's own frame; only the dashboard views and
  the stored ring/decimal scores are relative to the first shot.

Not built yet: relabelling or excluding shots after the fact, user accounts, OTA firmware hosting, CSV export,
match rules (when sighters are allowed, etc.).

## Try it (any computer)

    python3 -m fetserver serve --data-dir ./data --port 8080
    # in another terminal; the device token is in ./data/device_token
    python3 -m fetserver.simtarget --token "$(cat data/device_token)" --shots 20 --interval 1 --good
    curl http://127.0.0.1:8080/api/v1/targets
    curl http://127.0.0.1:8080/api/v1/targets/sim-01/shots

`--good` makes the simulated shooter mostly score 9s and 10s (with a fair number of inner tens); `--spread MM` sets the
scatter directly; without either it is a poor shooter (8 mm). Use `--miss-rate 0.1` to add some misses.

## Tests

    python3 -m unittest discover -s tests -t .

Tests: scoring boundaries and ISSF geometry, your real captured shot/miss/ECHO lines,
duplicate and out-of-order delivery, time reconstruction across outages and reboots,
all-or-nothing batches, outages with lost acknowledgements, 20 concurrent targets,
group statistics (an exact square, a rotated rectangle, collinear and tiny groups, oval containment), ammo-test scoring and re-scoring, the v0.4 to v0.5 database upgrade, a kill -9 test (no acknowledged event may be lost), control claims and mode assignment
(including late-delivered shots), the live stream, and the dashboard page logic.
The last needs Node.js to run (skipped without it); Node is not needed on the Pi.

## ESP-01 firmware

The bridge firmware for the target's ESP-01 is in `firmware/` (its own README: build, flash, portal setup, bench tests).
`python3 -m unittest tests.test_firmware_host` runs the real firmware logic against this server with injected power cuts
and flash faults (needs g++; skipped without it).

## Install on a Raspberry Pi

Step-by-step guide (SD card to first shot): **[docs/INSTALL.md](docs/INSTALL.md)**. In short, from an unpacked,
checksum-verified release archive on Raspberry Pi OS Bookworm:

    sudo ./install.sh                  # install, or upgrade when re-run (keeps data, token and password)
    sudo ./install.sh --help           # --reset-password, --no-password, --uninstall [--purge]

It installs the code in /opt/fet-monitor, the database in /var/lib/fet-monitor (backups nightly to
/var/backups/fet-monitor), the systemd units `fet-monitor` and `fet-monitor-backup.timer` and the system user
`fet-monitor`; it creates the device token and asks for the Range Control password. `python3 tools/make_release.py`
builds the archive (`dist/fet-monitor-<version>.tar.gz` and `dist/SHA256SUMS`). Hardware test list:
[docs/PI-TEST-CHECKLIST.md](docs/PI-TEST-CHECKLIST.md).

**Upgrading an older install** (named `freetarget-server`, data in /var/lib/freetarget): `install.sh` detects it, backs
it up and moves it with `deploy/migrate-from-freetarget.sh` (which can also be run on its own: `sudo sh
deploy/migrate-from-freetarget.sh` from the release folder). The migration stops the old service, archives the old data, copies it
to the new paths (the device token comes along, so targets keep working), installs and starts `fet-monitor`, and
checks it answers. The old folders and unit files are left in place (disabled) for rollback; the script prints how
to roll back or remove them.

Options: `--lease SECONDS` (control claim length, default 60), `--shot-lag SECONDS` (how long after
a shot is detected its line reaches the ESP-01; default 0, to be measured on the bench; it only matters
for shots fired within a few seconds of a mode change), `--offline-after`, `--heartbeat-interval`.

Other commands: `python3 -m fetserver check` (integrity check), `backup` (consistent snapshot,
keeps the newest 14), `rescore` (recompute scores from stored coordinates; raw events are never touched).
The server refuses to start if the database fails its integrity check; restore the newest
backup from /var/backups/fet-monitor in that case.

## API (JSON, base path /api/v1)

| Method and path | Who | Purpose |
|---|---|---|
| POST /events | target (X-FET-Token) | Batch of up to 50 events; reply `acked` is a list of `[boot_count, seq]` pairs (duplicates included), `rejected` lists malformed events |
| POST /heartbeat | target (X-FET-Token) | Status in; `server_time_ms`, `heartbeat_interval_ms` and pending `commands` out |
| GET /health | anyone | Liveness |
| GET /targets?client_id= | browser | Targets with online state, mode, shooter, settings, control claim (`mine` is relative to client_id) and `view`: the current stream's count, groups, aggregate, current target-picture shots and last shot |
| GET /targets/<id>/shots?since_id=&limit=&segment_id= | browser | Shots with aligned x/y in mm, ring, decimal score, mode |
| GET /targets/<id>/series | browser | Series history, newest first, with scored and sighter totals |
| GET /series/<n>?stream=scored\|sighter&upto=<k>&bull=<j> | browser | A series stream as it stood after its first k shots (shot-by-shot review; `full_count` is the real length), or its j-th target picture |
| GET /series/<n>/sight?after=<k> | browser | Sight adjustment for a practice series from its shots numbered above k: `ready` (3+ shots on paper), `kept`, `flyers`, `points`, aim point `center_*_mm`, and `move_x_mm`/`move_y_mm` (how far the impact must move, +x right, +y up). 400 for match and ammo series |
| GET /targets/<id>/segments | browser | Mode segments (older view of the same history) |
| POST /targets/<id>/settings | browser | `{client_id, bull_size, group_size, total_practice, total_match, projectile_mm}`; needs the control claim. `projectile_mm` (1-10) re-scores a live ammo test |
| POST /targets/<id>/control | browser | `{client_id, name, takeover}`: claim or renew; 409 if someone else holds it |
| POST /targets/<id>/control/release | browser | Release the claim |
| POST /targets/<id>/mode | browser | `{client_id, mode, shooter, new_series, label}`; 403 without the claim. Practice is its own series; sighter and record share a match series; `mode: "ammo"` starts an open-ended ammo test (the view then carries `ammo`: group size, oval, centre) |
| GET /admin/status | Range Control | `{password_set, logged_in}` |
| POST /admin/login, /admin/logout | Range Control | `{password}` → session cookie (HttpOnly, SameSite=Strict, Path=/api/v1/admin); 401 wrong, 429 locked (`retry_after`), 409 no password set |
| GET /admin/targets | Range Control | All targets plus `banned` |
| POST /admin/firmware | Range Control | Raw `.bin` upload (`Content-Type: application/octet-stream`, ≤ 1 MB); 400 with the reason if it is not a FeT Monitor bridge image |
| GET /admin/firmware; POST /admin/firmware/<sha256>/delete | Range Control | Stored images (version, size, flash size, SHA-256) |
| GET /firmware/<sha256> | target (X-FET-Token) | The image a target was told to install |
| POST /admin/targets/<id>/mode\|remove\|ban\|unban\|firmware | Range Control | `firmware`: `{sha256}` queues an update (refused with the reason if the target cannot take it). `mode`: `{mode, new_series, session, label}` without the control claim. Every /admin call needs the header `X-FET-Admin: 1` and, if the browser sends one, an Origin naming this server |
| GET /stream | browser | Server-sent events: small "something changed" hints that make pages refresh |
| POST /targets/<id>/commands | browser | Queue `{"type":"serial","line":"{\"NEW\":1}"}`, `wifi_portal` or `reboot` (serial lines are allowlisted: ECHO, VERSION, NEW) |

## Durability notes

* SQLite WAL with `synchronous=FULL`: a batch is committed to disk before the target is told it arrived.
  A batch commits entirely or not at all, and a target keeps and resends anything not acknowledged.
* Events are append-only and unique on (device, boot_count, seq); scores are derived and can be rebuilt.
* Unparseable events are stored with a `parse_error`, never dropped and never blocking the queue.
* If a target ever reuses an event identity with different content (for example its boot counter restarted after
  a factory reset), the event is acknowledged but kept in `event_conflicts` and counted in `/api/v1/health`,
  never discarded silently. The firmware must restart its boot counter from a random value, not from 1.
* Tested: the process being killed mid-write. Not testable here: sudden power loss. `synchronous=FULL` is
  the protection for that, but an SD card can still fail, so prefer a USB SSD for /var/lib/fet-monitor
  and keep the nightly backups on a different device.
* Viewing and shooting need no login: browser endpoints are open to the LAN; device endpoints need the token.
  The control claim stops accidental clashes, it is not security. Range Control (/api/v1/admin) needs its password.

## License

Copyright 2026 Jake Miller. Licensed under the Apache License, Version 2.0 (see `LICENSE` and `NOTICE`).
Redistributions, including modified versions, must keep the `NOTICE` file and its credit. Provided "as is",
without warranty of any kind; see the license for the limitation of liability.
