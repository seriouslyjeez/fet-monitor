# freETarget club server (v0.1 core)

Receives shots and status from freETarget targets over the club LAN, stores them
durably in SQLite, scores them (ISSF 10 m air rifle, exact arithmetic) and serves a JSON API.
Standard library only: Python 3.9 or newer, nothing to install.

This is the server core. Not built yet: sessions and practice/sighter/record modes, the
control lease, the web dashboard, and OTA firmware hosting. The protocol it speaks is
described in the project spec ("freETarget Club Network: Protocol and Architecture Spec").

## Try it (any computer)

    python3 -m fetserver serve --data-dir ./data --port 8080
    # in another terminal; the device token is in ./data/device_token
    python3 -m fetserver.simtarget --token "$(cat data/device_token)" --shots 20 --interval 1
    curl http://127.0.0.1:8080/api/v1/targets
    curl http://127.0.0.1:8080/api/v1/targets/sim-01/shots

## Tests

    python3 -m unittest discover -s tests -t .

55 tests: scoring boundaries and ISSF geometry, your real captured shot/miss/ECHO lines,
duplicate and out-of-order delivery, time reconstruction across outages and reboots,
all-or-nothing batches, outages with lost acknowledgements, 20 concurrent targets,
and a kill -9 test (no acknowledged event may be lost).

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
| GET /targets | browser | Targets with online state, versions, queue depth, shot count |
| GET /targets/<id>/shots?since_id=&limit= | browser | Shots with aligned x/y in mm, ring, decimal score |
| POST /targets/<id>/commands | browser | Queue `{"type":"serial","line":"{\"NEW\":1}"}`, `wifi_portal` or `reboot` (serial lines are allowlisted: ECHO, VERSION, NEW) |

## Durability notes

* SQLite WAL with `synchronous=FULL`: a batch is committed to disk before the target is told it arrived.
  A batch commits entirely or not at all, and a target keeps and resends anything not acknowledged.
* Events are append-only and unique on (device, boot_count, seq); scores are derived and can be rebuilt.
* Unparseable events are stored with a `parse_error`, never dropped and never blocking the queue.
* Tested: the process being killed mid-write. Not testable here: sudden power loss. `synchronous=FULL` is
  the protection for that, but an SD card can still fail, so prefer a USB SSD for /var/lib/freetarget
  and keep the nightly backups on a different device.
* There are no logins yet. Browser endpoints are open to the LAN; device endpoints need the token.
