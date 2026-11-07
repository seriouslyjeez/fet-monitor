// A shot as one text line for the server, signed with the target's key (HMAC-SHA256, first 8 bytes):
//
//   FT1 S b=<boot> q=<seq> t=<ms> n=<N> e=<E> s=<S> w=<W> m=<mask> f=<face us|-> c=<temp 0.1 C|-> k=<settings id>
//         d=<dropped> [r=<counts per second>] h=<16 hex>
//
// b boot count, q shot number in this boot, t ms since start, n/e/s/w raw counter values, m the microphones that
// triggered (hex, bit 0 = North), f the face sensor's first edge relative to the counters' stop (us), c the
// temperature in tenths of a degree, k the id of the settings in force (CRC-32, hex), d shots lost so far because
// the queue was full, r the rate of n/e/s/w (protocol v2; absent: the board revision's clock, 10 MHz ESP32, 8 MHz
// Mega). h covers every byte before " h="; "h=-" means the target has no key yet (the server marks
// such shots as not authenticated). Parsed and checked by fetcore/targetproto.py. No braces: lines may pass
// through equipment that treats braces as commands.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "capture.h"

namespace tgt {

static const int16_t NO_TEMP = -32768;
static const size_t SHOT_LINE_MAX = 200;   // the longest possible line is about 175 (every number at its maximum)

class LineBuf {
 public:
  LineBuf(char* buf, size_t cap) : s_(buf), cap_(cap) { s_[0] = 0; }
  void add(const char* t);
  void addU(uint32_t v);
  void addI(int32_t v);
  void addHex(uint32_t v, int digits);
  size_t len() const { return n_; }
  bool overflowed() const { return over_; }

 private:
  void put(char c);
  char* s_;
  size_t cap_, n_ = 0;
  bool over_ = false;
};

// Appends " h=<16 hex>" (HMAC of everything in b so far; "-" without a key) and "\n"; returns the length, or 0 if
// the buffer was too small. b must write into out. Used for every signed line the target sends.
size_t signLine(LineBuf& b, char* out, const uint8_t* key, size_t keyLen);

// Writes the line (with "\n") into out; returns its length, or 0 if cap is too small. key may be null (h=-).
// rateHz: the counts' rate, sent as r= (0: not sent, the board revision's clock applies).
size_t formatShot(const Shot& s, uint32_t boot, int16_t tempDeci, uint32_t settingsId, uint16_t dropped,
                  const uint8_t* key, size_t keyLen, char* out, size_t cap, uint32_t rateHz = 0);

}  // namespace tgt
