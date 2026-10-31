#include "settings.h"
#include <string.h>

namespace tgt {

static const SettingDef DEFS[S_COUNT] = {
    {"paper_ms", 0, 5000, 0},
    {"step_count", 0, 2000, 0},
    {"step_ms", 0, 100, 0},
    {"paper_eco_mm", 0, 100, 0},
    {"led_pct", 0, 100, 50},
    {"wait_ms", 1, 20, 2},
    {"ring_ms", 10, 2000, 50},
    {"face", 0, 1, 0},
    {"face_alone_ms", 1, 100, 5},
    {"vref_mv", 0, 3300, 0},
};

uint32_t crc32(const uint8_t* p, size_t n, uint32_t crc) {
  crc = ~crc;
  for (size_t i = 0; i < n; i++) {
    crc ^= p[i];
    for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

static void put32(uint8_t* p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static uint32_t get32(const uint8_t* p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

const SettingDef& Settings::def(int i) { return DEFS[i]; }

int Settings::find(const char* key) {
  for (int i = 0; i < S_COUNT; i++)
    if (strcmp(DEFS[i].key, key) == 0) return i;
  return -1;
}

void Settings::defaults() {
  for (int i = 0; i < S_COUNT; i++) v_[i] = DEFS[i].def;
}

const char* Settings::set(int i, int32_t value) {
  if (i < 0 || i >= S_COUNT) return "unknown setting";
  if (value < DEFS[i].min || value > DEFS[i].max) return "out of range";
  if (v_[i] != value) { v_[i] = value; dirty_ = true; }
  return 0;
}

uint32_t Settings::id() const {
  uint8_t b[4 * S_COUNT];
  for (int i = 0; i < S_COUNT; i++) put32(b + 4 * i, (uint32_t)v_[i]);
  return crc32(b, sizeof b);
}

bool Settings::readSlot(int slot, int32_t* vals, uint16_t& gen) {
  uint8_t b[SLOT_SIZE];
  if (!nvm_.read(base_ + slot * SLOT_SIZE, b, SLOT_SIZE)) return false;
  if ((uint16_t)(b[0] | b[1] << 8) != MAGIC || b[2] != VERSION || b[3] != S_COUNT) return false;
  if (crc32(b, SLOT_SIZE - 4) != get32(b + SLOT_SIZE - 4)) return false;
  gen = (uint16_t)(b[4] | b[5] << 8);
  for (int i = 0; i < S_COUNT; i++) {
    int32_t v = (int32_t)get32(b + 8 + 4 * i);
    if (v < DEFS[i].min || v > DEFS[i].max) return false;    // a value this firmware cannot accept: not usable
    vals[i] = v;
  }
  return true;
}

bool Settings::load() {
  int32_t a[S_COUNT], b[S_COUNT];
  uint16_t ga = 0, gb = 0;
  bool va = readSlot(0, a, ga), vb = readSlot(1, b, gb);
  dirty_ = false;
  if (!va && !vb) { defaults(); newest_ = -1; gen_ = 0; return false; }
  bool useB = vb && (!va || (int16_t)(gb - ga) > 0);           // newer generation, wrapping
  memcpy(v_, useB ? b : a, sizeof v_);
  gen_ = useB ? gb : ga;
  newest_ = useB ? 1 : 0;
  return true;
}

bool Settings::save() {
  if (!dirty_ && newest_ >= 0) return true;
  uint8_t b[SLOT_SIZE];
  uint16_t gen = (uint16_t)(gen_ + 1);
  b[0] = (uint8_t)MAGIC; b[1] = (uint8_t)(MAGIC >> 8); b[2] = VERSION; b[3] = S_COUNT;
  b[4] = (uint8_t)gen; b[5] = (uint8_t)(gen >> 8); b[6] = 0; b[7] = 0;
  for (int i = 0; i < S_COUNT; i++) put32(b + 8 + 4 * i, (uint32_t)v_[i]);
  put32(b + SLOT_SIZE - 4, crc32(b, SLOT_SIZE - 4));
  int slot = newest_ == 0 ? 1 : 0;                              // never overwrite the newest good copy
  if (!nvm_.write(base_ + slot * SLOT_SIZE, b, SLOT_SIZE)) return false;
  newest_ = (int8_t)slot;
  gen_ = gen;
  dirty_ = false;
  return true;
}

}  // namespace tgt
