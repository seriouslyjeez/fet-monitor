#include "bridge.h"
#include <cstdio>
#include <cstdlib>
#include "numfmt.h"
#include "jmini.h"

namespace fet {

static const uint32_t BACKOFF_MIN = 1000, BACKOFF_MAX = 30000;
static const size_t BATCH_BYTES = 5000;

Bridge::Bridge(Fs& fs, Net& net, Uart& uart, Hooks& hooks, const BridgeInfo& info, size_t cap, size_t segBytes)
    : fs_(fs), net_(net), uart_(uart), hooks_(hooks), info_(info), log_(fs, cap, segBytes), asm_(*this) {}

void Bridge::begin(uint64_t now) {
  now_ = now;
  std::string d;
  unsigned long old = 0;
  bool have = fs_.read("/boot.cnt", d) && !d.empty();
  if (have) old = strtoul(d.c_str(), nullptr, 10);
  if (have && old > 0 && old < 4000000000UL) {
    boot_ = (uint32_t)old + 1;
  } else {
    // The counter is gone (flash erased, filesystem rebuilt): start from a random number rather than 1, so this run
    // cannot be mistaken for an earlier run of the same device that also began at 1, 2, 3...
    boot_ = 1000 + hooks_.random32() % 3000000UL;
    lastCmd_ = 0;
  }
  fsOk = fs_.writeAtomic("/boot.cnt", u32s(boot_));
  if (fs_.read("/cmd.id", d)) lastCmd_ = atoll(d.c_str());
  log_.load();
  nextEvent_ = now;
  nextHb_ = now;
}

void Bridge::onByte(uint8_t c, uint64_t now) {
  now_ = now;
  asm_.feed(c, now);
}

void Bridge::learnVersion(const char* type, const std::string& text) {
  std::string v;
  if (std::string(type) == "arduino_boot") {
    size_t a = text.find('"');
    size_t b = a == std::string::npos ? a : text.find('"', a + 1);
    if (b != std::string::npos) v = text.substr(a + 1, b - a - 1);
  } else {
    size_t p = text.find("\"VERSION\"");
    if (p != std::string::npos) {
      size_t a = text.find('"', p + 9);
      size_t b = a == std::string::npos ? a : text.find('"', a + 1);
      if (b != std::string::npos) v = text.substr(a + 1, b - a - 1);
    }
  }
  if (!v.empty() && v.size() <= 40) arduinoVersion_ = v;
}

void Bridge::onRecord(const char* type, const std::string& text) {
  learnVersion(type, text);
  std::string esc;
  jescape(esc, text);
  seq_++;
  log_.append(boot_, seq_, type, now_, esc);
  if (evBackoff_ == 0) nextEvent_ = now_;                  // send promptly unless we are backing off
}

void Bridge::tick(uint64_t now) {
  now_ = now;
  asm_.tick(now);
  if (!net_.ready()) return;
  if (log_.pending() && now >= nextEvent_) sendEvents(now);
  if (now >= nextHb_) sendHeartbeat(now);
}

void Bridge::sendEvents(uint64_t now) {
  std::string list;
  std::vector<std::pair<uint32_t, uint32_t>> ids;
  if (!log_.nextBatch(batchMax_, BATCH_BYTES, list, ids)) return;
  std::string body = "{\"device_id\":\"" + info_.deviceId + "\",\"boot_count\":" + u32s(boot_) +
                     ",\"uptime_ms\":" + u64s(now) + ",\"events\":[" + list + "]}";
  int status = 0;
  std::string resp;
  sentBatches++;
  bool got = net_.post("/api/v1/events", body, status, resp);
  lastStatus = got ? status : 0;
  auto fail = [&](const char* why) {
    httpErrors++;
    lastError_ = why;
    evBackoff_ = evBackoff_ ? (evBackoff_ * 2 > BACKOFF_MAX ? BACKOFF_MAX : evBackoff_ * 2) : BACKOFF_MIN;
    nextEvent_ = now + evBackoff_;
  };
  if (!got) return fail("no response from server");
  if (status == 401 || status == 403) { authError = true; evBackoff_ = BACKOFF_MAX; nextEvent_ = now + BACKOFF_MAX; lastError_ = "server refused our token"; httpErrors++; return; }
  authError = false;
  if (status == 400 || status == 413) {                    // the server will not take this batch: find the bad event
    if (ids.size() > 1) { batchMax_ = 1; nextEvent_ = now; return; }
    log_.ack(ids[0].first, ids[0].second);                 // a single event it will never accept: drop it, do not block the queue
    droppedPoison++;
    lastError_ = "server rejected an event; dropped";
    nextEvent_ = now;
    return;
  }
  if (status != 200) return fail("server error");
  JVal v;
  if (!jparse(resp, v) || v.type != JVal::Obj) return fail("unreadable reply");
  size_t before = log_.pending();
  const JVal* acked = v.get("acked");
  if (acked && acked->type == JVal::Arr) {
    for (size_t k = 0; k < acked->a.size(); k++) {
      const JVal& p = acked->a[k];
      long long b, s;
      if (p.type == JVal::Arr && p.a.size() == 2 && p.a[0].asInt(b) && p.a[1].asInt(s) && b >= 0 && s >= 0) {
        log_.ack((uint32_t)b, (uint32_t)s);
        ackedEvents++;
      }
    }
  }
  const JVal* rej = v.get("rejected");
  if (rej && rej->type == JVal::Arr) {                     // the server will never accept these: stop retrying them
    for (size_t k = 0; k < rej->a.size(); k++) {
      const JVal* b = rej->a[k].get("boot_count");
      const JVal* s = rej->a[k].get("seq");
      long long bv, sv;
      if (b && s && b->asInt(bv) && s->asInt(sv) && bv >= 0 && sv >= 0) {
        log_.ack((uint32_t)bv, (uint32_t)sv);
        rejectedEvents++;
      }
    }
  }
  if (log_.pending() >= before) return fail("server acknowledged nothing");
  evBackoff_ = 0;
  batchMax_ = 10;
  lastError_.clear();
  nextEvent_ = now;                                        // more waiting? go again straight away
}

void Bridge::sendHeartbeat(uint64_t now) {
  std::string name;
  jescape(name, info_.name);
  std::string body = "{\"device_id\":\"" + info_.deviceId + "\",\"boot_count\":" + u32s(boot_) +
                     ",\"uptime_ms\":" + u64s(now) + ",\"name\":\"" + name + "\",\"bridge_fw\":\"" +
                     info_.fwVersion + "\"";
  if (!arduinoVersion_.empty()) { std::string av; jescape(av, arduinoVersion_); body += ",\"arduino_version\":\"" + av + "\""; }
  body += ",\"rssi\":" + i32s(net_.rssi()) + ",\"queue_depth\":" + u32s((uint32_t)log_.pending()) +
          ",\"overflow_count\":" + u32s((uint32_t)log_.overflow()) + ",\"cmd_results\":[";
  size_t sentResults = results_.size();
  for (size_t k = 0; k < sentResults; k++) {
    std::string r;
    jescape(r, results_[k].reply);
    if (k) body += ",";
    body += "{\"id\":" + i64s(results_[k].id) + ",\"status\":\"" + (results_[k].ok ? "done" : "failed") + "\",\"reply\":\"" + r + "\"}";
  }
  body += "]}";
  int status = 0;
  std::string resp;
  bool got = net_.post("/api/v1/heartbeat", body, status, resp);
  if (!got || status != 200) {
    if (got && (status == 401 || status == 403)) { authError = true; lastError_ = "server refused our token"; }
    else if (!got) lastError_ = "no response from server";
    hbBackoff_ = hbBackoff_ ? (hbBackoff_ * 2 > BACKOFF_MAX ? BACKOFF_MAX : hbBackoff_ * 2) : BACKOFF_MIN;
    nextHb_ = now + hbBackoff_;
    return;
  }
  authError = false;
  hbBackoff_ = 0;
  results_.erase(results_.begin(), results_.begin() + sentResults);     // the server has them now
  JVal v;
  nextHb_ = now + hbInterval_;
  if (!jparse(resp, v) || v.type != JVal::Obj) return;
  long long iv;
  const JVal* x = v.get("heartbeat_interval_ms");
  if (x && x->asInt(iv)) hbInterval_ = (uint32_t)(iv < 1000 ? 1000 : iv > 60000 ? 60000 : iv);
  nextHb_ = now + hbInterval_;
  const JVal* cmds = v.get("commands");
  bool ran = false;
  if (cmds && cmds->type == JVal::Arr) {
    for (size_t k = 0; k < cmds->a.size(); k++) {
      const JVal& c = cmds->a[k];
      long long id;
      const JVal* t = c.get("type");
      const JVal* ln = c.get("line");
      if (!c.get("id") || !c.get("id")->asInt(id) || id <= 0 || !t || t->type != JVal::Str) continue;
      runCommand(id, t->s, ln && ln->type == JVal::Str ? ln->s : "");
      ran = true;
    }
  }
  if (ran) nextHb_ = now;                                  // report the results at once
  if (rebootPending_ && results_.empty()) { rebootPending_ = false; hooks_.reboot(); }
}

void Bridge::runCommand(long long id, const std::string& type, const std::string& line) {
  if (id <= lastCmd_) {                                    // already run before a restart: just report it again
    results_.push_back(Result{id, true, "already done"});
    return;
  }
  fs_.writeAtomic("/cmd.id", i64s(id));          // record BEFORE running: at most once
  lastCmd_ = id;
  if (type == "serial") {
    // Same allowlist as the server: {"ECHO":0}, {"VERSION":0}, {"NEW":<digits>}. Anything else never reaches the Arduino.
    bool ok = line == "{\"ECHO\":0}" || line == "{\"VERSION\":0}";
    if (!ok && line.size() >= 9 && line.size() <= 14 && line.compare(0, 7, "{\"NEW\":") == 0 && line[line.size() - 1] == '}') {
      ok = line.size() > 8;
      for (size_t k = 7; ok && k + 1 < line.size(); k++) if (line[k] < '0' || line[k] > '9') ok = false;
    }
    if (!ok) { results_.push_back(Result{id, false, "refused: not an allowed command"}); return; }
    uart_.write(line + "\n");
    results_.push_back(Result{id, true, "sent"});
  } else if (type == "wifi_portal") {
    hooks_.openPortal();
    results_.push_back(Result{id, true, "portal opened"});
  } else if (type == "reboot") {
    results_.push_back(Result{id, true, "rebooting"});
    rebootPending_ = true;
  } else {
    results_.push_back(Result{id, false, "unknown command"});
  }
}

std::string Bridge::statusJson(uint64_t now) const {
  std::string e, av, name, fw;
  jescape(e, lastError_);
  jescape(av, arduinoVersion_);
  jescape(fw, info_.fwVersion);
  auto num = [](const char* k, uint64_t v) { return std::string(",\"") + k + "\":" + u64s(v); };
  auto flag = [](const char* k, bool v) { return std::string(",\"") + k + "\":" + (v ? "true" : "false"); };
  std::string o = "{\"device_id\":\"" + info_.deviceId + "\",\"fw\":\"" + fw + "\"";
  o += num("boot_count", boot_) + num("uptime_s", now / 1000) + num("queued", log_.pending()) + num("overflow", log_.overflow());
  o += num("memory_only", log_.memoryOnly()) + num("corrupt_lines", log_.corruptLines()) + num("acked", ackedEvents);
  o += num("rejected", rejectedEvents) + num("dropped_poison", droppedPoison) + num("http_errors", httpErrors);
  o += ",\"last_status\":" + i32s(lastStatus) + flag("auth_error", authError);
  o += ",\"last_error\":\"" + e + "\",\"arduino_version\":\"" + av + "\"";
  o += num("discarded_objects", asm_.discarded()) + num("ignored_lines", asm_.ignoredLines()) + flag("fs_ok", fsOk) + "}";
  return o;
}

}  // namespace fet
