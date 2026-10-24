// A small JSON reader (for the server's replies and the config file) and an escaper (for what we send).
#pragma once
#include <string>
#include <vector>

namespace fet {

struct JVal {
  enum Type { Null, Bool, Num, Str, Arr, Obj } type = Null;
  bool b = false;
  bool isInt = false;
  long long i = 0;
  double d = 0;
  std::string s;
  std::vector<JVal> a;                 // array items, or object values
  std::vector<std::string> keys;       // object keys, parallel to `a`
  const JVal* get(const char* key) const;                 // object member or nullptr
  bool asInt(long long& out) const;
};

// False on any syntax error, nesting deeper than 8, or trailing text. Never throws.
bool jparse(const std::string& text, JVal& out);
// Appends s as a JSON string body (no quotes). Output is pure ASCII: control and non-ASCII bytes become \u00XX or '?'.
void jescape(std::string& out, const std::string& s);

}  // namespace fet
