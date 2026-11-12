#include "net_esp32.h"
#include <LittleFS.h>
#include <WiFiClient.h>
#include <httpparse.h>
#include <esp_mac.h>
#include <esp_system.h>
#include <esp_wifi.h>
#include <freertos/task.h>
#include <map>
#include <Update.h>
#include <tgt_sha256.h>
#if FET_HTTPS
#include <NetworkClientSecure.h>
#include <mbedtls/x509_crt.h>
#include <time.h>
#include <device_ca.h>                            // FET_DEVICE_CA: the deepTENS device CA's root (tools/device_ca.py)
#include <isrg_root_x1.h>                         // FET_ISRG_ROOT_X1: Let's Encrypt's root, the backup
#endif

namespace fete {

extern const char* userAgent;                     // fet_target_esp32.ino: the firmware's identification

uint64_t nowMs() { return (uint64_t)(esp_timer_get_time() / 1000); }

std::string deviceIdFromChip() {                  // from the chip's factory MAC (eFuse): valid before WiFi starts
  uint8_t mac[6] = {0};
  esp_efuse_mac_get_default(mac);
  char b[16];
  snprintf(b, sizeof b, "fet-%02x%02x%02x", mac[3], mac[4], mac[5]);
  return b;
}

void say(const char* label, const std::string& value) {   // one write: two tasks print, lines must not interleave
  std::string line = label;
  for (size_t i = 0; i < value.size(); i++) {
    unsigned char c = (unsigned char)value[i];
    line += (c < 0x20 || c > 0x7e) ? '?' : (char)c;
  }
  line += '\n';
  Serial.write((const uint8_t*)line.data(), line.size());
}

// ---------------------------------------------------------------- the queues between the two tasks
bool Pipe::put(const char* p, size_t n) {
  xSemaphoreTake(lock_, portMAX_DELAY);
  bool ok = buf_.size() + n <= cap_;
  if (ok) buf_.append(p, n);
  else dropped_++;
  xSemaphoreGive(lock_);
  return ok;
}

std::string Pipe::take() {
  std::string out;
  xSemaphoreTake(lock_, portMAX_DELAY);
  out.swap(buf_);
  xSemaphoreGive(lock_);
  return out;
}

// ---------------------------------------------------------------- filesystem (the "spiffs" partition, as LittleFS)
static std::string withSlash(const char* n) {
  std::string s = n;
  if (s.empty() || s[0] != '/') s = "/" + s;
  return s;
}

bool LfsFs::begin() { return LittleFS.begin(true); }          // formats on first use
size_t LfsFs::totalBytes() { return LittleFS.totalBytes(); }
size_t LfsFs::freeBytes() { size_t t = LittleFS.totalBytes(), u = LittleFS.usedBytes(); return t > u ? t - u : 0; }

bool LfsFs::read(const std::string& path, std::string& out) {
  File f = LittleFS.open(path.c_str(), "r");
  if (!f) return false;
  size_t n = f.size();
  out.assign(n, '\0');
  size_t got = n ? f.read((uint8_t*)&out[0], n) : 0;
  f.close();
  if (got != n) { out.clear(); return false; }
  return true;
}

bool LfsFs::readRange(const std::string& path, size_t off, size_t len, std::string& out) {
  File f = LittleFS.open(path.c_str(), "r");
  if (!f) return false;
  size_t sz = f.size();
  if (off > sz) { f.close(); return false; }
  size_t n = sz - off < len ? sz - off : len;
  out.assign(n, '\0');
  bool ok = f.seek(off, SeekSet);
  size_t got = (ok && n) ? f.read((uint8_t*)&out[0], n) : 0;
  f.close();
  if (!ok || got != n) { out.clear(); return false; }
  return true;
}

bool LfsFs::writeAtomic(const std::string& path, const std::string& data) {
  std::string tmp = path + ".tmp";
  File f = LittleFS.open(tmp.c_str(), "w");
  if (!f) return false;
  size_t n = data.empty() ? 0 : f.write((const uint8_t*)data.data(), data.size());
  f.close();
  if (n != data.size()) { LittleFS.remove(tmp.c_str()); return false; }
  if (LittleFS.rename(tmp.c_str(), path.c_str())) return true;
  LittleFS.remove(path.c_str());                  // in case it will not rename over an existing file
  if (LittleFS.rename(tmp.c_str(), path.c_str())) return true;
  LittleFS.remove(tmp.c_str());
  return false;
}

bool LfsFs::append(const std::string& path, const std::string& data) {
  File f = LittleFS.open(path.c_str(), "a");
  if (!f) return false;
  size_t n = f.write((const uint8_t*)data.data(), data.size());
  f.close();
  return n == data.size();
}

bool LfsFs::remove(const std::string& path) { return LittleFS.remove(path.c_str()); }

bool LfsFs::size(const std::string& path, size_t& out) {
  File f = LittleFS.open(path.c_str(), "r");
  if (!f) return false;
  out = f.size();
  f.close();
  return true;
}

std::vector<std::string> LfsFs::list(const std::string& prefix) {
  std::vector<std::string> r;
  File root = LittleFS.open("/");
  if (!root) return r;
  for (File f = root.openNextFile(); f; f = root.openNextFile()) {
    std::string n = withSlash(f.name());
    if (n.compare(0, prefix.size(), prefix) == 0) r.push_back(n);
  }
  return r;
}

// ---------------------------------------------------------------- WiFi
static volatile uint16_t lastReason = 0;          // the last disconnect's reason code (the WiFi event task writes it)

void WifiNet::startWifi(const std::string& hostname, bool withAccessPoint) {
  hostname_ = hostname;
  reset_ = (int)esp_reset_reason();               // why this start: power-on, brownout, crash, watchdog ... (esp_system.h)
  silence_.start(nowMs());
  WiFi.onEvent([](arduino_event_id_t e, arduino_event_info_t info) {
    if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) lastReason = info.wifi_sta_disconnected.reason;
    if (e == ARDUINO_EVENT_WIFI_STA_GOT_IP) esp_wifi_set_ps(WIFI_PS_NONE);   // awake, whatever the core did before (its
                                                                            // default on the S3 is power save)
  });
  WiFi.persistent(false);                         // our settings live in /config.json, not the SDK's NVS
  WiFi.setHostname(hostname.c_str());
  // Nothing to join: the access point alone. A station with nowhere to go scans channels, and the setup network's
  // replies to a phone get lost meanwhile (B59, 2026-11-02: DHCP answered, the phone never got the answer).
  WiFi.mode(!withAccessPoint ? WIFI_STA : cfg_.wifiConfigured() ? WIFI_AP_STA : WIFI_AP);
  WiFi.setSleep(false);                           // the radio stays awake: a reply sent while it dozes is lost
  // 802.11b/g as the ESP-01 (FET_WIFI_PHY_G): on the author's ASUS ZenWiFi an 802.11n link let the ESP-01 join but not
  // get through (firmware 0.1.14), and this board's heartbeats came minutes apart where the ESP-01's never did (2026-11-05)
  if (cfg_.wifiConfigured()) {                    // (the station side exists only then)
    esp_err_t pe = esp_wifi_set_protocol(WIFI_IF_STA, FET_WIFI_STA_11B ? WIFI_PROTOCOL_11B : WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G);
    say("WiFi options: ", std::string(FET_WIFI_STA_11B ? "sleep off, 802.11b only" : "sleep off, 802.11b/g") + (pe == ESP_OK ? "" : " (not set: " + std::string(esp_err_to_name(pe)) + ")"));
  }
  WiFi.setAutoReconnect(true);
  if (!cfg_.wifiConfigured()) return;
  if (cfg_.fixedIp()) {                           // skip DHCP
    uint8_t a[4], g[4], m[4] = {255, 255, 255, 0};
    fet::parseIpv4(cfg_.ip, a); fet::parseIpv4(cfg_.gateway, g);
    if (!cfg_.mask.empty()) fet::parseIpv4(cfg_.mask, m);
    WiFi.config(IPAddress(a[0], a[1], a[2], a[3]), IPAddress(g[0], g[1], g[2], g[3]), IPAddress(m[0], m[1], m[2], m[3]),
                IPAddress(g[0], g[1], g[2], g[3]));
  }
  lastBegin_ = firstBegin_ = nowMs();
  WiFi.begin(cfg_.ssid.c_str(), cfg_.pass.c_str());
}

