#include "platform.h"
#include "../core/httpparse.h"
#include <ESP8266WiFi.h>
#include <LittleFS.h>
#include <WiFiClient.h>
#include <stdlib.h>
#include "build_config.h"
#if FET_ENABLE_MDNS
#include <ESP8266mDNS.h>
#endif

namespace fetp {

// ---------------------------------------------------------------- clock
uint64_t nowMs() {
  static uint32_t last = 0;
  static uint64_t high = 0;
  uint32_t m = millis();
  if (m < last) high += (uint64_t)1 << 32;
  last = m;
  return high | m;
}

std::string deviceIdFromChip() {
  char b[16];
  snprintf(b, sizeof b, "fet-%06x", (unsigned)(ESP.getChipId() & 0xFFFFFF));
  return b;
}

// ---------------------------------------------------------------- filesystem
static std::string withSlash(const char* n) {
  std::string s = n;
  if (s.empty() || s[0] != '/') s = "/" + s;
  return s;
}

bool LfsFs::begin() {
  if (LittleFS.begin()) return true;
  if (!LittleFS.format()) return false;     // first boot on a fresh flash
  return LittleFS.begin();
}

size_t LfsFs::totalBytes() {
  FSInfo fi;
  if (!LittleFS.info(fi)) return 0;
  return fi.totalBytes;
}

size_t LfsFs::freeBytes() {
  FSInfo fi;
  if (!LittleFS.info(fi)) return 0;
  return fi.totalBytes > fi.usedBytes ? fi.totalBytes - fi.usedBytes : 0;
}

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
  LittleFS.remove(path.c_str());                  // some versions will not rename over an existing file
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
  Dir d = LittleFS.openDir("/");
  while (d.next()) {
    std::string n = withSlash(d.fileName().c_str());
    if (n.compare(0, prefix.size(), prefix) == 0) r.push_back(n);
  }
  return r;
}

// ---------------------------------------------------------------- serial
void RxHold::pump() {
  while (Serial.available() > 0 && bytes.size() < MAX) {
    int c = Serial.read();
    if (c < 0) break;
    bytes += (char)c;
  }
}

void SerialUart::write(const std::string& s) {
  Serial.write((const uint8_t*)s.data(), s.size());
  Serial.flush();
}

void PlatformHooks::reboot() { rebootAt = nowMs() + 400; }

uint32_t PlatformHooks::random32() { return (uint32_t)RANDOM_REG32 ^ (uint32_t)micros(); }   // hardware RNG register, plus start-up jitter

// ---------------------------------------------------------------- status lines
void say(const char* label, const std::string& value) {
#if FET_STATUS_MESSAGES
  Serial.print(label);
  for (size_t i = 0; i < value.size(); i++) {
    unsigned char c = (unsigned char)value[i];
    Serial.write((c < 0x20 || c > 0x7e || c == '{' || c == '}') ? (uint8_t)'?' : (uint8_t)c);   // never let a name look like a command
  }
  Serial.write((uint8_t)'\n');
#else
  (void)label; (void)value;
#endif
}

