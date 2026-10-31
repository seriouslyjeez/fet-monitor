// FeT target firmware for the Arduino Mega 2560 on a freETarget-compatible board v3.x (built for 3.1).
//
// The board does the timing: a RUN latch and a 16-bit counter (8 MHz) per microphone. This sketch is the platform
// layer around firmware/target/core: it reads those counters for Capture (1 kHz Timer1 interrupt), sends every
// shot as a signed line with the raw times (the server computes the position), and carries out signed commands
// (settings, paper). Pins and the counter read sequence are the ones upstream freETarget uses on this board
// (gpio.h / gpio.ino, MIT, "Copyright 2020, ten-point-nine project").
//
// Ports: Serial (USB) and Serial3 (the WiFi module, FeT Bridge) both get every line and may send commands; the key
// can be set ONLY on USB ("FT1 K ..."). Lines never contain braces.
//
// Build: arduino-cli compile -b arduino:avr:mega --library firmware/target/core firmware/target/mega/fet_target_mega
// NOT YET RUN ON A BOARD.
#include <EEPROM.h>
#include <Wire.h>
#include <capture.h>
#include <commands.h>
#include <identity.h>
#include <paper.h>
#include <settings.h>
#include <shotline.h>

#define FW_VERSION "0.1.0"
#define USE_WATCHDOG 0   // off until the board's bootloader is known to clear the watchdog (old Mega bootloaders
                         // restart forever when it fires, and then cannot be flashed over USB)

// ---------------------------------------------------------------------------------------------- pins (board 3.x)
static const uint8_t DATA_PINS[8] = {30, 31, 32, 33, 34, 35, 36, 37};      // D7 .. D0 (read MSB first)
static const uint8_t ADDR_PINS[8] = {50, 51, 48, 49, 43, 47, 41, 42};      // N hi/lo, E hi/lo, S hi/lo, W hi/lo
static const uint8_t PIN_QUIET = 29, PIN_RCLK = 40, PIN_CLR_N = 39, PIN_STOP_N = 52, PIN_CLOCK_START = 53;
static const uint8_t PIN_SW_A = 11, PIN_SW_B = 10;                         // multifunction switches (DIP_2, DIP_1)
static const uint8_t PIN_LED_RDY = 4, PIN_LED_X = 3, PIN_LED_Y = 2;        // status LEDs, lit when LOW
static const uint8_t PIN_LED_PWM = 5;                                      // face lighting
static const uint8_t PIN_PAPER = 18;                                       // paper motor FET
static const uint8_t PIN_FACE = 19;                                        // face sensor (INT)
static const uint8_t PIN_BD_REV = A5, PIN_VREF = A0;
static const uint8_t TEMP_I2C = 0x9E >> 1;                                 // LM75

// ---------------------------------------------------------------------------------------------- the board
struct MegaCounters : tgt::CounterHw {
  uint8_t running() override { return (PINA >> 3) & 0x0F; }                // PA3..PA6 = N E S W
  void stopAndRead(uint32_t out[4]) override {
    digitalWrite(PIN_STOP_N, 0);                                           // stop the counters
    digitalWrite(PIN_QUIET, 0);                                            // and the oscillator
    for (uint8_t i = 0; i < 8; i++) digitalWrite(ADDR_PINS[i], 1);
    digitalWrite(PIN_RCLK, 1);                                             // latch the counts
    for (uint8_t d = 0; d < 4; d++) {
      uint16_t hi = readByte(ADDR_PINS[2 * d]), lo = readByte(ADDR_PINS[2 * d + 1]);
      out[d] = (uint32_t)hi << 8 | lo;
    }
  }
  void clearRun() override { digitalWrite(PIN_STOP_N, 0); digitalWrite(PIN_STOP_N, 1); }
  void arm() override {
    digitalWrite(PIN_CLOCK_START, 0);
    digitalWrite(PIN_STOP_N, 0);
    digitalWrite(PIN_RCLK, 0);
    digitalWrite(PIN_QUIET, 1);
    digitalWrite(PIN_CLR_N, 0);
    digitalWrite(PIN_CLR_N, 1);
    digitalWrite(PIN_STOP_N, 1);
  }

 private:
  static uint8_t readByte(uint8_t select) {
    digitalWrite(select, 0);
    uint8_t v = 0;
    for (uint8_t i = 0; i < 8; i++) v = (uint8_t)(v << 1 | (digitalRead(DATA_PINS[i]) & 1));
    digitalWrite(select, 1);
    return v;
  }
};

struct EepromNvm : tgt::Nvm {
  bool read(size_t a, uint8_t* b, size_t n) override {
    if (a + n > EEPROM.length()) return false;
    for (size_t i = 0; i < n; i++) b[i] = EEPROM.read(a + i);
    return true;
  }
  bool write(size_t a, const uint8_t* b, size_t n) override {               // update(): only changed bytes wear
    if (a + n > EEPROM.length()) return false;
    for (size_t i = 0; i < n; i++) EEPROM.update(a + i, b[i]);
    return true;
  }
};

