"""Everything the setup changes on the Pi, through one `run(argv, stdin)`: no shell, every value its own argument
(an SSID or password is never parsed by a shell). Raspberry Pi OS Bookworm/Trixie: NetworkManager (nmcli),
systemd (hostnamectl, timedatectl, systemctl), raspi-config for the WiFi country.

Connections FeT Monitor owns, by name: fet-setup (the open setup network), fet-test (a trial join of the club WiFi),
fet-wifi, fet-eth, fet-range (the final network). Nothing else NetworkManager knows is changed, except that the other
WiFi connections are switched to autoconnect=no when the final network is chosen, so the Pi does not wander.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

SETUP_IP = "192.168.4.1"
WIFI, ETH = "wlan0", "eth0"
CATCH_ALL = "/etc/NetworkManager/dnsmasq-shared.d/fet-setup.conf"     # every name -> the setup page (setup AP only)
DATA_DIR = "/var/lib/fet-monitor"
CODE_DIR = "/opt/fet-monitor"
SVC_USER = "fet-monitor"
OURS = ("fet-setup", "fet-test", "fet-wifi", "fet-eth", "fet-range")

Runner = Callable[[List[str], Optional[str], float], Tuple[int, str]]


def real_run(argv: List[str], stdin: Optional[str] = None, timeout: float = 60.0) -> Tuple[int, str]:
    """(exit code, stdout+stderr). A missing program is exit code 127, a timeout 124 (like the shell)."""
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{argv[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: no answer within {timeout:.0f} s"


class SystemError_(RuntimeError):
    """A system change failed; the message is for the setup page."""


def split_terse(line: str) -> List[str]:
    """One line of `nmcli -t` output: fields separated by ':', a literal ':' or '\\' escaped with '\\'."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


WHY = ((re.compile(r"Secrets were required|802-1x|psk|password", re.I), "wrong WiFi password"),
       (re.compile(r"No network with SSID|could not be found|not found", re.I), "the WiFi network was not found (out of range, or 5 GHz only?)"),
       (re.compile(r"IP configuration could not be reserved|no IP|DHCP|timed? ?out", re.I), "joined, but the router gave no address (DHCP)"))


def why(text: str) -> str:
    for rx, msg in WHY:
        if rx.search(text):
            return msg
    return (text.strip().splitlines() or ["unknown error"])[-1][:200]


