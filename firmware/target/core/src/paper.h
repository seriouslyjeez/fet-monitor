// Witness paper drive without blocking (upstream busy-waits in drive_paper(), so nothing else runs meanwhile).
// A DC motor runs for paper_ms; a stepper gets step_count pulses of step_ms with 5 ms between them. Requests while
// it runs are counted (up to MAX_PENDING) and done one after another. tick() from the main loop.
#pragma once
#include <stdint.h>

namespace tgt {

struct MotorOut {
  virtual void motor(bool on) = 0;
};

class PaperDrive {
 public:
  static const uint8_t MAX_PENDING = 5;
  static const uint16_t STEP_GAP_MS = 5;
  explicit PaperDrive(MotorOut& m) : out_(m) {}
  // Settings in force for the next advance (0/0 = no paper drive).
  void configure(uint16_t paperMs, uint16_t stepCount, uint16_t stepMs) { paperMs_ = paperMs; steps_ = stepCount; stepMs_ = stepMs; }
  bool request();                                 // false if nothing is configured or too many are waiting
  void tick(uint32_t nowMs);
  bool busy() const { return phase_ != IDLE || pending_ != 0; }

 private:
  enum Phase { IDLE, ON, GAP };
  bool start(uint32_t nowMs);
  MotorOut& out_;
  uint16_t paperMs_ = 0, steps_ = 0, stepMs_ = 0;
  uint8_t pending_ = 0;
  uint8_t phase_ = IDLE;
  uint16_t left_ = 0;                             // stepper pulses still to give in this advance
  uint32_t until_ = 0;
};

}  // namespace tgt
