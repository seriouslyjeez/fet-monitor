#pragma once
#include "ESP8266WiFi.h"
enum HTTPMethod { HTTP_ANY, HTTP_GET, HTTP_POST };
class ESP8266WebServer {
 public:
  ESP8266WebServer(int) {}
  typedef std::function<void(void)> THandlerFunction;
  void on(const char*, HTTPMethod, THandlerFunction) {}
  void onNotFound(THandlerFunction) {}
  void begin() {}
  void handleClient() {}
  String arg(const char*) { return String(); }
  void sendHeader(const String&, const String&, bool = false) {}
  void send(int, const char*, const String&) {}
};
