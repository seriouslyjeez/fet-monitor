// Build-time switches. Defaults suit the ESP-01S (4 MB). If the sketch is too big for the 1 MB ESP-01 over-the-air
// layout (the compiler prints "Sketch uses N bytes"), turn off MDNS and/or WEB_UPDATE below, or pick the
// "1MB (FS:64KB OTA:~470KB)" flash layout.
#pragma once

#define FET_FW_VERSION "0.1.9"

#ifndef FET_ENABLE_OTA
#define FET_ENABLE_OTA 1          // Arduino OTA push from the IDE / espota (needs a token; the token is the password)
#endif
#ifndef FET_ENABLE_WEB_UPDATE
#define FET_ENABLE_WEB_UPDATE 1   // /update page while the setup portal is open (browser upload of a .bin)
#endif
#ifndef FET_ENABLE_MDNS
#define FET_ENABLE_MDNS 1         // find the server via _freetarget._tcp when no server address is configured
#endif

#define FET_SERIAL_BAUD 115200
#define FET_RX_BUFFER 2048
#define FET_HTTP_TIMEOUT_MS 6000
#define FET_CONNECT_TIMEOUT_MS 3000
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
// WiFi/DHCP experiments (see README "Slow IP address"). Change ONE at a time and compare "Connected ... after N s".
#ifndef FET_WIFI_NO_SLEEP
#define FET_WIFI_NO_SLEEP 1      // 1 = modem sleep off: the radio stays awake so a DHCP reply is not missed
#endif
#ifndef FET_WIFI_PHY_G
#define FET_WIFI_PHY_G 0         // 1 = force 802.11g (avoids 802.11n/newer negotiation on the 2.4 GHz radio)
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