// ---------------------------------------------------------------- WiFi + HTTP
void WifiNet::startWifi(const std::string& hostname, bool withAccessPoint) {
  hostname_ = hostname;
  WiFi.persistent(false);                         // our config lives in /config.json, not the SDK's flash area
  WiFi.setAutoReconnect(true);
  WiFi.mode(withAccessPoint ? WIFI_AP_STA : WIFI_STA);   // one switch, straight from "off": the well-trodden path
  WiFi.hostname(hostname.c_str());
  static WiFiEventHandler onDisc, onConn;           // must stay alive for the handlers to keep working
  if (!handlerSet_) {
    handlerSet_ = true;
    onDisc = WiFi.onStationModeDisconnected([this](const WiFiEventStationModeDisconnected& e) { assoc_ = false; lastReason_ = (int)e.reason; });
    onConn = WiFi.onStationModeConnected([this](const WiFiEventStationModeConnected& e) { assoc_ = true; assocChannel_ = (int)e.channel; });
  }
  if (cfg_.wifiConfigured()) {
#if FET_STATUS_MESSAGES
    // One look at the air BEFORE connecting (a scan while connecting aborts the attempt): what does the ESP see?
    int n = WiFi.scanNetworks(false, true);
    int found = 0;
    for (int i = 0; i < n; i++) {
      if (WiFi.SSID(i) == cfg_.ssid.c_str()) {
        found++;
        uint8_t enc = WiFi.encryptionType(i);
        const char* e = enc == 4 ? "WPA2" : enc == 2 ? "WPA/TKIP" : enc == 7 ? "open" : enc == 8 ? "WPA/WPA2" : enc == 5 ? "WEP" : "other";
        notes_.push_back("Scan: found " + cfg_.ssid + " channel " + std::to_string((int)WiFi.channel(i)) + ", signal " +
                         std::to_string((int)WiFi.RSSI(i)) + " dBm, security " + e);
      }
    }
    if (n < 0) notes_.push_back("Scan: failed");
    else if (!found) notes_.push_back("Scan: " + cfg_.ssid + " not visible. Networks seen: " + std::to_string(n));
    WiFi.scanDelete();
#endif
    if (cfg_.fixedIp()) {                                       // skip DHCP entirely
      uint8_t a[4], g[4], m[4] = {255, 255, 255, 0};
      fet::parseIpv4(cfg_.ip, a); fet::parseIpv4(cfg_.gateway, g);
      if (!cfg_.mask.empty()) fet::parseIpv4(cfg_.mask, m);
      WiFi.config(IPAddress(a[0], a[1], a[2], a[3]), IPAddress(g[0], g[1], g[2], g[3]), IPAddress(m[0], m[1], m[2], m[3]), IPAddress(g[0], g[1], g[2], g[3]));
      notes_.push_back("Using fixed IP address " + cfg_.ip);
    }
    lastBegin_ = nowMs();
    WiFi.begin(cfg_.ssid.c_str(), cfg_.pass.c_str());
  }
}

void WifiNet::announce() {
  if (cfg_.wifiConfigured()) say("Attempting to connect to network: ", cfg_.ssid);
  else say("No WiFi settings saved: ", "waiting for setup");
  for (size_t i = 0; i < notes_.size(); i++) say("", notes_[i]);
}

static const char* reasonText(int r) {
  switch (r) {
    case 0: return "";
    case 2: return "authentication expired";
    case 5: return "access point is full";
    case 15: return "wrong password?";
    case 200: return "lost the beacon (signal too weak?)";
    case 201: return "network not found";
    case 202: return "authentication failed (wrong password?)";
    case 203: return "access point refused the association";
    case 204: return "handshake timed out (wrong password?)";
    default: return "see SDK reason code";
  }
}

void WifiNet::stopSta() { WiFi.disconnect(); }

bool WifiNet::wifiUp() const { return WiFi.status() == WL_CONNECTED; }

int WifiNet::rssi() { return wifiUp() ? (int)WiFi.RSSI() : 0; }

std::string WifiNet::serverText() const {
  if (!haveIp_) return cfg_.host.empty() ? "searching (mDNS)" : "not resolved";
  return std::string(ip_.toString().c_str()) + ":" + std::to_string(port_);
}

void WifiNet::update(uint64_t now) {
  bool up = wifiUp();
#if FET_ENABLE_MDNS
  if (up && !wasUp_) {                            // (re)joined the network: announce ourselves again
    if (mdnsUp_) MDNS.end();
    mdnsUp_ = MDNS.begin(hostname_.c_str());
    if (mdnsUp_) MDNS.addService("http", "tcp", 80);
  }
  if (mdnsUp_ && up) MDNS.update();
#endif
  if (up) lastReason_ = 0;                                              // a stale failure reason must not outlive the failure
#if FET_STATUS_MESSAGES
  if (up && !wasUp_) say("Connected to network: ", std::string(WiFi.SSID().c_str()) + " (" + WiFi.localIP().toString().c_str() + ")");
#endif
#if FET_STATUS_MESSAGES
  if (!up && cfg_.wifiConfigured() && now >= nextWifiNote_) {          // say why, every 10 s, until it connects
    nextWifiNote_ = now + 10000;
    wl_status_t st = WiFi.status();
    const char* why = st == WL_NO_SSID_AVAIL ? "network not found"
                    : (st == WL_CONNECT_FAILED || st == WL_WRONG_PASSWORD) ? "wrong password"
                    : st == WL_IDLE_STATUS ? "connecting"
                    : st == WL_DISCONNECTED ? "not connected yet" : "waiting";
    std::string line = cfg_.ssid + ": " + why;
    if (lastReason_) line += std::string(" [reason ") + std::to_string(lastReason_) + ": " + reasonText(lastReason_) + "]";
    line += std::string(" (status ") + std::to_string((int)st) + (assoc_ ? ", associated on channel " + std::to_string(assocChannel_) + ": waiting for an IP address" : ", not associated") + ")";
    say("Not connected to ", line);
  }
#endif
  if (!up && cfg_.wifiConfigured() && lastBegin_ && now - lastBegin_ > (assoc_ ? 180000UL : 45000UL)) {   // a stuck attempt: start it afresh (a slow DHCP wait gets longer: a restart only delays it)
    lastBegin_ = now;
    say("Retrying connection to ", cfg_.ssid);
    WiFi.disconnect();
    WiFi.begin(cfg_.ssid.c_str(), cfg_.pass.c_str());
  }
  if (!up && wasUp_) { haveIp_ = false; nextResolve_ = 0; }
  wasUp_ = up;
  (void)now;
}

