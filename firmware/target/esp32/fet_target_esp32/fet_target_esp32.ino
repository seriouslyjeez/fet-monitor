// FeT target firmware for the ESP32-S3 board "V6.2F NGT" (freETarget-compatible, upstream revision 620).
//
// The board does the timing: a RUN flip-flop per microphone gates one of the ESP32-S3's pulse counters (PCNT), which
// count a 10 MHz reference oscillator from the moment that microphone heard the shot until the counters are stopped.
// This sketch is the platform layer around firmware/target/core, as the Mega's is: Capture (1 kHz esp_timer) reads
// those counters, every shot goes out as a signed line with the raw times (the server computes the position), and
// signed commands change the settings or move the paper. Pins, polarities and the arm/stop sequence are upstream
// freETarget's for this board (Software/ESP32 6.4.2 drivers/gpio_define.c, gpio.c, dac.c, analog_io.c; MIT,
// "Copyright 2020, ten-point-nine project").
//
// Phase E1 (this file): the hardware layer; lines go to the USB port (UART0, the board's FTDI). The WiFi side (the
// FeT Bridge core built in, so the target talks to the Pi directly) follows in E2.
//
// Build: arduino-cli compile -b esp32:esp32:esp32s3:FlashSize=8M,PartitionScheme=default_8MB,FlashMode=dio
//        --library firmware/target/core firmware/target/esp32/fet_target_esp32
// The board's USB link is reliable only up to 230400 baud for flashing (2026-11-02).
#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <esp_timer.h>
#include <driver/pulse_cnt.h>
#include <freertos/semphr.h>
#include <capture.h>
#include <commands.h>
#include <identity.h>
#include <paper.h>
#include <settings.h>
#include <shotline.h>

#define FW_VERSION "0.1.0"
// In the flash image, so the Pi's flash station can tell what an image is and what a board runs.
extern const char FW_ID[] __attribute__((used)) = "fet-target-esp32/" FW_VERSION;
static const uint16_t BOARD_REV = 620;

// ---------------------------------------------------------------------------------------------- pins (board 6.2)
static const int PIN_RUN[4] = {5, 6, 7, 15};                               // RUN_NORTH/EAST/SOUTH/WEST_LO: PCNT gates
static const int PIN_REF_CLK = 8;                                          // 10 MHz reference: what the PCNTs count
static const int PIN_STOP_N = 21;                                          // 0 clears the RUN flip-flops, 1 lets them run
static const int PIN_OSC = 48;                                             // 1 oscillator on
static const int PIN_CLK_START = 47;                                       // diagnostic trigger, kept 0
static const int PIN_LDAC_N = 41;                                          // DAC load: not used, kept 1
static const int PIN_RS485 = 9;                                            // MAX485 driver enable: kept 0 (not used)
static const int PIN_PAPER = 12;                                           // 1 motor on
static const int PIN_LED_PWM = 2;                                          // face lights
static const int PIN_STATUS = 45;                                          // 3 WS2812 status LEDs (V12, COM, RDY)
static const int PIN_SDA = 14, PIN_SCL = 13;
static const int PIN_V12 = 1, PIN_BD_REV = 4, PIN_VREF_LO = 19;            // ADC
static const int PIN_SW_A = 38, PIN_SW_B = 37;                             // switch inputs, pressed = 0
static const uint8_t DAC_I2C = 0x60, TEMP_I2C = 0x48;                      // MCP4725, TMP1075
static const float DAC_FULL_MV = 4750.0f;                                  // 5 V less the diode drop (upstream dac.c)
static const int32_t VREF_DEFAULT_MV = 1250;                               // upstream VREF_LO default
static const int32_t PAPER_DEFAULT_MS = 500;                               // upstream PAPER_TIME default on this board
// Sound needs about 676 us across the 232 mm sensor circle at 20 C (700 us at 0 C): 10 MHz ticks, with margin.
static const uint32_t MAX_SPREAD_TICKS = 7500;                             // 750 us

// ---------------------------------------------------------------------------------------------- the board
static pcnt_unit_handle_t pcnt[4];