void WifiNet::announce() {
  if (cfg_.wifiConfigured()) say("Attempting to connect to network: ", cfg_.ssid + (cfg_.fixedIp() ? " (fixed IP " + cfg_.ip + ")" : ""));
  else say("No WiFi settings saved: ", "waiting for setup");
}

void WifiNet::watch(uint64_t now, uint64_t lastOkMs, bool portalOpen) {
  if (!cfg_.wifiConfigured() || portalOpen) return;
  if (!silence_.due(now, lastOkMs)) return;
  rejoins_++;
  say("No answer from FeT Monitor ", std::string(lastOkMs ? "for " + std::to_string((unsigned long)((now - lastOkMs) / 1000)) + " s" : "since the start") + " (WiFi says " +
      (wifiUp() ? "connected" : "not connected") + "): joining " + cfg_.ssid + " again, re-join " + std::to_string((unsigned long)rejoins_));
  haveIp_ = false; nextResolve_ = 0;
  WiFi.disconnect();
  WiFi.begin(cfg_.ssid.c_str(), cfg_.pass.c_str());
  lastBegin_ = now;
}

// Where the CPU time went since the last call (FreeRTOS run-time counters, CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS):
// each core's idle share and the busiest task other than the idle ones. Called from the network task only.
static std::string cpuJson() {
  static std::map<TaskHandle_t, configRUN_TIME_COUNTER_TYPE> last;
  static configRUN_TIME_COUNTER_TYPE lastTotal = 0;
  UBaseType_t n = uxTaskGetNumberOfTasks() + 4;
  TaskStatus_t* t = (TaskStatus_t*)malloc(n * sizeof(TaskStatus_t));
  if (!t) return std::string();
  configRUN_TIME_COUNTER_TYPE total = 0;
  n = uxTaskGetSystemState(t, n, &total);
  uint64_t span = total - lastTotal;            // microseconds of one core (the counter is esp_timer time)
  std::map<TaskHandle_t, configRUN_TIME_COUNTER_TYPE> now;
  uint64_t idle[2] = {0, 0}, topD = 0;
  std::string top;
  for (UBaseType_t i = 0; i < n; i++) {
    configRUN_TIME_COUNTER_TYPE prev = last.count(t[i].xHandle) ? last[t[i].xHandle] : t[i].ulRunTimeCounter;
    uint64_t d = t[i].ulRunTimeCounter - prev;
    now[t[i].xHandle] = t[i].ulRunTimeCounter;
    const char* nm = t[i].pcTaskName;
    if (strncmp(nm, "IDLE", 4) == 0) { int c = nm[4] == '1' ? 1 : 0; idle[c] += d; continue; }
    if (d > topD) { topD = d; top = nm; }
  }
  free(t);
  bool first = lastTotal == 0;
  last.swap(now);
  lastTotal = total;
  if (first || span == 0) return std::string();
  std::string safe;
  for (char ch : top) if (isalnum((unsigned char)ch) || ch == '_' || ch == '-' || ch == ' ') safe += ch;
  auto pct = [&](uint64_t d) { return std::to_string((unsigned)(d * 100 / span > 100 ? 100 : d * 100 / span)); };
  return ",\"idle0\":" + pct(idle[0]) + ",\"idle1\":" + pct(idle[1]) + ",\"top\":\"" + safe.substr(0, 16) + "\",\"top_pct\":" + pct(topD);
}

