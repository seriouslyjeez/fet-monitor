// Stand-in for the ESP8266 core's Updater (type checking only).
#pragma once
#include <Arduino.h>
#define U_FLASH 0
class UpdaterClass {
 public:
  bool begin(size_t size, int command = U_FLASH);
  size_t write(uint8_t* data, size_t len);
  bool end(bool evenIfRemaining = false);
  String getErrorString();
  bool hasError();
};
extern UpdaterClass Update;
