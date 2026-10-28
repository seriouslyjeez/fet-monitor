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
  Arduino's start-up banner, e.g. `freETarget "4.3.0"`), `log`. Unknown types are rejected (that event only).
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
| `fw_update` | `<sha256> <size>` | fetches `GET /api/v1/firmware/<sha256>` (same token header), checks size and SHA-256, installs, restarts |

The bridge never sends anything to the Arduino that contains other braces than these commands (it listens on the
same serial line).

## Other servers

A minimal server that stores events, acknowledges them by `[boot_count, seq]`, deduplicates by identity and answers
heartbeats with `{"server_time_ms": ..., "heartbeat_interval_ms": 5000, "commands": []}` is enough for FeT Bridge to
deliver every shot exactly once into its store. Things to get right (checked by `tools/protocol_check.py`):
acknowledge repeats; never acknowledge what was not stored; answer `401` to a wrong token without storing anything;
keep `rejected` events out of `acked`.

Planned (not in the firmware yet): a full URL as the server setting (host names, a base path, HTTPS on 4 MB modules);
see the project plan.
