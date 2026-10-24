// A durable queue of events waiting to be acknowledged by the server.
//
// Each event is appended to a segment file (q00001.log ...) as one line:  <crc32 of the rest, 8 hex> <json>\n
// The json is exactly what we later send:  {"seq":..,"boot_count":..,"type":"shot","uptime_ms":..,"raw":"..."}
// After a power cut a torn or corrupt line fails its CRC and is skipped; everything else is kept.
// An event leaves the queue only when the server acknowledges it. Delivery is at-least-once; the server
// de-duplicates on (boot_count, seq).
// If flash is full the oldest segment is dropped (counted in overflow). If flash writes fail, events are
// kept in RAM (a short queue) so a bad flash does not silently lose shots.
#pragma once
#include <cstdint>
#include <string>
#include <utility>
#include <vector>
#include "interfaces.h"

namespace fet {

class EventLog {
 public:
  EventLog(Fs& fs, size_t capBytes, size_t segBytes = 4096, size_t maxPending = 600)
      : fs_(fs), cap_(capBytes), segMax_(segBytes), maxPending_(maxPending) {}
  void load();                                                     // rebuild the index from flash (after boot)
  bool append(uint32_t boot, uint32_t seq, const char* type, uint64_t uptimeMs, const std::string& rawEscaped);
  size_t pending() const;                                          // events not yet acknowledged
  // Up to maxN oldest unacknowledged events as "{..},{..}" (at most maxBytes); ids says which.
  size_t nextBatch(size_t maxN, size_t maxBytes, std::string& jsonList, std::vector<std::pair<uint32_t, uint32_t>>& ids);
  void ack(uint32_t boot, uint32_t seq);
  uint32_t overflow() const { return overflow_; }
  uint32_t corruptLines() const { return corrupt_; }
  uint32_t memoryOnly() const { return (uint32_t)mem_.size(); }
  size_t bytesUsed() const;
  static std::string segName(uint32_t id);
 private:
  struct Item { uint32_t boot, seq; uint32_t seg; uint32_t off; uint32_t len; bool acked; };
  struct Seg { uint32_t id; uint32_t bytes; uint32_t live; };
  struct Mem { uint32_t boot, seq; std::string json; };
  Seg* find(uint32_t id);
  void dropOldestSegment();
  void releaseIfEmpty(uint32_t id);
  static bool numAfter(const std::string& s, const char* key, uint32_t& out);
  Fs& fs_;
  size_t cap_, segMax_, maxPending_;
  std::vector<Item> items_;
  std::vector<Seg> segs_;
  std::vector<Mem> mem_;
  uint32_t cur_ = 0;               // id of the segment being appended to (0 = none)
  uint32_t nextId_ = 1;
  uint32_t overflow_ = 0, corrupt_ = 0;
};

}  // namespace fet
