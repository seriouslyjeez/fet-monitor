#pragma once
#include "Arduino.h"
struct FSInfo { size_t totalBytes; size_t usedBytes; size_t blockSize; size_t pageSize; size_t maxOpenFiles; size_t maxPathLength; };
class File {
 public:
  operator bool() const { return true; }
  size_t size() const { return 0; }
  size_t read(uint8_t*, size_t) { return 0; }
  size_t write(const uint8_t*, size_t) { return 0; }
  bool seek(uint32_t, SeekMode) { return true; }
  void close() {}
};
class Dir {
 public:
  bool next() { return false; }
  String fileName() { return String(); }
};
class FS {
 public:
  bool begin() { return true; }
  bool format() { return true; }
  bool info(FSInfo&) { return true; }
  File open(const char*, const char*) { return File(); }
  Dir openDir(const char*) { return Dir(); }
  bool rename(const char*, const char*) { return true; }
  bool remove(const char*) { return true; }
};
extern FS LittleFS;
