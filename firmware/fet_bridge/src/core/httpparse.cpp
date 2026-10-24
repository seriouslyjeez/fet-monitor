#include "httpparse.h"
#include <cstdlib>

namespace fet {

static char lower(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }

bool parseHttpResponse(const std::string& raw, int& status, std::string& body) {
  size_t h = raw.find("\r\n\r\n");
  if (h == std::string::npos || raw.size() < 12 || raw.compare(0, 5, "HTTP/") != 0) return false;
  if (raw[8] != ' ' || raw[9] < '1' || raw[9] > '5') return false;
  int code = (raw[9] - '0') * 100;
  if (raw[10] < '0' || raw[10] > '9' || raw[11] < '0' || raw[11] > '9') return false;
  code += (raw[10] - '0') * 10 + (raw[11] - '0');
  std::string head = raw.substr(0, h);
  for (size_t i = 0; i < head.size(); i++) head[i] = lower(head[i]);
  size_t cl = head.find("\r\ncontent-length:");
  std::string b = raw.substr(h + 4);
  if (head.find("\r\ntransfer-encoding:") != std::string::npos) return false;     // we never ask for chunked; refuse to guess
  if (cl != std::string::npos) {
    const char* p = head.c_str() + cl + 17;
    while (*p == ' ') p++;
    if (*p < '0' || *p > '9') return false;
    unsigned long want = strtoul(p, nullptr, 10);
    if (b.size() < want) return false;                                              // cut short
    b.resize(want);
  }
  status = code;
  body = b;
  return true;
}

}  // namespace fet