std::string WifiNet::linkJson() {
  uint64_t now = nowMs(), down = downMs_ + (droppedAt_ ? now - droppedAt_ : 0);
  wifi_ps_type_t ps = WIFI_PS_NONE;
  bool psOk = esp_wifi_get_ps(&ps) == ESP_OK;
  uint8_t proto = 0;
  bool protoOk = esp_wifi_get_protocol(WIFI_IF_STA, &proto) == ESP_OK;
  std::string extra = std::string(psOk ? ",\"ps\":" + std::to_string((int)ps) : "") + (protoOk ? ",\"phy\":" + std::to_string((int)proto) : "") + ",\"ap\":" + ((WiFi.getMode() & WIFI_AP) ? "1" : "0") +
                      ",\"loop_max_ms\":" + std::to_string((unsigned long)loopMaxMs_);
  loopMaxMs_ = 0;                               // the longest turn since this report
  char mac[18] = "";
  if (haveBssid_) snprintf(mac, sizeof mac, "%02x:%02x:%02x:%02x:%02x:%02x", bssid_[0], bssid_[1], bssid_[2], bssid_[3], bssid_[4], bssid_[5]);
  return std::string("{\"reset\":") + std::to_string(reset_) + ",\"drops\":" + std::to_string((unsigned long)drops_) +
         ",\"reason\":" + std::to_string((unsigned)dropReason_) + ",\"last_drop_ms\":" + std::to_string((unsigned long long)lastDropUp_) +
         ",\"down_ms\":" + std::to_string((unsigned long long)down) + ",\"longest_ms\":" + std::to_string((unsigned long long)longestMs_) +
         ",\"bssid\":\"" + mac + "\",\"bssid_changes\":" + std::to_string((unsigned long)bssidChanges_) +
         ",\"ch\":" + std::to_string((int)(wifiUp() ? WiFi.channel() : 0)) + ",\"rejoins\":" + std::to_string((unsigned long)rejoins_) +
         ",\"posts\":" + std::to_string((unsigned long)posts_) + ",\"post_fail_connect\":" + std::to_string((unsigned long)failConnect_) +
         ",\"post_fail_reply\":" + std::to_string((unsigned long)failReply_) + ",\"post_max_ms\":" + std::to_string((unsigned long)postMaxMs_) +
         (restartWhy_.empty() ? std::string() : ",\"restart_why\":\"" + restartWhy_ + "\"") + extra + cpuJson() + "}";
}

