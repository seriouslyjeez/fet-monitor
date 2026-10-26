// The bridge: serial lines in -> durable queue -> HTTP to the server -> acknowledgements out.
// Also runs the server's commands and reports status. Pure logic: all I/O goes through the interfaces.
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "assembler.h"
#include "eventlog.h"
#include "interfaces.h"

namespace fet {

struct BridgeInfo {
  std::string deviceId;      // fet-a38f31 : unique per ESP, from its chip id
  std::string name;          // shown on the dashboard
  std::string fwVersion;     // this firmware
};

class Bridge : public RecordSink {
 public:
  Bridge(Fs& fs, Net& net, Uart& uart, Hooks& hooks, const BridgeInfo& info, size_t queueCapBytes, size_t segBytes = 4096);
  void begin(uint64_t now);                       // after the filesystem is mounted
  void onByte(uint8_t c, uint64_t now);           // every byte from the Arduino
  void tick(uint64_t now);                        // call often; does the network work when it is time
  void setName(const std::string& n) { info_.name = n; }

  uint32_t bootCount() const { return boot_; }
  size_t queued() const { return log_.pending(); }
  std::string statusJson(uint64_t now) const;
  // exposed for tests and the status page
  uint32_t sentBatches = 0, ackedEvents = 0, rejectedEvents = 0, droppedPoison = 0, httpErrors = 0;
  int lastStatus = 0;
  bool authError = false;
  bool fsOk = true;                               // false if the boot counter could not be saved

  void onRecord(const char* type, const std::string& text);   // RecordSink

 private:
  struct Result { long long id; bool ok; std::string reply; };
  void sendEvents(uint64_t now);
  void sendHeartbeat(uint64_t now);
  void runCommand(long long id, const std::string& type, const std::string& line);
  void learnVersion(const char* type, const std::string& text);
  Fs& fs_; Net& net_; Uart& uart_; Hooks& hooks_;
  BridgeInfo info_;
  EventLog log_;
  Assembler asm_;
  uint32_t boot_ = 0, seq_ = 0;
  uint64_t now_ = 0;
  uint64_t nextEvent_ = 0, nextHb_ = 0;
  uint32_t evBackoff_ = 0, hbBackoff_ = 0, hbInterval_ = 5000;
  size_t batchMax_ = 10;
  long long lastCmd_ = 0;
  std::vector<Result> results_;
  bool rebootPending_ = false, erasePending_ = false;
  std::string arduinoVersion_, lastError_;
};

}  // namespace fet
