// ESP8266 implementations of the core's interfaces: LittleFS, WiFi + a hand-written HTTP client, the serial port.
#pragma once
#include <Arduino.h>
#include <ESP8266WiFi.h>   // IPAddress, WiFi
#include <string>
#include <vector>
#include "../core/config.h"
#include "../core/interfaces.h"

namespace fetp {

void say(const char* label, const std::string& value);   // one plain status line on the serial port (no-op if FET_STATUS_MESSAGES is 0)
uint64_t nowMs();                         // millis() extended to 64 bits (call at least every few weeks)

class LfsFs : public fet::Fs {
 public:
  bool begin();                           // mounts; formats once if it will not mount
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

// Bytes that arrive from the Arduino while we wait on the network are parked here, then fed to the bridge.
struct RxHold {
  std::string bytes;
  static const size_t MAX = 4096;
  void pump();                            // move whatever the UART has into 'bytes' (leaves the rest in the FIFO if full)
};

class WifiNet : public fet::Net {
 public:
  WifiNet(const fet::Config& cfg, RxHold& hold) : cfg_(cfg), hold_(hold) {}
  bool ready() override;                  // WiFi up AND the server address known
  int rssi() override;
  bool post(const char* path, const std::string& body, int& status, std::string& resp) override;
  bool updateFirmware(const std::string& sha256hex, uint32_t size, std::string& err) override;
  uint32_t otaSpace() override;
  uint32_t flashSize() override;
  void startWifi(const std::string& hostname, bool withAccessPoint);
  void announce();                        // the start-up lines, printed a few seconds after boot so a monitor that reattaches slowly sees them   // non-blocking; auto-reconnects; sets the radio mode once
  void stopSta();
  void update(uint64_t now);              // mDNS upkeep, connection bookkeeping
  bool wifiUp() const;
  std::string serverText() const;         // "10.0.0.5:8080" or why not
 private:
  bool postOnce(const char* path, const std::string& body, int& status, std::string& resp);
  bool resolveServer(uint64_t now);
  const fet::Config& cfg_;
  RxHold& hold_;
  IPAddress ip_;
  bool haveIp_ = false;
  uint64_t nextResolve_ = 0, ipAt_ = 0;
  int connectFails_ = 0;
  int port_ = 8080;
  bool mdnsUp_ = false, wasUp_ = false, reportedFirst_ = false;
  uint64_t assocAt_ = 0, lastReassoc_ = 0, nextWifiNote_ = 0, lastBegin_ = 0, firstBegin_ = 0;
  bool timedFirst_ = false;
  unsigned reassocCount_ = 0;
  bool assoc_ = false;                    // associated with an access point (before DHCP gives an address)
  int assocChannel_ = 0;
  std::vector<std::string> notes_;        // one-time findings from before the first connection attempt
  int lastReason_ = 0;                    // why the station last lost or failed to get a connection (SDK code)
  bool handlerSet_ = false;
  std::string hostname_;
};

class SerialUart : public fet::Uart {
 public:
  void write(const std::string& s) override;
};

struct PlatformHooks : fet::Hooks {
  bool portalWanted = false;
  uint64_t rebootAt = 0;                  // 0 = none
  void reboot() override;
  void openPortal() override { portalWanted = true; }
  uint32_t random32() override;
};

std::string deviceIdFromChip();           // fet-xxxxxx

}  // namespace fetp
