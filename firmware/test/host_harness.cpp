// Runs the firmware core on a PC against a real server. Driven line by line from stdin by tests/test_firmware_host.py.
// A fake flash (can tear writes, fill up, fail), a real TCP client (can be "down" or lose replies), a captured UART.
#include <arpa/inet.h>
#include <netdb.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include "../fet_bridge/src/core/bridge.h"
#include "../fet_bridge/src/core/config.h"
#include "../fet_bridge/src/core/jmini.h"
#include "../fet_bridge/src/core/httpparse.h"

using namespace fet;

struct MemFs : Fs {
  std::map<std::string, std::string> files;
  long tearNext = -1;       // next append writes only this many bytes, then fails (power cut mid-write)
  bool failAppends = false;
  long freeSet = -1;
  bool read(const std::string& p, std::string& o) override { auto i = files.find(p); if (i == files.end()) return false; o = i->second; return true; }
  bool readRange(const std::string& p, size_t off, size_t len, std::string& o) override {
    auto i = files.find(p); if (i == files.end() || off > i->second.size()) return false;
    o = i->second.substr(off, len); return true; }
  bool writeAtomic(const std::string& p, const std::string& d) override { files[p] = d; return true; }
  bool append(const std::string& p, const std::string& d) override {
    if (failAppends) return false;
    if (tearNext >= 0) { files[p] += d.substr(0, (size_t)tearNext); tearNext = -1; return false; }
    files[p] += d; return true; }
  bool remove(const std::string& p) override { return files.erase(p) > 0; }
  bool size(const std::string& p, size_t& o) override { auto i = files.find(p); o = i == files.end() ? 0 : i->second.size(); return i != files.end(); }
  std::vector<std::string> list(const std::string& pre) override {
    std::vector<std::string> r; for (auto& kv : files) if (kv.first.compare(0, pre.size(), pre) == 0) r.push_back(kv.first); return r; }
  size_t freeBytes() override { if (freeSet >= 0) return (size_t)freeSet; return 1 << 20; }
};

struct TcpNet : Net {
  std::string host = "127.0.0.1", token;
  int port = 0;
  bool up = true;
  int dropPct = 0;            // chance a reply is lost AFTER the server has handled the request
  int fakeStatus = 0;         // if set, answer this status without talking to the server
  unsigned requests = 0;
  unsigned rng = 12345;
  bool ready() override { return up; }
  int rssi() override { return -57; }
  bool post(const char* path, const std::string& body, int& status, std::string& resp) override {
    requests++;
    if (fakeStatus) { status = fakeStatus; resp = "{}"; return true; }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(port); inet_pton(AF_INET, host.c_str(), &a.sin_addr);
    if (connect(fd, (sockaddr*)&a, sizeof a) != 0) { close(fd); return false; }
    std::string req = std::string("POST ") + path + " HTTP/1.1\r\nHost: " + host + "\r\nX-FET-Token: " + token +
                      "\r\nContent-Type: application/json\r\nContent-Length: " + std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
    size_t sent = 0;
    while (sent < req.size()) { ssize_t n = write(fd, req.data() + sent, req.size() - sent); if (n <= 0) { close(fd); return false; } sent += (size_t)n; }
    std::string all; char buf[2048];
    for (;;) { pollfd p{fd, POLLIN, 0}; if (poll(&p, 1, 3000) <= 0) break; ssize_t n = read(fd, buf, sizeof buf); if (n <= 0) break; all.append(buf, (size_t)n); }
    close(fd);
    rng = rng * 1103515245u + 12345u;
    if ((int)((rng >> 8) % 100) < dropPct) return false;      // the server handled it; we never hear
    if (all.compare(0, 5, "HTTP/") != 0) return false;
    status = atoi(all.c_str() + 9);
    size_t h = all.find("\r\n\r\n");
    resp = h == std::string::npos ? "" : all.substr(h + 4);
    return true;
  }
};

struct CapUart : Uart { std::string log; void write(const std::string& s) override { log += s; } };
struct CapHooks : Hooks { int reboots = 0, portals = 0; uint32_t rnd = 12345; void reboot() override { reboots++; } void openPortal() override { portals++; }
  uint32_t random32() override { rnd = rnd * 1664525u + 1013904223u; return rnd; } };