static bool pcntBegin() {
  for (int i = 0; i < 4; i++) {
    pcnt_unit_config_t uc = {};
    uc.low_limit = -1000;
    uc.high_limit = 30000;
    uc.flags.accum_count = 1;                                              // beyond 16 bits: counted on in software
    if (pcnt_new_unit(&uc, &pcnt[i]) != ESP_OK) return false;
    pcnt_chan_config_t cc = {};
    cc.edge_gpio_num = PIN_REF_CLK;
    cc.level_gpio_num = PIN_RUN[i];
    pcnt_channel_handle_t ch;
    if (pcnt_new_channel(pcnt[i], &cc, &ch) != ESP_OK) return false;
    pcnt_channel_set_edge_action(ch, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_HOLD);
    pcnt_channel_set_level_action(ch, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_HOLD);  // count while RUN is 1
    pcnt_unit_add_watch_point(pcnt[i], uc.high_limit);                     // needed for accum_count
    if (pcnt_unit_enable(pcnt[i]) != ESP_OK || pcnt_unit_clear_count(pcnt[i]) != ESP_OK || pcnt_unit_start(pcnt[i]) != ESP_OK)
      return false;
  }
  return true;
}

struct Esp32Counters : tgt::CounterHw {
  uint8_t running() override {
    uint8_t m = 0;
    for (int i = 0; i < 4; i++) if (gpio_get_level((gpio_num_t)PIN_RUN[i])) m |= (uint8_t)(1 << i);
    return m;
  }
  void stopAndRead(uint32_t out[4]) override {
    gpio_set_level((gpio_num_t)PIN_OSC, 0);                                // the clock stops: every count freezes at once
    gpio_set_level((gpio_num_t)PIN_STOP_N, 0);
    for (int i = 0; i < 4; i++) {
      int v = 0;
      pcnt_unit_get_count(pcnt[i], &v);
      out[i] = v < 0 ? 0 : (uint32_t)v;
    }
  }
  void clearRun() override {                                               // the oscillator stays off: counts do not move
    gpio_set_level((gpio_num_t)PIN_STOP_N, 0);
    gpio_set_level((gpio_num_t)PIN_STOP_N, 1);
  }
  void arm() override {                                                    // upstream arm_timers()
    gpio_set_level((gpio_num_t)PIN_CLK_START, 0);
    gpio_set_level((gpio_num_t)PIN_STOP_N, 0);
    gpio_set_level((gpio_num_t)PIN_OSC, 0);
    for (int i = 0; i < 4; i++) pcnt_unit_clear_count(pcnt[i]);
    gpio_set_level((gpio_num_t)PIN_OSC, 1);
    gpio_set_level((gpio_num_t)PIN_STOP_N, 1);
  }
};

// Settings, key and boot count in one NVS blob (NVS writes are atomic per key: a power cut keeps the old blob).
struct NvsNvm : tgt::Nvm {
  static const size_t SIZE = 256;
  uint8_t img[SIZE];
  Preferences prefs;
  bool begin() {
    memset(img, 0xFF, SIZE);                                               // like an erased EEPROM
    if (!prefs.begin("fet-target", false)) return false;
    if (prefs.getBytesLength("nvm") == SIZE) prefs.getBytes("nvm", img, SIZE);
    return true;
  }
  bool read(size_t a, uint8_t* b, size_t n) override {
    if (a + n > SIZE) return false;
    memcpy(b, img + a, n);
    return true;
  }
  bool write(size_t a, const uint8_t* b, size_t n) override {
    if (a + n > SIZE) return false;
    if (memcmp(img + a, b, n) == 0) return true;                           // unchanged: no flash wear
    memcpy(img + a, b, n);
    return prefs.putBytes("nvm", img, SIZE) == SIZE;
  }
};

struct PaperPin : tgt::MotorOut {
  void motor(bool on) override { gpio_set_level((gpio_num_t)PIN_PAPER, on ? 1 : 0); }
};

