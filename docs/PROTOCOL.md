# The target ↔ server protocol (FeT Bridge and FeT Monitor)

How a target's WiFi bridge (FeT Bridge, ESP8266) reports shots and settings to a server, and how the server sends it
commands. Written from the code (`fetserver/core.py`, `fetserver/server.py`, `firmware/fet_bridge/src/core/bridge.cpp`),
so another program can be the server: anything that answers these two requests the same way works with FeT Bridge.
`python3 tools/protocol_check.py <base URL> <token>` checks a server against it.

## Transport

- HTTP/1.1 `POST` with a JSON body (`Content-Type: application/json`, `Content-Length` required, body at most 256 KB).
- Base URL: today `http://<server address>:<port>` (FeT Monitor's default port is 8080); paths below are appended.
- Authentication: every request carries the header **`X-FET-Token: <device token>`**, one shared secret per server
  (FeT Monitor compares it in constant time). A wrong or missing token: `401 {"error": "bad token"}`, nothing stored.
- Answers are JSON. Errors: `400` (malformed; `{"error": "..."}`), `401` (token), `403` (target banned on the server:
  nothing is stored and the target should keep its data), `413` (too large), `500`.

## Identity and delivery

- A target is identified by **`device_id`**: 1 to 64 characters `A-Z a-z 0-9 _ -` (FeT Bridge uses its chip ID).
- **`boot_count`**: a counter the bridge increments at every start (seeded randomly after a flash erase, so a wiped
  bridge does not repeat old numbers). **`uptime_ms`**: milliseconds since that start.
- Every event has a **`seq`** number, increasing within a boot. **`(device_id, boot_count, seq)` is the event's
  identity.** The bridge keeps every event in flash until the server acknowledges it, and resends what was not
  acknowledged: delivery is **at least once**, so the server **must** treat a repeated identity as the same event
  (store it once, acknowledge it again). FeT Monitor keeps the first copy; a repeat with *different* content is kept
  apart as a conflict and never replaces the original.

## POST /api/v1/events

Request:

```json
{"device_id": "a1b2c3", "boot_count": 104233, "uptime_ms": 912345,
 "events": [
   {"seq": 17, "type": "shot", "uptime_ms": 911870, "raw": "{\"shot\":12, \"miss\":0, \"x\":1.23, \"y\":-0.45, ...}"},
   {"seq": 18, "type": "config", "uptime_ms": 911990, "raw": "{\"NAME_ID\":1, ...}", "boot_count": 104233}
 ]}
```

- At most **50 events** per request. `uptime_ms` (top level) is the bridge's uptime when it sends the request;
  each event's `uptime_ms` is when the event happened in the same clock.
- An event may name its own `boot_count` (an event queued before a restart, sent after it); default: the request's.
- `type`: `shot` (a shot line from the Arduino), `config` (its `{"ECHO":0}` settings block), `arduino_boot` (the
  Arduino's start-up banner, e.g. `freETarget "4.3.0"`), `log`; from the FeT target firmware (firmware/target):
  `target_shot` (`FT1 S ...`), `target_hello` (`FT1 H ...`), `target_reply` (`FT1 R ...`), each the line exactly as
  the target sent it (signed by the target; see fetcore/targetproto.py). Unknown types are rejected (that event
  only).
- `raw`: the text line exactly as the Arduino printed it (at most 16384 characters), or `data`: the same as a JSON
  object. A shot needs `shot` (number) and, unless `miss` is 1, `x` and `y` in millimetres from the centre;
  optional `N E S W` (sensor counts), `time`, `V_REF`, `T`, `face`, `name`, `VERSION`.

Answer (always `200` when the request itself was valid):

```json
{"acked": [[104233, 17], [104233, 18]], "rejected": [], "server_time_ms": 1793228560195}
```

- **`acked`**: `[boot_count, seq]` of every event the server has stored, **including ones it already had**. The bridge
  deletes exactly these from its queue. An event not in `acked` is sent again later.
- `rejected`: `[{"boot_count", "seq", "reason"}]` for events that can never be stored (malformed). They are reported,
  not acknowledged.
- `server_time_ms`: the server's clock (Unix ms).

### Time

The bridge has no real clock. The server computes when an event happened as **(request arrival time − request
`uptime_ms`) + event `uptime_ms`**, keeping for each (device, boot) the earliest estimate of when that boot started
(the least affected by network delay). FeT Monitor marks each event's time quality: `ok`; `unknown` (an event from a
boot never heard from: arrival time used); `reanchored` (the device reused a boot number, e.g. after its flash was
wiped: the boot start disagrees with the current request by more than 30 s, so the current request is trusted).
A shot's time decides which session/series it belongs to (not its arrival time), so a late upload lands correctly.

## When a bridge sends (the 5-second goal)

A shot should reach the server within 5 seconds of the bridge receiving it (docs/PLAN-deeptens.md). FeT Bridge 0.4.1
and the ESP32 target firmware 0.3.1 send like this (firmware/fet_bridge/src/core/bridge.cpp):

- A new event goes at once, whatever wait an earlier failure started.
- While an event is under 5 s old, a failed send is tried again after 1 s at most. Only older backlog waits longer
  (doubling from 1 s up to 30 s).
- Events waiting to go are sent before a heartbeat. A due heartbeat is held back for at most 10 s, so commands still
  get through while a long backlog drains.
- A report connects within 1 s and is answered within 2.5 s, or it counts as failed. One failed attempt and its retry
  then fit inside the 5 s. Firmware downloads have their own, longer limits.

A server should answer an events request quickly (store, then answer) and never make a bridge wait on other work.
Range Control → Targets shows each target's delivery times over the last 24 h, worst and median, against the 5 s.

## POST /api/v1/heartbeat

Every few seconds (the server says how often), also when there is nothing to send. Request:

```json
{"device_id": "a1b2c3", "boot_count": 104233, "uptime_ms": 915000, "name": "Lane 3", "bridge_fw": "0.2.2",
 "arduino_version": "4.3.0", "ota_space": 438272, "flash_size": 1048576, "rssi": -61, "queue_depth": 0,
 "overflow_count": 0, "cmd_results": [{"id": 41, "status": "done", "reply": "..."}]}
```

Only `device_id`, `boot_count`, `uptime_ms` are required. `ota_space`/`flash_size`: room for an update and the flash
size (firmware 0.2.0+; a server uses them to pick a matching image). `cmd_results`: the outcome of commands received
earlier (`status` `done` or `failed`, `reply` at most 500 characters).

Answer:

```json
{"server_time_ms": 1793228560195, "server_time": "2026-10-28T19:02:40.195Z", "heartbeat_interval_ms": 5000,
 "commands": [{"id": 42, "type": "serial", "line": "{\"ECHO\":0}"}]}
```

Commands (at most 5 per answer; each has a server-wide unique `id` and is carried out **at most once**: the bridge
remembers the ids it has done and reports each result once in `cmd_results`; the server resends a command until a
result for its id arrives):

| type | line | What the bridge does |
|---|---|---|
| `serial` | an allowlisted Arduino command: `{"ECHO":0}`, `{"VERSION":0}`, `{"NEW":n}` | writes it to the Arduino. The bridge itself refuses anything else, whatever the server sends. |
| `wifi_portal` | — | restarts into its setup portal |
| `reboot` | — | restarts |
| `factory_reset` | `erase` (required) | erases its settings and queued events, restarts |
| `target` | one `FT1 C ...` line (printable ASCII, no braces, at most 300 characters) | writes it to the target firmware, which checks its signature, boot and id itself (FeT Bridge 0.4+) |

FeT target firmware verbs inside `FT1 C` lines (firmware/target/core/src/commands.h): `get`, `set k=v ...`, `paper`,
`status`, and `update sha=<64 hex> size=<bytes>`: an ESP32 target fetches `GET /api/v1/firmware/<sha>` (device token)
and installs it only if its SHA-256 is the signed one; the Mega answers `r=not_supported`.
| `fw_update` | `<sha256> <size>` | fetches `GET /api/v1/firmware/<sha256>` (same token header), checks size and SHA-256, installs, restarts |

The bridge never sends anything to the Arduino that contains other braces than these commands (it listens on the
same serial line).

## Other servers

A minimal server that stores events, acknowledges them by `[boot_count, seq]`, deduplicates by identity and answers
heartbeats with `{"server_time_ms": ..., "heartbeat_interval_ms": 5000, "commands": []}` is enough for FeT Bridge to
deliver every shot exactly once into its store. Things to get right (checked by `tools/protocol_check.py`):
acknowledge repeats; never acknowledge what was not stored; answer `401` to a wrong token without storing anything;
keep `rejected` events out of `acked`.

## Version 2 (written 2026-11-07)

Version 2 lets a target report to FeT Monitor on a LAN and, through it or directly, to deeptens.com, where remote
matches are run (docs/PLAN-deeptens.md). It keeps everything above: the two requests, at-least-once delivery, the
`(device_id, boot_count, seq)` identity, acknowledgements, commands on the heartbeat. Each part below says whether it
is **built** or **planned**. A version-1 bridge keeps working with a version-2 server.

### Who decides

- **Local match:** the authority is the range's FeT Monitor. It runs the clock, decides which shots count, scores them
  (fetcore), decides flagged shots and publishes the result.
- **Remote match:** the authority is deeptens, for all of the same. A FeT Monitor only carries, shows, stores and
  replays; its own score on the lane display is provisional until deeptens confirms it.
- Remote matches accept only lines signed by FeT target firmware (`target_shot`). A shot from the original freETarget
  firmware (`type: shot`, unsigned JSON) is never counted in a remote match.

### Shot lines: the counter rate (built: ESP32 firmware 0.3.2, FeT Monitor 0.7.15)

```
FT1 S b=<boot> q=<seq> t=<ms> n=<N> e=<E> s=<S> w=<W> m=<mask> f=<face us|-> c=<temp|-> k=<settings id>
      d=<dropped> r=<counts per second> h=<16 hex>
```

- `r=` (optional, signed with the rest): the rate of `n e s w`. The ESP32 board (0.3.2+) sends its capture timers'
  full resolution, `r=80000000` (12.5 ns). Without `r=`, the board revision decides as before: 10 MHz for revision
  500 and up (ESP32 boards), 8 MHz for the Mega.
- The rate travels with **each shot**, not in the hello: a board's queue may still hold shots taken by older firmware.
- Counts: one per microphone, larger for a microphone that heard the shot earlier; only differences matter (the
  solver takes arrival times up to a constant). `0` = no time for that microphone.

### The Monitor carries signed lines (planned, Phase 1)

A linked FeT Monitor forwards each target line it stores (`target_shot`, `target_hello`, `target_reply`) to deeptens
at once, **as the target sent it**, with its identity and the Monitor's boot anchor for that boot (the earliest
`arrival − uptime` it has seen). It never re-signs or alters a line; deeptens checks the target's own signature.
Forwarding is not batched except to clear a backlog (5-second goal). The link is one outbound WebSocket from the
Monitor (no inbound connections); deeptens acknowledges by identity as above, and the Monitor resends what was not
acknowledged.

