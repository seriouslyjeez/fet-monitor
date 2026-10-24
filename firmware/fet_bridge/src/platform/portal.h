// The target's own little web server (port 80).
//   normal:  GET /          a read-only status page      GET /status   the same as JSON
//   portal:  GET /          the WiFi + server setup form  POST /save, POST /factory-reset, GET /scan, /update
// The portal is an open access point "freETarget-xxxxxx" with a catch-all DNS so phones pop up the form.
// It opens after 3 quick power cycles, or when the server sends a wifi_portal command, and closes after
// FET_PORTAL_MINUTES without use (unless the unit has no WiFi settings at all).
#pragma once
#include <Arduino.h>
#include <string>
#include "../core/bridge.h"
#include "../core/config.h"
#include "platform.h"

namespace fetp {

class Portal {
 public:
  Portal(fet::Fs& fs, fet::Config& cfg, PlatformHooks& hooks, WifiNet& net, const std::string& deviceId);
  void begin(fet::Bridge* bridge);          // start the web server
  void open(uint64_t now);
  void close();
  bool isOpen() const { return open_; }
  void loop(uint64_t now);
 private:
  void routes();
  void sendPage(int code, const std::string& html);
  void handleRoot();
  void handleStatus();
  void handleScan();
  void handleSave();
  void handleFactoryReset();
  void handleNotFound();
  std::string form(const std::string& error);
  std::string statusPage();
  std::string statusJson();
  bool portalOnly(bool needToken);
  fet::Fs& fs_; fet::Config& cfg_; PlatformHooks& hooks_; WifiNet& net_;
  std::string id_;
  fet::Bridge* bridge_ = nullptr;
  bool open_ = false, started_ = false, updaterReady_ = false;
  uint64_t deadline_ = 0, now_ = 0;
};

}  // namespace fetp
