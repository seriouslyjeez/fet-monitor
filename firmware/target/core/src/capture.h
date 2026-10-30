// Shot capture: the acquisition state machine and the queue between the timer interrupt and the main loop.
//
// The hardware latches a RUN bit per microphone when its sound crosses the threshold, and a counter per microphone
// runs from that moment until it is stopped. tick() (from a 1 kHz timer interrupt) watches the RUN bits:
//   IDLE   a RUN bit is set              -> WAIT up to waitMs for the other microphones
//   WAIT   all four set, or time is up   -> stop and read the counters, queue the shot, RING
//   RING   any RUN bit set again (echo)  -> clear it and restart the ring time; quiet for ringMs -> re-arm, IDLE
// A microphone that did not trigger within waitMs is reported as missing (its bit is 0 in Shot::mask); the counts
// are what the counters hold, for the record. Freely after: upstream freETarget timer.ino / timer.c.
//
// The face sensor (optional) is time-stamped, not counted: face() keeps the first edge after arming, and the shot
// records its time relative to the moment the counters were stopped. A face edge with no microphone within
// faceAloneMs is reported on its own (mask 0, hasFace): a plate hit that the microphones did not hear.
//
// tick() and face() run in interrupts: no blocking, no allocation, no output. pop() runs in the main loop; the
// platform calls it with interrupts disabled (it is a few instructions).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace tgt {

struct CounterHw {
  virtual uint8_t running() = 0;                  // RUN bits: bit 0 North, 1 East, 2 South, 3 West
  virtual void stopAndRead(uint32_t out[4]) = 0;  // stop the counters and read all four
  virtual void clearRun() = 0;                    // reset the RUN latches (counters stay stopped)
  virtual void arm() = 0;                         // clear the counters and latches, start listening again
};

struct Shot {
  uint32_t seq;          // per boot, from 1
  uint32_t ms;           // ms since start when the first microphone triggered (1 ms resolution)
  uint32_t count[4];     // counter values N E S W (ticks from each microphone's trigger to the stop)
  uint8_t mask;          // microphones that triggered
  bool hasFace;          // the face sensor fired
  int32_t faceUs;        // its first edge relative to the stop (negative: before), if hasFace
};

class Capture {
 public:
  static const uint8_t ALL = 0x0F;
  static const uint8_t QSIZE = 8;
  explicit Capture(CounterHw& hw) : hw_(hw) {}

  void begin(uint32_t nowMs);
  void tick(uint32_t nowMs, uint32_t nowUs);      // 1 kHz, from the timer interrupt
  void face(uint32_t nowUs);                      // face sensor edge, from its interrupt
  bool pop(Shot& out);                            // main loop, with interrupts off
  uint16_t dropped() const { return dropped_; }   // shots lost because the queue was full

  uint16_t waitMs = 2;                            // longest spread between the first and last microphone (+1)
  uint16_t ringMs = 50;                           // quiet time before re-arming
  uint16_t faceAloneMs = 5;                       // a face edge with no microphone after this long is a shot

 private:
  enum State { IDLE, WAIT, RING };
  void push(const Shot& s);
  CounterHw& hw_;
  volatile uint8_t state_ = IDLE;
  uint32_t until_ = 0, firstMs_ = 0, seq_ = 0;
  volatile bool faceSeen_ = false;
  volatile uint32_t faceAt_ = 0, faceAtMs_ = 0;
  uint32_t lastMs_ = 0;
  Shot q_[QSIZE];
  volatile uint8_t head_ = 0, tail_ = 0;          // push at head_ (interrupt), pop at tail_ (main loop)
  volatile uint16_t dropped_ = 0;
};

}  // namespace tgt
