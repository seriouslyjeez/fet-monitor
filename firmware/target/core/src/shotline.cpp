#include "shotline.h"
#include "tgt_sha256.h"

namespace tgt {

static const char HEX[] = "0123456789abcdef";

void LineBuf::put(char c) {
  if (n_ + 1 < cap_) { s_[n_++] = c; s_[n_] = 0; }
  else over_ = true;
}

void LineBuf::add(const char* t) {
  while (*t) put(*t++);
}

void LineBuf::addU(uint32_t v) {
  char d[10];
  int k = 0;
  do { d[k++] = (char)('0' + v % 10); v /= 10; } while (v);
  while (k) put(d[--k]);
}

void LineBuf::addI(int32_t v) {
  if (v < 0) { put('-'); addU((uint32_t)0 - (uint32_t)v); }   // also right for INT32_MIN
  else addU((uint32_t)v);
}

void LineBuf::addHex(uint32_t v, int digits) {
  for (int i = digits - 1; i >= 0; i--) put(HEX[(v >> (4 * i)) & 15]);
}

size_t formatShot(const Shot& s, uint32_t boot, int16_t tempDeci, uint32_t settingsId, uint16_t dropped,
                  const uint8_t* key, size_t keyLen, char* out, size_t cap) {
  LineBuf b(out, cap);
  b.add("FT1 S b="); b.addU(boot);
  b.add(" q="); b.addU(s.seq);
  b.add(" t="); b.addU(s.ms);
  static const char names[] = "nesw";
  for (int i = 0; i < 4; i++) {
    char k[4] = {' ', names[i], '=', 0};
    b.add(k);
    b.addU(s.count[i]);
  }
  b.add(" m="); b.addHex(s.mask, 1);
  b.add(" f=");
  if (s.hasFace) b.addI(s.faceUs); else b.add("-");
  b.add(" c=");
  if (tempDeci == NO_TEMP) b.add("-"); else b.addI(tempDeci);
  b.add(" k="); b.addHex(settingsId, 8);
  b.add(" d="); b.addU(dropped);
  return signLine(b, out, key, keyLen);
}

size_t signLine(LineBuf& b, char* out, const uint8_t* key, size_t keyLen) {
  size_t signedLen = b.len();
  b.add(" h=");
  if (key) {
    uint8_t mac[32];
    hmacSha256(key, keyLen, (const uint8_t*)out, signedLen, mac);
    for (int i = 0; i < 8; i++) { char h[3] = {HEX[mac[i] >> 4], HEX[mac[i] & 15], 0}; b.add(h); }
  } else {
    b.add("-");
  }
  b.add("\n");
  return b.overflowed() ? 0 : b.len();
}

}  // namespace tgt
