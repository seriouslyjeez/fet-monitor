"""The flash station: a device plugged into one of the Pi's USB ports is identified and, if it is a FeT Bridge (ESP-01 /
ESP-01S on a USB programmer), given the newest FeT Bridge image for its flash size.

Range Control -> Flash a device. The server runs esptool / avrdude itself (the service user is in the dialout group;
no root needed), one job at a time in the background, and the page follows the job. Nothing is written to a device
before it has been identified and the match director (Range Control) confirmed, and the device must still be the
same one (its MAC) when the write starts.

Identification, read-only:
- the USB side from sysfs (vendor/product/serial of /dev/ttyUSB* and /dev/ttyACM*);
- `esptool flash_id`: ESP8266 / ESP32 chip, MAC, flash size; for an ESP8266 the start of its flash is read and
  searched for FeT Bridge's identification string (`fet-monitor-bridge/x.y.z`) to know what it runs now;
- otherwise `avrdude -n` (read-only) asks for an ATmega2560's signature: a freETarget Arduino Mega, and what it prints
  at start-up (or answers to {"VERSION":0}) names its freETarget version.
FeT Bridge images come from the firmware store (signed releases or Range Control uploads; firmware.py checks them).
A device that does not run FeT Bridge yet is first copied whole to flash-backups/, then erased (a first install
needs a full erase), written and verified. Arduino Mega and ESP32 boards are identified only, not flashed (planned).

Configure (FeT Bridge 0.3.0 and newer): the settings (WiFi, this server's address, port and token, the target's name)
go over the serial port in the bridge's first minute after start (firmware src/core/provision.h): "FETCFG HELLO", then
one checked line, then the bridge restarts with them. On an auto-reset programmer the station restarts the ESP itself;
with a PROG/UART switch the person sets UART and plugs it in again while the station waits. The WiFi password is never
kept in the job (the page shows the job).
"""
from __future__ import annotations

import binascii
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .firmware import ID_RE, flash_label, version_tuple
from .seriallink import SerialLink

SYS_TTY = "/sys/class/tty"
BAUD = "460800"
APP_READ = 0x80000                     # the first 512 KB holds the application (and its identification string)
ESP_ADAPTERS = {("1a86", "7523"): "CH340", ("1a86", "55d4"): "CH9102", ("10c4", "ea60"): "CP2102", ("0403", "6001"): "FTDI",
                ("0403", "6015"): "FTDI"}
ARDUINO_VID = "2341"
ESPRESSIF_VID = "303a"                 # ESP32-S2/S3/C3 native USB
CHIP_RE = re.compile(r"Chip (?:is|type:)\s*(ESP[0-9A-Z\-]+)", re.I)
MAC_RE = re.compile(r"MAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})", re.I)
SIZE_RE = re.compile(r"Detected flash size:\s*(\d+)\s*(MB|KB)", re.I)
PCT_RE = re.compile(r"\((\d{1,3}) ?%\)")
SIG_RE = re.compile(r"device signature = 0x([0-9a-f]{6})", re.I)
M2560 = "1e9801"
HELLO_RE = re.compile(r"FETCFG HELLO (\S+) (\S+)")
OK_RE = re.compile(r"FETCFG OK (\S+)")
ERROR_RE = re.compile(r"FETCFG ERROR (.*)")
BRIDGE_RE = re.compile(r"FET bridge firmware (\d+\.\d+\.\d+)")           # its start-up line, any version
FET_BANNER_RE = re.compile(r'freETarget\W*"?([0-9][^"\r\n]*)')
FET_VERSION_RE = re.compile(r'"?VERSION"?\s*:\s*(?:"([0-9][^"]*)"|([0-9][^",}\s]*))')
PROVISION_FROM = (0, 3, 0)
SETTING_KEYS = ("ssid", "pass", "host", "port", "token", "name", "ip", "gw", "mask")
PROG_SWITCH = ("set the programmer's switch to UART, unplug it and plug it in again (or press its reset button)")
ONE_MB_ADVICE = ("1 MB module: FeT Monitor on this network only. A 4 MB ESP-01S costs a few dollars more and can also be "
                 "managed remotely over HTTPS (planned).")
