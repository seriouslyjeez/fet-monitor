"""The first-time setup settings: checked here (pure, no system calls), applied by system.py.

A setup has: the Pi's name, country (WiFi rules) and time zone, the Range Control password, an optional first match
director, the admin login (a Linux user, for when someone does need the shell) and whether SSH is on, and the
network:
  cable  the club router by cable; address automatic (DHCP) or fixed
  wifi   the club WiFi (WPA2); address automatic or fixed
  own    the Pi runs its own WiFi network for the targets and phones (WPA2; the ESP-01 needs 2.4 GHz WPA2); the Pi is
         OWN_IP, there is no internet
"""
from __future__ import annotations

import ipaddress
import re
from typing import Dict, List, Optional

from fetserver.admin import MIN_PASSWORD, USERNAME_RE

HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
LINUX_USER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
RESERVED_USERS = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy", "www-data",
                  "backup", "list", "irc", "nobody", "systemd-network", "systemd-resolve", "messagebus", "sshd", "avahi",
                  "fet-monitor", "pi"}           # "pi": the old default, which attackers try first
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
MODES = ("cable", "wifi", "own")
OWN_IP = "10.42.0.1"                        # NetworkManager's shared-network address; the targets' server address
OWN_CHANNELS = (1, 6, 11)
MIN_ADMIN_PASSWORD = 8


class SetupError(ValueError):
    """A setting a person has to change; the message says what (shown on the setup page)."""


def wpa_psk_ok(psk: str) -> bool:
    """A WPA2 passphrase: 8 to 63 printable ASCII characters (or 64 hex digits)."""
    if re.fullmatch(r"[0-9A-Fa-f]{64}", psk):
        return True
    return 8 <= len(psk) <= 63 and all(32 <= ord(ch) <= 126 for ch in psk)


def ssid_ok(ssid: str) -> bool:
    return 1 <= len(ssid.encode("utf-8")) <= 32 and ssid == ssid.strip() and "\n" not in ssid


def fixed_address(d: dict, what: str) -> dict:
    """{ip, prefix, gateway, dns} checked: the gateway and the address in one network, not the network or broadcast."""
    try:
        iface = ipaddress.IPv4Interface(f"{d.get('ip')}/{int(d.get('prefix'))}")
        gw = ipaddress.IPv4Address(str(d.get("gateway")))
        dns = [ipaddress.IPv4Address(x) for x in (d.get("dns") or [])]
    except (ValueError, TypeError):
        raise SetupError(f"{what}: the fixed address, prefix, router or DNS is not a valid IPv4 address")
    net = iface.network
    if not 8 <= net.prefixlen <= 30:
        raise SetupError(f"{what}: the network prefix must be 8 to 30 (usually 24)")
    if iface.ip in (net.network_address, net.broadcast_address):
        raise SetupError(f"{what}: {iface.ip} is the network's own address, not one a device can use")
    if gw not in net or gw == iface.ip:
        raise SetupError(f"{what}: the router {gw} must be another address in {net}")
    return {"ip": str(iface.ip), "prefix": net.prefixlen, "gateway": str(gw), "dns": [str(x) for x in dns[:3]] or [str(gw)]}


