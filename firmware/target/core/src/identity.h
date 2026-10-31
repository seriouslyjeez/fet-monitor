// Who the target is: its key (signs shots and replies, checks commands) and its boot count (shot identity and
// replay protection: every start counts up, so a command recorded in one boot is refused in the next).
//
// Non-volatile layout (addresses from the start of the target's area):
//    0..39   key block: magic "FK", version, length 32, the key, CRC-32
//   40..55   boot count in two slots of 8 bytes (count, CRC-32); a start writes the slot after the newest, so a
//            power cut while counting keeps the previous count (that start never ran)
//   64..     settings (settings.h, two slots)
//
// The key is set over USB only (setupLine(): the platform passes lines from the USB port, never from the WiFi side):
//   FT1 K <key, 64 hex> <CRC-32 of the key bytes, 8 hex>   ->  FT1 K ok fp=<fingerprint>  |  FT1 K err <reason>
//   FT1 I                                                   ->  FT1 I b=<boot> key=<fingerprint|none> fw=<version>
//                                                               rev=<board> k=<settings id>
// The fingerprint is the first 4 bytes of SHA-256(key) in hex: shows which key is in, never the key itself.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "settings.h"

namespace tgt {

class Identity {
 public:
  static const size_t KEY_ADDR = 0, BOOT_ADDR = 40, SETTINGS_ADDR = 64;
  static const size_t KEY_LEN = 32;
  explicit Identity(Nvm& nvm) : nvm_(nvm) {}

  void load();                                    // key (if valid) and the newest boot count
  uint32_t startBoot();                           // count this start; returns the new boot number (0 if not saved)
  uint32_t boot() const { return boot_; }
  bool hasKey() const { return hasKey_; }
  const uint8_t* key() const { return key_; }
  bool setKey(const uint8_t* key);                // write, read back, check
  void fingerprint(char out[9]) const;            // "none" if no key

  // A USB setup line (without newline). Returns the reply length (with "\n"), 0 if the line is not a setup line.
  size_t setupLine(const char* line, const char* fwVersion, uint16_t boardRev, uint32_t settingsId, char* out, size_t cap);

 private:
  bool readBoot(int slot, uint32_t& count);
  Nvm& nvm_;
  uint8_t key_[KEY_LEN] = {0};
  bool hasKey_ = false;
  uint32_t boot_ = 0, newestBoot_ = 0;
  int8_t newestSlot_ = -1;
};

}  // namespace tgt