Runner = Callable[[List[str], float, Optional[Callable[[str], None]]], tuple]


class FlashError(ValueError):
    """Something a person has to do or know (shown on the page)."""


def run_tool(argv: List[str], timeout: float, on_line: Optional[Callable[[str], None]] = None) -> tuple:
    """(exit code, all output). Lines (split at \\n and \\r: esptool's progress) go to on_line as they come. A tool
    that goes silent and never ends is stopped after timeout seconds (exit code 124)."""
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except FileNotFoundError:
        return 127, f"{argv[0]} is not installed"
    out = []

    def read():
        buf = b""
        while True:
            chunk = p.stdout.read(256)
            if not chunk:
                break
            buf += chunk
            while True:
                i = min([k for k in (buf.find(b"\n"), buf.find(b"\r")) if k >= 0], default=-1)
                if i < 0:
                    break
                line, buf = buf[:i].decode(errors="replace"), buf[i + 1:]
                out.append(line + "\n")
                if on_line and line:
                    on_line(line)
        out.append(buf.decode(errors="replace"))
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        code = 124
    reader.join(5)
    p.stdout.close()
    text = "".join(out)
    return code, text + (f"\n{argv[0]}: no end within {timeout:.0f} s" if code == 124 else "")


def list_ports(sys_tty: str = SYS_TTY) -> List[dict]:
    """USB serial ports with what sysfs says about the USB device behind them."""
    out = []
    root = Path(sys_tty)
    for tty in sorted(list(root.glob("ttyUSB*")) + list(root.glob("ttyACM*"))):
        dev, info = tty / "device", {}
        try:
            d = dev.resolve()
        except OSError:
            continue
        for p in [d] + list(d.parents)[:6]:                 # the USB device is a few levels up
            if (p / "idVendor").is_file():
                for k in ("idVendor", "idProduct", "manufacturer", "product", "serial"):
                    try:
                        info[k] = (p / k).read_text().strip()
                    except OSError:
                        info[k] = ""
                break
        if not info:
            continue
        vid, pid = info["idVendor"].lower(), info["idProduct"].lower()
        hint = ("arduino" if vid == ARDUINO_VID else "esp32" if vid == ESPRESSIF_VID
                else "adapter" if (vid, pid) in ESP_ADAPTERS else "unknown")
        out.append({"port": f"/dev/{tty.name}", "vid": vid, "pid": pid, "manufacturer": info["manufacturer"], "product": info["product"],
                    "serial": info["serial"], "hint": hint, "adapter": ESP_ADAPTERS.get((vid, pid), "")})
    return out


def settings_line(settings: dict) -> bytes:
    """FETCFG <JSON as hex> <crc32 of the JSON>: no braces on the wire, and a damaged line is refused."""
    body = json.dumps(settings, separators=(",", ":")).encode()
    return b"FETCFG %s %08x\n" % (body.hex().encode(), binascii.crc32(body))


def why_esptool(out: str) -> str:
    if re.search(r"Failed to connect|Timed out waiting for packet header|No serial data received", out, re.I):
        return "no answer from an ESP chip. On an ESP-01 programmer with a switch: set it to PROG (or UART), unplug and plug in again"
    if re.search(r"Permission denied|could not open port", out, re.I):
        return "the port could not be opened (in use, or no permission)"
    return (out.strip().splitlines() or ["?"])[-1][:200]


