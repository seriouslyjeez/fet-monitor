# FeT Display's settings (/config.json) and the "3 quick power cycles open setup" rule, as on the targets.
import json
import time

CONFIG_FILE = "/config.json"
QUICK_FILE = "/quickboot"
QUICK_TRIGGER = 3            # the 3rd quick boot in a row opens setup
QUICK_STABLE_MS = 8000       # running this long: it was not a quick power cycle


def load():
    """The settings, or None when not set up: {"ssid", "password", "server": "host:port"} (+ "token" once paired)."""
    try:
        with open(CONFIG_FILE) as fh:
            c = json.load(fh)
        return c if isinstance(c, dict) and c.get("ssid") and c.get("server") else None
    except (OSError, ValueError):
        return None


def save(c):
    tmp = CONFIG_FILE + ".new"
    with open(tmp, "w") as fh:
        json.dump(c, fh)
    import os
    os.rename(tmp, CONFIG_FILE)              # replaced in one step: a power cut leaves the old or the new


DEEPTENS = "deeptens.com:8443"                     # until deepTENS goes public on 443
_HOST = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"   # (MicroPython's str has no isalnum)


def check(form):
    """The setup page's fields -> settings, or ValueError with what to fix. form: {"ssid", "password", "open", "where"
    ("range": a FeT Monitor at a range, by its IP address; "home": deepTENS, the owner's targets, over HTTPS),
    "server"} as strings."""
    ssid = form.get("ssid", "").strip()
    pw = form.get("password", "")
    open_net = form.get("open") == "1"
    home = form.get("where") == "home"
    server = form.get("server", "").strip() or (DEEPTENS if home else "")
    if not 1 <= len(ssid.encode()) <= 32:
        raise ValueError("The WiFi network's name: 1 to 32 characters.")
    if open_net:
        pw = ""
    elif not 8 <= len(pw) <= 63:
        raise ValueError("The WiFi password: 8 to 63 characters (or tick 'no password').")
    host, _, port = server.partition(":")
    if home:
        port = port or "443"
        ok = 3 <= len(host) <= 64 and "." in host and all(c in _HOST for c in host) and host[0] not in ".-"
        if not ok:
            raise ValueError("deepTENS's address: e.g. " + DEEPTENS + ".")
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("The port: a number from 1 to 65535.")
        return {"ssid": ssid, "password": pw, "server": "%s:%d" % (host, int(port)), "home": True}
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 and (p == "0" or not p.startswith("0")) for p in parts):
        raise ValueError("The FeT Monitor's address: its IP address, e.g. 192.168.1.20 (names ending in .local do not work on every network).")
    port = port or "8080"
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("The port: a number from 1 to 65535 (FeT Monitor uses 8080).")
    return {"ssid": ssid, "password": pw, "server": "%s:%d" % (host, int(port))}


class QuickBoot:
    """Counts boots that end within QUICK_STABLE_MS: the 3rd in a row opens setup (power off and on 3 times)."""

    def __init__(self):
        self.armed = False
        self.t0 = time.ticks_ms()

    def on_boot(self):
        try:
            with open(QUICK_FILE) as fh:
                n = int(fh.read() or 0)
        except (OSError, ValueError):
            n = 0
        n += 1
        if n >= QUICK_TRIGGER:
            self.forget()
            return True
        with open(QUICK_FILE, "w") as fh:
            fh.write(str(n))
        self.armed = True
        return False

    def tick(self):
        if self.armed and time.ticks_diff(time.ticks_ms(), self.t0) >= QUICK_STABLE_MS:
            self.forget()

    def forget(self):
        """A deliberate restart (new settings) or a boot that lasted: not a quick power cycle."""
        self.armed = False
        try:
            import os
            os.remove(QUICK_FILE)
        except OSError:
            pass
