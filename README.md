# freETarget club server (v0.6.2: series, zoom, scoring screens, ammo test; ESP-01 firmware included)

Receives shots and status from freETarget targets over the club LAN, stores them
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

    sudo useradd --system --home /var/lib/freetarget --shell /usr/sbin/nologin freetarget
    sudo mkdir -p /opt/freetarget-server /var/lib/freetarget /var/backups/freetarget
    sudo cp -r fetserver /opt/freetarget-server/
    sudo chown -R freetarget:freetarget /var/lib/freetarget /var/backups/freetarget
    sudo cp deploy/freetarget-server.service deploy/freetarget-backup.service deploy/freetarget-backup.timer /etc/systemd/system/
    sudo cp deploy/freetarget.avahi.service /etc/avahi/services/freetarget.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now freetarget-server freetarget-backup.timer
    sudo cat /var/lib/freetarget/device_token      # enter this in each target's setup portal
    journalctl -u freetarget-server -f

Options: `--lease SECONDS` (control claim length, default 60), `--shot-lag SECONDS` (how long after
a shot is detected its line reaches the ESP-01; default 0, to be measured on the bench; it only matters
for shots fired within a few seconds of a mode change), `--offline-after`, `--heartbeat-interval`.

Other commands: `python3 -m fetserver check` (integrity check), `backup` (consistent snapshot,
keeps the newest 14), `rescore` (recompute scores from stored coordinates; raw events are never touched).
The server refuses to start if the database fails its integrity check; restore the newest
backup from /var/backups/freetarget in that case.

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
| GET /targets/<id>/segments | browser | Mode segments (older view of the same history) |
| POST /targets/<id>/settings | browser | `{client_id, bull_size, group_size, total_practice, total_match, projectile_mm}`; needs the control claim. `projectile_mm` (1-10) re-scores a live ammo test |
| POST /targets/<id>/control | browser | `{client_id, name, takeover}`: claim or renew; 409 if someone else holds it |
| POST /targets/<id>/control/release | browser | Release the claim |
| POST /targets/<id>/mode | browser | `{client_id, mode, shooter, new_series, label}`; 403 without the claim. Practice is its own series; sighter and record share a match series; `mode: "ammo"` starts an open-ended ammo test (the view then carries `ammo`: group size, oval, centre) |
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
  the protection for that, but an SD card can still fail, so prefer a USB SSD for /var/lib/freetarget
  and keep the nightly backups on a different device.
* There are no logins yet. Browser endpoints are open to the LAN; device endpoints need the token.
  The control claim stops accidental clashes, it is not security.
