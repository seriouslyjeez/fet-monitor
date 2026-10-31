// Commands from the server, and the target's replies, one line each:
//
//   FT1 C b=<boot> i=<id> <verb> [key=value ...] h=<16 hex>
//   FT1 R b=<boot> i=<id> r=<ok|reason> [key=value ...] h=<16 hex>
//
// A command is carried out only if
//   - the target has a key (otherwise: r=not_provisioned; the key is set over USB only),
//   - h is the HMAC-SHA256 (first 8 bytes) of everything before " h=", with that key,
//   - b is this boot's count (a command recorded earlier cannot be replayed after a restart),
//   - i is higher than the last command carried out in this boot (nor within it).
// Verbs: get (all settings), set key=value ... (all checked first, then applied together and saved once), paper
// (advance the witness paper now), status. Replies are signed with the same key and repeat b and i, so the server
// can match them. Reasons have no spaces: r=bad_signature, old_command, wrong_boot, unknown_verb, malformed,
// unknown_setting:<key>, out_of_range:<key>, save_failed, not_provisioned. Lines from fetserver/targetproto.py.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "settings.h"

namespace tgt {

struct Actions {                                    // what the platform does for a command
  virtual void paperAdvance() = 0;
  virtual void settingsChanged(const Settings& s) = 0;   // apply LED level, capture timing, threshold, ...
  virtual uint32_t uptimeMs() = 0;
  virtual uint16_t dropped() = 0;
};

class Commands {
 public:
  static const size_t MAX_LINE = 200;
  Commands(Settings& s, Actions& a, uint32_t boot) : st_(s), act_(a), boot_(boot) {}
  void setKey(const uint8_t* key, size_t len) { key_ = key; keyLen_ = len; }
  // Handle one command line (without the newline). Writes the reply (with "\n") into out, returns its length;
  // 0 if the line is not a command at all (not "FT1 C ...": not ours, no reply).
  size_t handle(const char* line, char* out, size_t cap);
  uint32_t lastId() const { return last_; }

 private:
  Settings& st_;
  Actions& act_;
  uint32_t boot_;
  uint32_t last_ = 0;
  const uint8_t* key_ = 0;
  size_t keyLen_ = 0;
};

// Collects bytes into lines; overlong lines are dropped whole (never cut and acted on).
class LineReader {
 public:
  // Feed one byte; returns true when a complete line is in line() (then call next()).
  bool feed(char c);
  const char* line() const { return buf_; }
  void next() { n_ = 0; buf_[0] = 0; ready_ = false; }

 private:
  char buf_[Commands::MAX_LINE + 1] = {0};
  size_t n_ = 0;
  bool over_ = false, ready_ = false;
};

}  // namespace tgt
