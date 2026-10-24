// Decimal formatting that does not depend on the C library supporting 64-bit printf (newlib-nano on the ESP8266 may not).
#pragma once
#include <cstdint>
#include <string>

namespace fet {

inline std::string u64s(uint64_t v) {
  char b[24]; int i = 24;
  do { b[--i] = (char)('0' + (int)(v % 10)); v /= 10; } while (v);
  return std::string(b + i, (size_t)(24 - i));
}
inline std::string i64s(long long v) {
  if (v < 0) return "-" + u64s((uint64_t)0 - (uint64_t)v);
  return u64s((uint64_t)v);
}
inline std::string u32s(uint32_t v) { return u64s(v); }
inline std::string i32s(int v) { return i64s(v); }

}  // namespace fet