bool WifiNet::wifiUp() const { return WiFi.status() == WL_CONNECTED; }
int WifiNet::rssi() { return wifiUp() ? (int)WiFi.RSSI() : 0; }

std::string WifiNet::serverText() const {
  if (!haveIp_) return cfg_.host.empty() ? "no server address set" : "not resolved";
  return std::string(ip_.toString().c_str()) + ":" + std::to_string(port_);
}

void WifiNet::update(uint64_t now) {
  bool up = wifiUp();
  if (up && !wasUp_) {
    if (droppedAt_) {                             // back after a drop
      uint64_t d = now - droppedAt_;
      downMs_ += d;
      if (d > longestMs_) longestMs_ = d;
      droppedAt_ = 0;
    }
    uint8_t* b = WiFi.BSSID();
    if (b) {
      if (haveBssid_ && memcmp(b, bssid_, 6) != 0) bssidChanges_++;
      memcpy(bssid_, b, 6);
      haveBssid_ = true;
    }
    std::string t;
    if (firstBegin_) { t = ", after " + std::to_string((unsigned long)((now - firstBegin_ + 500) / 1000)) + " s"; firstBegin_ = 0; }
    say("Connected to network: ", std::string(WiFi.SSID().c_str()) + " (" + WiFi.localIP().toString().c_str() + ")" + t);
  }
  if (!up && cfg_.wifiConfigured() && now >= nextWifiNote_) {   // say why, every 10 s, until it connects
    nextWifiNote_ = now + 10000;
    wl_status_t st = WiFi.status();
    const char* why = st == WL_NO_SSID_AVAIL ? "network not found" : st == WL_CONNECT_FAILED ? "wrong password?"
                    : st == WL_IDLE_STATUS ? "connecting" : st == WL_DISCONNECTED ? "not connected yet" : "waiting";
    say("Not connected to ", cfg_.ssid + ": " + why + " (status " + std::to_string((int)st) + ")");
  }
  if (!up && cfg_.wifiConfigured() && lastBegin_ && now - lastBegin_ > 45000UL) {   // a stuck attempt: start afresh
    lastBegin_ = now;
    say("Retrying connection to ", cfg_.ssid);
    WiFi.disconnect();
    WiFi.begin(cfg_.ssid.c_str(), cfg_.pass.c_str());
  }
  if (up) lastBegin_ = now;
  if (up || !cfg_.wifiConfigured()) noWifiSinceS_ = 0;
  else if (!noWifiSinceS_) noWifiSinceS_ = (uint32_t)(now / 1000) + 1;
  if (!up && wasUp_) {
    haveIp_ = false; nextResolve_ = 0;
    drops_++; droppedAt_ = now; lastDropUp_ = now; dropReason_ = lastReason;
    say("WiFi dropped: reason ", std::to_string((unsigned)dropReason_) + " (ESP-IDF wifi_err_reason_t), drop " + std::to_string((unsigned long)drops_) +
        " since start, signal was " + std::to_string((int)WiFi.RSSI()) + " dBm");
  }
  wasUp_ = up;
}

