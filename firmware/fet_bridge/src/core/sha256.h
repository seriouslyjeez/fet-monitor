// SHA-256 (FIPS 180-4), small and portable, for checking firmware images. Tested on the PC against Python's hashlib.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace fet {

class Sha256 {
 public:
  Sha256() { reset(); }
  void reset() {
    static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(h_, init, sizeof h_);
    len_ = 0;
    used_ = 0;
  }
  void update(const uint8_t* p, size_t n) {
    len_ += n;
    while (n) {
      size_t k = 64 - used_ < n ? 64 - used_ : n;
      memcpy(buf_ + used_, p, k);
      used_ += k; p += k; n -= k;
      if (used_ == 64) { block(buf_); used_ = 0; }
    }
  }
  void finish(uint8_t out[32]) {
    uint64_t bits = len_ * 8;
    uint8_t pad = 0x80;
    update(&pad, 1);
    uint8_t zero = 0;
    while (used_ != 56) update(&zero, 1);
    uint8_t lenb[8];
    for (int i = 0; i < 8; i++) lenb[i] = (uint8_t)(bits >> (56 - 8 * i));
    update(lenb, 8);
    for (int i = 0; i < 8; i++) {
      out[4 * i] = (uint8_t)(h_[i] >> 24); out[4 * i + 1] = (uint8_t)(h_[i] >> 16);
      out[4 * i + 2] = (uint8_t)(h_[i] >> 8); out[4 * i + 3] = (uint8_t)h_[i];
    }
  }
  std::string hexdigest() {
    uint8_t d[32];
    finish(d);
    static const char* hx = "0123456789abcdef";
    std::string s;
    for (int i = 0; i < 32; i++) { s += hx[d[i] >> 4]; s += hx[d[i] & 15]; }
    return s;
  }

 private:
  static uint32_t ror(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
  void block(const uint8_t* b) {
    static const uint32_t K[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
        0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
        0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
        0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
        0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
        0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    uint32_t w[64];
    for (int i = 0; i < 16; i++) w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16 | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    for (int i = 16; i < 64; i++) {
      uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
      uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h_[0], bb = h_[1], c = h_[2], d = h_[3], e = h_[4], f = h_[5], g = h_[6], h = h_[7];
    for (int i = 0; i < 64; i++) {
      uint32_t t1 = h + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
      uint32_t t2 = (ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
      h = g; g = f; f = e; e = d + t1; d = c; c = bb; bb = a; a = t1 + t2;
    }
    h_[0] += a; h_[1] += bb; h_[2] += c; h_[3] += d; h_[4] += e; h_[5] += f; h_[6] += g; h_[7] += h;
  }
  uint32_t h_[8];
  uint8_t buf_[64];
  uint64_t len_;
  size_t used_;
};

// Firmware image check while it streams in. Every byte is hashed as it arrives; all but the image's LAST byte may be
// written to flash at once. The last byte is written only after the whole image matched its SHA-256, so a wrong image
// is never complete in flash and the updater can abandon it (the running firmware stays).
class FwCheck {
 public:
  bool begin(const std::string& shaHex, uint32_t size) {
    if (shaHex.size() != 64 || size == 0) return false;
    for (char c : shaHex) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    want_ = shaHex; size_ = size; got_ = 0; sha_.reset(); verified_ = false; done_ = false;
    return true;
  }
  // Hash n received bytes; returns how many of them may be written now. Bytes beyond the image size are refused (0).
  size_t take(const uint8_t* p, size_t n) {
    if (done_ || n > size_ - got_) return 0;
    sha_.update(p, n);
    got_ += (uint32_t)n;
    if (got_ == size_) { done_ = true; verified_ = sha_.hexdigest() == want_; return n - 1; }
    return n;
  }
  bool complete() const { return done_; }
  bool verified() const { return verified_; }      // only meaningful once complete()
  uint32_t received() const { return got_; }
  bool overflow(size_t n) const { return n > size_ - got_; }

 private:
  Sha256 sha_;
  std::string want_;
  uint32_t size_ = 0, got_ = 0;
  bool verified_ = false, done_ = false;
};

}  // namespace fet
