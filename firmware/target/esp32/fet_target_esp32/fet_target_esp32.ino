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
// Lines go to the USB port (UART0, the board's FTDI) and to FeT Monitor over WiFi: the FeT Bridge core
// (firmware/fet_bridge/src/core, the fet-bridge-core library) runs here in its own task (net_esp32.*, portal_esp32.*),
// exactly as on an ESP-01 bridge: durable at-least-once queue, heartbeats, the server's commands, the setup portal
// (unconfigured, or 3 quick power cycles), settings over USB from the Pi's flash station (FETCFG, first minute). The
// main loop keeps the target running (capture, paper, switches) while the network task waits on the network.
//
// Paper: the shared default (125 ms), as on the Mega (upstream used 500 ms on this board).
// Build: arduino-cli compile -b esp32:esp32:esp32s3:FlashSize=8M,PartitionScheme=default_8MB,FlashMode=dio
//        --library firmware/target/core --library firmware/fet_bridge/src/core firmware/target/esp32/fet_target_esp32
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
#include <LittleFS.h>
#include <bridge.h>
#include <config.h>
#include <provision.h>
#include "net_esp32.h"
#include "portal_esp32.h"
#include <esp_ota_ops.h>

#define FW_VERSION "0.2.2"
// In the flash image, so the Pi's flash station can tell what an image is and what a board runs.
extern const char FW_ID[] __attribute__((used)) = "fet-target-esp32/" FW_VERSION;
static const uint16_t BOARD_REV = 620;
namespace fete { const char* userAgent = "fet-target-esp32/" FW_VERSION; }
static const char BRIDGE_FW[] = "esp32/" FW_VERSION;                    // the heartbeat's bridge_fw: not an ESP-01 bridge

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
static bool sleeping = false;                                              // lights and status LEDs out, shots ignored (not saved)
static uint32_t shotFlashUntil = 0;

struct Esp32Actions : tgt::Actions {
  void paperAdvance() override { paper.request(); }
  void settingsChanged(const tgt::Settings& s) override;
  uint32_t uptimeMs() override { return millis(); }
  uint16_t dropped() override { return capture.dropped(); }
  uint16_t muted() override { return capture.muted(); }
  const char* firmwareUpdate(const uint8_t sha[32], uint32_t size) override;
  int32_t vrefMv() override { return vrefMeas; }                           // as read back (-1: ADC2 busy with WiFi)
  const char* sleep(bool on) override;
  int asleep() override { return sleeping ? 1 : 0; }
};
static Esp32Actions actions;
static struct { volatile bool pending = false; uint8_t sha[32]; uint32_t size = 0; } upd;   // main loop -> network task

const char* Esp32Actions::firmwareUpdate(const uint8_t sha[32], uint32_t size) {
  if (upd.pending) return "busy";
  const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
  if (!next) return "no_ota_slot";
  if (size > next->size) return "too_big";
  memcpy(upd.sha, sha, 32);
  upd.size = size;
  upd.pending = true;                                                      // the network task takes it from here
  return 0;
}

// A new image is on probation: the bootloader goes back to the previous one if it restarts before this firmware has
// reached FeT Monitor once (esp_ota_mark_app_valid_cancel_rollback in netTask).
extern "C" bool verifyRollbackLater() { return true; }
static tgt::Commands* commands = 0;
static tgt::LineReader usbIn;

// ---------------------------------------------------------------------------------------------- the WiFi side
static fete::Pipe toBridge(16384);                                         // target lines -> bridge (network task)
static fete::Pipe toTarget(4096);                                          // server commands -> target (main loop)
static fete::LfsFs lfs;
static fet::Config cfg;
static fete::Hooks hooks;
static fete::UsbUart usbUart;
static fete::PipeUart cmdUart(toTarget);
static fete::WifiNet* net = 0;
static fet::Bridge* bridge = 0;
static fete::Portal* portal = 0;
static fet::QuickBoot* quick = 0;
static fet::Provision* prov = 0;
static volatile uint8_t comState = 0;                                     // 0 no WiFi settings, 1 joining, 2 WiFi up, 3 server OK, 4 portal

static void lights() { ledcWrite(PIN_LED_PWM, sleeping || lightsOff ? 0 : (uint32_t)(settings.get(tgt::S_LED_PCT) * 4095L / 100)); }

