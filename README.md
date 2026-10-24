# freETarget club server (v0.4: series, zoom, scoring screens)

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
* *Mode*: Practice or Match. In Match the **Sighter** and **Record** buttons appear on the main screen
  (usable only by whoever is in control). Everyone else sees the current mode.
* *Shooter* name, and a *New series* button. A new shooter starts a new series.
* *Advanced*: shots per target picture, shots per aggregate group, total shots for practice and for match
  (defaults 10 / 10 / 20 / 60). Saved per target and applied to the live series and new ones.
* *Review*: any past series; page through its target pictures; in a match, switch between record and sighters.

A shot takes the mode that was active when it was fired, not when it arrived, so a delayed upload still lands in
the right series. Misses are listed as M, count as 0 and take a shot number.

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
a kill -9 test (no acknowledged event may be lost), control claims and mode assignment
(including late-delivered shots), the live stream, and the dashboard page logic.
The last needs Node.js to run (skipped without it); Node is not needed on the Pi.

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
| GET /series/<n>?stream=scored\|sighter&bull=<k> | browser | Full view of a series stream, with the k-th target picture (for review) |
| GET /targets/<id>/segments | browser | Mode segments (older view of the same history) |
| POST /targets/<id>/settings | browser | `{client_id, bull_size, group_size, total_practice, total_match}`; needs the control claim |
| POST /targets/<id>/control | browser | `{client_id, name, takeover}`: claim or renew; 409 if someone else holds it |
| POST /targets/<id>/control/release | browser | Release the claim |
| POST /targets/<id>/mode | browser | `{client_id, mode, shooter, new_series}`; 403 without the claim. Practice is its own series; sighter and record share a match series |
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
