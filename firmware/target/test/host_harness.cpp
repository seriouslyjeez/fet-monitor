// Host harness for the target core: drives Capture with a fake counter board and prints shot lines, one command per
// line on stdin, "." after each reply. Used by tests/test_target_core.py.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>
#include "../core/src/capture.h"
#include "../core/src/commands.h"
#include "../core/src/identity.h"
#include "../core/src/paper.h"
#include "../core/src/settings.h"
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

struct FakeNvm : Nvm {                       // erased EEPROM reads 0xFF; a write can fail or tear partway
  std::vector<uint8_t> mem = std::vector<uint8_t>(4096, 0xFF);
  int writes = 0, failNext = 0, lieNext = 0;
  long tearNext = -1;
  bool read(size_t addr, uint8_t* buf, size_t n) override {
    if (addr + n > mem.size()) return false;
    memcpy(buf, &mem[addr], n);
    return true;
  }
  bool write(size_t addr, const uint8_t* buf, size_t n) override {
    if (addr + n > mem.size()) return false;
    writes++;
    if (failNext) { failNext--; return false; }
    size_t k = tearNext >= 0 && (size_t)tearNext < n ? (size_t)tearNext : n;
    memcpy(&mem[addr], buf, k);
    if (tearNext >= 0) { tearNext = -1; return false; }       // the power went: the caller never hears back
    if (lieNext) { lieNext--; mem[addr + n / 2] ^= 0x01; }     // a bad cell: reports success, stores a flipped bit
    return true;
  }
};

struct FakeActions : Actions {
  int papers = 0, changes = 0;
  void paperAdvance() override { papers++; }
  void settingsChanged(const Settings&) override { changes++; }
  uint32_t uptimeMs() override { return 123456; }
  uint16_t dropped() override { return 2; }
};