// Three WS2812 LEDs (GRB, 800 kHz) by RMT at 10 MHz: 0 = 4 ticks high + 8 low, 1 = 8 high + 4 low.
static void statusLeds(uint32_t c0, uint32_t c1, uint32_t c2) {
  static rmt_data_t bits[72];
  uint32_t cs[3] = {c0, c1, c2};
  int k = 0;
  for (int led = 0; led < 3; led++) {
    uint32_t rgb = cs[led], grb = ((rgb >> 8) & 0xFF) << 16 | ((rgb >> 16) & 0xFF) << 8 | (rgb & 0xFF);
    for (int b = 23; b >= 0; b--, k++) {
      bool one = (grb >> b) & 1;
      bits[k].level0 = 1; bits[k].duration0 = one ? 8 : 4;
      bits[k].level1 = 0; bits[k].duration1 = one ? 4 : 8;
    }
  }
  rmtWrite(PIN_STATUS, bits, 72, 20);
}
static const uint32_t OFF = 0, RED = 0x200000, GREEN = 0x002000, BLUE = 0x000020, YELLOW = 0x181000, WHITE = 0x101010;

static int16_t readTempDeci() {                                            // TMP1075: 1/256 C per LSB; NO_TEMP if absent
  Wire.beginTransmission(TEMP_I2C);
  Wire.write(0);
  if (Wire.endTransmission() != 0) return tgt::NO_TEMP;
  if (Wire.requestFrom((int)TEMP_I2C, 2) != 2) return tgt::NO_TEMP;
  int16_t raw = (int16_t)((uint16_t)Wire.read() << 8 | (uint16_t)Wire.read());
  return (int16_t)((int32_t)raw * 10 / 256);
}

static bool dacWrite(int32_t mv) {                                         // MCP4725 fast write, not saved in its EEPROM
  if (mv < 0) mv = 0;
  uint32_t code = (uint32_t)((float)mv / DAC_FULL_MV * 4095.0f + 0.5f);
  if (code > 4095) code = 4095;
  Wire.beginTransmission(DAC_I2C);
  Wire.write((uint8_t)(code >> 8));
  Wire.write((uint8_t)(code & 0xFF));
  return Wire.endTransmission() == 0;
}

static int32_t vrefMeasured() {                                            // VREF_LO through a 1:2 divider; -1 if unreadable
  int mv = analogReadMilliVolts(PIN_VREF_LO);                              // ADC2: may fail while WiFi is on
  return mv <= 0 ? -1 : (int32_t)mv * 2;
}

static int32_t vrefSet = 0, vrefMeas = -1;
static void applyVref(int32_t mv) {                                        // upstream dac.c: write, measure, correct a little
  vrefSet = mv;
  int32_t adj = 0;
  for (int i = 0; i < 8; i++) {
    if (!dacWrite(mv + adj)) { vrefMeas = -1; return; }
    delay(20);
    vrefMeas = vrefMeasured();
    if (vrefMeas < 0 || abs(vrefMeas - mv) <= 10) return;
    adj += (mv - vrefMeas) / 2;
    if (adj > 400 || adj < -400) return;                                   // something else is wrong: leave it
  }
}

static float v12Volts() { return analogReadMilliVolts(PIN_V12) / 1000.0f * ((40.2f + 4.7f) / 4.7f) + 0.75f; }

// ---------------------------------------------------------------------------------------------- the core
static Esp32Counters counters;
static NvsNvm nvm;
static PaperPin paperPin;
static tgt::Capture capture(counters);
static tgt::Identity identity(nvm);
static tgt::Settings settings(nvm, tgt::Identity::SETTINGS_ADDR);
static tgt::PaperDrive paper(paperPin);
static SemaphoreHandle_t capLock;                                         // Capture: the 1 kHz timer task vs the loop
static volatile uint32_t msTick = 0;
static int16_t tempDeci = tgt::NO_TEMP;
static bool lightsOff = false;
static uint32_t shotFlashUntil = 0;

struct Esp32Actions : tgt::Actions {
  void paperAdvance() override { paper.request(); }
  void settingsChanged(const tgt::Settings& s) override;
  uint32_t uptimeMs() override { return millis(); }
  uint16_t dropped() override { return capture.dropped(); }
  uint16_t muted() override { return capture.muted(); }
};
static Esp32Actions actions;
static tgt::Commands* commands = 0;
static tgt::LineReader usbIn;

