#pragma once
#include "Arduino.h"
class IPAddress {
 public:
  IPAddress() {}
  IPAddress(uint8_t, uint8_t, uint8_t, uint8_t) {}
  bool fromString(const char*) { return true; }
  String toString() const { return String(); }
};