bool WifiNet::resolveServer(uint64_t now) {
  if (haveIp_ && now - ipAt_ < 600000UL) return true;            // look again every 10 min, in case the Pi moved
  if (now < nextResolve_) return haveIp_;
  nextResolve_ = now + 15000;
  IPAddress ip;
  bool ok = false;
  if (!cfg_.host.empty()) ok = ip.fromString(cfg_.host.c_str()) || WiFi.hostByName(cfg_.host.c_str(), ip) == 1;
  if (ok) { ip_ = ip; port_ = cfg_.port; haveIp_ = true; ipAt_ = now; connectFails_ = 0; }
  else if (haveIp_ && now - ipAt_ > 1800000UL) haveIp_ = false;
  return haveIp_;
}

bool WifiNet::ready() { return wifiUp() && resolveServer(nowMs()); }

bool WifiNet::post(const char* path, const std::string& body, int& status, std::string& resp) {
  uint64_t began = nowMs();
  posts_++;
#if FET_HTTPS
  bool got = cfg_.tls ? postTls(path, body, status, resp) : postPlain(path, body, status, resp);
#else
  bool got = postPlain(path, body, status, resp);
#endif
  uint32_t took = (uint32_t)(nowMs() - began);
  if (took > postMaxMs_) postMaxMs_ = took;
  if (!reportedFirst_) {
    reportedFirst_ = true;
    say("First report to FET: ", (got && status == 200) ? "success" : "fail");
  }
  return got;
}

bool WifiNet::postPlain(const char* path, const std::string& body, int& status, std::string& resp) {
  WiFiClient c;
  c.setTimeout(FET_CONNECT_TIMEOUT_MS / 1000 + 1);
  bool got = false;
  if (!c.connect(ip_, (uint16_t)port_, FET_CONNECT_TIMEOUT_MS)) {
    failConnect_++;
    if (++connectFails_ >= 3) { haveIp_ = false; nextResolve_ = 0; connectFails_ = 0; }
  } else {
    connectFails_ = 0;
    c.setNoDelay(true);
    std::string req = std::string("POST ") + path + " HTTP/1.1\r\nHost: " + ip_.toString().c_str() + "\r\nUser-Agent: " + userAgent +
                      "\r\nX-FET-Token: " + cfg_.token + "\r\nContent-Type: application/json\r\nContent-Length: " +
                      std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
    uint64_t t0 = nowMs();
    bool sent = c.write((const uint8_t*)req.data(), req.size()) == req.size();
    std::string raw;
    bool complete = false;
    while (sent && nowMs() - t0 < FET_HTTP_TIMEOUT_MS) {
      int avail = c.available();
      if (avail > 0) {
        char buf[256];
        int n = c.read((uint8_t*)buf, avail > (int)sizeof buf ? (int)sizeof buf : avail);
        if (n > 0) raw.append(buf, (size_t)n);
        if (raw.size() > FET_MAX_RESPONSE) break;
        continue;
      }
      if (!c.connected()) { complete = true; break; }      // the server closed: the end of the body
      delay(2);
    }
    c.stop();
    got = complete && fet::parseHttpResponse(raw, status, resp);
    if (!got) failReply_++;
  }
  return got;
}