static uint16_t boardRev = 0;
struct PaperFet : tgt::MotorOut {
  void motor(bool on) override { digitalWrite(PIN_PAPER, boardRev >= 300 ? on : !on); }   // 3.0 reversed the sense
};

static uint16_t readBoardRev() {                                          // upstream analog_io.ino revision()
  static const uint16_t table[16] = {210, 1, 2, 3, 320, 5, 6, 220, 8, 9, 10, 310, 12, 13, 14, 15};
  uint16_t r = table[analogRead(PIN_BD_REV) * 16 / 1024];
  return r <= 100 ? 300 : r;
}

static int16_t readTempDeci() {                                           // LM75, 0.5 C steps; NO_TEMP if absent
  Wire.beginTransmission(TEMP_I2C);
  Wire.write(0);
  if (Wire.endTransmission() != 0) return tgt::NO_TEMP;
  if (Wire.requestFrom((int)TEMP_I2C, 2) != 2) return tgt::NO_TEMP;
  int16_t raw = (int16_t)((uint16_t)Wire.read() << 8 | (uint16_t)Wire.read());
  return (int16_t)((raw >> 7) * 5);                                        // arithmetic shift keeps the sign
}

// ---------------------------------------------------------------------------------------------- the core
static MegaCounters counters;
static EepromNvm nvm;
static PaperFet paperFet;
static tgt::Capture capture(counters);
static tgt::Identity identity(nvm);
static tgt::Settings settings(nvm, tgt::Identity::SETTINGS_ADDR);
static tgt::PaperDrive paper(paperFet);

struct MegaActions : tgt::Actions {
  void paperAdvance() override { paper.request(); }
  void settingsChanged(const tgt::Settings& s) override;
  uint32_t uptimeMs() override { return millis(); }
  uint16_t dropped() override { return capture.dropped(); }
};
static MegaActions actions;
static tgt::Commands* commands = 0;                                        // made once the boot count is known
static tgt::LineReader usbIn, wifiIn;
static volatile uint32_t msTick = 0;
static int16_t tempDeci = tgt::NO_TEMP;

static void faceEdge() { capture.face(micros()); }

void MegaActions::settingsChanged(const tgt::Settings& s) {
  analogWrite(PIN_LED_PWM, (int)(s.get(tgt::S_LED_PCT) * 255L / 100));
  paper.configure((uint16_t)s.get(tgt::S_PAPER_MS), (uint16_t)s.get(tgt::S_STEP_COUNT), (uint16_t)s.get(tgt::S_STEP_MS));
  noInterrupts();                                                          // the interrupt reads these
  capture.waitMs = (uint16_t)s.get(tgt::S_WAIT_MS);
  capture.ringMs = (uint16_t)s.get(tgt::S_RING_MS);
  capture.faceAloneMs = (uint16_t)s.get(tgt::S_FACE_ALONE_MS);
  interrupts();
  if (s.get(tgt::S_FACE)) attachInterrupt(digitalPinToInterrupt(PIN_FACE), faceEdge, CHANGE);
  else detachInterrupt(digitalPinToInterrupt(PIN_FACE));
  // S_VREF_MV: board 3.1 sets the threshold with a trimmer (RV1); nothing to do here. It is read and reported.
}

ISR(TIMER1_COMPA_vect) {                                                  // 1 kHz: Capture's clock
  uint32_t ms = ++msTick;
  capture.tick(ms, micros());
}

static void sendAll(const char* line, size_t n) {
  if (!n) return;
  Serial.write(line, n);
  Serial3.write(line, n);
}

static void sendHello() {
  char out[tgt::SHOT_LINE_MAX];
  tgt::LineBuf b(out, sizeof out);
  char fp[9];
  identity.fingerprint(fp);
  b.add("FT1 H b="); b.addU(identity.boot());
  b.add(" fw="); b.add(FW_VERSION);
  b.add(" rev="); b.addU(boardRev);
  b.add(" k="); b.addHex(settings.id(), 8);
  b.add(" key="); b.add(fp);
  sendAll(out, tgt::signLine(b, out, identity.hasKey() ? identity.key() : 0, tgt::Identity::KEY_LEN));
}

static void handleLine(const char* line, bool usb) {
  char out[320];
  size_t n = 0;
  if (usb) {
    n = identity.setupLine(line, FW_VERSION, boardRev, settings.id(), out, sizeof out);
    if (n) {
      if (identity.hasKey()) commands->setKey(identity.key(), tgt::Identity::KEY_LEN);
      Serial.write(out, n);                                                // setup replies go back to USB only
      return;
    }
  }
  n = commands->handle(line, out, sizeof out);
  if (n) (usb ? (Stream&)Serial : (Stream&)Serial3).write(out, n);
}

