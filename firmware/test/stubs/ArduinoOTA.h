#pragma once
#include "ESP8266WiFi.h"
struct ArduinoOTAClass {
  void setHostname(const char*) {}
  void setPassword(const char*) {}
  void begin() {}
  void handle() {}
};
extern ArduinoOTAClass ArduinoOTA;