const char* Esp32Actions::sleep(bool on) {                                 // from the main loop (commands, the lights button)
  sleeping = on;
  if (!on) lightsOff = false;
  lights();
  if (on) statusLeds(OFF, OFF, OFF);                                       // awake: the loop lights them again within a second
  return 0;
}

void Esp32Actions::settingsChanged(const tgt::Settings& s) {
  lights();
  paper.configure((uint16_t)s.get(tgt::S_PAPER_MS));
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

static void sendAll(const char* line, size_t n) {                          // USB and FeT Monitor
  if (!n) return;
  Serial.write((const uint8_t*)line, n);
  toBridge.put(line, n);
}

static volatile uint32_t netAliveS = 0;                                    // the network task's last turn (s since start)

static void netTask(void*) {                                               // the bridge: may wait seconds on the network
  for (;;) {
    uint64_t now = fete::nowMs();
    netAliveS = (uint32_t)(now / 1000);
    std::string b = toBridge.take();
    for (size_t i = 0; i < b.size(); i++) bridge->onByte((uint8_t)b[i], now);
    net->update(now);
    bridge->tick(fete::nowMs());
    net->watch(fete::nowMs(), bridge->lastOkMs, portal->isOpen());
    quick->tick(fete::nowMs());
    portal->loop(fete::nowMs());
    if (hooks.portalWanted) { hooks.portalWanted = false; portal->open(fete::nowMs()); }
    comState = portal->isOpen() ? 4 : !cfg.wifiConfigured() ? 0 : !net->wifiUp() ? 1 : bridge->lastStatus == 200 ? 3 : 2;
    static bool confirmed = false;
    if (!confirmed && bridge->lastStatus == 200) {                         // FeT Monitor answered: this firmware works
      confirmed = true;
      esp_ota_img_states_t st;
      if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
        esp_ota_mark_app_valid_cancel_rollback();
        fete::say("Firmware update: confirmed (FeT Monitor reached)", "");
      }
    }
    if (upd.pending) {
      std::string err;
      if (net->fetchImage(upd.sha, upd.size, err)) hooks.reboot();
      upd.pending = false;
    }
    if (hooks.eraseAt && fete::nowMs() >= hooks.eraseAt) {                 // factory reset: WiFi/server settings and the queue;
      fete::say("Factory reset: erasing the WiFi settings and the shot queue", "");   // the target's key stays (USB only)
      LittleFS.format();
      ESP.restart();
    }
    if (hooks.rebootAt && fete::nowMs() >= hooks.rebootAt) ESP.restart();
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

static void bridgeBegin() {
  std::string id = fete::deviceIdFromChip();
  bool fsOk = lfs.begin();
  fet::loadConfig(lfs, cfg);                                                // missing/invalid -> unconfigured
  if (!fet::validName(cfg.name)) cfg.name = id;
  quick = new fet::QuickBoot(lfs);
  bool wantPortal = fsOk && quick->onBoot();                               // third quick power cycle in a row
  size_t cap = lfs.totalBytes() / 2;                                        // room for settings and wear-levelling
  if (cap < 16000) cap = 16000;
  net = new fete::WifiNet(cfg);
  fet::BridgeInfo info{id, cfg.name, BRIDGE_FW};
  bridge = new fet::Bridge(lfs, *net, cmdUart, hooks, info, cap, FET_LOG_SEGMENT_BYTES);
  bridge->begin(fete::nowMs());
  std::string why;                                                         // the board restarted itself last time: say why
  if (fsOk && lfs.read(FET_RESTART_WHY, why)) {
    lfs.remove(FET_RESTART_WHY);
    if (why == "net_stuck" || why == "no_wifi") { net->setRestartWhy(why); fete::say("Restarted by the WiFi safety net: ", why); }
  }
  prov = new fet::Provision(lfs, usbUart, hooks, id, BRIDGE_FW);
  bool openPortal = wantPortal || !cfg.wifiConfigured();
  net->startWifi(cfg.name, openPortal);
  portal = new fete::Portal(lfs, cfg, hooks, *net, id);
  portal->begin(bridge);
  if (openPortal) portal->open(fete::nowMs());
  net->announce();
  fete::say("Device: ", id + ", target " + cfg.name);
}

static uint32_t comColor() {
  switch (comState) {
    case 1: return YELLOW;                                                 // joining the WiFi
    case 2: return BLUE;                                                   // WiFi up, FeT Monitor not answering (yet)
    case 3: return GREEN;                                                  // delivering to FeT Monitor
    case 4: return WHITE;                                                  // setup portal open
    default: return OFF;                                                   // no WiFi settings
  }
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
  if (pressed & 2) {
    if (sleeping) { actions.sleep(false); sendHello(); }                   // woken here: the hello makes the server ask
    else { lightsOff = !lightsOff; lights(); }
  }
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

  diagLine();
  bridgeBegin();
  xTaskCreatePinnedToCore(netTask, "net", 12288, 0, 1, 0, 0);              // core 0, with the WiFi stack
  statusLeds(v12Volts() >= 10.0f ? GREEN : RED, comColor(), countersOk ? GREEN : RED);
  sendHello();
}

// The last resort, from this task (it runs even if the network task hangs): restart the board when the network task
// has not moved for FET_NET_STUCK_MS (not during a firmware download), or there has been no WiFi at all for
// FET_NO_WIFI_RESTART_MS despite the retries. Never within 2 minutes of a shot. The queue is in flash: nothing
// queued is lost. The reason goes to flash and into the next heartbeats ("restart_why").
static uint32_t lastShotS = 0;
static void safetyNet() {
  if (!net || !portal) return;
  const char* why = fet::restartWhy((uint32_t)(fete::nowMs() / 1000), lastShotS, netAliveS, net->noWifiSinceS(), upd.pending,
                                    portal->isOpen(), FET_NET_STUCK_MS / 1000, FET_NO_WIFI_RESTART_MS / 1000);
  if (!why) return;
  fete::say("WiFi safety net: restarting the board: ", why);
  lfs.writeAtomic(FET_RESTART_WHY, why);
  delay(50);
  ESP.restart();
}

void loop() {
  uint32_t now = millis();
  tgt::Shot s;
  for (;;) {
    xSemaphoreTake(capLock, portMAX_DELAY);
    bool got = capture.pop(s);
    xSemaphoreGive(capLock);
    if (!got) break;
    lastShotS = (uint32_t)(fete::nowMs() / 1000);
    if (sleeping) continue;                                                // asleep: shots are ignored
    char out[tgt::SHOT_LINE_MAX];
    size_t n = tgt::formatShot(s, identity.boot(), tempDeci, settings.id(), capture.dropped(),
                               identity.hasKey() ? identity.key() : 0, tgt::Identity::KEY_LEN, out, sizeof out);
    sendAll(out, n);
    if (tgt::placeable(s, MAX_SPREAD_TICKS) && settings.get(tgt::S_PAPER_ECO_MM) == 0) paper.request();
    shotFlashUntil = now + 200;
  }
  while (Serial.available()) {                                             // USB: provisioning first (it keeps its lines)
    std::string pass;
    prov->feed((uint8_t)Serial.read(), fete::nowMs(), pass);
    for (size_t i = 0; i < pass.size(); i++)
      if (usbIn.feed(pass[i])) { handleUsbLine(usbIn.line()); usbIn.next(); }
  }
  std::string cmds = toTarget.take();                                      // the server's signed commands
  static std::string partial;
  partial += cmds;
  for (size_t nl; (nl = partial.find('\n')) != std::string::npos;) {
    std::string line = partial.substr(0, nl);
    partial.erase(0, nl + 1);
    if (!line.empty() && line[line.size() - 1] == '\r') line.erase(line.size() - 1);
    char out[320];
    size_t n = commands->handle(line.c_str(), out, sizeof out);
    if (n) sendAll(out, n);
  }
  if (partial.size() > 1024) partial.clear();
  pollSwitches(now);
  paper.tick(now);
  if (paper.busy()) { xSemaphoreTake(capLock, portMAX_DELAY); capture.quietFor(now); xSemaphoreGive(capLock); }
  static bool flashing = false;
  static uint32_t lastLeds = 0;
  bool flash = (int32_t)(shotFlashUntil - now) > 0;
  if (flash != flashing || now - lastLeds >= 1000) {                      // and once a second: the 12 V may come later
    flashing = flash;
    lastLeds = now;
    if (sleeping) statusLeds(OFF, OFF, OFF);
    else statusLeds(v12Volts() >= 10.0f ? GREEN : RED, comColor(), flash ? WHITE : GREEN);
  }
  static uint32_t lastTemp = 0;
  if (now - lastTemp >= 1000) { lastTemp = now; tempDeci = readTempDeci(); safetyNet(); }
  delay(1);
}