### Signatures (planned, Phase 1 for ranges, Phase 3 for home targets)

| Target | Signs shots with | Who can check | Who holds the secret |
|---|---|---|---|
| ESP32 board | ECDSA P-256 key pair made on the board (deterministic, RFC 6979) **and** the shared HMAC key (commands) | anyone with the public key | only the board |
| Mega (+ ESP-01) | the shared HMAC key | FeT Monitor; deeptens | the board, FeT Monitor, deeptens (encrypted, key kept outside the database) |

- P-256, not Ed25519: Arduino-ESP32 3.3.12's Mbed TLS has ECDSA P-256 (`CONFIG_MBEDTLS_ECDSA_C`, `SECP256R1`,
  deterministic) and no EdDSA; it is also the curve of the DeepTens device CA. The Mega cannot sign with a key pair at
  all (Ed25519 takes about 5 s on a 16 MHz AVR).
- Lines: the hello adds `p=<public key, 66 hex: compressed point>`; a shot adds `g=<signature, 128 hex: r || s>`
  after `h=` and over the same bytes as `h=` covers. A version-1 parser stops at `h=`; version 2 checks both.
- Results from shared-key (Mega) targets are accepted but marked "may require submission of the witness paper to
  certify the result". deeptens may stop accepting them from a date it announces (a forced upgrade).
