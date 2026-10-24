#pragma once
#include "ESP8266WiFi.h"
enum class DNSReplyCode { NoError = 0 };
class DNSServer {
 public:
  void setErrorReplyCode(const DNSReplyCode&) {}
  bool start(uint16_t, const String&, const IPAddress&) { return true; }
  void stop() {}
  void processNextRequest() {}
};