#if FET_HTTPS
// ---------------------------------------------------------------- HTTPS (P3), as FeT Bridge 0.5.0 (platform.cpp postTls)

void WifiNet::tlsDrop() { if (tls_) { tls_->stop(); delete tls_; tls_ = nullptr; } }

// This core's mbedTLS is built without MBEDTLS_HAVE_TIME_DATE: it checks the chain and the name but no dates. The
// server's own certificate's dates are checked here instead (the roots and Let's Encrypt's intermediate are not).
static bool certCurrent(const mbedtls_x509_crt* c, std::string& why) {
  if (!c) { why = "no certificate"; return false; }
  time_t t = time(nullptr);
  struct tm u;
  gmtime_r(&t, &u);
  auto key = [](int y, int mo, int d, int h, int mi, int se) {
    return ((((uint64_t)y * 13 + mo) * 32 + d) * 24 + h) * 3600 + mi * 60 + se; };
  uint64_t now = key(u.tm_year + 1900, u.tm_mon + 1, u.tm_mday, u.tm_hour, u.tm_min, u.tm_sec);
  const mbedtls_x509_time &f = c->valid_from, &e = c->valid_to;
  if (now < key(f.year, f.mon, f.day, f.hour, f.min, f.sec)) { why = "the server's certificate is not valid yet"; return false; }
  if (now > key(e.year, e.mon, e.day, e.hour, e.min, e.sec)) { why = "the server's certificate has expired"; return false; }
  return true;
}

static const std::string& trustPem() {                                // the deepTENS device CA, and Let's Encrypt's root
  static const std::string trust = std::string(FET_DEVICE_CA) + "\n" + FET_ISRG_ROOT_X1;
  return trust;
}

// A connection of its own for a firmware download (0.4.1): the kept-open report connection is closed first (the
// ESP32-S3 has room for one TLS session at a time with room to spare); the same checks as reports: the chain to the
// trusted roots, the server's name (SNI), the certificate's dates against the time from NTP.
NetworkClientSecure* WifiNet::tlsForDownload(std::string& err) {
  if (time(nullptr) < 1700000000) { err = "no time yet (NTP): the certificate cannot be checked"; return nullptr; }
  tlsDrop();
  NetworkClientSecure* s = new NetworkClientSecure();
  s->setCACert(trustPem().c_str());
  s->setHandshakeTimeout(FET_TLS_HANDSHAKE_MS / 1000);
  std::string why;
  if (s->connect(cfg_.host.c_str(), (uint16_t)cfg_.port, FET_TLS_CONNECT_MS) != 1) {
    char e[100] = "";
    int code = s->lastError(e, sizeof e);
    why = "secure connection failed: " + std::to_string(code) + " " + e;
  } else if (!certCurrent(s->getPeerCertificate(), why)) {
    why = "secure connection failed: " + why;
  }
  if (!why.empty()) { s->stop(); delete s; err = why; return nullptr; }
  return s;
}

