#pragma once
#include "ESP8266WiFi.h"
struct MDNSResponder {
  bool begin(const char*) { return true; }
  bool end() { return true; }
  bool update() { return true; }
  bool addService(const char*, const char*, uint16_t) { return true; }
  uint32_t queryService(const char*, const char*) { return 0; }
  IPAddress IP(uint32_t) { return IPAddress(); }
  uint16_t port(uint32_t) { return 0; }
};
extern MDNSResponder MDNS;