def check(raw: dict, countries: List[str], zones: List[str]) -> dict:
    """A clean setup from the page's form, or SetupError naming the first thing to fix."""
    if not isinstance(raw, dict):
        raise SetupError("the settings are missing")
    s = lambda k: raw.get(k) if isinstance(raw.get(k), str) else ""
    out: Dict[str, object] = {}
    host = s("hostname").strip().lower()
    if not HOSTNAME_RE.match(host):
        raise SetupError("Pi name: letters a-z, digits and dashes, up to 63, not starting or ending with a dash")
    out["hostname"] = host
    if not COUNTRY_RE.match(s("country")) or s("country") not in countries:
        raise SetupError("choose the country (it sets the WiFi channels allowed there)")
    out["country"] = s("country")
    if s("timezone") not in zones:
        raise SetupError("choose the time zone (shot times use it)")
    out["timezone"] = s("timezone")
    clock = raw.get("clock_ms")
    if clock is not None and (not isinstance(clock, int) or isinstance(clock, bool) or not 1_700_000_000_000 <= clock <= 4_102_444_800_000):
        raise SetupError("the clock from this device is not a usable time")
    out["clock_ms"] = clock
    if len(s("range_password")) < MIN_PASSWORD:
        raise SetupError(f"Range Control password: at least {MIN_PASSWORD} characters")
    if s("range_password") != s("range_password2"):
        raise SetupError("Range Control password: the two entries differ")
    out["range_password"] = s("range_password")
    if s("director"):
        if not USERNAME_RE.match(s("director")):
            raise SetupError("match director: a name of 2 to 32 letters, digits, dots, dashes or underscores")
        if len(s("director_password")) < MIN_PASSWORD:
            raise SetupError(f"match director password: at least {MIN_PASSWORD} characters")
        out["director"], out["director_password"] = s("director"), s("director_password")
    else:
        out["director"] = None
    admin = s("admin").strip()
    if not LINUX_USER_RE.match(admin) or admin in RESERVED_USERS:
        raise SetupError("admin login: 2 to 32 lowercase letters, digits, dashes or underscores, starting with a letter "
                         "(not root, pi or a system name)")
    if len(s("admin_password")) < MIN_ADMIN_PASSWORD:
        raise SetupError(f"admin password: at least {MIN_ADMIN_PASSWORD} characters")
    if s("admin_password") != s("admin_password2"):
        raise SetupError("admin password: the two entries differ")
    out["admin"], out["admin_password"] = admin, s("admin_password")
    out["ssh"] = raw.get("ssh") is True
    out["network"] = check_network(raw.get("network"))
    return out


def check_network(n) -> dict:
    if not isinstance(n, dict) or n.get("mode") not in MODES:
        raise SetupError("choose how the Pi connects: cable, the club WiFi, or its own network")
    mode = n["mode"]
    if mode == "own":
        ssid, psk = str(n.get("ssid") or ""), str(n.get("psk") or "")
        if not ssid_ok(ssid):
            raise SetupError("range network name: 1 to 32 characters")
        if not wpa_psk_ok(psk):
            raise SetupError("range network password: 8 to 63 characters (letters, digits, symbols; no accents)")
        ch = n.get("channel", 6)
        if ch not in OWN_CHANNELS:
            raise SetupError("channel: 1, 6 or 11")
        return {"mode": "own", "ssid": ssid, "psk": psk, "channel": ch, "address": {"ip": OWN_IP, "prefix": 24}}
    out = {"mode": mode}
    if mode == "wifi":
        ssid, psk = str(n.get("ssid") or ""), str(n.get("psk") or "")
        if not ssid_ok(ssid):
            raise SetupError("club WiFi: choose the network")
        if not wpa_psk_ok(psk):
            raise SetupError("club WiFi password: 8 to 63 characters (a WPA2 network; the targets need one too)")
        out.update(ssid=ssid, psk=psk)
    addr = n.get("address", "dhcp")
    out["address"] = "dhcp" if addr == "dhcp" else fixed_address(addr if isinstance(addr, dict) else {}, "address")
    return out


def plan(cfg: dict) -> List[tuple]:
    """The steps to apply, in order: everything that does not touch the network first (the phone stays connected
    to the setup network while they run), the network last. Each step is (name, argument)."""
    steps = [("country", cfg["country"]), ("timezone", cfg["timezone"])]
    if cfg.get("clock_ms"):
        steps.append(("clock", cfg["clock_ms"]))
    steps += [("hostname", cfg["hostname"]), ("admin", (cfg["admin"], cfg["admin_password"])),
              ("range_password", cfg["range_password"])]
    if cfg.get("director"):
        steps.append(("director", (cfg["director"], cfg["director_password"])))
    steps.append(("ssh", cfg["ssh"]))
    return steps


def server_address(cfg: dict, live: Optional[str] = None) -> Optional[str]:
    """The address the targets are given: the fixed one, the Pi's own network's, or (automatic) what it has now."""
    a = cfg["network"]["address"]
    return a["ip"] if isinstance(a, dict) else live