// One report over the kept-open connection; a connection the server closed meanwhile is opened again once.
bool WifiNet::postTls(const char* path, const std::string& body, int& status, std::string& resp) {
  const std::string& trust = trustPem();
  if (!timeAsked_) { configTime(0, 0, "pool.ntp.org", "time.nist.gov"); timeAsked_ = true; }
  if (time(nullptr) < 1700000000) {                                  // no clock yet: no certificate can be checked
    if (tlsState_ != 3) say("Secure connection: ", "waiting for the time (NTP)");
    tlsState_ = 3;
    failConnect_++;
    return false;
  }
  for (int attempt = 0; attempt < 2; attempt++) {
    bool fresh = false;
    if (!tls_ || !tls_->connected()) {
      tlsDrop();
      tls_ = new NetworkClientSecure();
      tls_->setCACert(trust.c_str());
      tls_->setHandshakeTimeout(FET_TLS_HANDSHAKE_MS / 1000);
      uint64_t t0 = nowMs();
      bool ok = tls_->connect(cfg_.host.c_str(), (uint16_t)cfg_.port, FET_TLS_CONNECT_MS) == 1;   // by name: SNI and the name check
      uint32_t hs = (uint32_t)(nowMs() - t0);
      std::string why;
      if (ok && !certCurrent(tls_->getPeerCertificate(), why)) ok = false;
      if (!ok) {
        if (why.empty()) {
          char e[100] = "";
          int code = tls_->lastError(e, sizeof e);
          why = std::to_string(code) + " " + e;
        }
        failConnect_++;
        tlsDrop();
        if (++connectFails_ >= 3) { haveIp_ = false; nextResolve_ = 0; connectFails_ = 0; }   // look the name up again
        if (tlsState_ != 2) say("Secure connection failed: ", why);
        tlsState_ = 2;
        return false;
      }
      connectFails_ = 0;
      if (tlsState_ != 1) say("Secure connection to ", cfg_.host + " (" + std::to_string((unsigned long)hs) + " ms)");
      tlsState_ = 1;
      tls_->setNoDelay(true);
      fresh = true;
    }
    std::string req = std::string("POST ") + path + " HTTP/1.1\r\nHost: " + cfg_.host + "\r\nUser-Agent: " + userAgent +
                      "\r\nX-FET-Token: " + cfg_.token + "\r\nContent-Type: application/json\r\nContent-Length: " +
                      std::to_string(body.size()) + "\r\nConnection: keep-alive\r\n\r\n" + body;
    uint64_t t0 = nowMs();
    bool broken = tls_->write((const uint8_t*)req.data(), req.size()) != req.size();
    std::string raw;
    size_t headEnd = std::string::npos;
    long want = -1;
    bool done = false, closeAfter = false;
    uint64_t limit = fresh ? FET_TLS_HANDSHAKE_MS : FET_HTTP_TIMEOUT_MS;
    while (!broken && nowMs() - t0 < limit) {
      int avail = tls_->available();
      if (avail > 0) {
        char buf[256];
        int n = tls_->read((uint8_t*)buf, avail > (int)sizeof buf ? (int)sizeof buf : avail);
        if (n > 0) raw.append(buf, (size_t)n);
        if (raw.size() > FET_MAX_RESPONSE) { broken = true; break; }
        if (headEnd == std::string::npos && (headEnd = raw.find("\r\n\r\n")) != std::string::npos) {
          std::string lower = raw.substr(0, headEnd + 2);
          for (char& ch : lower) if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
          size_t cl = lower.find("\r\ncontent-length:");
          want = cl == std::string::npos ? -1 : atol(lower.c_str() + cl + 17);
          closeAfter = lower.find("\r\nconnection: close") != std::string::npos;
        }
        if (headEnd != std::string::npos && want >= 0 && raw.size() >= headEnd + 4 + (size_t)want) { done = true; break; }
        continue;
      }
      if (!tls_->connected()) { if (headEnd != std::string::npos && want < 0) done = true; else broken = true; break; }
      delay(2);
    }
    if (done) {
      if (closeAfter) tlsDrop();
      bool got = fet::parseHttpResponse(raw, status, resp);
      if (!got) failReply_++;
      return got;
    }
    tlsDrop();
    if (fresh || !raw.empty()) { failReply_++; return false; }    // a new connection that failed, or a half answer: give up
  }                                                                // the server had closed the kept connection: once more
  failReply_++;
  return false;
}
#endif