static std::string unescape(const std::string& s) {
  std::string o;
  for (size_t i = 0; i < s.size(); i++) {
    if (s[i] != '\\' || i + 1 >= s.size()) { o += s[i]; continue; }
    char c = s[++i];
    if (c == 'n') o += '\n'; else if (c == 'r') o += '\r'; else if (c == '\\') o += '\\';
    else if (c == 'x' && i + 2 < s.size()) { o += (char)strtol(s.substr(i + 1, 2).c_str(), nullptr, 16); i += 2; }
    else o += c;
  }
  return o;
}
static std::string escape(const std::string& s) {
  std::string o; char b[8];
  for (unsigned char c : s) { if (c == '\n') o += "\\n"; else if (c == '\\') o += "\\\\"; else if (c < 0x20 || c > 0x7e) { snprintf(b, sizeof b, "\\x%02x", c); o += b; } else o += (char)c; }
  return o;
}

int main() {
  MemFs fs; TcpNet net; CapUart uart; CapHooks hooks;
  BridgeInfo info{"fet-test01", "Test Lane", "fw-test"};
  size_t cap = 90000;
  std::unique_ptr<Bridge> br;
  uint64_t t = 0;
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream is(line); std::string cmd; is >> cmd;
    std::string rest; std::getline(is, rest); if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
    if (cmd == "cfg") { std::istringstream a(rest); std::string tok; a >> net.port >> tok; net.token = tok; }
    else if (cmd == "cap") cap = (size_t)atol(rest.c_str());
    else if (cmd == "boot") { br.reset(); t = 0; br.reset(new Bridge(fs, net, uart, hooks, info, cap)); br->begin(t); }
    else if (cmd == "uart") { std::string d = unescape(rest); for (unsigned char c : d) { t += 1; br->onByte(c, t); } }
    else if (cmd == "tick") { long ms = atol(rest.c_str()); for (long k = 0; k < ms; k += 50) { t += 50; br->tick(t); } }
    else if (cmd == "wipe") { fs.files.clear(); }
    else if (cmd == "net") net.up = rest == "up";
    else if (cmd == "drop") net.dropPct = atoi(rest.c_str());
    else if (cmd == "fake") net.fakeStatus = atoi(rest.c_str());
    else if (cmd == "token") net.token = rest;
    else if (cmd == "tear") fs.tearNext = atol(rest.c_str());
    else if (cmd == "failappends") fs.failAppends = rest == "1";
    else if (cmd == "free") fs.freeSet = atol(rest.c_str());
    else if (cmd == "status") std::cout << br->statusJson(t) << "\n";
    else if (cmd == "uartlog") std::cout << escape(uart.log) << "\n";
    else if (cmd == "flags") std::cout << hooks.reboots << " " << hooks.portals << " " << net.requests << " " << br->bootCount() << " " << br->queued() << "\n";
    else if (cmd == "files") { for (auto& kv : fs.files) if (kv.first.compare(0, 2, "/q") == 0) std::cout << kv.first << " "; std::cout << "\n"; }
    else if (cmd == "jparse") { JVal v; std::cout << (jparse(unescape(rest), v) ? "OK" : "ERR") << "\n"; }
    else if (cmd == "cfgcheck") { Config c; if (!configFromJson(unescape(rest), c)) std::cout << "BADJSON\n"; else { std::string e = validateConfig(c); std::cout << (e.empty() ? "VALID" : e) << "\n"; } }
    else if (cmd == "qboot") { QuickBoot q(fs); bool r = q.onBoot(); std::string v; fs.read("/qb.cnt", v); std::cout << (r ? "PORTAL" : "normal") << " " << v << "\n"; }
    else if (cmd == "qtick") { QuickBoot q(fs); q.onBoot(); q.tick((uint64_t)atol(rest.c_str())); std::string v; fs.read("/qb.cnt", v); std::cout << v << "\n"; }
    else if (cmd == "httpparse") { int st = 0; std::string b; bool ok = parseHttpResponse(unescape(rest), st, b); std::cout << (ok ? "OK " + std::to_string(st) + " [" + escape(b) + "]" : std::string("BAD")) << "\n"; }
    else if (cmd == "quit") break;
    std::cout << "." << std::endl;
  }
  return 0;
}