- The HMAC key keeps its second job, authorising commands (including firmware updates). With a key pair, a pairing
  step decides who may give orders; the public key alone never authorises anything.

### Credentials (planned, Phase 1)

- Each device gets its own token instead of the shared `X-FET-Token` (header unchanged). The server maps the token
  to one `device_id` and refuses a request whose `device_id` differs (`403`).
- On a LAN, FeT Monitor keeps accepting the shared token during a changeover, as a Range Control setting.
- Set over USB (`FETCFG`, the flash station) or in the setup portal (a claim code for deeptens).

### Server address (planned, Phase 3)

A full URL replaces host and port: scheme, host name, port, base path. HTTPS on 4 MB modules and the ESP32 only,
trusting the DeepTens device CA (ECDSA P-256) and ISRG Root X1 as a backup; the 1 MB ESP-01 stays plain HTTP to a FeT
Monitor. Connections stay open between requests (HTTP keep-alive), so the TLS handshake is not paid per shot.

### Settings fixed during a remote match (planned, Phase 4)

- The entry records the target's settings id (`k=`), geometry, calibration and face. A shot whose `k=` differs from
  the entry's is not counted automatically; the Match Director sees it.
- The authority sends a signed `lock` command at the start; the target refuses `set` (from the server, the portal or
  USB) until a signed `unlock` or its own next start after the match's end.

