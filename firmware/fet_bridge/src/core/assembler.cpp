#include "assembler.h"

namespace fet {

void Assembler::feed(uint8_t c, uint64_t now) {
  last_ = now;
  if (c >= 0x80) c = '?';                                  // the Arduino speaks ASCII; anything else is line noise
  if (depth_ > 0) {                                        // inside an object
    if (c == '\r') return;
    if (obj_.size() >= MAX_RECORD) {                       // runaway: give up on it and resynchronise
      discarded_++;
      obj_.clear(); depth_ = 0; inStr_ = esc_ = false;
      return;
    }
    obj_ += (char)c;
    if (inStr_) {
      if (esc_) esc_ = false;
      else if (c == '\\') esc_ = true;
      else if (c == '"') inStr_ = false;
      return;
    }
    if (c == '"') inStr_ = true;
    else if (c == '{') depth_++;
    else if (c == '}') { if (--depth_ == 0) finishObject(); }
    return;
  }
  if (c == '{') {                                          // an object starts
    line_.clear();
    obj_.assign(1, '{');
    depth_ = 1; inStr_ = esc_ = false;
    return;
  }
  if (c == '\n') { finishLine(); return; }
  if (c == '\r') return;
  if (line_.size() < 200) line_ += (char)c;
}

void Assembler::finishObject() {
  std::string text;
  text.swap(obj_);
  inStr_ = esc_ = false;
  if (text.find("\"shot\"") != std::string::npos) sink_.onRecord("shot", text);
  else if (text.find("\"NAME\"") != std::string::npos) sink_.onRecord("config", text);
  else ignoredObjects_++;
}

void Assembler::finishLine() {
  std::string l;
  l.swap(line_);
  size_t s = 0;
  while (s < l.size() && (l[s] == ' ' || l[s] == '\t')) s++;
  if (l.compare(s, 10, "freETarget") == 0) sink_.onRecord("arduino_boot", l.substr(s));
  else if (!l.empty()) ignoredLines_++;
}

void Assembler::tick(uint64_t now) {
  if (depth_ > 0 && now - last_ > SILENCE_MS) {
    discarded_++;
    obj_.clear(); depth_ = 0; inStr_ = esc_ = false;
  }
  if (depth_ == 0 && !line_.empty() && now - last_ > SILENCE_MS) { line_.clear(); }   // a stray partial line
}

}  // namespace fet
