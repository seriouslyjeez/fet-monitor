#include "jmini.h"
#include <cstdlib>
#include <cstring>

namespace fet {

const JVal* JVal::get(const char* key) const {
  if (type != Obj) return nullptr;
  for (size_t k = 0; k < keys.size(); k++)
    if (keys[k] == key) return &a[k];
  return nullptr;
}

bool JVal::asInt(long long& out) const {
  if (type != Num || !isInt) return false;
  out = i;
  return true;
}

namespace {

struct P {
  const char* s;
  size_t n, pos;
  void ws() { while (pos < n && (s[pos] == ' ' || s[pos] == '\t' || s[pos] == '\n' || s[pos] == '\r')) pos++; }
  bool lit(const char* w, size_t len) {
    if (pos + len > n || memcmp(s + pos, w, len) != 0) return false;
    pos += len;
    return true;
  }
  static void utf8(std::string& o, unsigned c) {
    if (c < 0x80) o += (char)c;
    else if (c < 0x800) { o += (char)(0xC0 | (c >> 6)); o += (char)(0x80 | (c & 63)); }
    else { o += (char)(0xE0 | (c >> 12)); o += (char)(0x80 | ((c >> 6) & 63)); o += (char)(0x80 | (c & 63)); }
  }
  bool str(std::string& o) {
    if (pos >= n || s[pos] != '"') return false;
    pos++;
    while (pos < n) {
      unsigned char c = (unsigned char)s[pos++];
      if (c == '"') return true;
      if (c < 0x20) return false;
      if (c != '\\') { o += (char)c; continue; }
      if (pos >= n) return false;
      char e = s[pos++];
      switch (e) {
        case '"': o += '"'; break;
        case '\\': o += '\\'; break;
        case '/': o += '/'; break;
        case 'b': o += '\b'; break;
        case 'f': o += '\f'; break;
        case 'n': o += '\n'; break;
        case 'r': o += '\r'; break;
        case 't': o += '\t'; break;
        case 'u': {
          if (pos + 4 > n) return false;
          unsigned v = 0;
          for (int k = 0; k < 4; k++) {
            char h = s[pos++];
            v <<= 4;
            if (h >= '0' && h <= '9') v |= h - '0';
            else if (h >= 'a' && h <= 'f') v |= h - 'a' + 10;
            else if (h >= 'A' && h <= 'F') v |= h - 'A' + 10;
            else return false;
          }
          if (v >= 0xD800 && v <= 0xDFFF) o += '?';      // surrogate halves: not needed for our data
          else utf8(o, v);
          break;
        }
        default: return false;
      }
    }
    return false;
  }
  bool val(JVal& v, int depth) {
    if (depth > 8) return false;
    ws();
    if (pos >= n) return false;
    char c = s[pos];
    if (c == '{') {
      pos++;
      v.type = JVal::Obj;
      ws();
      if (pos < n && s[pos] == '}') { pos++; return true; }
      for (;;) {
        ws();
        std::string k;
        if (!str(k)) return false;
        ws();
        if (pos >= n || s[pos++] != ':') return false;
        JVal child;
        if (!val(child, depth + 1)) return false;
        v.keys.push_back(k);
        v.a.push_back(child);
        ws();
        if (pos >= n) return false;
        if (s[pos] == ',') { pos++; continue; }
        if (s[pos] == '}') { pos++; return true; }
        return false;
      }
    }
    if (c == '[') {
      pos++;
      v.type = JVal::Arr;
      ws();
      if (pos < n && s[pos] == ']') { pos++; return true; }
      for (;;) {
        JVal child;
        if (!val(child, depth + 1)) return false;
        v.a.push_back(child);
        ws();
        if (pos >= n) return false;
        if (s[pos] == ',') { pos++; continue; }
        if (s[pos] == ']') { pos++; return true; }
        return false;
      }
    }
    if (c == '"') { v.type = JVal::Str; return str(v.s); }
    if (c == 't') { v.type = JVal::Bool; v.b = true; return lit("true", 4); }
    if (c == 'f') { v.type = JVal::Bool; v.b = false; return lit("false", 5); }
    if (c == 'n') { v.type = JVal::Null; return lit("null", 4); }
    if (c == '-' || (c >= '0' && c <= '9')) {
      size_t st = pos;
      bool isf = false;
      if (s[pos] == '-') pos++;
      if (pos >= n || s[pos] < '0' || s[pos] > '9') return false;
      while (pos < n && s[pos] >= '0' && s[pos] <= '9') pos++;
      if (pos < n && s[pos] == '.') { isf = true; pos++; while (pos < n && s[pos] >= '0' && s[pos] <= '9') pos++; }
      if (pos < n && (s[pos] == 'e' || s[pos] == 'E')) {
        isf = true; pos++;
        if (pos < n && (s[pos] == '+' || s[pos] == '-')) pos++;
        while (pos < n && s[pos] >= '0' && s[pos] <= '9') pos++;
      }
      std::string t(s + st, pos - st);
      v.type = JVal::Num;
      v.d = strtod(t.c_str(), nullptr);
      v.isInt = !isf && t.size() <= 18;
      if (v.isInt) v.i = strtoll(t.c_str(), nullptr, 10);
      return true;
    }
    return false;
  }
};

}  // namespace

bool jparse(const std::string& text, JVal& out) {
  P p{text.data(), text.size(), 0};
  out = JVal();
  if (!p.val(out, 0)) return false;
  p.ws();
  return p.pos == p.n;
}

void jescape(std::string& out, const std::string& s) {
  static const char* hex = "0123456789abcdef";
  for (size_t k = 0; k < s.size(); k++) {
    unsigned char c = (unsigned char)s[k];
    if (c == '"') out += "\\\"";
    else if (c == '\\') out += "\\\\";
    else if (c == '\n') out += "\\n";
    else if (c == '\r') out += "\\r";
    else if (c == '\t') out += "\\t";
    else if (c < 0x20 || c == 0x7f) { out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
    else if (c >= 0x80) out += '?';
    else out += (char)c;
  }
}

}  // namespace fet
