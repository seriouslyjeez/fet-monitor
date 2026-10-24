#include "config.h"
#include <cstdlib>
#include "numfmt.h"
#include "jmini.h"

namespace fet {

bool validName(const std::string& n) {
  if (n.empty() || n.size() > 32) return false;
  for (size_t k = 0; k < n.size(); k++) {
    char c = n[k];
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
  }
  return true;
}

static bool printable(const std::string& s, bool allowSpace) {
  for (size_t k = 0; k < s.size(); k++) {
    unsigned char c = (unsigned char)s[k];
    if (c < 0x20 || c > 0x7e || (!allowSpace && c == ' ')) return false;
  }
  return true;
}

std::string validateConfig(const Config& c) {
  if (c.ssid.empty()) return "WiFi name is required";
  if (c.ssid.size() > 32) return "WiFi name is longer than 32 characters";
  if (!printable(c.ssid, true)) return "WiFi name has characters that cannot be used";
  if (!c.pass.empty() && (c.pass.size() < 8 || c.pass.size() > 63)) return "WiFi password must be 8 to 63 characters (or empty for an open network)";
  if (!printable(c.pass, true)) return "WiFi password has characters that cannot be used";
  if (c.host.size() > 64 || !printable(c.host, false)) return "Server address is not valid";
  for (size_t k = 0; k < c.host.size(); k++)
    if (c.host[k] == '/' || c.host[k] == '"' || c.host[k] == '\\' || c.host[k] == ':') return "Server address must be a name or IP address only (no http:// and no port)";
  if (c.port < 1 || c.port > 65535) return "Server port must be 1 to 65535";
  if (c.token.size() > 64 || !printable(c.token, false)) return "Token must be at most 64 characters without spaces";
  if (!validName(c.name)) return "Target name must be 1 to 32 letters, digits, - or _";
  return "";
}

std::string configToJson(const Config& c) {
  std::string o = "{\"ssid\":\"";
  jescape(o, c.ssid); o += "\",\"pass\":\"";
  jescape(o, c.pass); o += "\",\"host\":\"";
  jescape(o, c.host); o += "\",\"port\":" + i32s(c.port) + ",\"token\":\"";
  jescape(o, c.token); o += "\",\"name\":\"";
  jescape(o, c.name); o += "\"}";
  return o;
}

bool configFromJson(const std::string& json, Config& out) {
  JVal v;
  if (!jparse(json, v) || v.type != JVal::Obj) return false;
  Config c = out;
  const JVal* x;
  if ((x = v.get("ssid")) && x->type == JVal::Str) c.ssid = x->s;
  if ((x = v.get("pass")) && x->type == JVal::Str) c.pass = x->s;
  if ((x = v.get("host")) && x->type == JVal::Str) c.host = x->s;
  long long n;
  if ((x = v.get("port")) && x->asInt(n)) c.port = (int)n;
  if ((x = v.get("token")) && x->type == JVal::Str) c.token = x->s;
  if ((x = v.get("name")) && x->type == JVal::Str) c.name = x->s;
  out = c;
  return true;
}

bool loadConfig(Fs& fs, Config& out) {
  std::string data;
  Config c = out;
  if (!fs.read("/config.json", data) || !configFromJson(data, c) || !validateConfig(c).empty()) return false;
  out = c;
  return true;
}

bool saveConfig(Fs& fs, const Config& c) {
  if (!validateConfig(c).empty()) return false;
  return fs.writeAtomic("/config.json", configToJson(c));
}

bool QuickBoot::onBoot() {
  std::string d;
  long n = 0;
  if (fs_.read("/qb.cnt", d)) n = atol(d.c_str());
  if (n < 0 || n > 100) n = 0;
  n++;
  if (n >= TRIGGER) {
    fs_.writeAtomic("/qb.cnt", "0");
    armed_ = false;
    return true;
  }
  fs_.writeAtomic("/qb.cnt", i32s(n));
  armed_ = true;
  return false;
}

void QuickBoot::tick(uint64_t now) {
  if (armed_ && now >= STABLE_MS) {
    fs_.writeAtomic("/qb.cnt", "0");
    armed_ = false;
  }
}

}  // namespace fet
