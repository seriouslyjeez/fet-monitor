#pragma once
#include <cstdint>
#include <cstddef>
namespace fet {
inline uint32_t crc32(const char* p, size_t n, uint32_t crc = 0) {
  crc = ~crc;
  for (size_t i = 0; i < n; i++) {
    crc ^= (uint8_t)p[i];
    for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
}  // namespace fet
