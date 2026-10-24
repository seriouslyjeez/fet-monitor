#include "eventlog.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "numfmt.h"
#include "crc32.h"
#include "jmini.h"

namespace fet {

static const size_t MAX_MEM = 24;      // events kept in RAM only when flash writes fail

std::string EventLog::segName(uint32_t id) {
  char b[24];
  snprintf(b, sizeof b, "/q%05u.log", (unsigned)id);
  return b;
}

bool EventLog::numAfter(const std::string& s, const char* key, uint32_t& out) {
  size_t p = s.find(key);
  if (p == std::string::npos) return false;
  p += strlen(key);
  if (p >= s.size() || s[p] < '0' || s[p] > '9') return false;
  unsigned long long v = 0;
  while (p < s.size() && s[p] >= '0' && s[p] <= '9') { v = v * 10 + (s[p] - '0'); p++; if (v > 0xFFFFFFFFull) return false; }
  out = (uint32_t)v;
  return true;
}

EventLog::Seg* EventLog::find(uint32_t id) {
  for (size_t k = 0; k < segs_.size(); k++) if (segs_[k].id == id) return &segs_[k];
  return nullptr;
}

size_t EventLog::bytesUsed() const {
  size_t n = 0;
  for (size_t k = 0; k < segs_.size(); k++) n += segs_[k].bytes;
  return n;
}

size_t EventLog::pending() const {
  size_t n = mem_.size();
  for (size_t k = 0; k < items_.size(); k++) if (!items_[k].acked) n++;
  return n;
}

void EventLog::load() {
  items_.clear(); segs_.clear(); mem_.clear(); cur_ = 0;
  std::vector<std::string> names = fs_.list("/q");
  std::sort(names.begin(), names.end());
  for (size_t n = 0; n < names.size(); n++) {
    const std::string& name = names[n];
    unsigned id = 0;
    if (sscanf(name.c_str(), "/q%5u.log", &id) != 1 || name != segName(id)) continue;   // not ours
    nextId_ = std::max<uint32_t>(nextId_, id + 1);
    std::string data;
    if (!fs_.read(name, data)) continue;
    Seg seg{id, (uint32_t)data.size(), 0};
    size_t pos = 0;
    while (pos < data.size()) {
      size_t nl = data.find('\n', pos);
      if (nl == std::string::npos) { if (pos < data.size()) corrupt_++; break; }       // torn last line
      std::string line = data.substr(pos, nl - pos);
      bool ok = line.size() > 10 && line[8] == ' ';
      uint32_t boot = 0, seq = 0;
      if (ok) {
        std::string frag = line.substr(9);
        unsigned long want = strtoul(line.substr(0, 8).c_str(), nullptr, 16);
        ok = crc32(frag.data(), frag.size()) == (uint32_t)want && numAfter(frag, "\"seq\":", seq) && numAfter(frag, "\"boot_count\":", boot);
      }
      if (ok) {
        items_.push_back(Item{boot, seq, id, (uint32_t)pos, (uint32_t)(nl - pos), false});
        seg.live++;
      } else {
        corrupt_++;
      }
      pos = nl + 1;
    }
    if (seg.live == 0) { fs_.remove(name); continue; }
    segs_.push_back(seg);
  }
}

void EventLog::releaseIfEmpty(uint32_t id) {
  Seg* s = find(id);
  if (!s || s->live != 0) return;
  fs_.remove(segName(id));
  items_.erase(std::remove_if(items_.begin(), items_.end(), [id](const Item& i) { return i.seg == id; }), items_.end());
  segs_.erase(std::remove_if(segs_.begin(), segs_.end(), [id](const Seg& g) { return g.id == id; }), segs_.end());
  if (cur_ == id) cur_ = 0;
}

void EventLog::dropOldestSegment() {
  if (segs_.empty()) return;
  uint32_t id = segs_[0].id;
  overflow_ += segs_[0].live;
  fs_.remove(segName(id));
  items_.erase(std::remove_if(items_.begin(), items_.end(), [id](const Item& i) { return i.seg == id; }), items_.end());
  segs_.erase(segs_.begin());
  if (cur_ == id) cur_ = 0;
}

bool EventLog::append(uint32_t boot, uint32_t seq, const char* type, uint64_t uptimeMs, const std::string& rawEscaped) {
  std::string frag;
  frag.reserve(rawEscaped.size() + 96);
  frag += "{\"seq\":" + u32s(seq) + ",\"boot_count\":" + u32s(boot) + ",\"type\":\"" + type + "\",\"uptime_ms\":" +
          u64s(uptimeMs) + ",\"raw\":\"";
  frag += rawEscaped;
  frag += "\"}";
  char crc[12];
  snprintf(crc, sizeof crc, "%08x ", (unsigned)crc32(frag.data(), frag.size()));
  std::string line = std::string(crc) + frag + "\n";

  // make room: respect the byte cap, the entry cap, and leave the filesystem some headroom
  while ((!segs_.empty()) && (bytesUsed() + line.size() > cap_ || items_.size() >= maxPending_ ||
                              fs_.freeBytes() < line.size() + 3000))
    dropOldestSegment();

  if (cur_ != 0) {
    Seg* s = find(cur_);
    if (!s || s->bytes + line.size() > segMax_) cur_ = 0;                // segment full: start another
  }
  if (cur_ == 0) { cur_ = nextId_++; segs_.push_back(Seg{cur_, 0, 0}); }
  Seg* s = find(cur_);
  size_t before = 0;
  fs_.size(segName(cur_), before);
  if (fs_.append(segName(cur_), line)) {
    items_.push_back(Item{boot, seq, cur_, (uint32_t)before, (uint32_t)(line.size() - 1), false});
    s->bytes = (uint32_t)(before + line.size());
    s->live++;
    return true;
  }
  // flash write failed: the file may hold a torn tail; never append to it again
  s->bytes = (uint32_t)before + (uint32_t)line.size();
  cur_ = 0;
  if (mem_.size() >= MAX_MEM) { mem_.erase(mem_.begin()); overflow_++; }
  mem_.push_back(Mem{boot, seq, frag});
  return false;
}

size_t EventLog::nextBatch(size_t maxN, size_t maxBytes, std::string& jsonList, std::vector<std::pair<uint32_t, uint32_t>>& ids) {
  jsonList.clear(); ids.clear();
  for (size_t k = 0; k < items_.size() && ids.size() < maxN; k++) {
    const Item& it = items_[k];
    if (it.acked) continue;
    std::string line;
    if (!fs_.readRange(segName(it.seg), it.off, it.len, line) || line.size() < 10) continue;
    std::string frag = line.substr(9);
    if (!ids.empty() && jsonList.size() + frag.size() + 1 > maxBytes) break;
    if (!ids.empty()) jsonList += ',';
    jsonList += frag;
    ids.push_back(std::make_pair(it.boot, it.seq));
  }
  for (size_t k = 0; k < mem_.size() && ids.size() < maxN; k++) {
    if (!ids.empty() && jsonList.size() + mem_[k].json.size() + 1 > maxBytes) break;
    if (!ids.empty()) jsonList += ',';
    jsonList += mem_[k].json;
    ids.push_back(std::make_pair(mem_[k].boot, mem_[k].seq));
  }
  return ids.size();
}

void EventLog::ack(uint32_t boot, uint32_t seq) {
  for (size_t k = 0; k < mem_.size(); k++)
    if (mem_[k].boot == boot && mem_[k].seq == seq) { mem_.erase(mem_.begin() + k); return; }
  for (size_t k = 0; k < items_.size(); k++) {
    Item& it = items_[k];
    if (it.boot == boot && it.seq == seq && !it.acked) {
      it.acked = true;
      Seg* s = find(it.seg);
      if (s && s->live > 0) s->live--;
      releaseIfEmpty(it.seg);
      return;
    }
  }
}

}  // namespace fet
