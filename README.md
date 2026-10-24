# freETarget club server (v0.2: core + dashboards)

Receives shots and status from freETarget targets over the club LAN, stores them
durably in SQLite, scores them (ISSF 10 m air rifle, exact arithmetic) and serves a JSON API.
Standard library only: Python 3.9 or newer, nothing to install.

Open `http://<pi-address>:8080/` in any browser (phone, tablet, PC):

* **All targets (coach view)**: a card per target with online state, mode, shooter, a mini target
  face with the latest shots, the current series total (decimal and integer), and who is in control.
  Click a card to open that target.
* **Single target (shooter view)**: a full ISSF face with numbered shots, last shot, series total,
  the shot list, and control. Review: pick any past series from the list to replay it on the face.
* **Control**: enter your name and tap *Take control*. While you hold it you can switch
  Practice / Sighter / Record, set the shooter name, and start a new series. The claim lasts 60 s
  and renews itself while the page is open; if you close the page it frees itself. Someone else can
  *Take over* (after a confirmation). Mode changes need the claim; viewing does not.
* A shot takes the mode that was active when it was fired, not when it arrived, so a delayed
  upload still lands in the right series. Misses are listed with no position and count as 0.

Not built yet: relabelling or excluding shots after the fact, user accounts, OTA firmware hosting.
The protocol is described in the project spec ("freETarget Club Network: Protocol and Architecture Spec").

## Try it (any computer)

    python3 -m fetserver serve --data-dir ./data --port 8080
    # in another terminal; the device token is in ./data/device_token
    python3 -m fetserver.simtarget --token "$(cat data/device_token)" --shots 20 --interval 1
    curl http://127.0.0.1:8080/api/v1/targets
    curl http://127.0.0.1:8080/api/v1/targets/sim-01/shots

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
| GET /targets?client_id= | browser | Targets with online state, mode, shooter, series totals, recent shots, control claim (`mine` is relative to client_id) |
| GET /targets/<id>/shots?since_id=&limit=&segment_id= | browser | Shots with aligned x/y in mm, ring, decimal score, mode |
| GET /targets/<id>/segments | browser | Series history, newest first, with totals |
| POST /targets/<id>/control | browser | `{client_id, name, takeover}`: claim or renew; 409 if someone else holds it |
| POST /targets/<id>/control/release | browser | Release the claim |
| POST /targets/<id>/mode | browser | `{client_id, mode, shooter, new_segment}`; 403 without the claim |
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
