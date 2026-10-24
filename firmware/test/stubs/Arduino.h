// Minimal stand-ins for the ESP8266 Arduino core API, ONLY for syntax/type checking on a PC (never linked).
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string>
#include <vector>
#include <functional>
#include <string.h>

class String {
 public:
  String() {}
  String(const char* s) : s_(s) {}
  const char* c_str() const { return s_.c_str(); }
  bool operator==(const char* o) const { return s_ == o; }
  bool operator==(const String& o) const { return s_ == o.s_; }
  unsigned length() const { return (unsigned)s_.size(); }
  bool isEmpty() const { return s_.empty(); }
 private:
  std::string s_;
};
unsigned long millis();
void delay(unsigned long ms);
class HardwareSerial {
 public:
  void setRxBufferSize(size_t) {}
  void begin(unsigned long) {}
  int available() { return 0; }
  int read() { return -1; }
  size_t write(const uint8_t*, size_t) { return 0; }
  void flush() {}
  size_t print(const char*) { return 0; }
  size_t print(unsigned) { return 0; }
  size_t printf(const char*, ...) __attribute__((format(printf, 2, 3))) { return 0; }
  size_t println(int) { return 0; }
  size_t println(const char*) { return 0; }
};
extern HardwareSerial Serial;
struct rst_info { uint32_t reason, exccause, epc1, epc2, epc3, excvaddr, depc; };
struct EspClass {
  rst_info* getResetInfoPtr();
  uint32_t getFlashChipSize();
  uint32_t getFlashChipRealSize();
  const char* getSdkVersion();
  String getCoreVersion();
  uint32_t getChipId();
  uint32_t getFreeHeap();
  void restart();
};
extern EspClass ESP;
enum SeekMode { SeekSet = 0, SeekCur = 1, SeekEnd = 2 };
