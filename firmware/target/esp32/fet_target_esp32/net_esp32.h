// The ESP32 side of the FeT Bridge core (firmware/fet_bridge/src/core, linked as the fet-bridge-core library): the
// filesystem, WiFi + HTTP to FeT Monitor, the hooks, and the two line queues between the target (main loop) and the
// bridge (its own task). Follows firmware/fet_bridge/src/platform/platform.* (the ESP8266 version); keep them in step.
#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <freertos/semphr.h>
#include <string>
#include <vector>
#include <config.h>
#include <interfaces.h>
#include <safetynet.h>

namespace fete {

#define FET_HTTP_TIMEOUT_MS 6000
#define FET_CONNECT_TIMEOUT_MS 3000
#define FET_MAX_RESPONSE 6000
#define FET_PORTAL_MINUTES 15
#define FET_LOG_SEGMENT_BYTES 7000
#ifndef FET_WIFI_STA_11B
#define FET_WIFI_STA_11B 0                  // 1: the station joins in 802.11b only (test build 0.2.4); 0: 802.11b/g
#endif
#define FET_SILENT_REJOIN_MS 60000UL        // FeT Monitor silent this long: drop the WiFi and join again ...
#define FET_REJOIN_MAX_MS 600000UL          // ... waiting twice as long each time, up to 10 min
#define FET_NO_WIFI_RESTART_MS 1200000UL    // no WiFi at all this long: restart the board (the main loop decides)
#define FET_NET_STUCK_MS 180000UL           // the network task made no progress this long: restart the board
#define FET_RESTART_WHY "/restart_why"      // why the board restarted itself (read and removed at the next start)

uint64_t nowMs();
std::string deviceIdFromChip();                    // fet-xxxxxx (the last 3 bytes of the WiFi MAC)
void say(const char* label, const std::string& value);   // one status line on USB

// Bytes from one task to another, locked; a full queue drops the bytes (counted), never blocks the writer.
class Pipe {
 public:
  explicit Pipe(size_t cap) : cap_(cap) { lock_ = xSemaphoreCreateMutex(); }
  bool put(const char* p, size_t n);
  std::string take();
  uint32_t dropped() const { return dropped_; }
 private:
  SemaphoreHandle_t lock_;
  std::string buf_;
  size_t cap_;
  uint32_t dropped_ = 0;
};

class LfsFs : public fet::Fs {
 public:
  bool begin();
  size_t totalBytes();
  bool read(const std::string& path, std::string& out) override;
  bool readRange(const std::string& path, size_t off, size_t len, std::string& out) override;
  bool writeAtomic(const std::string& path, const std::string& data) override;
  bool append(const std::string& path, const std::string& data) override;
  bool remove(const std::string& path) override;
  bool size(const std::string& path, size_t& out) override;
  std::vector<std::string> list(const std::string& prefix) override;
  size_t freeBytes() override;
};

class WifiNet : public fet::Net {
 public:
  explicit WifiNet(const fet::Config& cfg) : cfg_(cfg) {}
  bool ready() override;
  int rssi() override;
  bool post(const char* path, const std::string& body, int& status, std::string& resp) override;
  // GET /api/v1/firmware/<sha> from FeT Monitor into the next OTA slot; installed (boots next start) only if every byte
  // arrived and the SHA-256 is the one the signed "update" command named. False (reason in err): nothing changes.
  bool fetchImage(const uint8_t sha[32], uint32_t size, std::string& err);
  void startWifi(const std::string& hostname, bool withAccessPoint);
  void announce();
  void update(uint64_t now);
  bool wifiUp() const;
  std::string serverText() const;
  std::string linkJson() override;
  // FeT Monitor's last answer (Bridge::lastOkMs): long silent while the WiFi says connected (the core keeps
  // WL_CONNECTED after some disconnects, e.g. reason 2), drop the WiFi and join again. Not while the portal is open.
  void watch(uint64_t now, uint64_t lastOkMs, bool portalOpen);
  uint32_t noWifiSinceS() const { return noWifiSinceS_; }   // seconds since start when the WiFi went (0 = up); any task
  void setRestartWhy(const std::string& why) { restartWhy_ = why; }
  void noteLoop(uint32_t ms) { if (ms > loopMaxMs_) loopMaxMs_ = ms; }   // one turn of the network task took this long
 private:
  bool resolveServer(uint64_t now);
  const fet::Config& cfg_;
  IPAddress ip_;
  bool haveIp_ = false, wasUp_ = false, reportedFirst_ = false;
  uint64_t nextResolve_ = 0, ipAt_ = 0, lastBegin_ = 0, firstBegin_ = 0, nextWifiNote_ = 0;
  int connectFails_ = 0, port_ = 8080;
  std::string hostname_;
  // The link's history since this start (linkJson): drops are connected -> not connected; downMs_/longestMs_ the time
  // without WiFi after a drop until connected again; bssid_ the access point (a mesh moves units between its nodes).
  int reset_ = 0;
  uint32_t drops_ = 0, bssidChanges_ = 0;
  uint64_t downMs_ = 0, longestMs_ = 0, droppedAt_ = 0, lastDropUp_ = 0;
  uint16_t dropReason_ = 0;
  uint8_t bssid_[6] = {0, 0, 0, 0, 0, 0};
  bool haveBssid_ = false;
  uint32_t rejoins_ = 0;
  uint32_t posts_ = 0, failConnect_ = 0, failReply_ = 0, postMaxMs_ = 0;   // every request to FeT Monitor since the start
  fet::SilenceWatch silence_{FET_SILENT_REJOIN_MS, FET_REJOIN_MAX_MS};
  volatile uint32_t noWifiSinceS_ = 0;
  std::string restartWhy_;
  uint32_t loopMaxMs_ = 0;                        // the network task's longest turn since the last report                        // "net_stuck" / "no_wifi" when the board restarted itself, else ""
};

// Commands from the server to the target: queued for the main loop, which answers through toBridge.
struct PipeUart : fet::Uart {
  explicit PipeUart(Pipe& p) : pipe(p) {}
  Pipe& pipe;
  void write(const std::string& s) override { pipe.put(s.data(), s.size()); }
};

// Replies of the USB provisioning (FETCFG) go back to USB.
struct UsbUart : fet::Uart {
  void write(const std::string& s) override { Serial.write((const uint8_t*)s.data(), s.size()); }
};

struct Hooks : fet::Hooks {
  volatile bool portalWanted = false;
  volatile uint64_t rebootAt = 0, eraseAt = 0;
  void reboot() override { rebootAt = nowMs() + 400; }
  void factoryReset() override { eraseAt = nowMs() + 400; }
  void openPortal() override { portalWanted = true; }
  uint32_t random32() override { return esp_random(); }
};

}  // namespace fete