static void lights() { ledcWrite(PIN_LED_PWM, lightsOff ? 0 : (uint32_t)(settings.get(tgt::S_LED_PCT) * 4095L / 100)); }

void Esp32Actions::settingsChanged(const tgt::Settings& s) {
  lights();
  paper.configure((uint16_t)s.get(tgt::S_PAPER_MS), (uint16_t)s.get(tgt::S_STEP_COUNT), (uint16_t)s.get(tgt::S_STEP_MS));
  xSemaphoreTake(capLock, portMAX_DELAY);
  capture.waitMs = (uint16_t)s.get(tgt::S_WAIT_MS);
  capture.ringMs = (uint16_t)s.get(tgt::S_RING_MS);
  capture.faceAloneMs = (uint16_t)s.get(tgt::S_FACE_ALONE_MS);
  xSemaphoreGive(capLock);
  int32_t mv = s.get(tgt::S_VREF_MV);
  if (mv != vrefSet) applyVref(mv == 0 ? VREF_DEFAULT_MV : mv);            // the trip point is software here
}

static void onTick(void*) {                                                // 1 kHz, esp_timer task
  uint32_t ms = ++msTick;
  if (xSemaphoreTake(capLock, 0) != pdTRUE) return;                        // the loop holds it for a moment: next tick
  capture.tick(ms, (uint32_t)esp_timer_get_time());
  xSemaphoreGive(capLock);
}

static void sendAll(const char* line, size_t n) {                          // E2 adds the WiFi side (the bridge core)
  if (n) Serial.write((const uint8_t*)line, n);
}

static void sendHello() {
  char out[tgt::SHOT_LINE_MAX];
  tgt::LineBuf b(out, sizeof out);
  char fp[9];
  identity.fingerprint(fp);
  b.add("FT1 H b="); b.addU(identity.boot());
  b.add(" fw="); b.add(FW_VERSION);
  b.add(" rev="); b.addU(BOARD_REV);
  b.add(" k="); b.addHex(settings.id(), 8);
  b.add(" key="); b.add(fp);
  sendAll(out, tgt::signLine(b, out, identity.hasKey() ? identity.key() : 0, tgt::Identity::KEY_LEN));
}

static void diagLine() {                                                   // USB only: what a bench test needs to see
  char out[200];
  snprintf(out, sizeof out, "FT1 D id=%s rev=%u bdrev_mv=%d v12=%.1f vref=%ld vref_meas=%ld temp=%d run=%x swA=%d swB=%d paper_ms=%ld\n",
           FW_ID, BOARD_REV, analogReadMilliVolts(PIN_BD_REV), v12Volts(), (long)vrefSet, (long)vrefMeas, (int)tempDeci,
           counters.running(), digitalRead(PIN_SW_A), digitalRead(PIN_SW_B), (long)settings.get(tgt::S_PAPER_MS));
  Serial.print(out);
}

static void handleUsbLine(const char* line) {
  char out[320];
  if (strcmp(line, "FT1 D") == 0) { diagLine(); return; }
  size_t n = identity.setupLine(line, FW_VERSION, BOARD_REV, settings.id(), out, sizeof out);
  if (n) {
    if (identity.hasKey()) commands->setKey(identity.key(), tgt::Identity::KEY_LEN);
    Serial.write((const uint8_t*)out, n);                                  // setup replies go back to USB only
    return;
  }
  n = commands->handle(line, out, sizeof out);
  if (n) Serial.write((const uint8_t*)out, n);
}

static void pollSwitches(uint32_t now) {                                   // A pressed: paper once; B pressed: lights on/off
  static uint8_t last = 0;
  static uint32_t since = 0;
  uint8_t sw = (uint8_t)((digitalRead(PIN_SW_A) == 0) | (digitalRead(PIN_SW_B) == 0) << 1);
  if (sw == last) { since = now; return; }
  if (now - since < 50) return;                                            // debounce
  uint8_t pressed = (uint8_t)(sw & ~last);
  last = sw;
  if (pressed & 1) paper.request();
  if (pressed & 2) { lightsOff = !lightsOff; lights(); }
}

