// freETarget WiFi bridge for the ESP-01 / ESP-01S (ESP8266).
// Reads the Arduino's shot lines on the serial port, stores them durably in flash, and delivers each one to the
// Raspberry Pi server exactly once (retrying until acknowledged). See README.md in this folder.
#include <ESP8266WiFi.h>
#include "src/core/bridge.h"
#include "src/core/config.h"
#include "src/platform/build_config.h"
#include "src/platform/platform.h"
#include "src/platform/portal.h"
#if FET_ENABLE_OTA
#include <ArduinoOTA.h>
#endif

using namespace fetp;

static LfsFs g_fs;
static RxHold g_hold;
static fet::Config g_cfg;
static PlatformHooks g_hooks;
static SerialUart g_uart;
static WifiNet* g_net = nullptr;
static fet::Bridge* g_bridge = nullptr;
static Portal* g_portal = nullptr;
static fet::QuickBoot* g_quick = nullptr;
static std::string g_id;
static bool g_otaStarted = false;

static void startOta() {
#if FET_ENABLE_OTA
  if (g_otaStarted || g_cfg.token.empty()) return;        // no token = no OTA: never leave updates open
  ArduinoOTA.setHostname(g_id.c_str());
  ArduinoOTA.setPassword(g_cfg.token.c_str());
  ArduinoOTA.begin();
  g_otaStarted = true;
#endif
}

void setup() {
  Serial.setRxBufferSize(FET_RX_BUFFER);                  // before begin(); the Arduino keeps talking while we wait on WiFi
  Serial.begin(FET_SERIAL_BAUD);
  g_id = deviceIdFromChip();
#if FET_DEBUG
  {                                                       // why did the chip last restart? (survives the crash itself)
    struct rst_info* r = ESP.getResetInfoPtr();
    Serial.printf("\n[fet] last reset: reason=%u exccause=%u epc1=0x%08x epc2=0x%08x epc3=0x%08x excvaddr=0x%08x depc=0x%08x\n",
                  (unsigned)r->reason, (unsigned)r->exccause, (unsigned)r->epc1, (unsigned)r->epc2, (unsigned)r->epc3,
                  (unsigned)r->excvaddr, (unsigned)r->depc);
  }
#endif
#if FET_DEBUG
  Serial.printf("[fet] flash: configured=%u real=%u sdk=%s core=%s\n", (unsigned)ESP.getFlashChipSize(),
                (unsigned)ESP.getFlashChipRealSize(), ESP.getSdkVersion(), ESP.getCoreVersion().c_str());
#endif
  FET_STAGE(1);

  bool fsOk = g_fs.begin();
  FET_STAGE(2);
#if FET_DEBUG
  Serial.printf("[fet] filesystem: %u bytes\n", (unsigned)g_fs.totalBytes());
#endif
  fet::loadConfig(g_fs, g_cfg);                           // missing/invalid -> defaults (unprovisioned)
  if (!fet::validName(g_cfg.name)) g_cfg.name = g_id;

  FET_STAGE(3);
  g_quick = new fet::QuickBoot(g_fs);
  bool wantPortal = fsOk && g_quick->onBoot();            // third quick power cycle in a row
  FET_STAGE(4);

  size_t total = g_fs.totalBytes();
  size_t cap = total / 2;                                 // leave the rest for settings, temp files, flash wear-levelling
  if (cap < 16000) cap = 16000;
  fet::BridgeInfo info{g_id, g_cfg.name, FET_FW_VERSION};
  FET_STAGE(5);
  g_net = new WifiNet(g_cfg, g_hold);
  g_bridge = new fet::Bridge(g_fs, *g_net, g_uart, g_hooks, info, cap, FET_LOG_SEGMENT_BYTES);
  FET_STAGE(6);
  g_bridge->begin(nowMs());
  FET_STAGE(7);

  bool openPortal = wantPortal || !g_cfg.wifiConfigured() || g_hooks.portalWanted;
  g_net->startWifi(g_cfg.name, openPortal);
  FET_STAGE(8);
  g_portal = new Portal(g_fs, g_cfg, g_hooks, *g_net, g_id);
  FET_STAGE(9);
  g_portal->begin(g_bridge);
  FET_STAGE(10);
  if (openPortal) g_portal->open(nowMs());
  g_hooks.portalWanted = false;
  FET_STAGE(11);
}

void loop() {
#if FET_DEBUG
  static bool first = true;
  if (first) { first = false; FET_STAGE(12); }
#endif
  uint64_t now = nowMs();

  // 1. serial in: whatever was parked during a network wait, then whatever is waiting now
  g_hold.pump();
  if (!g_hold.bytes.empty()) {
    std::string b;
    b.swap(g_hold.bytes);
    for (size_t i = 0; i < b.size(); i++) g_bridge->onByte((uint8_t)b[i], now);
  }

  // 2. WiFi bookkeeping first, so "Connected to network" prints before the first attempt to reach the server
  g_net->update(nowMs());

  // 3. bridge: sending, heartbeat, commands (may block for a few seconds on the network; serial is parked meanwhile)
  g_bridge->tick(nowMs());

  // 4. housekeeping
  g_quick->tick(nowMs());
  g_portal->loop(nowMs());
  if (g_hooks.portalWanted) { g_hooks.portalWanted = false; g_portal->open(nowMs()); }
  if (g_net->wifiUp()) startOta();
#if FET_ENABLE_OTA
  if (g_otaStarted) ArduinoOTA.handle();
#endif
  if (g_hooks.rebootAt && nowMs() >= g_hooks.rebootAt) ESP.restart();
  delay(1);                                               // lets the WiFi stack run; the UART has a 2 KB FIFO behind this
}
