#include "paper.h"

namespace tgt {

bool PaperDrive::request() {
  if (paperMs_ == 0) return false;
  if (pending_ >= MAX_PENDING) return false;
  pending_++;
  return true;
}

bool PaperDrive::start(uint32_t nowMs) {
  if (paperMs_ == 0) { pending_ = 0; return false; }            // switched off meanwhile: drop the requests
  out_.motor(true);
  until_ = nowMs + paperMs_;
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
        until_ = nowMs + GAP_MS;
        phase_ = GAP;
      }
      break;
    case GAP:
      if ((int32_t)(nowMs - until_) >= 0) phase_ = IDLE;
      break;
  }
}

}  // namespace tgt