class FlashStation:
    def __init__(self, images, data_dir: Optional[Path], run: Runner = run_tool, sys_tty: str = SYS_TTY,
                 esptool: Optional[List[str]] = None, now: Callable[[], float] = time.time, background: bool = True,
                 opener: Callable[[str], SerialLink] = SerialLink):
        self.images, self.run, self.sys_tty, self.now, self.background = images, run, sys_tty, now, background
        self.opener = opener
        self.hello_wait, self.hello_every, self.reply_wait, self.mega_wait = 90.0, 1.5, 8.0, 6.0        # seconds
        self.backups = Path(data_dir) / "flash-backups" if data_dir is not None else None
        self.esptool = esptool
        self.lock = threading.Lock()
        self.job: Optional[dict] = None
        self.probes: Dict[str, dict] = {}

    # ------------------------------------------------------------------------------------- tools
    def _esptool(self) -> List[str]:
        if self.esptool is None:                 # Debian installs "esptool"; pip installs "esptool.py"
            for name in ("esptool", "esptool.py"):
                if self.run([name, "version"], 20, None)[0] != 127:
                    self.esptool = [name]
                    break
            else:
                raise FlashError("esptool is not installed on this Pi (sudo apt install esptool)")
        return self.esptool

    def _esp(self, port: str, args: List[str], timeout: float, progress: bool = False) -> str:
        code, out = self.run(self._esptool() + ["--port", port, "--baud", BAUD] + args, timeout,
                             self._progress if progress else None)
        if code != 0:
            raise FlashError(why_esptool(out))
        return out

    def _progress(self, line: str) -> None:
        m = PCT_RE.search(line)
        with self.lock:
            if self.job is not None:
                self.job["log"] = (self.job.get("log", []) + [line])[-12:]
                if m:
                    self.job["percent"] = min(100, int(m.group(1)))

    # ------------------------------------------------------------------------------------- identify
    def identify(self, port: str) -> dict:
        """What is behind a port (read-only), and what the flash station can do for it."""
        if port not in {p["port"] for p in list_ports(self.sys_tty)}:
            raise FlashError("that port is gone: plug the device in again")
        hint = next(p for p in list_ports(self.sys_tty) if p["port"] == port)["hint"]
        if hint != "arduino":
            code, out = self.run(self._esptool() + ["--port", port, "--baud", BAUD, "--chip", "auto", "flash_id"], 60, None)
            if code == 0:
                return self._esp_found(port, out)
            esp_err = why_esptool(out)
        else:
            esp_err = ""
        code, out = self.run(["avrdude", "-p", "m2560", "-c", "wiring", "-P", port, "-b", "115200", "-n"], 60, None)
        m = SIG_RE.search(out)
        if m and m.group(1).lower() == M2560:
            ver = self._mega_version(port)
            return {"port": port, "device": "mega", "freetarget": ver,
                    "label": "Arduino Mega 2560, " + (f"freETarget {ver}" if ver else "no freETarget start-up message (other firmware?)"),
                    "offer": None, "why_not": "Flashing firmware onto the Mega is not offered yet (planned)."}
        raise FlashError(esp_err or "not identified: no ESP chip and no Arduino Mega answered")

    def _esp_found(self, port: str, out: str) -> dict:
        chip = (CHIP_RE.search(out) or [None, ""])[1].upper()
        mac = (MAC_RE.search(out) or [None, ""])[1].lower()
        sm = SIZE_RE.search(out)
        size = int(sm.group(1)) * (1 << 20 if sm.group(2).upper() == "MB" else 1 << 10) if sm else 0
        if not chip.startswith("ESP8266"):
            return {"port": port, "device": "esp32" if chip.startswith("ESP32") else "esp", "chip": chip, "mac": mac, "flash_size": size,
                    "label": f"{chip or 'An ESP chip'} ({flash_label(size)} flash)", "offer": None,
                    "why_not": "ESP32 boards (freETarget version 5/6) are not flashed here yet: use freETarget's PC client or its "
                               "over-the-air update. FeT Monitor connecting to them directly is planned."}
        running = self._fet_version(port)
        res = {"port": port, "device": "esp8266", "chip": chip, "mac": mac, "flash_size": size, "running": running,
               "label": f"ESP-01 / ESP-01S ({flash_label(size)} flash), " + (f"FeT Bridge {running}" if running else "not FeT Bridge (first install)"),
               "advice": ONE_MB_ADVICE if size == 1 << 20 else "",
               "configurable": running is not None and (version_tuple(running) or (0, 0, 0)) >= PROVISION_FROM}
        if size not in (1 << 20, 4 << 20):
            res.update(offer=None, why_not=f"flash size {flash_label(size)}: FeT Bridge is built for 1 MB and 4 MB modules")
        else:
            imgs = [m for m in self.images.list() if m["flash_size"] == size]
            best = max(imgs, key=lambda m: (version_tuple(m["version"]) or (0, 0, 0), m.get("uploaded", 0))) if imgs else None
            if best is None:
                res.update(offer=None, why_not=f"no {flash_label(size)} FeT Bridge image yet: Range Control → Check GitHub for FeT "
                                               "Bridge firmware, or upload one")
            else:
                res.update(offer={"sha256": best["sha256"], "version": best["version"], "size": best["size"]}, why_not="",
                           first_install=running is None,
                           same=running is not None and version_tuple(running) == version_tuple(best["version"]))
        with self.lock:
            self.probes[port] = res
        return res

    def _mega_version(self, port: str) -> Optional[str]:
        """Opening the port restarts a Mega (DTR); freETarget prints its version as it starts. If nothing comes, ask."""
        try:
            link = self.opener(port)
        except OSError:
            return None
        try:
            deadline, asked = time.monotonic() + self.mega_wait, False
            while time.monotonic() < deadline:
                if not asked and time.monotonic() > deadline - self.mega_wait / 3:   # after its boot loader is done
                    link.write(b'{"VERSION":0}\n')
                    asked = True
                line = link.readline(0.2)
                if line is None:
                    continue
                m = FET_BANNER_RE.search(line) or (FET_VERSION_RE.search(line) if asked else None)
                if m:
                    return next(g for g in m.groups() if g).strip()[:60]
        except OSError:
            return None
        finally:
            link.close()
        return None

    def _fet_version(self, port: str) -> Optional[str]:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "app.bin"
            self._esp(port, ["read_flash", "0", hex(APP_READ), str(f)], 300)
            found = sorted(set(m.group(1).decode() for m in ID_RE.finditer(f.read_bytes())))
        return found[-1] if found else None

    # ------------------------------------------------------------------------------------- flash
    def start_flash(self, port: str, sha: str, by: str, settings: Optional[dict] = None) -> dict:
        """Install image sha on the device identified on port (must still be the same device), then configure it if
        settings are given (and the image can take them)."""
        with self.lock:
            self._not_busy()
            probe = self.probes.get(port)
        if not probe or not probe.get("offer") or probe["offer"]["sha256"] != sha:
            raise FlashError("identify the device again first")
        img = self.images.get(sha)
        path = self.images.path(sha)
        if not img or path is None or img["flash_size"] != probe["flash_size"]:
            raise FlashError("that image is not for this device's flash size")
        if settings is not None and (version_tuple(img["version"]) or (0, 0, 0)) < PROVISION_FROM:
            raise FlashError(f"FeT Bridge {img['version']} cannot take settings over USB (0.3.0 and newer can): set it up on its own WiFi")
        line = settings_line(self._settings(settings)) if settings is not None else None
        job = {"id": int(self.now() * 1000), "port": port, "state": "running", "step": "starting", "percent": 0, "log": [],
               "kind": "flash", "what": f"FeT Bridge {img['version']}", "version": img["version"], "by": by,
               "first_install": probe["first_install"], "configure": line is not None}
        with self.lock:
            self.job = job

        def work():
            try:
                self._flash(port, probe, path)
            except FlashError as exc:
                with self.lock:
                    job.update(state="failed", message=str(exc))
                return
            with self.lock:                      # what it runs now; a later Configure needs no new identification
                probe.update(running=img["version"], first_install=False, same=True,
                             label=f"ESP-01 / ESP-01S ({flash_label(probe['flash_size'])} flash), FeT Bridge {img['version']}",
                             configurable=(version_tuple(img["version"]) or (0, 0, 0)) >= PROVISION_FROM)
            done = f"FeT Bridge {img['version']} installed and verified"
            if line is None:
                with self.lock:
                    job.update(state="done", step="done", percent=100, message=done + ": unplug it")
                return
            try:
                dev = self._configure(port, line, self.reply_wait * 2)
            except FlashError as exc:
                with self.lock:
                    job.update(state="done", step="done", percent=100, configured=False,
                               message=f"{done}, not configured yet: {exc}")
                return
            with self.lock:
                job.update(state="done", step="done", percent=100, configured=True, device_id=dev,
                           message=f"{done} and configured ({dev}): it joins the WiFi now; unplug it")
        self._go(work)
        return job

    def start_configure(self, port: str, settings: dict, by: str) -> dict:
        """Send settings to a FeT Bridge (0.3.0+) on port. Waits up to hello_wait seconds for it to start: on a PROG/UART
        switch programmer the person sets UART and plugs it in again meanwhile."""
        if not re.fullmatch(r"/dev/tty(USB|ACM)\d{1,3}", port or ""):        # it may be unplugged right now (PROG/UART switch)
            raise FlashError("not a USB serial port")
        line = settings_line(self._settings(settings))
        with self.lock:
            self._not_busy()
            job = {"id": int(self.now() * 1000), "port": port, "state": "running", "step": "waiting for FeT Bridge to start",
                   "percent": 0, "log": [], "kind": "configure", "what": "Settings", "by": by}
            self.job = job

        def work():
            try:
                dev = self._configure(port, line, self.hello_wait)
            except FlashError as exc:
                with self.lock:
                    job.update(state="failed", message=str(exc))
                return
            with self.lock:
                job.update(state="done", step="done", percent=100, configured=True, device_id=dev,
                           message=f"Configured ({dev}): it joins the WiFi now; unplug it")
        self._go(work)
        return job

    def _not_busy(self) -> None:                 # with self.lock held
        if self.job and self.job["state"] == "running":
            raise FlashError("busy with another device: wait for it to finish")

    def _go(self, work: Callable[[], None]) -> None:
        if self.background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    @staticmethod
    def _settings(raw: dict) -> dict:
        """Only the keys the bridge knows, port as a number, empty optional ones left out. The bridge checks the values
        itself (the same checks as its setup page) and says what is wrong."""
        if not isinstance(raw, dict):
            raise FlashError("no settings")
        out = {}
        for k in SETTING_KEYS:
            v = raw.get(k)
            if v is None or (v == "" and k in ("ip", "gw", "mask")):
                continue
            if k == "port":
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    raise FlashError("the server port must be a number")
            elif not isinstance(v, str):
                raise FlashError(f"{k} must be text")
            else:
                out[k] = v.strip() if k not in ("pass",) else v
        for k in ("ssid", "host", "port", "token", "name"):
            if k not in out or out[k] == "":
                raise FlashError({"ssid": "the WiFi name is missing", "host": "the server address is missing",
                                  "port": "the server port is missing", "token": "the server token is missing",
                                  "name": "the target name is missing"}[k])
        return out

    def _configure(self, port: str, line: bytes, wait: float) -> str:
        """HELLO until FeT Bridge answers (re-opening the port when it comes back after a replug), then the settings line
        until OK. Returns the device id."""
        deadline = time.monotonic() + wait
        link, old, restarted = None, None, False
        try:
            while True:
                if time.monotonic() >= deadline:
                    if old:
                        raise FlashError(f"this FeT Bridge ({old}) is too old to take settings over USB: install the newest first")
                    raise FlashError("FeT Bridge did not answer. With a PROG/UART switch: " + PROG_SWITCH +
                                     ", then press Configure again. With an auto-reset programmer: press Configure again")
                if link is None:
                    try:
                        link = self.opener(port)
                    except OSError:
                        time.sleep(self.hello_every / 3)           # unplugged right now: wait for it to come back
                        continue
                    if not restarted:                              # auto-reset programmers: start the program now
                        restarted = True
                        link.restart_esp()
                try:
                    link.write(b"FETCFG HELLO\n")
                    end = time.monotonic() + self.hello_every
                    while time.monotonic() < end:
                        got = link.readline(max(0.0, end - time.monotonic()))
                        if got is None:
                            continue
                        m = BRIDGE_RE.search(got)
                        if m:
                            old = m.group(1)
                        h = HELLO_RE.search(got)
                        if h:
                            return self._send_settings(link, line, h.group(1), h.group(2))
                except OSError:
                    link.close()
                    link = None
        finally:
            if link is not None:
                link.close()

    def _send_settings(self, link, line: bytes, dev: str, fw: str) -> str:
        self._set_step(f"sending the settings to {dev} (FeT Bridge {fw})")
        for _ in range(3):                                 # a damaged line is refused (checksum): send it again
            link.write(line)
            end = time.monotonic() + self.reply_wait
            while time.monotonic() < end:
                got = link.readline(max(0.0, end - time.monotonic()))
                if got is None:
                    continue
                if OK_RE.search(got):
                    return dev
                e = ERROR_RE.search(got)
                if e and "checksum" in e.group(1):
                    break
                if e:
                    raise FlashError("FeT Bridge refused the settings: " + e.group(1).strip())
            else:
                raise FlashError("FeT Bridge did not confirm the settings: press Configure again")
        raise FlashError("the settings arrived damaged three times: check the USB cable and press Configure again")

    def _set_step(self, step: str) -> None:
        with self.lock:
            if self.job is not None:
                self.job.update(step=step)

    def _step(self, step: str) -> None:
        with self.lock:
            self.job.update(step=step, percent=0)

    def _flash(self, port: str, probe: dict, path: Path) -> None:
        self._step("checking it is the same device")
        out = self._esp(port, ["--chip", "auto", "flash_id"], 60)
        mac = (MAC_RE.search(out) or [None, ""])[1].lower()
        if mac != probe["mac"]:
            raise FlashError("a different device is plugged in now: identify it again")
        if probe["first_install"]:
            if self.backups is None:
                raise FlashError("no data folder for the backup of the original firmware")
            self.backups.mkdir(parents=True, exist_ok=True)
            name = f"{mac.replace(':', '')}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.now()))}.bin"
            self._step("saving the original firmware")
            self._esp(port, ["read_flash", "0", hex(probe["flash_size"]), str(self.backups / name)], 900, progress=True)
            with self.lock:
                self.job["backup"] = name
            self._step("erasing the whole chip (first install)")
            self._esp(port, ["erase_flash"], 180)
        self._step("writing")
        self._esp(port, ["write_flash", "--flash_mode", "keep", "--flash_size", "keep", "0x0", str(path)], 600, progress=True)
        self._step("verifying")
        self._esp(port, ["verify_flash", "0x0", str(path)], 300)

    # ------------------------------------------------------------------------------------- the page
    def view(self) -> dict:
        with self.lock:
            job = dict(self.job) if self.job else None
            probes = dict(self.probes)
        ports = list_ports(self.sys_tty)
        here = {p["port"] for p in ports}
        backups = []
        if self.backups is not None and self.backups.is_dir():
            backups = sorted(({"name": p.name, "size": p.stat().st_size} for p in self.backups.glob("*.bin")), key=lambda b: b["name"], reverse=True)
        return {"ports": ports, "probes": {k: v for k, v in probes.items() if k in here}, "job": job, "backups": backups}

    def backup_path(self, name: str) -> Optional[Path]:
        if self.backups is None or not re.fullmatch(r"[0-9a-f]{12}-\d{8}-\d{6}\.bin", name or ""):
            return None
        p = self.backups / name
        return p if p.is_file() else None