bool WifiNet::resolveServer(uint64_t now) {
  if (haveIp_ && now - ipAt_ < 600000UL) return true;      // re-resolve every 10 min in case the Pi moved
  if (now < nextResolve_) return haveIp_;
  nextResolve_ = now + 15000;                             // a failed lookup blocks; don't retry more than every 15 s
  IPAddress ip;
  int port = cfg_.port;
  bool ok = false;
  if (!cfg_.host.empty()) {
    if (ip.fromString(cfg_.host.c_str())) ok = true;
    else ok = WiFi.hostByName(cfg_.host.c_str(), ip, 2000) == 1;
  }
#if FET_ENABLE_MDNS
  else if (mdnsUp_) {
    int n = MDNS.queryService("freetarget", "tcp");
    if (n > 0) { ip = MDNS.IP(0); port = MDNS.port(0); ok = true; }
  }
#endif
  if (ok) { ip_ = ip; port_ = port; haveIp_ = true; ipAt_ = now; connectFails_ = 0; }
  else if (haveIp_ && now - ipAt_ > 1800000UL) haveIp_ = false;
  return haveIp_;
}

bool WifiNet::ready() { return wifiUp() && resolveServer(nowMs()); }

bool WifiNet::postOnce(const char* path, const std::string& body, int& status, std::string& resp) {
  WiFiClient c;
  c.setTimeout(FET_CONNECT_TIMEOUT_MS);
  if (!c.connect(ip_, (uint16_t)port_)) {
    if (++connectFails_ >= 3) { haveIp_ = false; nextResolve_ = 0; connectFails_ = 0; }   // maybe the server moved
    return false;
  }
  connectFails_ = 0;
  c.setNoDelay(true);
  std::string req = std::string("POST ") + path + " HTTP/1.1\r\nHost: " + ip_.toString().c_str() +
                    "\r\nX-FET-Token: " + cfg_.token + "\r\nContent-Type: application/json\r\nContent-Length: " +
                    std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
  size_t sent = 0;
  uint64_t t0 = nowMs();
  while (sent < req.size()) {
    size_t n = c.write((const uint8_t*)req.data() + sent, req.size() - sent);
    sent += n;
    hold_.pump();
    if (n == 0) {
      if (!c.connected() || nowMs() - t0 > FET_HTTP_TIMEOUT_MS) { c.stop(); return false; }
      delay(1);
    }
  }
  std::string raw;
  bool complete = false;
  while (nowMs() - t0 < FET_HTTP_TIMEOUT_MS) {
    hold_.pump();
    int avail = c.available();
    if (avail > 0) {
      char buf[256];
      int n = c.read((uint8_t*)buf, avail > (int)sizeof buf ? (int)sizeof buf : avail);
      if (n > 0) raw.append(buf, (size_t)n);
      if (raw.size() > FET_MAX_RESPONSE) break;           // far too big to be ours
      continue;
    }
    if (!c.connected()) { complete = true; break; }       // server closed: that is the end of the body
    delay(1);
  }
  c.stop();
  if (!complete) return false;
  return fet::parseHttpResponse(raw, status, resp);
}

bool WifiNet::post(const char* path, const std::string& body, int& status, std::string& resp) {
  bool got = postOnce(path, body, status, resp);
#if FET_STATUS_MESSAGES
  if (!reportedFirst_) {
    reportedFirst_ = true;
    say("First report to FET: ", (got && status == 200) ? "success" : "fail");
  }
#endif
  return got;
}

}  // namespace fetp