### Which shots count (FeT Monitor's match engine, planned in fetcore; the same rules on deeptens)

- Fired during firing time: counted. Fired after STOP: as the rulebook says (flagged for the Match Director today).
- **"You may discharge air downrange"**: a range officer command (local and remote matches). A shot fired after it
  never counts and is not flagged: discharging air, or a pellet left in the bore, is expected.
- A relay **closes** per its rulebook, otherwise when provisional scores are posted. A shot fired in time that
  arrives after the close is kept and shown to the Match Director, who can reopen the result.
- "Fired" means the event time (above): it needs a boot anchored by the authority. A shot of a boot never anchored is
  never counted automatically.

### Playback (planned, Phase 3)

A home target keeps an archive of its acknowledged signed lines for 90 days, oldest dropped first: the archive never
blocks a new shot. A signed `replay since=<event time>` command makes it send them again as ordinary events with
`"replay": true`; the server deduplicates by identity, so a replay never adds or changes a shot. A FeT Monitor keeps
every line it stored, and replays from there.

### Versions

The heartbeat carries `"protocol": 2` from a version-2 device; a version-2 server answers with `"protocol": 2`.
Neither side relies on the other's version: every field above is optional to a version-1 peer.

## The Monitor link: FeT Monitor ⇄ deepTENS (P1, written and built 2026-11-08)

A FeT Monitor (the Pi) linked to a range on deepTENS sends it that range's matches and its own status. Decisions in
docs/PLAN-deeptens.md (P1 Q1-Q4). The Pi is the authority for its local matches; deepTENS keeps copies, and shows a
match publicly only when its Match Director published it.

**Transport.** HTTPS to the deepTENS address (Range Control setting; `https://deeptens.com:8443` until deepTENS goes
public), JSON bodies, Python's standard library on the Pi (certificates checked against the system's). Every call
but the two link calls carries `Authorization: Bearer <token>`. Connect timeout 5 s, whole request 20 s.

### Linking (device-code flow, RFC 8628 style)
1. `POST /api/monitor/v1/link` `{"name": "Club Pi", "version": "0.7.18"}` →
   `{"device_code", "user_code": "KQ7-M4P", "verification_uri", "verification_uri_complete", "expires_in": 900, "interval": 5}`.
   The user code uses 2-9 and A-Z without I, L, O, U (no look-alikes); it is valid 15 minutes.
