// The target's saved settings (/config.json) and the quick-power-cycle reset logic.
#pragma once
#include <cstdint>
#include <string>
#include "interfaces.h"

namespace fet {

struct Config {
  std::string ssid, pass;      // WiFi
  std::string host;            // server name or IP; empty = discover it with mDNS (_freetarget._tcp)
  int port = 8080;
  std::string token;           // the server's device token
  std::string name;            // shown on the dashboard; also the network host name
  std::string ip, gateway, mask;   // fixed address; all empty = ask the router (DHCP). mask empty with ip set = 255.255.255.0
  bool fixedIp() const { return !ip.empty(); }
  bool wifiConfigured() const { return !ssid.empty(); }
};

// Why a setting was refused, or empty if it is fine.
std::string validateConfig(const Config& c);
std::string configToJson(const Config& c);
bool configFromJson(const std::string& json, Config& out);        // false if it is not valid JSON; fields are then untouched
bool loadConfig(Fs& fs, Config& out);                             // false if missing or invalid (out is then defaults)
bool saveConfig(Fs& fs, const Config& c);                         // validates, then writes atomically
bool validName(const std::string& n);
bool parseIpv4(const std::string& s, uint8_t out[4]);              // strict dotted quad, no leading zeros / spaces

// Three power cycles in quick succession reopen the setup portal, with no extra hardware.
// Call onBoot() once at start-up (it records this boot); tick() clears the record once the unit has stayed up.
class QuickBoot {
 public:
  static const int TRIGGER = 3;
  static const uint64_t STABLE_MS = 8000;
  explicit QuickBoot(Fs& fs) : fs_(fs) {}
  bool onBoot();                    // true when this is the 3rd quick boot in a row (and the counter is reset)
  void tick(uint64_t now);
 private:
  Fs& fs_;
  bool armed_ = false;
};

}  // namespace fet
