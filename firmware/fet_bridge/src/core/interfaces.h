// Platform-independent interfaces. The core never touches Arduino headers, so it can be tested on a PC.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace fet {

struct Fs {
  virtual ~Fs() {}
  virtual bool read(const std::string& path, std::string& out) = 0;                      // whole file; false if missing
  virtual bool readRange(const std::string& path, size_t off, size_t len, std::string& out) = 0;
  virtual bool writeAtomic(const std::string& path, const std::string& data) = 0;        // temp file + rename
  virtual bool append(const std::string& path, const std::string& data) = 0;             // creates; false unless ALL written
  virtual bool remove(const std::string& path) = 0;
  virtual bool size(const std::string& path, size_t& out) = 0;
  virtual std::vector<std::string> list(const std::string& prefix) = 0;                  // file names starting with prefix
  virtual size_t freeBytes() = 0;
};

struct Net {
  virtual ~Net() {}
  virtual bool ready() = 0;   // WiFi is up and the server address is known
  virtual int rssi() = 0;
  // POST a JSON body. True only if an HTTP response was received (any status). Must keep the serial port serviced
  // while it waits (the platform does that), and must cap the response at a few KB.
  virtual bool post(const char* path, const std::string& body, int& status, std::string& resp) = 0;
  // Firmware update from the server: GET /api/v1/firmware/<sha256>, checked with FwCheck while it streams into the
  // updater, staged for the next start only if the whole image matched. False (with a reason in err) leaves the
  // running firmware as it is. Keeps the serial port serviced, like post().
  virtual bool updateFirmware(const std::string& sha256hex, uint32_t size, std::string& err) {
    (void)sha256hex; (void)size; err = "not supported here"; return false;
  }
  virtual uint32_t otaSpace() { return 0; }    // bytes free for a new firmware image; 0 = cannot update
  virtual uint32_t flashSize() { return 0; }   // the flash size this firmware was built for (an image must match it)
};

struct Uart {
  virtual ~Uart() {}
  virtual void write(const std::string& s) = 0;       // to the Arduino
};

struct Hooks {
  virtual ~Hooks() {}
  virtual void reboot() = 0;
  virtual void openPortal() = 0;
  virtual uint32_t random32() = 0;        // hardware randomness (used to seed the boot number when the counter is lost)
};

}  // namespace fet