2. Range Control shows the code and a QR code for `verification_uri_complete`. A person signs in on deepTENS with a
   strong sign-in, enters the code, and picks one of their ranges where they are Range Manager, or creates a range
   (they become its Range Manager).
3. The Pi polls `POST /api/monitor/v1/link/poll` `{"device_code"}` no faster than `interval`:
   `{"status": "pending"}`, `{"status": "slow_down"}`, `{"status": "denied"}`, `{"status": "expired"}`, or once:
   `{"status": "approved", "token", "monitor_id", "range": {"id", "name"}}`. The token is shown only this once;
   deepTENS keeps its SHA-256. The Pi keeps it in `<data dir>/deeptens.json` (mode 600), never in its database (so
   never in a database backup).

### Unlinking
From the Pi: `POST /api/monitor/v1/unlink`, then the Pi forgets the token (it forgets it even when deepTENS cannot be
reached; the Range Manager can unlink it there too). From deepTENS: the Range Manager unlinks it, and every later call
answers `401`; the Pi then forgets the token and shows "unlinked on deepTENS". Linking again is a new link.

### Heartbeat (every 30 s)
`POST /api/monitor/v1/heartbeat`
`{"version", "name", "lanes": [{"lane": 3, "name": "North", "online": true}], "outbox": 12, "now_ms"}` →
`{"ok": true, "server_time_ms", "range": {"id", "name"}, "lane_conflicts": [3]}`. `lanes` lists the Pi's targets
that have a lane (spares are never sent). `lane_conflicts`: lane numbers another Monitor of the same range also
reports (each lane is used once per range); Range Control shows them. The deepTENS icon on the Pi is green while the
last heartbeat or upload succeeded less than 90 s ago, red otherwise; no icon when the Pi is not linked.

### Uploads (the outbox)
Everything for deepTENS waits in the Pi's outbox (table `deeptens_outbox`) until deepTENS acknowledges it; nothing
for deepTENS ever blocks shooting. Each item is a whole snapshot with a key and a revision:
`POST /api/monitor/v1/items` `{"items": [{"id": 41, "kind": "match", "key": "m12", "rev": 5, "data": {...}}]}` →
`{"acks": [41], "rejected": [{"id": 42, "why": "..."}]}`. The body may be gzip-compressed
(`Content-Encoding: gzip`); at most 20 items and 8 MB unpacked per request.
- **kinds**: `match` (a single match: key `m<id>`) and `competition` (key `c<id>`, its relays and finals).
- **rev** grows by one each time the Pi queues a new snapshot of that key. deepTENS keeps the highest revision per
  (monitor, key) and acknowledges an older or equal one without changing anything, so a resend after an outage, a
  restart or a lost answer is harmless. While an item is unsent, a newer snapshot of the same key replaces it.
- **data**: FeT Monitor's results view of the match or competition (the same JSON as its results pages, with each
  shooter's shots), plus `"state"`, `"official"` (true once the results are official) and `"publish"` (the Match
  Director's choice, below). Names are as typed in Match Control.
- **When**: a snapshot is queued when a match or competition changes state (started, finished, posted, official,
  reopened), when a correction or decision changes its results, and when its Match Director publishes or unpublishes.
  Matches that never started are not sent.
- A rejected item (malformed) is kept, marked, and shown in Range Control; it is not retried.

### Publishing
`"publish": true` is the Match Director's choice on the Pi ("Would you like to publish these results on
deeptens.com?" when a match ends; "Publish to deepTENS" once official; "Unpublish"). deepTENS shows a match publicly
only when publish is true and its results are official; in a competition, each finished relay shows as provisional and
the whole becomes official only when the competition's results are. Names typed in Match Control are shown publicly
as initials; full names only for competitors linked to a deepTENS athlete profile, by that athlete's own choice.

### Retrying
On a network error, a 5xx or 429, the Pi waits 2 s, then doubles up to 60 s; a success resets it. Uploads go oldest
first. The heartbeat is sent even while uploads wait, so the icon tells the truth.
