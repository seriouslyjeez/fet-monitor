// Turns the Arduino's serial output into records worth sending.
//   * a JSON object (possibly spread over several lines, e.g. the ECHO reply) is one record;
//       containing "shot"  -> type "shot" (hits and misses)
//       containing "NAME"  -> type "config" (the ECHO reply)
//       anything else      -> dropped
//   * a text line starting with freETarget (the boot banner) -> type "arduino_boot"
//   * a line from the FeT target firmware (firmware/target): "FT1 S ..." -> "target_shot", "FT1 H ..." ->
//     "target_hello", "FT1 R ..." -> "target_reply", passed on byte for byte (they are signed: the server checks)
//   * everything else (the +++ / AT probes, noise) is ignored, so we never answer the Arduino's AT probe.
// Objects cut short by a silence, or longer than MAX_RECORD, are discarded and counted. A text line longer than
// MAX_LINE is dropped whole (a cut line would only fail its signature check).
#pragma once
#include <cstdint>
#include <string>

namespace fet {

struct RecordSink {
  virtual ~RecordSink() {}
  virtual void onRecord(const char* type, const std::string& text) = 0;
};

class Assembler {
 public:
  static const size_t MAX_RECORD = 4096;
  static const uint32_t SILENCE_MS = 3000;
  static const size_t MAX_LINE = 400;
  explicit Assembler(RecordSink& sink) : sink_(sink) {}
  void feed(uint8_t c, uint64_t now);
  void tick(uint64_t now);                  // drops an unfinished object after SILENCE_MS without bytes
  uint32_t discarded() const { return discarded_; }    // objects abandoned (too long, cut short, unbalanced)
  uint32_t ignoredObjects() const { return ignoredObjects_; }
  uint32_t ignoredLines() const { return ignoredLines_; }
 private:
  void finishObject();
  void finishLine();
  RecordSink& sink_;
  std::string obj_, line_;
  int depth_ = 0;
  bool inStr_ = false, esc_ = false, lineOver_ = false;
  uint64_t last_ = 0;
  uint32_t discarded_ = 0, ignoredObjects_ = 0, ignoredLines_ = 0;
};

}  // namespace fet