struct FakeMotor : MotorOut {
  uint32_t now = 0;
  std::string log;
  void motor(bool on) override { log += (on ? "+" : "-") + std::to_string(now) + " "; }
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
  FakeNvm nvm;
  std::unique_ptr<Settings> st(new Settings(nvm, 64));
  FakeActions acts;
  Identity ident(nvm);
  FakeMotor motor;
  PaperDrive paper(motor);
  uint32_t cboot = 7;
  std::unique_ptr<Commands> cmds(new Commands(*st, acts, cboot));
  std::vector<uint8_t> ckey;
  LineReader reader;
  size_t replyCap = 320;
  auto answer = [&](const char* l) {
    char out[400];
    size_t n = cmds->handle(l, out, replyCap);
    if (n == 0) std::cout << "none\n"; else std::cout << std::string(out, n - 1) << "\n";
  };
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
    else if (cmd == "sload") { bool ok = st->load(); std::cout << (ok ? "loaded" : "defaults") << " " << st->generation(); for (int i = 0; i < S_COUNT; i++) std::cout << " " << st->get(i); std::cout << "\n"; }
    else if (cmd == "sset") { std::string k; long v; is >> k >> v; const char* r = st->set(Settings::find(k.c_str()), (int32_t)v); std::cout << (r ? r : "ok") << "\n"; }
    else if (cmd == "sseti") { int i; long v; is >> i >> v; const char* r = st->set(i, (int32_t)v); std::cout << (r ? r : "ok") << "\n"; }
    else if (cmd == "sget") { std::string k; is >> k; int i = Settings::find(k.c_str()); std::cout << (i < 0 ? std::string("?") : std::to_string(st->get(i))) << "\n"; }
    else if (cmd == "ssave") { bool ok = st->save(); std::cout << (ok ? "saved" : "failed") << " " << nvm.writes << " " << st->generation() << "\n"; }
    else if (cmd == "sid") { char b[9]; snprintf(b, sizeof b, "%08x", (unsigned)st->id()); std::cout << b << "\n"; }
    else if (cmd == "sdirty") std::cout << (st->dirty() ? 1 : 0) << "\n";
    else if (cmd == "nvmfail") is >> nvm.failNext;
    else if (cmd == "nvmtear") is >> nvm.tearNext;
    else if (cmd == "nvmlie") is >> nvm.lieNext;
    else if (cmd == "nvmset") { size_t a; std::string h; is >> a >> h; auto d = unhex(h); memcpy(&nvm.mem[a], d.data(), d.size()); }
    else if (cmd == "nvmpoke") { size_t a; int v; is >> a >> v; nvm.mem[a] = (uint8_t)v; }
    else if (cmd == "reboot") { st.reset(new Settings(nvm, 64)); st->load(); cboot++; cmds.reset(new Commands(*st, acts, cboot));
                                if (!ckey.empty()) cmds->setKey(ckey.data(), ckey.size()); }
    else if (cmd == "cboot") { is >> cboot; cmds.reset(new Commands(*st, acts, cboot)); if (!ckey.empty()) cmds->setKey(ckey.data(), ckey.size()); }
    else if (cmd == "ckey") { std::string hx; is >> hx; ckey = unhex(hx); cmds->setKey(ckey.data(), ckey.size()); }
    else if (cmd == "cmd") { std::string rest; std::getline(is, rest); if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1); answer(rest.c_str()); }
    else if (cmd == "replycap") is >> replyCap;
    else if (cmd == "feed") { std::string rest; std::getline(is, rest); if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
      bool any = false;
      for (size_t k = 0; k < rest.size(); k++) {
        char c = rest[k];
        if (c == '\\' && k + 1 < rest.size()) { char e = rest[++k]; c = e == 'n' ? '\n' : e == 'r' ? '\r' : e; }
        if (reader.feed(c)) { answer(reader.line()); reader.next(); any = true; }
      }
      if (!any) std::cout << "none\n"; }
    else if (cmd == "rfeed") { std::string rest; std::getline(is, rest); if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
      bool r = false;
      for (size_t k = 0; k < rest.size(); k++) { char c = rest[k]; if (c == '\\' && k + 1 < rest.size()) { char e = rest[++k]; c = e == 'n' ? '\n' : e; } r = reader.feed(c); }
      std::cout << (r ? 1 : 0) << " [" << reader.line() << "]\n"; }
    else if (cmd == "rnext") reader.next();
    else if (cmd == "iload") { ident.load(); char fp[9]; ident.fingerprint(fp); std::cout << ident.boot() << " " << fp << "\n"; }
    else if (cmd == "istart") std::cout << ident.startBoot() << "\n";
    else if (cmd == "isetup") { std::string rest; std::getline(is, rest); if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
      char out[200]; size_t n = ident.setupLine(rest.c_str(), "0.1.0", 310, st->id(), out, sizeof out);
      std::cout << (n ? std::string(out, n - 1) : std::string("none")) << "\n"; }
    else if (cmd == "ikeyhex") { char b[3]; std::string h; for (size_t k = 0; k < Identity::KEY_LEN; k++) { snprintf(b, 3, "%02x", ident.key()[k]); h += b; } std::cout << h << "\n"; }
    else if (cmd == "pconf") { int a, b, c; is >> a >> b >> c; paper.configure((uint16_t)a, (uint16_t)b, (uint16_t)c); }
    else if (cmd == "preq") std::cout << (paper.request() ? 1 : 0) << "\n";
    else if (cmd == "ptick") { uint32_t from, to; is >> from >> to; for (uint32_t t = from; t <= to; t++) { motor.now = t; paper.tick(t); } }
    else if (cmd == "plog") { std::string l = motor.log; if (!l.empty()) l.pop_back(); std::cout << l << "\n"; motor.log.clear(); }
    else if (cmd == "pbusy") std::cout << (paper.busy() ? 1 : 0) << "\n";
    else if (cmd == "acts") std::cout << acts.papers << " " << acts.changes << "\n";
    else if (cmd == "quit") break;
    else std::cout << "?\n";
    std::cout << ".\n" << std::flush;
  }
  return 0;
}