static void pollPort(Stream& port, tgt::LineReader& reader, bool usb) {
  while (port.available()) {
    if (reader.feed((char)port.read())) { handleLine(reader.line(), usb); reader.next(); }
  }
}

// multifunction switches: A pressed = advance the paper once; B pressed = lights on/off
static void pollSwitches(uint32_t now) {
  static uint8_t last = 0;
  static uint32_t since = 0;
  static bool lightsOff = false;
  uint8_t sw = (uint8_t)((digitalRead(PIN_SW_A) == 0) | (digitalRead(PIN_SW_B) == 0) << 1);
  if (sw == last) { since = now; return; }
  if (now - since < 50) return;                                            // debounce
  uint8_t pressed = (uint8_t)(sw & ~last);
  last = sw;
  if (pressed & 1) paper.request();
  if (pressed & 2) {
    lightsOff = !lightsOff;
    analogWrite(PIN_LED_PWM, lightsOff ? 0 : (int)(settings.get(tgt::S_LED_PCT) * 255L / 100));
  }
}

void setup() {
  Serial.begin(115200);
  Serial3.begin(115200);
  Wire.begin();
  Wire.setWireTimeout(3000, true);                                         // a stuck I2C bus must not stop the target
  for (uint8_t i = 0; i < 8; i++) { pinMode(DATA_PINS[i], INPUT_PULLUP); pinMode(ADDR_PINS[i], OUTPUT); digitalWrite(ADDR_PINS[i], 1); }
  for (uint8_t p = 25; p <= 28; p++) pinMode(p, INPUT_PULLUP);             // RUN N E S W
  const uint8_t outs[] = {PIN_QUIET, PIN_RCLK, PIN_CLR_N, PIN_STOP_N};
  for (uint8_t i = 0; i < sizeof outs; i++) { pinMode(outs[i], OUTPUT); digitalWrite(outs[i], 1); }
  pinMode(PIN_CLOCK_START, OUTPUT); digitalWrite(PIN_CLOCK_START, 0);
  pinMode(PIN_SW_A, INPUT_PULLUP); pinMode(PIN_SW_B, INPUT_PULLUP);
  pinMode(PIN_LED_RDY, OUTPUT); pinMode(PIN_LED_X, OUTPUT); pinMode(PIN_LED_Y, OUTPUT);
  digitalWrite(PIN_LED_RDY, 1); digitalWrite(PIN_LED_X, 0); digitalWrite(PIN_LED_Y, 1);   // X on: starting
  pinMode(PIN_LED_PWM, OUTPUT); analogWrite(PIN_LED_PWM, 0);
  pinMode(PIN_FACE, INPUT_PULLUP);
  boardRev = readBoardRev();
  pinMode(PIN_PAPER, OUTPUT); paperFet.motor(false);

  identity.load();
  identity.startBoot();                                                    // 0 if it could not be counted: commands refuse b=0
  settings.load();
  static tgt::Commands cmds(settings, actions, identity.boot());
  commands = &cmds;
  if (identity.hasKey()) commands->setKey(identity.key(), tgt::Identity::KEY_LEN);
  actions.settingsChanged(settings);
  tempDeci = readTempDeci();

  noInterrupts();                                                          // Timer1: CTC, /64, 1 kHz
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;
  OCR1A = 16000000UL / 64 / 1000 - 1;
  TCCR1B = (1 << WGM12) | (1 << CS11) | (1 << CS10);
  TIMSK1 = (1 << OCIE1A);
  capture.begin(0);
  interrupts();

  digitalWrite(PIN_LED_X, 1);
  digitalWrite(PIN_LED_RDY, 0);                                            // ready
  sendHello();
}

void loop() {
  uint32_t now = millis();
  tgt::Shot s;
  for (;;) {
    noInterrupts();
    bool got = capture.pop(s);
    interrupts();
    if (!got) break;
    char out[tgt::SHOT_LINE_MAX];
    size_t n = tgt::formatShot(s, identity.boot(), tempDeci, settings.id(), capture.dropped(),
                               identity.hasKey() ? identity.key() : 0, tgt::Identity::KEY_LEN, out, sizeof out);
    sendAll(out, n);
    if (s.mask && settings.get(tgt::S_PAPER_ECO_MM) == 0) paper.request();   // eco: the server asks (it knows where)
  }
  pollPort(Serial, usbIn, true);
  pollPort(Serial3, wifiIn, false);
  pollSwitches(now);
  paper.tick(now);
  static uint32_t lastTemp = 0;
  if (now - lastTemp >= 1000) { lastTemp = now; tempDeci = readTempDeci(); }
}
