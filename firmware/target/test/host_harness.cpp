// Host harness for the target core: drives Capture with a fake counter board and prints shot lines, one command per
// line on stdin, "." after each reply. Used by tests/test_target_core.py.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include "../core/src/capture.h"
#include "../core/src/shotline.h"
#include "../core/src/tgt_sha256.h"

using namespace tgt;

struct FakeHw : CounterHw {
  uint8_t run = 0;
  uint32_t counts[4] = {0, 0, 0, 0};
  int arms = 0, clears = 0, reads = 0;
  uint8_t running() override { return run; }
  void stopAndRead(uint32_t out[4]) override { reads++; for (int i = 0; i < 4; i++) out[i] = counts[i]; }
  void clearRun() override { clears++; run = 0; }
  void arm() override { arms++; run = 0; }
};

static std::vector<uint8_t> unhex(const std::string& h) {
  std::vector<uint8_t> v;
  for (size_t i = 0; i + 1 < h.size(); i += 2) v.push_back((uint8_t)strtol(h.substr(i, 2).c_str(), nullptr, 16));
  return v;
}

static std::string hex(const uint8_t* p, size_t n) {
  static const char* hx = "0123456789abcdef";
  std::string s;
  for (size_t i = 0; i < n; i++) { s += hx[p[i] >> 4]; s += hx[p[i] & 15]; }
  return s;
}

int main() {
  FakeHw hw;
  Capture cap(hw);
  std::vector<uint8_t> key;
  bool haveKey = false;
  int16_t temp = NO_TEMP;
  uint32_t sid = 0x1234abcd, boot = 7;
  size_t cap_ = SHOT_LINE_MAX;
  cap.begin(0);
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream is(line);
    std::string cmd;
    is >> cmd;
    if (cmd == "run") { int m; is >> std::hex >> m; hw.run |= (uint8_t)m; }
    else if (cmd == "counts") { for (int i = 0; i < 4; i++) is >> hw.counts[i]; }
    else if (cmd == "tick") { uint32_t ms, us; is >> ms >> us; cap.tick(ms, us); }
    else if (cmd == "face") { uint32_t us; is >> us; cap.face(us); }
    else if (cmd == "key") { std::string h; is >> h; key = unhex(h); haveKey = true; }
    else if (cmd == "nokey") haveKey = false;
    else if (cmd == "temp") { int t; is >> t; temp = (int16_t)t; }
    else if (cmd == "linecap") is >> cap_;
    else if (cmd == "set") { std::string k; int v; is >> k >> v;
      if (k == "wait") cap.waitMs = (uint16_t)v; else if (k == "ring") cap.ringMs = (uint16_t)v; else if (k == "facealone") cap.faceAloneMs = (uint16_t)v; }
    else if (cmd == "pop") {
      Shot s;
      if (!cap.pop(s)) { std::cout << "none\n"; }
      else {
        char out[SHOT_LINE_MAX + 40];
        size_t n = formatShot(s, boot, temp, sid, cap.dropped(), haveKey ? key.data() : nullptr, key.size(), out, cap_);
        if (n == 0) std::cout << "TOO LONG\n"; else std::cout << std::string(out, n - 1) << "\n";
      }
    }
    else if (cmd == "hw") std::cout << hw.arms << " " << hw.clears << " " << hw.reads << " " << (int)hw.run << "\n";
    else if (cmd == "dropped") std::cout << cap.dropped() << "\n";
    else if (cmd == "sha") { std::string h; is >> h; auto d = unhex(h); Sha256 s; s.update(d.data(), d.size()); uint8_t o[32]; s.finish(o); std::cout << hex(o, 32) << "\n"; }
    else if (cmd == "hmac") { std::string k, m; is >> k >> m; auto kk = unhex(k), mm = unhex(m); uint8_t o[32];
      hmacSha256(kk.data(), kk.size(), mm.data(), mm.size(), o); std::cout << hex(o, 32) << "\n"; }
    else if (cmd == "quit") break;
    else std::cout << "?\n";
    std::cout << ".\n" << std::flush;
  }
  return 0;
}
