// Compile check only: proves the target core builds with the Mega's toolchain (no STL, no heap) and shows its size.
//   arduino-cli compile -b arduino:avr:mega --library ../../core firmware/target/test/avr_compile
#include <capture.h>
#include <shotline.h>

struct Dummy : tgt::CounterHw {
  uint8_t running() override { return PINC & 0x0F; }
  void stopAndRead(uint32_t out[4]) override { for (int i = 0; i < 4; i++) out[i] = TCNT1 + i; }
  void clearRun() override { PORTA = 0; }
  void arm() override { PORTA = 1; }
};

static Dummy hw;
static tgt::Capture cap(hw);
static const uint8_t key[32] = {1};

void setup() { Serial.begin(115200); cap.begin(millis()); }

void loop() {
  cap.tick(millis(), micros());
  tgt::Shot s;
  noInterrupts();
  bool got = cap.pop(s);
  interrupts();
  if (got) {
    char line[tgt::SHOT_LINE_MAX];
    size_t n = tgt::formatShot(s, 1, 200, 0x12345678, cap.dropped(), key, sizeof key, line, sizeof line);
    Serial.write(line, n);
  }
}
