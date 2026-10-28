// Settings over the serial port, for the Pi's flash station ("flash and configure"): right after flashing, the Pi
// sends one line
//     FETCFG <the settings JSON, as hex> <crc32 of that JSON, 8 hex digits>
// and the bridge checks it (validateConfig), stores it (saveConfig) and restarts with it. "FETCFG HELLO" asks who it
// is. Replies (to the serial port, i.e. back to the Pi; in a target they would reach the Arduino, so never braces):
//     FETCFG HELLO <device id> <firmware version>
//     FETCFG OK <device id>
//     FETCFG ERROR <reason>
// Only in the first WINDOW_MS after start-up (a target's Arduino cannot print such a line anyway). Every byte that is
// not part of a provisioning line passes on to the bridge unchanged and in order; a provisioning line never does
// (the bridge would store it as an event, and the WiFi password with it). Hex keeps braces out of the line.
#pragma once
#include <cstdint>
#include <string>
#include "interfaces.h"

namespace fet {

class Provision {
 public:
  static const uint64_t WINDOW_MS = 60000;
  static const size_t MAX_LINE = 2400;            // "FETCFG " + 2 x settings (at most ~1 KB) + crc
  Provision(Fs& fs, Uart& uart, Hooks& hooks, const std::string& deviceId, const std::string& fwVersion)
      : fs_(fs), uart_(uart), hooks_(hooks), id_(deviceId), fw_(fwVersion) {}
  // One byte from the serial port; whatever belongs to the bridge is appended to passOn.
  void feed(uint8_t c, uint64_t now, std::string& passOn);

 private:
  void handle(const std::string& body);
  void reply(const std::string& s);
  Fs& fs_; Uart& uart_; Hooks& hooks_;
  std::string id_, fw_;
  std::string held_;                              // the current line while it may still be (or is) a provisioning line
  bool passing_ = false;                          // the current line is not one: bytes go straight on
  bool swallowing_ = false;                       // the current line is one: keep it until the end of the line
  bool tooLong_ = false;
};

}  // namespace fet
