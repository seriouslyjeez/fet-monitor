// Shot times from capture timestamps (the ESP32 board without its 10 MHz clock, firmware 0.3.0+).
//
// The ESP32-S3 stamps each microphone's RUN edge with a capture timer (MCPWM, 80 MHz). Its four channels sit in two
// groups with a timer each; both timers run from the same clock, and both stamp the same re-arm edge (STOP_N going
// high), so offset = arm stamp (group 1) - arm stamp (group 0) turns a group 1 stamp into group 0's time.
//
// The result is what the board's counters gave before (shotline.h, the server's solver): one count per microphone in
// 10 MHz ticks, larger for a microphone that heard the shot earlier. Only the differences carry the shot (the solver
// takes the arrival times "up to a constant"); the microphone that heard it last gets STAMP_BASE, so a count of 0 still
// means "no time". 32-bit stamps wrap every 53 s at 80 MHz; a shot's stamps lie within milliseconds, so the arithmetic
// is done on differences.
#pragma once
#include <stdint.h>

namespace tgt {

static const uint32_t STAMP_BASE = 1000;             // 100 us at 10 MHz: the latest microphone's count
static const uint32_t COUNT_HZ = 10000000;           // the unit of a count (the ESP32 board's former clock)

// stamp[i]: microphone i's stamp in its group's timer (N, E, S, W); have: bit i set if microphone i has one;
// group[i]: 0 or 1; offset: group 1 time - group 0 time; resolutionHz: the capture timers' rate. Microphones without a
// stamp get 0.
inline void stampsToCounts(const uint32_t stamp[4], uint8_t have, const uint8_t group[4], uint32_t offset,
                           uint32_t resolutionHz, uint32_t out[4]) {
  int32_t d[4] = {0, 0, 0, 0};
  int first = -1;
  uint32_t ref = 0;
  for (int i = 0; i < 4; i++) {
    out[i] = 0;
    if (!(have & (1 << i))) continue;
    uint32_t t = stamp[i] - (group[i] ? offset : 0u);   // in group 0's time
    if (first < 0) { first = i; ref = t; }
    d[i] = (int32_t)(t - ref);                          // relative to the first one found: no wrap
  }
  if (first < 0 || resolutionHz == 0) return;
  int32_t latest = d[first];
  for (int i = 0; i < 4; i++)
    if ((have & (1 << i)) && d[i] > latest) latest = d[i];
  for (int i = 0; i < 4; i++) {
    if (!(have & (1 << i))) continue;
    uint64_t ticks = (uint64_t)(uint32_t)(latest - d[i]);           // how much earlier than the latest
    out[i] = STAMP_BASE + (uint32_t)((ticks * COUNT_HZ + resolutionHz / 2) / resolutionHz);
  }
}

}  // namespace tgt
