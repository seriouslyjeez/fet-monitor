#include "provision.h"
#include "config.h"
#include "crc32.h"

namespace fet {

static const char PREFIX[] = "FETCFG ";
static const size_t PREFIX_LEN = sizeof(PREFIX) - 1;

static int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

void Provision::reply(const std::string& s) {
  std::string o = "FETCFG ";
  for (size_t i = 0; i < s.size(); i++) {         // never a brace or a line break towards the Arduino
    char c = s[i];
    o += (c == '{' || c == '}' || c == '\n' || c == '\r') ? ' ' : c;
  }
  uart_.write(o + "\n");
}

void Provision::feed(uint8_t c, uint64_t now, std::string& passOn) {
  bool open = now < WINDOW_MS;
  if (!open && !swallowing_) {                     // the window has closed: anything held belongs to the bridge
    if (!held_.empty()) { passOn += held_; held_.clear(); }
    passOn += (char)c;
    return;
  }
  if (passing_) {
    passOn += (char)c;
    if (c == '\n') passing_ = false;
    return;
  }
  if (swallowing_) {
    if (c == '\n') {
      if (tooLong_) reply("ERROR line too long");
      else handle(held_.substr(PREFIX_LEN));
      held_.clear();
      swallowing_ = tooLong_ = false;
    } else if (c != '\r') {
      if (held_.size() < MAX_LINE) held_ += (char)c;
      else tooLong_ = true;
    }
    return;
  }
  held_ += (char)c;                                // deciding: does this line start with "FETCFG "?
  if (held_.size() <= PREFIX_LEN && held_.compare(0, held_.size(), PREFIX, held_.size()) == 0) {
    if (held_.size() == PREFIX_LEN) swallowing_ = true;
    return;
  }
  passOn += held_;                                 // not ours: everything held goes on, in order
  held_.clear();
  passing_ = c != '\n';
}

void Provision::handle(const std::string& body) {
  if (body == "HELLO") { reply("HELLO " + id_ + " " + fw_); return; }
  size_t sp = body.find(' ');
  if (sp == std::string::npos || body.size() - sp - 1 != 8 || sp % 2 != 0 || sp == 0) { reply("ERROR not a settings line"); return; }
  std::string json;
  for (size_t i = 0; i < sp; i += 2) {
    int h = hexval(body[i]), l = hexval(body[i + 1]);
    if (h < 0 || l < 0) { reply("ERROR not a settings line"); return; }
    json += (char)(h * 16 + l);
  }
  uint32_t want = 0;
  for (size_t i = sp + 1; i < body.size(); i++) {
    int v = hexval(body[i]);
    if (v < 0) { reply("ERROR not a settings line"); return; }
    want = (want << 4) | (uint32_t)v;
  }
  if (crc32(json.data(), json.size()) != want) { reply("ERROR checksum mismatch: send it again"); return; }
  Config c;
  if (!configFromJson(json, c)) { reply("ERROR the settings are not valid JSON"); return; }
  std::string why = validateConfig(c);
  if (!why.empty()) { reply("ERROR " + why); return; }
  if (!saveConfig(fs_, c)) { reply("ERROR the settings could not be saved"); return; }
  QuickBoot::forget(fs_);                          // flashing, this restart and a port reopen must not open the portal
  reply("OK " + id_);
  hooks_.reboot();                                 // start again with the new settings
}

}  // namespace fet
