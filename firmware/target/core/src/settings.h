// The target's own settings: what it does physically (paper, LEDs, capture timing, face sensor, threshold). Where
// the microphones are and everything else the position depends on is calibration on the server (it computes the
// position from the raw times), not here.
//
// Every setting is an integer with a range and a default. They are stored as one record in TWO slots of
// non-volatile memory, each with a generation number and a CRC-32: a save writes the slot that is not the newest,
// so a power cut during a save leaves the previous settings intact. load() takes the valid slot with the higher
// generation (wrapping), or the defaults if neither is valid. Saving happens only when something changed (EEPROM
// cells wear out). id() is the CRC-32 of the values: every shot carries it, so the server knows which settings were
// in force.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace tgt {

struct Nvm {                                       // EEPROM on the Mega, NVS/flash on the ESP32, a file on the PC
  virtual bool read(size_t addr, uint8_t* buf, size_t n) = 0;
  virtual bool write(size_t addr, const uint8_t* buf, size_t n) = 0;
};

struct SettingDef {
  const char* key;
  int32_t min, max, def;
};

enum SettingIndex {
  S_PAPER_MS,        // DC motor: on-time per advance, ms (0 = no paper drive, or a stepper)
  S_STEP_COUNT,      // stepper: steps per advance (0 = DC motor)
  S_STEP_MS,         // stepper: pulse length, ms
  S_PAPER_ECO_MM,    // advance only when the shot is within this radius, mm (0 = always); needs the server's verdict
  S_LED_PCT,         // face lighting, %
  S_WAIT_MS,         // longest spread between first and last microphone (+1 tick)
  S_RING_MS,         // quiet time before re-arming
  S_FACE,            // face sensor fitted and used (0/1)
  S_FACE_ALONE_MS,   // a face edge with no microphone after this long is reported on its own
  S_VREF_MV,         // microphone threshold, mV (boards that can set it; 0 = leave the board's own)
  S_COUNT
};

class Settings {
 public:
  static const uint16_t MAGIC = 0x4654;           // "FT"
  static const uint8_t VERSION = 1;
  static const size_t SLOT_SIZE = 8 + 4 * S_COUNT + 4;   // magic, version, count, generation, values, CRC

  Settings(Nvm& nvm, size_t base) : nvm_(nvm), base_(base) { defaults(); }
  static const SettingDef& def(int i);
  static int find(const char* key);               // index, or -1

  void defaults();
  bool load();                                    // false: neither slot valid, defaults in force
  bool save();                                    // only if changed since load/save; false if the write failed
  int32_t get(int i) const { return v_[i]; }
  // Set one value. Returns 0, or a reason (static text) and changes nothing.
  const char* set(int i, int32_t value);
  uint32_t id() const;                            // CRC-32 of the values
  bool dirty() const { return dirty_; }
  uint16_t generation() const { return gen_; }

 private:
  bool readSlot(int slot, int32_t* vals, uint16_t& gen);
  Nvm& nvm_;
  size_t base_;
  int32_t v_[S_COUNT];
  uint16_t gen_ = 0;
  int8_t newest_ = -1;                            // slot holding the newest valid record, -1 none
  bool dirty_ = false;
};

uint32_t crc32(const uint8_t* p, size_t n, uint32_t crc = 0);

}  // namespace tgt