class System:
    def __init__(self, run: Runner = real_run, root: str = "/"):
        self.run_, self.root = run, root

    def run(self, argv: List[str], stdin: Optional[str] = None, timeout: float = 60.0, check: bool = True) -> str:
        code, out = self.run_(argv, stdin, timeout)
        if check and code != 0:
            raise SystemError_(f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: {why(out) if argv[0] == 'nmcli' else out.strip()[:200]}")
        return out

    def path(self, p: str) -> Path:
        return Path(self.root) / p.lstrip("/")

    # ------------------------------------------------------------------------------------- lists for the page
    def countries(self) -> List[Tuple[str, str]]:
        """(code, name) from the system's ISO 3166 table."""
        out = []
        try:
            for line in self.path("/usr/share/zoneinfo/iso3166.tab").read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and "\t" in line:
                    code, name = line.split("\t", 1)
                    out.append((code.strip(), name.strip()))
        except OSError:
            pass
        return sorted(out, key=lambda x: x[1])

    def zones(self) -> List[str]:
        return [z for z in self.run(["timedatectl", "list-timezones"], check=False).split() if "/" in z or z == "UTC"]

    def serial_suffix(self) -> str:
        """Last 4 hex digits of the Pi's serial (the setup network's name), or of wlan0's MAC."""
        for p in ("/sys/firmware/devicetree/base/serial-number", f"/sys/class/net/{WIFI}/address"):
            try:
                raw = re.sub(r"[^0-9a-fA-F]", "", self.path(p).read_text(errors="ignore"))
                if len(raw) >= 4:
                    return raw[-4:].upper()
            except OSError:
                continue
        return "0000"

    # ------------------------------------------------------------------------------------- network
    def scan(self) -> List[dict]:
        """Nearby WiFi networks, strongest first, one entry per name: {ssid, signal, secure}. Only while no AP runs."""
        return self._wifi_list(True)

    def scan_cached(self) -> List[dict]:
        """The networks NetworkManager saw last (no new scan: works without root and keeps the radio as it is)."""
        return self._wifi_list(False)

    def _wifi_list(self, rescan: bool) -> List[dict]:
        out = self.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes" if rescan else "no"],
                       timeout=30 if rescan else 10, check=False)
        best = {}
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) < 3 or not f[0]:
                continue
            sig = int(f[1]) if f[1].isdigit() else 0
            if f[0] not in best or sig > best[f[0]]["signal"]:
                best[f[0]] = {"ssid": f[0], "signal": sig, "secure": f[2] not in ("", "--")}
        return sorted(best.values(), key=lambda x: -x["signal"])

    def _delete(self, name: str) -> None:
        self.run(["nmcli", "connection", "delete", name], check=False)

    def radio_on(self) -> None:
        """A new Raspberry Pi OS card keeps WiFi blocked (rfkill) until a country is set: the setup network has to
        start before anyone has chosen one (any country allows channels 1-11 on 2.4 GHz)."""
        self.run(["rfkill", "unblock", "wifi"], check=False)
        self.run(["nmcli", "radio", "wifi", "on"], check=False)

    def hotspot_on(self, ssid: str) -> None:
        """The open setup network on wlan0 at SETUP_IP, with every DNS name pointing at it (captive portal)."""
        self.radio_on()
        self._delete("fet-setup")
        conf = self.path(CATCH_ALL)
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(f"address=/#/{SETUP_IP}\n")
        self.run(["nmcli", "connection", "add", "type", "wifi", "ifname", WIFI, "con-name", "fet-setup", "autoconnect", "no",
                  "ssid", ssid, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
                  "ipv4.method", "shared", "ipv4.addresses", f"{SETUP_IP}/24", "ipv6.method", "disabled"])
        self.run(["nmcli", "connection", "up", "fet-setup"], timeout=40)

    def hotspot_off(self) -> None:
        self.run(["nmcli", "connection", "down", "fet-setup"], check=False, timeout=30)
        self._delete("fet-setup")
        try:
            self.path(CATCH_ALL).unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _ipv4(address) -> List[str]:
        if address == "dhcp":
            return ["ipv4.method", "auto", "ipv6.method", "auto"]
        return ["ipv4.method", "manual", "ipv4.addresses", f"{address['ip']}/{address['prefix']}",
                "ipv4.gateway", address["gateway"], "ipv4.dns", ",".join(address["dns"]), "ipv6.method", "auto"]

    def lease(self, ifname: str) -> Optional[dict]:
        """The IPv4 address an interface has now: {ip, prefix, gateway, dns} or None."""
        out = self.run(["nmcli", "-t", "-f", "GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", ifname], check=False)
        addr, gw, dns, up = None, None, [], False
        for line in out.splitlines():
            key, _, val = line.partition(":")
            if key == "GENERAL.STATE":
                up = val.startswith("100")
            elif key.startswith("IP4.ADDRESS") and addr is None and "/" in val:
                addr = val
            elif key == "IP4.GATEWAY" and val and val != "--":
                gw = val
            elif key.startswith("IP4.DNS") and val:
                dns.append(val)
        if not up or not addr:
            return None
        ip, prefix = addr.split("/", 1)
        return {"ip": ip, "prefix": int(prefix), "gateway": gw, "dns": dns}

    def try_wifi(self, ssid: str, psk: str, wait_s: int = 40) -> dict:
        """Join the club WiFi for a moment (the setup AP must be down): {"ok": True, "lease": {...}} or
        {"ok": False, "why": reason}. The trial connection is removed afterwards."""
        self._delete("fet-test")
        self.radio_on()
        try:
            self.run(["nmcli", "connection", "add", "type", "wifi", "ifname", WIFI, "con-name", "fet-test", "autoconnect", "no",
                      "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk])
            code, out = self.run_(["nmcli", "--wait", str(wait_s), "connection", "up", "fet-test"], None, wait_s + 10)
            if code != 0:
                return {"ok": False, "why": why(out)}
            got = self.lease(WIFI)
            return {"ok": True, "lease": got} if got else {"ok": False, "why": "joined, but the router gave no address (DHCP)"}
        finally:
            self.run(["nmcli", "connection", "down", "fet-test"], check=False, timeout=20)
            self._delete("fet-test")

    def use_network(self, net: dict) -> None:
        """Make the chosen network the Pi's own, starting now and at every boot."""
        mode = net["mode"]
        for name in ("fet-wifi", "fet-eth", "fet-range"):
            self._delete(name)
        if mode != "cable":                      # one radio: other saved WiFi networks must not take it over
            self.radio_on()
            for line in self.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], check=False).splitlines():
                f = split_terse(line)
                if len(f) >= 2 and f[1] == "802-11-wireless" and f[0] not in OURS:
                    self.run(["nmcli", "connection", "modify", f[0], "connection.autoconnect", "no"], check=False)
        if mode == "cable":
            self.run(["nmcli", "connection", "add", "type", "ethernet", "ifname", ETH, "con-name", "fet-eth", "autoconnect", "yes",
                      "connection.autoconnect-priority", "50"] + self._ipv4(net["address"]))
            self.run(["nmcli", "connection", "up", "fet-eth"], timeout=40, check=False)
        elif mode == "wifi":
            self.run(["nmcli", "connection", "add", "type", "wifi", "ifname", WIFI, "con-name", "fet-wifi", "autoconnect", "yes",
                      "connection.autoconnect-priority", "50", "ssid", net["ssid"], "wifi-sec.key-mgmt", "wpa-psk",
                      "wifi-sec.psk", net["psk"]] + self._ipv4(net["address"]))
            self.run(["nmcli", "connection", "up", "fet-wifi"], timeout=60, check=False)
        else:                                    # the Pi's own network: WPA2-CCMP on 2.4 GHz for the ESP-01 bridges
            self.run(["nmcli", "connection", "add", "type", "wifi", "ifname", WIFI, "con-name", "fet-range", "autoconnect", "yes",
                      "connection.autoconnect-priority", "50", "ssid", net["ssid"], "802-11-wireless.mode", "ap",
                      "802-11-wireless.band", "bg", "802-11-wireless.channel", str(net["channel"]),
                      "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp",
                      "wifi-sec.psk", net["psk"], "ipv4.method", "shared", "ipv4.addresses", f"{net['address']['ip']}/24",
                      "ipv6.method", "disabled"])
            self.run(["nmcli", "connection", "up", "fet-range"], timeout=40, check=False)

    def has_network(self) -> bool:
        """A wired or WiFi connection with an address (not our setup network)."""
        out = self.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], check=False)
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) >= 3 and f[0] != "fet-setup" and f[1] in ("802-3-ethernet", "802-11-wireless") and f[2]:
                if self.lease(f[2]) or f[0] == "fet-range":
                    return True
        return False

    def active_wifi(self) -> Optional[str]:
        """The WiFi connection (client, not an access point of ours) up now, by name, or None."""
        out = self.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], check=False)
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) >= 3 and f[1] == "802-11-wireless" and f[0] not in ("fet-setup", "fet-range", "fet-test"):
                return f[0]
        return None

    def reconnect(self, name: str) -> None:
        self.run(["nmcli", "connection", "up", name], timeout=60, check=False)

    def start_setup(self) -> None:
        """The setup network for the network settings (fet-setup.service: it waits for a network first)."""
        self.run(["systemctl", "start", "--no-block", "fet-setup.service"], check=False)

    def ntp_synced(self) -> bool:
        return self.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], check=False).strip() == "yes"

    def facts(self) -> dict:
        """What Range Control's System page shows (read-only; works without root)."""
        conns = []
        for line in self.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], check=False).splitlines():
            f = split_terse(line)
            if len(f) >= 3 and f[1] in ("802-3-ethernet", "802-11-wireless") and f[2]:
                conns.append({"name": f[0], "type": "cable" if f[1] == "802-3-ethernet" else "wifi", "device": f[2],
                              "own": f[0] == "fet-range", "setup": f[0] == "fet-setup", "lease": self.lease(f[2])})
        tz = self.run(["timedatectl", "show", "-p", "Timezone", "--value"], check=False).strip()
        ssh = self.run(["systemctl", "is-active", "ssh"], check=False).strip() == "active"
        try:
            host = self.path("/etc/hostname").read_text().strip()
        except OSError:
            host = ""
        return {"hostname": host, "timezone": tz, "ntp": self.ntp_synced(), "ssh": ssh, "connections": conns}

    # ------------------------------------------------------------------------------------- the rest
    def set_country(self, cc: str) -> None:
        code, out = self.run_(["raspi-config", "nonint", "do_wifi_country", cc], None, 30)
        if code != 0:                            # not Raspberry Pi OS: the regulatory domain directly
            self.run(["iw", "reg", "set", cc])

    def set_timezone(self, tz: str) -> None:
        self.run(["timedatectl", "set-timezone", tz])

    def set_clock(self, ms: int) -> None:
        """The time from the phone, when the Pi has no other source (no internet, no clock chip)."""
        if not self.ntp_synced():
            self.run(["date", "-u", "-s", "@%d" % (ms // 1000)])

    def set_hostname(self, name: str) -> None:
        self.run(["hostnamectl", "set-hostname", name])
        hosts = self.path("/etc/hosts")
        try:
            lines = hosts.read_text().splitlines()
        except OSError:
            lines = ["127.0.0.1\tlocalhost"]
        lines = [ln for ln in lines if not ln.startswith("127.0.1.1")] + [f"127.0.1.1\t{name}"]
        tmp = hosts.with_suffix(".fet-tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, hosts)

    def set_admin(self, user: str, password: str) -> None:
        """The admin login: created if new (sudo rights), its password set."""
        exists = self.run(["id", "-u", user], check=False).strip().isdigit()
        if not exists:
            self.run(["useradd", "-m", "-s", "/bin/bash", "-G", "sudo,adm,netdev", user])
        self.run(["chpasswd"], stdin=f"{user}:{password}\n")

    def set_ssh(self, on: bool) -> None:
        self.run(["systemctl", "enable" if on else "disable", "--now", "ssh"])

    def _fetserver(self, args: List[str], stdin: str) -> None:
        self.run(["runuser", "-u", SVC_USER, "--", "env", f"PYTHONPATH={CODE_DIR}", "python3", "-m", "fetserver"] + args
                 + ["--password-stdin", "--data-dir", DATA_DIR], stdin=stdin + "\n")

    def set_range_password(self, pw: str) -> None:
        self._fetserver(["set-password"], pw)

    def add_director(self, name: str, pw: str) -> None:
        self._fetserver(["match-user", "add", name, "--role", "director"], pw)

    def device_token(self) -> Optional[str]:
        try:
            return self.path(f"{DATA_DIR}/device_token").read_text().strip() or None
        except OSError:
            return None

    def apply(self, step: str, arg) -> None:
        {"country": self.set_country, "timezone": self.set_timezone, "clock": self.set_clock, "hostname": self.set_hostname,
         "admin": lambda a: self.set_admin(*a), "range_password": self.set_range_password,
         "director": lambda a: self.add_director(*a), "ssh": self.set_ssh}[step](arg)
