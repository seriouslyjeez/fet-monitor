#include "portal.h"
#include <DNSServer.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include "../core/jmini.h"
#include "build_config.h"
#if FET_ENABLE_WEB_UPDATE
#include <ESP8266HTTPUpdateServer.h>
#endif

namespace fetp {

static ESP8266WebServer server(80);
static DNSServer dns;
static IPAddress apIp(192, 168, 4, 1);
#if FET_ENABLE_WEB_UPDATE
static ESP8266HTTPUpdateServer updater;
#endif

static std::string html(const std::string& s) {                  // escape for HTML text and attribute values
  std::string o;
  for (size_t i = 0; i < s.size(); i++) {
    char c = s[i];
    if (c == '&') o += "&amp;"; else if (c == '<') o += "&lt;"; else if (c == '>') o += "&gt;";
    else if (c == '"') o += "&quot;"; else if (c == '\'') o += "&#39;"; else o += c;
  }
  return o;
}

static const char STYLE[] =
    "<meta name=viewport content='width=device-width,initial-scale=1'><style>"
    "body{font:16px system-ui,sans-serif;max-width:30rem;margin:1rem auto;padding:0 1rem;color:#222}"
    "h1{font-size:1.3rem}label{display:block;margin:.8rem 0 .2rem;font-weight:600}"
    "input,select,button{font:inherit;padding:.5rem;width:100%;box-sizing:border-box}"
    "button{margin-top:1rem;background:#0b5;color:#fff;border:0;border-radius:6px}"
    ".err{background:#fde;border:1px solid #c33;padding:.6rem;border-radius:6px}"
    ".note{color:#666;font-size:.85rem}table{border-collapse:collapse;width:100%}"
    "td{padding:.25rem .4rem;border-bottom:1px solid #ddd;word-break:break-all}td:first-child{color:#666;width:45%}"
    ".danger{background:#b33}</style>";

Portal::Portal(fet::Fs& fs, fet::Config& cfg, PlatformHooks& hooks, WifiNet& net, const std::string& deviceId)
    : fs_(fs), cfg_(cfg), hooks_(hooks), net_(net), id_(deviceId) {}

void Portal::begin(fet::Bridge* bridge) {
  bridge_ = bridge;
  routes();
  server.begin();
  started_ = true;
}

void Portal::routes() {
  server.on("/", HTTP_GET, [this]() { handleRoot(); });
  server.on("/status", HTTP_GET, [this]() { handleStatus(); });
  server.on("/scan", HTTP_GET, [this]() { handleScan(); });
  server.on("/save", HTTP_POST, [this]() { handleSave(); });
  server.on("/factory-reset", HTTP_POST, [this]() { handleFactoryReset(); });
  server.onNotFound([this]() { handleNotFound(); });
}

void Portal::open(uint64_t now) {
  if (open_) { deadline_ = now + (uint64_t)FET_PORTAL_MINUTES * 60000UL; return; }
  open_ = true;
  deadline_ = now + (uint64_t)FET_PORTAL_MINUTES * 60000UL;
  FET_STAGE(100);
  if (!(WiFi.getMode() & WIFI_AP)) WiFi.mode(WIFI_AP_STA);   // keep the station side up so shots keep flowing
  FET_STAGE(101);
  WiFi.softAPConfig(apIp, apIp, IPAddress(255, 255, 255, 0));
  std::string ssid = "freETarget-" + id_.substr(id_.size() > 6 ? id_.size() - 6 : 0);
  FET_STAGE(102);
  WiFi.softAP(ssid.c_str());
  FET_STAGE(103);                              // open: it only exists for a few minutes, after a deliberate action
  dns.setErrorReplyCode(DNSReplyCode::NoError);
  dns.start(53, "*", apIp);                               // every name -> us, so phones show the setup page
  FET_STAGE(104);
  WiFi.scanNetworks(true);                                // async; /scan picks it up
  FET_STAGE(105);
#if FET_ENABLE_WEB_UPDATE
  if (!updaterReady_ && !cfg_.token.empty()) {
    updater.setup(&server, "/update", "fet", cfg_.token.c_str());
    updaterReady_ = true;
  }
#endif
}

void Portal::close() {
  if (!open_) return;
  open_ = false;
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
}

void Portal::loop(uint64_t now) {
  now_ = now;
  if (!started_) return;
  if (open_) dns.processNextRequest();
  server.handleClient();
  if (open_ && cfg_.wifiConfigured() && now > deadline_) close();   // never close a portal on a unit with nothing to connect to
}

bool Portal::portalOnly(bool) {
  if (open_) return true;
  server.send(403, "text/plain", "Setup is closed. Power-cycle the target 3 times quickly to open it.");
  return false;
}

void Portal::sendPage(int code, const std::string& body) {
  server.sendHeader("Cache-Control", "no-store");
  server.send(code, "text/html; charset=utf-8", (std::string("<!doctype html><html><head><title>freETarget</title>") + STYLE +
                                                  "</head><body>" + body + "</body></html>").c_str());
}

void Portal::handleRoot() {
  if (open_) deadline_ = now_ + (uint64_t)FET_PORTAL_MINUTES * 60000UL;
  sendPage(200, open_ ? form("") : statusPage());
}

void Portal::handleNotFound() {
  if (!open_) { server.send(404, "text/plain", "not found"); return; }
  deadline_ = now_ + (uint64_t)FET_PORTAL_MINUTES * 60000UL;
  server.sendHeader("Location", "http://192.168.4.1/", true);        // captive-portal probes land on the form
  server.send(302, "text/plain", "");
}

std::string Portal::statusJson() {
  std::string j = bridge_ ? bridge_->statusJson(now_) : std::string("{");
  if (j.empty() || j[j.size() - 1] != '}') j = "{";
  else j.erase(j.size() - 1);
  if (j.size() > 1) j += ",";
  std::string ssid, name, srv;
  fet::jescape(ssid, cfg_.ssid);
  fet::jescape(name, cfg_.name);
  fet::jescape(srv, net_.serverText());
  j += "\"wifi\":" + std::string(net_.wifiUp() ? "true" : "false") + ",\"ssid\":\"" + ssid + "\",\"name\":\"" + name +
       "\",\"server\":\"" + srv + "\",\"rssi\":" + std::to_string(net_.rssi()) + ",\"heap\":" + std::to_string(ESP.getFreeHeap()) +
       ",\"portal\":" + (open_ ? "true" : "false") + ",\"ip\":\"" + WiFi.localIP().toString().c_str() + "\"}";
  return j;
}

void Portal::handleStatus() {
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", statusJson().c_str());
}

std::string Portal::statusPage() {
  return "<h1>freETarget bridge</h1><table id=t></table>"
         "<p class=note>Updates every 3 s. To change WiFi or server settings, switch the target off and on three times "
         "within a few seconds, then join the 'freETarget-…' WiFi network.</p>"
         "<script>function r(){fetch('/status').then(x=>x.json()).then(j=>{var h='';for(var k in j)h+='<tr><td>'+k+'</td><td>'+"
         "String(j[k]).replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</td></tr>';document.getElementById('t').innerHTML=h})"
         ".catch(()=>{})}r();setInterval(r,3000)</script>";
}

std::string Portal::form(const std::string& error) {
  std::string o = "<h1>freETarget setup</h1>";
  if (!error.empty()) o += "<p class=err>" + html(error) + "</p>";
  o += "<form method=post action=/save>"
       "<label>WiFi network</label><input name=ssid id=ssid list=nets value=\"" + html(cfg_.ssid) + "\" maxlength=32 required autocapitalize=off>"
       "<datalist id=nets></datalist>"
       "<label>WiFi password</label><input name=pass type=password maxlength=63 placeholder=\"" +
       std::string(cfg_.pass.empty() ? "" : "leave empty to keep the saved one") + "\">"
       "<label style='font-weight:400'><input type=checkbox name=open value=1 style='width:auto'> This network has no password</label>"
       "<label>Server address (the Raspberry Pi)</label><input name=host value=\"" + html(cfg_.host) + "\" maxlength=64 "
       "placeholder='empty = find it automatically' autocapitalize=off>"
       "<label>Server port</label><input name=port type=number min=1 max=65535 value=\"" + std::to_string(cfg_.port) + "\">"
       "<label>Server token</label><input name=token maxlength=64 autocapitalize=off placeholder=\"" +
       std::string(cfg_.token.empty() ? "" : "leave empty to keep the saved one") + "\">"
       "<label>Name of this target</label><input name=name value=\"" + html(cfg_.name) + "\" maxlength=32 "
       "pattern='[A-Za-z0-9_-]+' placeholder='Lane-1' required>"
       "<p class=note>Letters, digits, - and _ only.</p>"
       "<button>Save and restart</button></form>"
       "<p class=note>Settings are kept if you power off. Shots already stored on the target are kept too.</p>"
       "<form method=post action=/factory-reset onsubmit=\"return confirm('Forget WiFi and server settings?')\">"
       "<button class=danger>Forget all settings</button></form>"
#if FET_ENABLE_WEB_UPDATE
       "<p class=note>Firmware update: <a href=/update>/update</a> (user fet, password = server token)</p>"
#endif
       "<script>function s(){fetch('/scan').then(x=>x.json()).then(a=>{document.getElementById('nets').innerHTML="
       "a.map(n=>'<option value=\"'+n.replace(/\"/g,'&quot;')+'\">').join('')}).catch(()=>{})}s();setTimeout(s,4000);setTimeout(s,10000)</script>";
  return o;
}

void Portal::handleScan() {
  if (!open_) { server.send(403, "text/plain", "closed"); return; }
  std::string j = "[";
  int n = WiFi.scanComplete();
  if (n > 0) {
    bool first = true;
    for (int i = 0; i < n && i < 30; i++) {
      std::string s = WiFi.SSID(i).c_str(), e;
      if (s.empty() || s.size() > 32) continue;
      fet::jescape(e, s);
      if (!first) j += ",";
      j += "\"" + e + "\"";
      first = false;
    }
    WiFi.scanDelete();
    WiFi.scanNetworks(true);
  }
  j += "]";
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", j.c_str());
}

void Portal::handleSave() {
  if (!portalOnly(true)) return;
  deadline_ = now_ + (uint64_t)FET_PORTAL_MINUTES * 60000UL;
  fet::Config c = cfg_;
  std::string ssid = server.arg("ssid").c_str();
  std::string pass = server.arg("pass").c_str();
  c.ssid = ssid;
  if (server.arg("open") == "1") c.pass = "";
  else if (!pass.empty()) c.pass = pass;
  else if (ssid != cfg_.ssid) c.pass = "";                // a new network never inherits the old password
  c.host = server.arg("host").c_str();
  c.port = atoi(server.arg("port").c_str());
  std::string tok = server.arg("token").c_str();
  if (!tok.empty()) c.token = tok;
  c.name = server.arg("name").c_str();
  std::string err = fet::validateConfig(c);
  if (err.empty() && c.token.empty()) err = "The server token is required";
  if (!err.empty()) { sendPage(400, form(err)); return; }
  if (!fet::saveConfig(fs_, c)) { sendPage(500, form("Could not save the settings (storage error). Try again.")); return; }
  sendPage(200, "<h1>Saved</h1><p>The target is restarting and will join <b>" + html(c.ssid) +
                    "</b>. Rejoin your normal WiFi now.</p>");
  hooks_.rebootAt = now_ + 1500;
}

void Portal::handleFactoryReset() {
  if (!portalOnly(true)) return;
  fs_.remove("/config.json");                             // the shot queue is deliberately left alone
  sendPage(200, "<h1>Settings forgotten</h1><p>Restarting. Join the 'freETarget-…' network again to set it up.</p>");
  hooks_.rebootAt = now_ + 1500;
}

}  // namespace fetp
