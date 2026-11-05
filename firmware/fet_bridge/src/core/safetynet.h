// WiFi safety nets, as decisions only (no WiFi calls), so the PC harness can check them. Used by the ESP32 board's
// network layer (firmware/target/esp32): a WiFi stack can say "connected" while nothing gets through (Arduino-ESP32
// 3.3.12 keeps WL_CONNECTED after a disconnect with reason 2), so FeT Monitor's answers are what counts.
#pragma once
#include <stdint.h>

namespace fet {

// Re-join the WiFi when FeT Monitor has not answered for silentMs: the first time silentMs after the last answer (or
// the start), then twice as long after each re-join that brought no answer, up to maxWaitMs; an answer starts over.
class SilenceWatch {
 public:
  SilenceWatch(uint64_t silentMs, uint64_t maxWaitMs) : silent_(silentMs), max_(maxWaitMs), wait_(silentMs) {}
  void start(uint64_t now) { startedAt_ = now; }
  bool due(uint64_t now, uint64_t lastOkMs) {
    if (lastOkMs > lastRejoin_) wait_ = silent_;                       // answered since the last re-join
    uint64_t heard = lastOkMs > startedAt_ ? lastOkMs : startedAt_;
    if (now - heard < silent_ || (lastRejoin_ && now - lastRejoin_ < wait_)) return false;
    if (lastRejoin_ && lastOkMs <= lastRejoin_) wait_ = wait_ * 2 > max_ ? max_ : wait_ * 2;
    lastRejoin_ = now;
    return true;
  }
  uint64_t waitMs() const { return wait_; }
 private:
  uint64_t silent_, max_, wait_, startedAt_ = 0, lastRejoin_ = 0;
};

// The last resort (run from a task other than the network's): restart the board? "net_stuck" when the network task
// has not moved for stuckS (not while a firmware image downloads), "no_wifi" after noWifiS without any WiFi; never in
// the first 2 minutes, within 2 minutes of a shot, or while the setup portal is open. 0 = no. All in seconds since
// the start; aliveS / noWifiSinceS 0 = unknown / WiFi up.
inline const char* restartWhy(uint32_t nowS, uint32_t lastShotS, uint32_t aliveS, uint32_t noWifiSinceS, bool downloading,
                              bool portalOpen, uint32_t stuckS, uint32_t noWifiS) {
  if (nowS < 120 || nowS - lastShotS < 120 || portalOpen) return 0;
  if (!downloading && aliveS && nowS - aliveS > stuckS) return "net_stuck";
  if (noWifiSinceS && nowS - noWifiSinceS > noWifiS) return "no_wifi";
  return 0;
}

}  // namespace fet
