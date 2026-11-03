#include "net_esp32.h"
#include <LittleFS.h>
#include <WiFiClient.h>
#include <httpparse.h>
#include <esp_mac.h>

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
void WifiNet::startWifi(const std::string& hostname, bool withAccessPoint) {
  hostname_ = hostname;
  WiFi.persistent(false);                         // our settings live in /config.json, not the SDK's NVS
  WiFi.setHostname(hostname.c_str());
  // Nothing to join: the access point alone. A station with nowhere to go scans channels, and the setup network's
  // replies to a phone get lost meanwhile (B59, 2026-11-02: DHCP answered, the phone never got the answer).
  WiFi.mode(!withAccessPoint ? WIFI_STA : cfg_.wifiConfigured() ? WIFI_AP_STA : WIFI_AP);
  WiFi.setSleep(false);                           // the radio stays awake: a reply sent while it dozes is lost
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

bool WifiNet::wifiUp() const { return WiFi.status() == WL_CONNECTED; }
int WifiNet::rssi() { return wifiUp() ? (int)WiFi.RSSI() : 0; }

std::string WifiNet::serverText() const {
  if (!haveIp_) return cfg_.host.empty() ? "no server address set" : "not resolved";
  return std::string(ip_.toString().c_str()) + ":" + std::to_string(port_);
}

void WifiNet::update(uint64_t now) {
  bool up = wifiUp();
  if (up && !wasUp_) {
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
  if (!up && wasUp_) { haveIp_ = false; nextResolve_ = 0; }
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
  WiFiClient c;
  c.setTimeout(FET_CONNECT_TIMEOUT_MS / 1000 + 1);
  bool got = false;
  if (!c.connect(ip_, (uint16_t)port_, FET_CONNECT_TIMEOUT_MS)) {
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
  }
  if (!reportedFirst_) {
    reportedFirst_ = true;
    say("First report to FET: ", (got && status == 200) ? "success" : "fail");
  }
  return got;
}

}  // namespace fete
