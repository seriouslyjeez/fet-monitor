// Build-time switches. Defaults suit the ESP-01S (4 MB). If the sketch is too big for the 1 MB ESP-01 over-the-air
// layout (the compiler prints "Sketch uses N bytes"), turn off MDNS and/or WEB_UPDATE below, or pick the
// "1MB (FS:64KB OTA:~470KB)" flash layout.
#pragma once

// P3 step 1 (docs/PLAN-deeptens.md): an HTTPS measurement build for the 4 MB ESP-01S. Off in every normal build.
// Every report goes over one kept-open TLS connection to <host>:<port>, the server's certificate checked against the
// test CA in tls_test_ca.h (made by tools/tls_test.py, not in git) and the host's name; each request carries
// X-FET-TLS with the measurements (handshake time, free heap, largest block, failures).
#ifndef FET_TLS_TEST
#define FET_TLS_TEST 0
#endif
// HTTPS (P3): built into the 4 MB images only (tools/build_firmware.py; the 1 MB ESP-01 has no room: +115 KB). Used when
// the "tls" setting is on: one kept-open connection to <host>:<port>, the server's certificate checked against the
// deepTENS device CA and ISRG Root X1 and against the host's name, the date from NTP, the small 512-byte buffers when
// the server agrees (MFLN; without them a handshake leaves ~3.6 KB free), no connection while the setup portal is open.
#ifndef FET_HTTPS
#define FET_HTTPS 0
#endif
#if FET_TLS_TEST
#undef FET_HTTPS
#define FET_HTTPS 1
#if F_CPU == 160000000L
#define FET_FW_VERSION "0.5.0-tls160"
#else
#define FET_FW_VERSION "0.5.0-tls80"
#endif
#else
#define FET_FW_VERSION "0.5.0"
#endif
#define FET_TLS_HANDSHAKE_MS 10000   // a handshake (and the first answer on a new connection) may take this long
// Sent as the User-Agent; the server also finds it inside an uploaded image to recognise our firmware and its version.
#define FET_FW_ID "fet-monitor-bridge/" FET_FW_VERSION

#ifndef FET_ENABLE_OTA
#define FET_ENABLE_OTA 1          // Arduino OTA push from the IDE / espota (needs a token; the token is the password)
#endif
#ifndef FET_ENABLE_WEB_UPDATE
#define FET_ENABLE_WEB_UPDATE 1   // /update page while the setup portal is open (browser upload of a .bin)
#endif
#ifndef FET_ENABLE_MDNS
#define FET_ENABLE_MDNS 1         // find the server via _fetmonitor._tcp when no server address is configured
#endif

#define FET_SERIAL_BAUD 115200
#define FET_RX_BUFFER 2048
#define FET_HTTP_TIMEOUT_MS 2500         // a report: sent and answered within this (5-second goal: one retry fits)
#define FET_FW_IDLE_MS 15000        // firmware download: give up after this long without a byte
#define FET_FW_TOTAL_MS 180000      // ... or after this long in all
#define FET_CONNECT_TIMEOUT_MS 1000      // a report's connection (LAN: milliseconds; a lost SYN is faster tried anew)
#define FET_FW_CONNECT_MS 3000           // a firmware download's connection
#define FET_MAX_RESPONSE 6000
#define FET_PORTAL_MINUTES 15
#define FET_LOG_SEGMENT_BYTES 7000   // one LittleFS block is 8 KB; keep a segment inside one block

// Two plain status lines on the serial port (the port the Arduino is on; braces are never printed):
//   FET bridge firmware <version> (<id>)   4 s after start-up
//   Attempting to connect to network: <ssid>
//   Not connected to <ssid>: <status> [reason N: text]   every 10 s until connected
//   Setup portal open. Join WiFi: <name>
//   Connected to network: <ssid> (<ip>) each time WiFi connects
//   First report to FET: success|fail   the first HTTP exchange with the server after power-up
// WiFi/DHCP settings from the "Slow IP address" experiments (see README). The 0.1.14 values are the fix; leave them.
#ifndef FET_WIFI_NO_SLEEP
#define FET_WIFI_NO_SLEEP 1      // 1 = modem sleep off: the radio stays awake so a DHCP reply is not missed
#endif
#ifndef FET_WIFI_PHY_G
#define FET_WIFI_PHY_G 1         // 1 = force 802.11g: esp8266/Arduino#8299, ASUS routers answer DHCP only to 11g (core > 2.5.2)
#endif

#ifndef FET_DHCP_DIAG
#define FET_DHCP_DIAG 0          // 1 = show the DHCP client's state/tries in the status line (needs lwip headers)
#endif

#ifndef FET_REASSOC_MS
#define FET_REASSOC_MS 15000     // >0: associated but no IP after this many ms: drop and re-join the WiFi (0 = off)
#endif
#ifndef FET_REASSOC_LONG_MS
#define FET_REASSOC_LONG_MS 45000 // every 4th attempt waits this long, for networks whose DHCP is genuinely slow
#endif

#ifndef FET_STATUS_MESSAGES
#define FET_STATUS_MESSAGES 1
#endif

// Set to 1 to print numbered start-up markers (and the stage of any crash) on the serial port at 115200.
// Leave at 0 for normal use: the serial port belongs to the Arduino.
#ifndef FET_DEBUG
#define FET_DEBUG 0
#endif
#if FET_DEBUG
#define FET_STAGE(n) do { Serial.print("[fet] stage "); Serial.print(n); Serial.print(" heap "); Serial.println((unsigned)ESP.getFreeHeap()); Serial.flush(); } while (0)
#else
#define FET_STAGE(n) do {} while (0)
#endif
