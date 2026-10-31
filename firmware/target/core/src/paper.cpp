#include "paper.h"

namespace tgt {

bool PaperDrive::request() {
  bool stepper = steps_ != 0 && stepMs_ != 0;
  if (!stepper && paperMs_ == 0) return false;
  if (pending_ >= MAX_PENDING) return false;
  pending_++;
  return true;
}

bool PaperDrive::start(uint32_t nowMs) {
  bool stepper = steps_ != 0 && stepMs_ != 0;
  if (!stepper && paperMs_ == 0) { pending_ = 0; return false; }   // switched off meanwhile: drop the requests
  left_ = stepper ? steps_ : 1;
  out_.motor(true);
  until_ = nowMs + (stepper ? stepMs_ : paperMs_);
  phase_ = ON;
  return true;
}

void PaperDrive::tick(uint32_t nowMs) {
  switch (phase_) {
    case IDLE:
      if (pending_ && start(nowMs)) pending_--;
      break;
    case ON:
      if ((int32_t)(nowMs - until_) >= 0) {
        out_.motor(false);
        left_--;
        until_ = nowMs + STEP_GAP_MS;
        phase_ = GAP;
      }
      break;
    case GAP:
      if ((int32_t)(nowMs - until_) >= 0) {
        if (left_) {
          out_.motor(true);
          until_ = nowMs + stepMs_;
          phase_ = ON;
        } else {
          phase_ = IDLE;
        }
      }
      break;
  }
}

}  // namespace tgt