void setup() {
  Serial.begin(115200);
  const int outs[] = {PIN_STOP_N, PIN_OSC, PIN_CLK_START, PIN_LDAC_N, PIN_RS485, PIN_PAPER};
  const int init[] = {0, 0, 0, 1, 0, 0};
  for (int i = 0; i < 6; i++) { pinMode(outs[i], OUTPUT); digitalWrite(outs[i], init[i]); }
  for (int i = 0; i < 4; i++) pinMode(PIN_RUN[i], INPUT);
  pinMode(PIN_REF_CLK, INPUT);
  pinMode(PIN_SW_A, INPUT_PULLUP); pinMode(PIN_SW_B, INPUT_PULLUP);
  ledcAttach(PIN_LED_PWM, 5000, 12);                                       // 5 kHz like upstream; 13 bits is not possible here
  ledcWrite(PIN_LED_PWM, 0);
  rmtInit(PIN_STATUS, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 10000000);
  statusLeds(YELLOW, OFF, OFF);                                            // starting
  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  Wire.setTimeOut(20);                                                     // a stuck I2C bus must not stop the target
  analogSetAttenuation(ADC_11db);

  capLock = xSemaphoreCreateMutex();
  nvm.begin();
  identity.load();
  identity.startBoot();                                                    // 0 if it could not be counted: commands refuse b=0
  settings.setDefault(tgt::S_PAPER_MS, PAPER_DEFAULT_MS);
  settings.setDefault(tgt::S_VREF_MV, VREF_DEFAULT_MV);
  settings.load();
  static tgt::Commands cmds(settings, actions, identity.boot());
  commands = &cmds;
  if (identity.hasKey()) commands->setKey(identity.key(), tgt::Identity::KEY_LEN);
  bool countersOk = pcntBegin();
  actions.settingsChanged(settings);                                       // lights, paper, capture timing, the trip point
  tempDeci = readTempDeci();

  capture.begin(0);
  esp_timer_handle_t t;
  esp_timer_create_args_t ta = {};
  ta.callback = onTick;
  ta.name = "capture";
  esp_timer_create(&ta, &t);
  esp_timer_start_periodic(t, 1000);

  statusLeds(v12Volts() >= 10.0f ? GREEN : RED, OFF, countersOk ? GREEN : RED);
  diagLine();
  sendHello();
}

void loop() {
  uint32_t now = millis();
  tgt::Shot s;
  for (;;) {
    xSemaphoreTake(capLock, portMAX_DELAY);
    bool got = capture.pop(s);
    xSemaphoreGive(capLock);
    if (!got) break;
    char out[tgt::SHOT_LINE_MAX];
    size_t n = tgt::formatShot(s, identity.boot(), tempDeci, settings.id(), capture.dropped(),
                               identity.hasKey() ? identity.key() : 0, tgt::Identity::KEY_LEN, out, sizeof out);
    sendAll(out, n);
    if (tgt::placeable(s, MAX_SPREAD_TICKS) && settings.get(tgt::S_PAPER_ECO_MM) == 0) paper.request();
    shotFlashUntil = now + 200;
  }
  while (Serial.available()) {
    if (usbIn.feed((char)Serial.read())) { handleUsbLine(usbIn.line()); usbIn.next(); }
  }
  pollSwitches(now);
  paper.tick(now);
  if (paper.busy()) { xSemaphoreTake(capLock, portMAX_DELAY); capture.quietFor(now); xSemaphoreGive(capLock); }
  static bool flashing = false;
  static uint32_t lastLeds = 0;
  bool flash = (int32_t)(shotFlashUntil - now) > 0;
  if (flash != flashing || now - lastLeds >= 1000) {                      // and once a second: the 12 V may come later
    flashing = flash;
    lastLeds = now;
    statusLeds(v12Volts() >= 10.0f ? GREEN : RED, OFF, flash ? WHITE : GREEN);
  }
  static uint32_t lastTemp = 0;
  if (now - lastTemp >= 1000) { lastTemp = now; tempDeci = readTempDeci(); }
  delay(1);
}