bool WifiNet::fetchImage(const uint8_t sha[32], uint32_t size, std::string& err) {
  static const char HX[] = "0123456789abcdef";
  std::string shaHex;
  for (int i = 0; i < 32; i++) { shaHex += HX[sha[i] >> 4]; shaHex += HX[sha[i] & 15]; }
  WiFiClient plain;
  Client* cp = &plain;                                                    // the download: plain to a FeT Monitor, or HTTPS
  std::string host;
#if FET_HTTPS
  NetworkClientSecure* sec = nullptr;
  struct Own { NetworkClientSecure*& p; ~Own() { if (p) { p->stop(); delete p; } } } own{sec};
  if (cfg_.tls) {                                                         // a target at home, from deepTENS (0.4.1)
    sec = tlsForDownload(err);
    if (!sec) return false;
    cp = sec;
    host = cfg_.host;
  } else
#endif
  {
    if (!ready()) { err = "server not reachable"; return false; }
    if (!plain.connect(ip_, (uint16_t)port_, FET_FW_CONNECT_MS)) { err = "could not connect to the server"; return false; }
    host = ip_.toString().c_str();
  }
  Client& c = *cp;
  std::string req = "GET /api/v1/firmware/" + shaHex + " HTTP/1.1\r\nHost: " + host + "\r\nUser-Agent: " +
                    userAgent + "\r\nX-FET-Token: " + cfg_.token + "\r\nConnection: close\r\n\r\n";
  if (c.write((const uint8_t*)req.data(), req.size()) != req.size()) { c.stop(); err = "request not sent"; return false; }
  std::string head;
  uint64_t t0 = nowMs(), last = t0;
  while (head.find("\r\n\r\n") == std::string::npos) {                  // the response header, byte by byte
    if (c.available() > 0) {
      char ch;
      if (c.read((uint8_t*)&ch, 1) == 1) { head += ch; last = nowMs(); }
      if (head.size() > 2048) { c.stop(); err = "response header too long"; return false; }
      continue;
    }
    if (!c.connected() || nowMs() - last > 15000) { c.stop(); err = "no answer from the server"; return false; }
    delay(2);
  }
  int status = (head.compare(0, 9, "HTTP/1.1 ") == 0 || head.compare(0, 9, "HTTP/1.0 ") == 0) ? atoi(head.c_str() + 9) : 0;
  std::string lower = head;
  for (char& ch : lower) if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
  size_t cl = lower.find("\r\ncontent-length:");
  long len = cl == std::string::npos ? -1 : atol(lower.c_str() + cl + 17);
  if (status != 200) { c.stop(); err = "server answered " + std::to_string(status); return false; }
  if (len != (long)size) { c.stop(); err = "wrong length from the server"; return false; }
  if (!Update.begin(size, U_FLASH)) { c.stop(); err = std::string("updater: ") + Update.errorString(); return false; }
  say("Firmware update: downloading ", std::to_string(size) + " bytes");
  tgt::Sha256 h;
  uint8_t buf[1024];
  uint32_t got = 0;
  bool failed = false;
  while (got < size) {
    int avail = c.available();
    if (avail <= 0) {
      if (!c.connected()) { err = "download cut off"; failed = true; break; }
      if (nowMs() - last > 15000 || nowMs() - t0 > 300000) { err = "download too slow"; failed = true; break; }
      delay(2);
      continue;
    }
    size_t want = size - got < sizeof buf ? size - got : sizeof buf;
    int n = c.read(buf, (size_t)avail < want ? (size_t)avail : want);
    if (n <= 0) continue;
    last = nowMs();
    h.update(buf, (size_t)n);
    if (Update.write(buf, (size_t)n) != (size_t)n) { err = std::string("flash write: ") + Update.errorString(); failed = true; break; }
    got += (uint32_t)n;
  }
  c.stop();
  uint8_t digest[32];
  h.finish(digest);
  if (!failed && memcmp(digest, sha, 32) != 0) { err = "SHA-256 mismatch: not the signed image"; failed = true; }
  if (failed) {
    Update.abort();                                         // the running firmware stays; the next start is unchanged
    say("Firmware update failed: ", err);
    return false;
  }
  if (!Update.end(true)) {                                  // checks the image itself, then makes it the next start
    err = std::string("updater: ") + Update.errorString();
    say("Firmware update failed: ", err);
    return false;
  }
  say("Firmware update: verified and installed, restarting", "");
  return true;
}

}  // namespace fete
