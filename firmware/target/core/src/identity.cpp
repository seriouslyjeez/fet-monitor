#include "identity.h"
#include <string.h>
#include "shotline.h"
#include "tgt_sha256.h"

namespace tgt {

static const uint8_t KEY_HDR[4] = {'F', 'K', 1, (uint8_t)Identity::KEY_LEN};
static const size_t KEY_BLOCK = 4 + Identity::KEY_LEN + 4;

static void put32(uint8_t* p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static uint32_t get32(const uint8_t* p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

static int hexv(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;                                      // lower case only: one spelling per key
}

// CRC over "FB", the slot number and the count. Erased memory (all 0xFF) must not pass: the CRC-32 of four 0xFF
// bytes alone IS 0xFFFFFFFF, so a bare CRC would read a blank chip as count 4294967295 (found by the tests).
static uint32_t bootCrc(int slot, const uint8_t* count) {
  uint8_t m[7] = {'F', 'B', (uint8_t)slot, count[0], count[1], count[2], count[3]};
  return crc32(m, sizeof m);
}

bool Identity::readBoot(int slot, uint32_t& count) {
  uint8_t b[8];
  if (!nvm_.read(BOOT_ADDR + 8 * slot, b, 8)) return false;
  if (bootCrc(slot, b) != get32(b + 4)) return false;   // the slot number is in the CRC: no swapping
  count = get32(b);
  return count != 0xFFFFFFFFu;
}

void Identity::load() {
  uint8_t b[KEY_BLOCK];
  hasKey_ = nvm_.read(KEY_ADDR, b, KEY_BLOCK) && memcmp(b, KEY_HDR, 4) == 0 &&
            crc32(b, KEY_BLOCK - 4) == get32(b + KEY_BLOCK - 4);
  if (hasKey_) memcpy(key_, b + 4, KEY_LEN);
  else memset(key_, 0, KEY_LEN);
  uint32_t c0 = 0, c1 = 0;
  bool v0 = readBoot(0, c0), v1 = readBoot(1, c1);
  newestSlot_ = -1;
  newestBoot_ = 0;
  if (v0 || v1) {
    bool use1 = v1 && (!v0 || c1 > c0);
    newestSlot_ = use1 ? 1 : 0;
    newestBoot_ = use1 ? c1 : c0;
  }
  boot_ = newestBoot_;
}

uint32_t Identity::startBoot() {
  uint32_t next = newestBoot_ + 1;
  if (next == 0xFFFFFFFFu) { boot_ = 0; return 0; }   // exhausted (all ones is never a valid count)
  uint8_t b[8];
  put32(b, next);
  int slot = newestSlot_ == 0 ? 1 : 0;
  put32(b + 4, bootCrc(slot, b));
  if (!nvm_.write(BOOT_ADDR + 8 * slot, b, 8)) { boot_ = 0; return 0; }
  uint32_t check;
  if (!readBoot(slot, check) || check != next) { boot_ = 0; return 0; }
  newestSlot_ = (int8_t)slot;
  newestBoot_ = next;
  boot_ = next;
  return next;
}

bool Identity::setKey(const uint8_t* key) {
  uint8_t b[KEY_BLOCK];
  memcpy(b, KEY_HDR, 4);
  memcpy(b + 4, key, KEY_LEN);
  put32(b + KEY_BLOCK - 4, crc32(b, KEY_BLOCK - 4));
  if (!nvm_.write(KEY_ADDR, b, KEY_BLOCK)) return false;
  load();
  return hasKey_ && memcmp(key_, key, KEY_LEN) == 0;
}

void Identity::fingerprint(char out[9]) const {
  if (!hasKey_) { memcpy(out, "none", 5); return; }
  Sha256 h;
  h.update(key_, KEY_LEN);
  uint8_t d[32];
  h.finish(d);
  static const char hx[] = "0123456789abcdef";
  for (int i = 0; i < 4; i++) { out[2 * i] = hx[d[i] >> 4]; out[2 * i + 1] = hx[d[i] & 15]; }
  out[8] = 0;
}

size_t Identity::setupLine(const char* line, const char* fwVersion, uint16_t boardRev, uint32_t settingsId, char* out, size_t cap) {
  LineBuf r(out, cap);
  if (strcmp(line, "FT1 I") == 0) {
    char fp[9];
    fingerprint(fp);
    r.add("FT1 I b="); r.addU(boot_);
    r.add(" key="); r.add(fp);
    r.add(" fw="); r.add(fwVersion);
    r.add(" rev="); r.addU(boardRev);
    r.add(" k="); r.addHex(settingsId, 8);
    r.add("\n");
    return r.overflowed() ? 0 : r.len();
  }
  if (strncmp(line, "FT1 K ", 6) != 0) return 0;
  const char* p = line + 6;
  const char* why = 0;
  uint8_t key[KEY_LEN];
  if (strlen(p) != 2 * KEY_LEN + 1 + 8 || p[2 * KEY_LEN] != ' ') why = "malformed";
  for (size_t i = 0; !why && i < KEY_LEN; i++) {
    int h = hexv(p[2 * i]), l = hexv(p[2 * i + 1]);
    if (h < 0 || l < 0) why = "malformed";
    else key[i] = (uint8_t)(h << 4 | l);
  }
  uint32_t want = 0;
  for (int i = 0; !why && i < 8; i++) {
    int v = hexv(p[2 * KEY_LEN + 1 + i]);
    if (v < 0) why = "malformed";
    else want = want << 4 | (uint32_t)v;
  }
  if (!why && crc32(key, KEY_LEN) != want) why = "checksum";
  if (!why) {
    bool zero = true;
    for (size_t i = 0; i < KEY_LEN; i++) zero = zero && key[i] == 0;
    if (zero) why = "weak_key";
  }
  if (!why && !setKey(key)) why = "not_saved";
  if (why) { r.add("FT1 K err "); r.add(why); }
  else { char fp[9]; fingerprint(fp); r.add("FT1 K ok fp="); r.add(fp); }
  r.add("\n");
  return r.overflowed() ? 0 : r.len();
}

}  // namespace tgt
