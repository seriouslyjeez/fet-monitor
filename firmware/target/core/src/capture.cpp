#include "capture.h"

namespace tgt {

void Capture::begin(uint32_t nowMs) {
  hw_.arm();
  state_ = IDLE;
  faceSeen_ = false;
  lastMs_ = nowMs;
}

void Capture::face(uint32_t nowUs) {
  if (faceSeen_ || state_ == RING) return;          // the first edge only; echoes after the shot are not news
  faceAt_ = nowUs;
  faceAtMs_ = lastMs_;
  faceSeen_ = true;
}

void Capture::push(const Shot& s) {
  uint8_t next = (uint8_t)((head_ + 1) % QSIZE);
  if (next == tail_) {                              // full: keep the older, unreported shots; count the loss
    if (dropped_ != 0xFFFF) dropped_++;
    return;
  }
  q_[head_] = s;
  head_ = next;
}

bool Capture::pop(Shot& out) {
  if (tail_ == head_) return false;
  out = q_[tail_];
  tail_ = (uint8_t)((tail_ + 1) % QSIZE);
  return true;
}

void Capture::tick(uint32_t nowMs, uint32_t nowUs) {
  lastMs_ = nowMs;
  uint8_t run = (uint8_t)(hw_.running() & ALL);
  switch (state_) {
    case IDLE:
      if (run) {
        firstMs_ = nowMs;
        until_ = nowMs + waitMs;
        state_ = WAIT;
        if (run != ALL) break;                      // all four at once: finish below without waiting a tick
      } else {
        if (faceSeen_ && (uint32_t)(nowMs - faceAtMs_) >= faceAloneMs) {   // a face edge, and no microphone
          Shot s;
          s.seq = ++seq_;
          s.ms = faceAtMs_;
          for (int i = 0; i < 4; i++) s.count[i] = 0;
          s.mask = 0;
          s.hasFace = true;
          s.faceUs = (int32_t)(faceAt_ - nowUs);
          push(s);
          hw_.arm();
          faceSeen_ = false;
        }
        break;
      }
      // fall through - every microphone triggered within the same tick
    case WAIT:
      if (run == ALL || (int32_t)(nowMs - until_) >= 0) {
        Shot s;
        hw_.stopAndRead(s.count);
        s.seq = ++seq_;
        s.ms = firstMs_;
        s.mask = run;
        s.hasFace = faceSeen_;
        s.faceUs = faceSeen_ ? (int32_t)(faceAt_ - nowUs) : 0;
        push(s);
        hw_.clearRun();
        until_ = nowMs + ringMs;
        state_ = RING;
      }
      break;
    case RING:
      if (run) {                                    // still ringing (or an echo): wait for quiet
        hw_.clearRun();
        until_ = nowMs + ringMs;
      } else if ((int32_t)(nowMs - until_) >= 0) {
        hw_.arm();
        faceSeen_ = false;
        state_ = IDLE;
      }
      break;
  }
}

}  // namespace tgt
