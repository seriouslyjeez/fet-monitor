// Compile check only: proves the target core builds with the Mega's toolchain (no STL, no heap) and shows its size.
//   arduino-cli compile -b arduino:avr:mega --library ../../core firmware/target/test/avr_compile
#include <capture.h>
#include <shotline.h>
#include <commands.h>
#include <EEPROM.h>

struct Dummy : tgt::CounterHw {
  uint8_t running() override { return PINC & 0x0F; }
  void stopAndRead(uint32_t out[4]) override { for (int i = 0; i < 4; i++) out[i] = TCNT1 + i; }
  void clearRun() override { PORTA = 0; }
  void arm() override { PORTA = 1; }
};

struct EepromNvm : tgt::Nvm {
  bool read(size_t a, uint8_t* b, size_t n) override { for (size_t i = 0; i < n; i++) b[i] = EEPROM.read(a + i); return true; }
  bool write(size_t a, const uint8_t* b, size_t n) override { for (size_t i = 0; i < n; i++) EEPROM.update(a + i, b[i]); return true; }
};
struct Acts : tgt::Actions {
  void paperAdvance() override { PORTB ^= 1; }
  void settingsChanged(const tgt::Settings& s) override { analogWrite(5, s.get(tgt::S_LED_PCT) * 255 / 100); }
  uint32_t uptimeMs() override { return millis(); }
  uint16_t dropped() override { return 0; }
};

static Dummy hw;
static EepromNvm nvm;
static Acts acts;
static tgt::Settings settings(nvm, 64);
static tgt::Commands commands(settings, acts, 1);
static tgt::LineReader reader;
static tgt::Capture cap(hw);
static const uint8_t key[32] = {1};

void setup() { Serial.begin(115200); settings.load(); commands.setKey(key, sizeof key); cap.begin(millis()); }

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
  while (Serial.available()) {
    if (reader.feed((char)Serial.read())) {
      char reply[320];
      size_t n = commands.handle(reader.line(), reply, sizeof reply);
      Serial.write(reply, n);
      reader.next();
    }
  }
}
