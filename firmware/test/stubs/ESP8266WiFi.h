#pragma once
#include "Arduino.h"
#include "IPAddress.h"
#include <memory>
enum WiFiMode { WIFI_OFF = 0, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3 };
enum wl_status_t { WL_IDLE_STATUS = 0, WL_NO_SSID_AVAIL = 1, WL_CONNECTED = 3, WL_CONNECT_FAILED = 4, WL_WRONG_PASSWORD = 6, WL_DISCONNECTED = 7 };
#define WIFI_SCAN_RUNNING (-1)
#define WIFI_SCAN_FAILED (-2)
class WiFiClient {
 public:
  void setTimeout(uint32_t) {}
  int connect(IPAddress, uint16_t) { return 1; }
  void setNoDelay(bool) {}
  size_t write(const uint8_t*, size_t) { return 0; }
  int available() { return 0; }
  int read(uint8_t*, size_t) { return 0; }
  uint8_t connected() { return 0; }
  void stop() {}
};
struct WiFiEventStationModeDisconnected { String ssid; uint8_t bssid[6]; int reason; };
typedef std::shared_ptr<std::function<void(const WiFiEventStationModeDisconnected&)>> WiFiEventHandler;
class ESP8266WiFiClass {
 public:
  void persistent(bool) {}
  WiFiEventHandler onStationModeDisconnected(std::function<void(const WiFiEventStationModeDisconnected&)>) { return WiFiEventHandler(); }
  void setAutoReconnect(bool) {}
  bool hostname(const char*) { return true; }
  bool mode(WiFiMode) { return true; }
  WiFiMode getMode() { return WIFI_STA; }
  wl_status_t begin(const char*, const char*) { return WL_IDLE_STATUS; }
  bool disconnect() { return true; }
  wl_status_t status() { return WL_IDLE_STATUS; }
  int32_t RSSI() { return 0; }
  IPAddress localIP() { return IPAddress(); }
  int hostByName(const char*, IPAddress&, uint32_t = 5000) { return 1; }
  bool softAPConfig(IPAddress, IPAddress, IPAddress) { return true; }
  bool softAP(const char*) { return true; }
  bool softAPdisconnect(bool = false) { return true; }
  int8_t scanNetworks(bool = false, bool = false) { return 0; }
  uint8_t encryptionType(uint8_t) { return 0; }
  int32_t RSSI(uint8_t) { return 0; }
  int32_t channel(uint8_t) { return 0; }
  int8_t scanComplete() { return 0; }
  String SSID(uint8_t) { return String(); }
  String SSID() const { return String(); }
  void scanDelete() {}
};
extern ESP8266WiFiClass WiFi;
