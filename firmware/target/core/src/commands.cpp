#include "commands.h"
#include <string.h>
#include "shotline.h"
#include "tgt_sha256.h"

namespace tgt {

static const char HEXD[] = "0123456789abcdef";

// strict unsigned decimal: digits only, no sign, fits in 32 bits
static bool parseU32(const char* s, size_t n, uint32_t& out) {
  if (n == 0 || n > 10) return false;
  uint64_t v = 0;
  for (size_t i = 0; i < n; i++) {
    if (s[i] < '0' || s[i] > '9') return false;
    v = v * 10 + (uint64_t)(s[i] - '0');
  }
  if (v > 0xFFFFFFFFull) return false;
  out = (uint32_t)v;
  return true;
}

static bool parseI32(const char* s, size_t n, int32_t& out) {
  bool neg = n > 0 && s[0] == '-';
  uint32_t u;
  if (!parseU32(s + (neg ? 1 : 0), n - (neg ? 1 : 0), u)) return false;
  if (neg ? u > 2147483648u : u > 2147483647u) return false;
  out = neg ? (int32_t)(0u - u) : (int32_t)u;
  return true;
}

static void macHex(const uint8_t* key, size_t keyLen, const char* msg, size_t n, char out[17]) {
  uint8_t m[32];
  hmacSha256(key, keyLen, (const uint8_t*)msg, n, m);
  for (int i = 0; i < 8; i++) { out[2 * i] = HEXD[m[i] >> 4]; out[2 * i + 1] = HEXD[m[i] & 15]; }
  out[16] = 0;
}

namespace {
// The reply under construction: "FT1 R b= i= r=" + extras, then signed by finish().
struct Reply {
  LineBuf b;
  Reply(char* out, size_t cap, uint32_t boot, uint32_t id, const char* r, const char* key = 0) : b(out, cap) {
    b.add("FT1 R b="); b.addU(boot);
    b.add(" i="); b.addU(id);
    b.add(" r="); b.add(r);
    if (key) { b.add(":"); b.add(key); }
  }
  void kv(const char* k, int32_t v) { b.add(" "); b.add(k); b.add("="); b.addI(v); }
  size_t finish(char* out, const uint8_t* key, size_t keyLen) {
    size_t signedLen = b.len();
    b.add(" h=");
    if (key) { char h[17]; macHex(key, keyLen, out, signedLen, h); b.add(h); }
    else b.add("-");
    b.add("\n");
    return b.overflowed() ? 0 : b.len();
  }
};
}  // namespace

size_t Commands::handle(const char* line, char* out, size_t cap) {
  static const char PFX[] = "FT1 C ";
  size_t len = strlen(line);
  if (len < sizeof PFX - 1 || memcmp(line, PFX, sizeof PFX - 1) != 0) return 0;
  if (len > MAX_LINE) { Reply r(out, cap, boot_, 0, "malformed"); return r.finish(out, key_, keyLen_); }

  // split off the signature: the last " h="
  const char* h = 0;
  for (size_t i = len; i-- > 3;)
    if (memcmp(line + i - 3, " h=", 3) == 0) { h = line + i - 3; break; }
  size_t headLen = h ? (size_t)(h - line) : len;

  // tokens of the head after the prefix (single spaces only)
  char head[MAX_LINE + 1];
  memcpy(head, line, headLen);
  head[headLen] = 0;
  const char* tok[24];
  size_t tlen[24], nt = 0;
  bool bad = false;
  for (size_t i = sizeof PFX - 1; i <= headLen && !bad;) {
    size_t j = i;
    while (j < headLen && head[j] != ' ') j++;
    if (j == i || nt == 24) bad = true;
    else { tok[nt] = head + i; tlen[nt] = j - i; nt++; }
    i = j + 1;
  }
  uint32_t boot = 0, id = 0;
  if (!bad && nt >= 3 && tlen[0] > 2 && memcmp(tok[0], "b=", 2) == 0 && tlen[1] > 2 && memcmp(tok[1], "i=", 2) == 0) {
    bad = !parseU32(tok[0] + 2, tlen[0] - 2, boot) || !parseU32(tok[1] + 2, tlen[1] - 2, id);
  } else {
    bad = true;
  }
  if (!key_) { Reply r(out, cap, boot_, bad ? 0 : id, "not_provisioned"); return r.finish(out, 0, 0); }

  // the signature first: nothing in an unsigned line is believed (not even its id)
  bool sigOk = false;
  if (h && len - headLen == 3 + 16) {
    char want[17];
    macHex(key_, keyLen_, line, headLen, want);
    uint8_t diff = 0;
    for (int k = 0; k < 16; k++) diff |= (uint8_t)(want[k] ^ h[3 + k]);
    sigOk = diff == 0;
  }
  if (!sigOk) { Reply r(out, cap, boot_, 0, "bad_signature"); return r.finish(out, key_, keyLen_); }
  if (bad) { Reply r(out, cap, boot_, 0, "malformed"); return r.finish(out, key_, keyLen_); }
  if (boot != boot_) { Reply r(out, cap, boot_, id, "wrong_boot"); return r.finish(out, key_, keyLen_); }
  if (id <= last_) { Reply r(out, cap, boot_, id, "old_command"); return r.finish(out, key_, keyLen_); }
  last_ = id;                                       // used up, whatever happens next

  const char* verb = tok[2];
  size_t vl = tlen[2];
  if (vl == 3 && memcmp(verb, "get", 3) == 0 && nt == 3) {
    Reply r(out, cap, boot_, id, "ok");
    r.b.add(" k="); r.b.addHex(st_.id(), 8);
    for (int i = 0; i < S_COUNT; i++) r.kv(Settings::def(i).key, st_.get(i));
    return r.finish(out, key_, keyLen_);
  }
  if (vl == 6 && memcmp(verb, "status", 6) == 0 && nt == 3) {
    Reply r(out, cap, boot_, id, "ok");
    r.b.add(" k="); r.b.addHex(st_.id(), 8);
    r.b.add(" up="); r.b.addU(act_.uptimeMs());
    r.b.add(" d="); r.b.addU(act_.dropped());
    return r.finish(out, key_, keyLen_);
  }
  if (vl == 5 && memcmp(verb, "paper", 5) == 0 && nt == 3) {
    act_.paperAdvance();
    Reply r(out, cap, boot_, id, "ok");
    return r.finish(out, key_, keyLen_);
  }
  if (vl == 3 && memcmp(verb, "set", 3) == 0 && nt > 3) {
    int idx[21];
    int32_t val[21];
    size_t n = 0;
    for (size_t t = 3; t < nt; t++, n++) {           // check everything before changing anything
      const char* eq = (const char*)memchr(tok[t], '=', tlen[t]);
      if (!eq || eq == tok[t]) { Reply r(out, cap, boot_, id, "malformed"); return r.finish(out, key_, keyLen_); }
      char k[24];
      size_t kl = (size_t)(eq - tok[t]);
      if (kl >= sizeof k) kl = sizeof k - 1;
      memcpy(k, tok[t], kl);
      k[kl] = 0;
      idx[n] = Settings::find(k);
      if (idx[n] < 0) { Reply r(out, cap, boot_, id, "unknown_setting", k); return r.finish(out, key_, keyLen_); }
      const SettingDef& d = Settings::def(idx[n]);
      if (!parseI32(eq + 1, tlen[t] - kl - 1, val[n])) { Reply r(out, cap, boot_, id, "malformed"); return r.finish(out, key_, keyLen_); }
      if (val[n] < d.min || val[n] > d.max) { Reply r(out, cap, boot_, id, "out_of_range", k); return r.finish(out, key_, keyLen_); }
    }
    for (size_t t = 0; t < n; t++) st_.set(idx[t], val[t]);
    bool saved = st_.save();
    act_.settingsChanged(st_);
    Reply r(out, cap, boot_, id, saved ? "ok" : "save_failed");
    r.b.add(" k="); r.b.addHex(st_.id(), 8);
    return r.finish(out, key_, keyLen_);
  }
  Reply r(out, cap, boot_, id, "unknown_verb");
  return r.finish(out, key_, keyLen_);
}

bool LineReader::feed(char c) {
  if (ready_) return true;                          // the caller has not taken the last line yet
  if (c == '\r') return false;
  if (c == '\n') {
    if (over_ || n_ == 0) { over_ = false; n_ = 0; buf_[0] = 0; return false; }
    ready_ = true;
    return true;
  }
  if (n_ < Commands::MAX_LINE) { buf_[n_++] = c; buf_[n_] = 0; }
  else over_ = true;
  return false;
}

}  // namespace tgt
