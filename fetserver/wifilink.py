"""A target's WiFi link as its firmware reports it in the heartbeat ("link", FeT target firmware for the ESP32 board
0.2.2+): why it last started, how often the WiFi dropped since then and why, how long it was without WiFi, and the
access point it is on. The codes are ESP-IDF's (esp_system.h esp_reset_reason_t, esp_wifi_types_generic.h
wifi_err_reason_t, Arduino-ESP32 3.3.12); clean() keeps only well-formed values, view() names them.
"""
from __future__ import annotations

import re
from typing import Optional

RESETS = {0: "unknown", 1: "power-on", 2: "external reset pin", 3: "a software restart", 4: "a crash (panic)",
          5: "the interrupt watchdog", 6: "the task watchdog", 7: "another watchdog", 8: "waking from deep sleep",
          9: "a brownout (supply voltage dropped)", 10: "SDIO", 11: "USB", 12: "JTAG", 13: "an eFuse error",
          14: "a power glitch", 15: "a CPU lock-up"}
TROUBLE_RESETS = {4, 5, 6, 7, 9, 14, 15}         # not a power-on or a restart someone asked for

REASONS = {1: "unspecified", 2: "authentication expired", 3: "the router ended it (deauthenticated)",
           4: "disassociated for inactivity", 5: "the router has too many devices", 6: "class 2 frame from an unauthenticated station",
           7: "class 3 frame from an unassociated station", 8: "the target left (it restarted its connection)",
           9: "associated but not authenticated", 10: "poor power capability", 11: "unsupported channel",
           12: "moved to another access point (BSS transition)", 13: "invalid information element", 14: "MIC failure",
           15: "4-way handshake timeout (wrong password, or a weak link)", 16: "group key update timeout",
           17: "information element differs in the 4-way handshake", 18: "invalid group cipher", 19: "invalid pairwise cipher",
           20: "invalid AKMP", 21: "unsupported RSN IE version", 22: "invalid RSN IE capabilities", 23: "802.1X authentication failed",
           24: "cipher suite rejected", 34: "missing acknowledgements (a weak link)", 39: "timeout",
           46: "the other side ended it", 47: "the router ended it",
           200: "beacon timeout (the router went out of reach: weak signal or interference)", 201: "no access point found",
           202: "authentication failed", 203: "association failed", 204: "handshake timeout", 205: "connection failed",
           206: "the router restarted (TSF reset)", 207: "roaming", 208: "association comeback time too long",
           209: "SA query timeout (protected management frames)", 210: "no access point with compatible security",
           211: "no access point within the authentication threshold", 212: "no access point within the signal threshold"}

INTS = ("reset", "drops", "reason", "last_drop_ms", "down_ms", "longest_ms", "bssid_changes", "ch",
        "rejoins", "posts", "post_fail_connect", "post_fail_reply", "post_max_ms",
        "ps", "ap", "loop_max_ms", "idle0", "idle1", "top_pct", "phy")   # phy: 0.2.4, WIFI_PROTOCOL_11B 1 | 11G 2 | 11N 4     # 0.2.3: power save, setup network, CPU, network task
PS = {0: "off", 1: "on (minimum)", 2: "on (maximum)"}
TASK_RE = re.compile(r"^[A-Za-z0-9_ -]{1,16}$")
RESTARTS = {"net_stuck": "its network task was stuck for 3 minutes", "no_wifi": "it had no WiFi for 20 minutes"}
BSSID_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


def clean(link) -> Optional[dict]:
    """The well-formed part of a heartbeat's "link" object, or None."""
    if not isinstance(link, dict):
        return None
    out = {k: link[k] for k in INTS if isinstance(link.get(k), int) and not isinstance(link.get(k), bool) and 0 <= link[k] < 2 ** 53}
    b = link.get("bssid")
    if isinstance(b, str) and BSSID_RE.match(b):
        out["bssid"] = b
    if link.get("restart_why") in RESTARTS:                 # the board restarted itself (its WiFi safety net)
        out["restart_why"] = link["restart_why"]
    if isinstance(link.get("top"), str) and TASK_RE.match(link["top"]):
        out["top"] = link["top"]
    return out or None


def view(link: Optional[dict], uptime_ms: Optional[int] = None) -> Optional[dict]:
    """For Range Control: the codes named, and when the last drop was (seconds before the last heartbeat)."""
    if not link:
        return None
    v = dict(link)
    if "reset" in link:
        v["reset_name"] = RESETS.get(link["reset"], f"code {link['reset']}")
        v["reset_trouble"] = link["reset"] in TROUBLE_RESETS
    if link.get("restart_why") in RESTARTS:
        v["reset_name"], v["reset_trouble"] = "its WiFi safety net: " + RESTARTS[link["restart_why"]], True
    if "phy" in link:
        v["phy_name"] = "802.11" + "/".join(x for b, x in ((1, "b"), (2, "g"), (4, "n"), (8, "LR"), (16, "ax")) if link["phy"] & b)
    if "ps" in link:
        v["ps_name"] = PS.get(link["ps"], f"code {link['ps']}")
    if link.get("posts"):
        v["post_failed"] = link.get("post_fail_connect", 0) + link.get("post_fail_reply", 0)
    if link.get("drops"):
        v["reason_name"] = REASONS.get(link.get("reason"), f"code {link.get('reason')}")
        if uptime_ms is not None and link.get("last_drop_ms") is not None and uptime_ms >= link["last_drop_ms"]:
            v["last_drop_ago_s"] = (uptime_ms - link["last_drop_ms"]) // 1000
    return v
