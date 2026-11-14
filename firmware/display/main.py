# FeT Display: a CYD shot monitor at a firing point, paired with one target by FeT Monitor (docs/PLAN-cyd.md,
# docs/PROTOCOL.md "Lane displays"). Runs at power-on. Its libraries are in /lib.
#
# Start: 3 quick power cycles, or no settings yet -> the setup portal. A new display calibrates its touch screen
# first (again: switch it on with a finger on the screen). Then it joins the range WiFi, checks that FeT Monitor
# answers, pairs (a code to type in Range Control) and shows its target, live.
import time

import machine
import network

import config
import http
import portal
import ui
from ili9341 import Screen
from xpt2046 import Touch

VERSION = "0.1.0"

s = Screen()
s.light(True)
t = Touch()
page = ui.Page(s, t)
qb = config.QuickBoot()
quick = qb.on_boot()
sta = network.WLAN(network.STA_IF)
sta.active(True)
mac = "".join("%02x" % b for b in sta.config("mac"))
HW_ID = "cyd-" + mac[-6:]
FOOTER = "FeT Display %s  %s" % (VERSION, HW_ID)


def tick():
    qb.tick()


def setup(cfg):
    """The setup portal until a setup is saved (then the display restarts)."""
    name = "FeT-Display-" + mac[-6:]
    nets = {"at": 0, "names": []}

    def scan():
        if time.ticks_diff(time.ticks_ms(), nets["at"]) > 30_000 or not nets["names"]:
            try:
                found = sorted(sta.scan(), key=lambda n: -n[3])
                names = []
                for n in found:
                    try:
                        nm = n[0].decode("utf-8") if isinstance(n[0], bytes) else n[0]
                    except UnicodeError:
                        continue
                    if nm and nm not in names and nm != name:
                        names.append(nm)
                nets["names"], nets["at"] = names, time.ticks_ms()
            except OSError:
                pass
        return nets["names"]

    def saved(c):
        config.save(c)
        qb.forget()
        page.show([("SAVED", 3, ui.GOOD), ("Restarting...", 2, ui.FG)], footer=FOOTER)
        time.sleep_ms(1500)
        machine.reset()

    sta.disconnect()
    scan()                                              # before the network opens: scanning then disturbs nobody
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=name, authmode=network.AUTH_OPEN)
    page.show([("SET UP THIS DISPLAY", 2, ui.WARN), ("On a phone, join the WiFi", 2, ui.FG), (name, 2, ui.GOOD),
               ("then open " + portal.AP_IP + " (it may open by itself)", 2, ui.FG)], footer=FOOTER)
    portal.Portal(name, cfg, scan=scan, saved=saved).serve(tick=tick)


def join(cfg):
    """Joined the WiFi (True), or the person chose Set up (False)."""
    while True:
        page.show([("JOINING WIFI", 2, ui.FG), (cfg["ssid"], 2, ui.GOOD)], [("SET UP", "setup")], footer=FOOTER)
        sta.connect(cfg["ssid"], cfg["password"])
        t0 = time.ticks_ms()
        while not sta.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < 25_000:
            if page.wait(200, tick) == "setup":
                return False
        if sta.isconnected():
            return True
        st = sta.status()
        why = {network.STAT_WRONG_PASSWORD: "wrong password", network.STAT_NO_AP_FOUND: "network not found"}.get(st, "no answer")
        sta.disconnect()
        page.show([("CAN'T JOIN WIFI", 2, ui.BAD), (cfg["ssid"], 2, ui.FG), (why, 2, ui.WARN)],
                  [("TRY AGAIN", "again"), ("SET UP", "setup")], footer=FOOTER)
        if page.wait(60_000, tick) == "setup":
            return False


def paired(cfg):
    """Pair with FeT Monitor if not paired yet, then show the target, live, with its buttons. Returns when the WiFi
    is lost. The network task only keeps the newest view; the screen task decides what is drawn (the target, the
    sign-in screen, the keypad, a question), so a new view never draws over the keypad."""
    import asyncio
    import client
    import qr
    import target

    cl = client.Client(cfg["server"], HW_ID, VERSION, cfg.get("token"), secure=bool(cfg.get("home")) and not cfg.get("plain_http"))
    boot = machine.Pin(0, machine.Pin.IN, machine.Pin.PULL_UP)
    st = {"v": None, "at": 0, "done": False, "problem": None, "dirty": True, "screen": "target", "buttons": [],
          "dismissed": False, "drawn": (None, None, None, None), "kp": None,
          "zoom": cfg.get("zoom") if cfg.get("zoom") in target.ZOOMS else "auto", "boot_down": False, "choose_page": 0, "qr": (None, None), "busy": False, "msg": None, "code": None}

    def keep(token):
        if cfg.get("token") != token:
            if token:
                cfg["token"] = token
            else:
                cfg.pop("token", None)
            config.save(cfg)

    def now_server():
        v = st["v"]
        return v["server_time_ms"] + time.ticks_diff(time.ticks_ms(), st["at"]) if v else None

    def got(v):
        old = st["v"]
        st["v"], st["at"], st["problem"], st["dirty"] = v, time.ticks_ms(), None, True
        if old is None or (old.get("athlete") or {}).get("public_name") != (v.get("athlete") or {}).get("public_name"):
            st["dismissed"] = False                    # someone signed in or out: the sign-in screen may show again

    async def net():
        rev, backoff = None, 1
        while sta.isconnected():
            try:
                if not cl.token:
                    st["code"] = await cl.pair()
                    st["screen"], st["dirty"] = "pairing", True
                v = await cl.view(rev, client.WAIT_S if rev else 0)
                keep(cl.token)                         # approved: the token is this display's from now on
                backoff = 1
                if st["screen"] == "pairing":
                    st["screen"] = "target"
                if v["rev"] != rev or st["problem"]:
                    got(v)
                    rev = v["rev"]
            except client.NotAssigned as e:
                if e.pairing == "pending":
                    await asyncio.sleep_ms(2000)       # the code is on the screen: wait for Range Control
                else:                                  # removed, replaced, or the code expired: a new code
                    keep(None)
                    cl.token, rev, st["v"] = None, None, None
            except OSError as exc:
                st["problem"], st["dirty"] = "RECONNECTING" if st["v"] else "CAN'T REACH " + str(exc)[:24], True
                await asyncio.sleep_ms(backoff * 1000)
                backoff = min(backoff * 2, 15)
        st["done"] = True

    def default_screen(v):
        """Nobody signed in, nothing shot yet, sign-in possible, SHOOT not pressed: the sign-in screen."""
        if (not v.get("athlete") and not v.get("claim") and not v.get("match") and v["count"] == 0
                and (v.get("signin_url") or v.get("pin_signin")) and not st["dismissed"]):
            return "signin"
        return "target"

    def draw():
        """What changed since the last draw: the whole screen when it is another screen, else only the changes."""
        v, scr = st["v"], st["screen"]
        st["dirty"] = False
        if scr == "pairing":
            if st["drawn"] != ("pairing", st["code"], None):
                target.pairing(s, st["code"] or "", FOOTER, bool(cfg.get("home")))
                st["buttons"], st["drawn"] = [], ("pairing", st["code"], None, None)
            return
        if v is None:
            if st["problem"]:
                page.show([("CAN'T REACH", 2, ui.BAD), ("FeT Monitor " + cfg["server"], 2, ui.FG), (st["problem"][:38], 1, ui.WARN)],
                          footer=FOOTER)
            else:
                page.show([("CONNECTING", 2, ui.FG), ("FeT Monitor " + cfg["server"], 2, ui.DIM)], footer=FOOTER)
            st["buttons"], st["drawn"] = [], ("page", None, None, None)
            return
        drawn_scr, drawn_v, drawn_problem, drawn_zoom = st["drawn"]
        if v.get("state") == "choose":                 # home: no target chosen yet
            scr = st["screen"] = "choose"
            if drawn_scr != "choose" or drawn_v is not v:
                st["buttons"] = target.choose_screen(s, v.get("targets") or [], st["choose_page"])
            st["drawn"] = (scr, v, st["problem"], st["zoom"])
            return
        if scr == "choose":
            scr = st["screen"] = "target"
        if scr == "target":
            scr = st["screen"] = default_screen(v)
        elif scr == "signin" and (v.get("athlete") or v.get("claim") or v.get("match")):
            scr = st["screen"] = "target"              # signed in (a phone, a claim) or a match began meanwhile
        if scr == "signin":
            url = v.get("signin_url")
            if drawn_scr != "signin" or (drawn_v or {}).get("signin_url") != url or (drawn_v or {}).get("pin_signin") != v.get("pin_signin"):
                if url and st["qr"][0] != url:
                    st["qr"] = (url, qr.encode(url))
                st["buttons"] = target.signin_screen(s, v, st["qr"][1] if url else None)
        elif scr == "keypad":
            if drawn_scr != "keypad":                  # then only key presses redraw it (keypad_top, keypad_ok)
                kp = st["kp"]
                st["buttons"] = target.keypad_screen(s, kp["phase"], kp["digits"], kp.get("msg"))
        elif scr == "confirm":
            if drawn_scr != "confirm":
                name = (v.get("athlete") or {}).get("public_name") or ""
                st["buttons"] = target.confirm_screen(s, "Sign out %s?" % name, "SIGN OUT", "signout")
        else:
            prev = (drawn_v, drawn_problem, drawn_zoom) if drawn_scr == "target" and drawn_v is not None else None
            st["buttons"] = target.target_with_button(s, v, st["problem"], prev, st["buttons"] if prev else None, st["zoom"])
            if st["msg"]:
                if st["msg"] != st.get("msg_drawn") or prev is None or target._top(drawn_v) != target._top(v):
                    target.message(s, st["msg"][0], st["msg"][1])
                    st["msg_drawn"], st["msg_until"] = st["msg"], time.ticks_add(time.ticks_ms(), 5000)
            elif v.get("claim") or (drawn_v or {}).get("claim"):
                target.claim_band(s, v, now_server())
        if scr != "target":
            st["msg_drawn"] = None
        st["drawn"] = (scr, v, st["problem"], st["zoom"])

    async def act(action, **fields):
        st["busy"] = True
        target.message(s, "...", ui.DIM) if st["screen"] == "target" else None
        try:
            v = await cl.act(action, **fields)
            got(v)
            return True
        except ValueError as exc:
            return str(exc)
        except client.NotAssigned:
            return "this display is not assigned any more"
        except OSError:
            return "FeT Monitor did not answer: try again"
        finally:
            st["busy"] = False

    def kp_update(kp):
        """A key press on the keypad: only its top lines and the OK key are drawn again."""
        target.keypad_top(s, kp["phase"], kp["digits"], kp.get("msg"))
        st["buttons"][-1] = target.keypad_ok(s, kp["phase"], kp["digits"])

    async def tap(key):
        scr = st["screen"]
        st["msg"] = None
        if key.startswith("pick:"):
            r = await act("choose", device_id=key[5:])
            st["screen"], st["dismissed"] = "target", False
            if r is not True:
                st["msg"] = (r, ui.BAD)
        elif key == "more":
            st["choose_page"] += 1
            st["drawn"] = (None, None, None, None)
        elif key == "targets":
            r = await act("targets")
            st["choose_page"] = 0
            if r is not True:
                st["msg"] = (r, ui.BAD)
        elif key == "signin_screen":
            st["screen"] = "signin"
        elif key == "dismiss":
            st["screen"], st["dismissed"] = "target", True
        elif key == "keypad":
            st["screen"], st["kp"] = "keypad", {"phase": "number", "digits": "", "number": ""}
        elif key == "cancel" or key == "no":
            st["screen"], st["kp"], st["dismissed"] = "target", None, True
        elif key.startswith("k") and scr == "keypad":
            kp = st["kp"]
            k = key[1:]
            if k == "DEL":
                kp["digits"] = kp["digits"][:-1]
            elif len(kp["digits"]) < 6:
                kp["digits"] += k
            kp["msg"] = None
            kp_update(kp)
            return
        elif key == "ok" and scr == "keypad":
            kp = st["kp"]
            if kp["phase"] == "number":
                kp.update(phase="pin", number=kp["digits"], digits="", msg=None)
                kp_update(kp)
                return
            kp["msg"] = "Signing in..."
            kp_update(kp)
            r = await act("signin", number=kp["number"], pin=kp["digits"])
            if r is True:
                st["screen"], st["kp"] = "target", None
                st["msg"] = ("Signed in", ui.GOOD)
            else:
                kp.update(phase="pin", digits="", msg=r)
                kp_update(kp)
                return
        elif key == "signout?":
            st["screen"] = "confirm"
        elif key in ("signout", "claim_cancel", "start_record", "back_to_sighters"):
            st["screen"] = "target"
            r = await act(key)
            st["msg"] = ({"signout": "Signed out", "claim_cancel": "Claim cancelled", "start_record": "Record started",
                          "back_to_sighters": "Back to sighters"}[key], ui.GOOD) if r is True else (r, ui.BAD)
        st["dirty"] = True

    def trouble(exc):
        """A mistake in drawing or in a button: never a frozen display. Shown, printed to USB, back to the target."""
        import sys
        sys.print_exception(exc)
        st["screen"], st["kp"], st["busy"] = "target", None, False
        st["msg"], st["dirty"] = ("display error: " + (str(exc) or type(exc).__name__)[:24], ui.BAD), True
        try:
            draw()
        except Exception as exc2:                      # even the target screen fails: a plain page
            sys.print_exception(exc2)
            st["buttons"] = []
            page.show([("DISPLAY ERROR", 2, ui.BAD), (str(exc2)[:38], 1, ui.WARN)], footer=FOOTER)

    async def screen():
        while not st["done"]:
            try:
                await screen_once()
            except Exception as exc:
                trouble(exc)
            await asyncio.sleep_ms(40)

    last = {"sec": -1}

    def boot_button():
        """BOOT (on the back): Full, Bull, Auto zoom, in turn, when it is let go. Kept across restarts."""
        down = boot() == 0
        if down and not st["boot_down"]:
            st["boot_down"] = True
        elif not down and st["boot_down"]:
            st["boot_down"] = False
            z = target.ZOOMS[(target.ZOOMS.index(st["zoom"]) + 1) % len(target.ZOOMS)]
            st["zoom"], cfg["zoom"] = z, z
            config.save(cfg)
            if st["screen"] == "target" and st["v"]:
                st["msg"], st["dirty"] = ("Zoom: " + {"full": "Full", "bull": "Bull", "auto": "Auto"}[z], ui.DIM), True

    async def screen_once():
        tick()
        boot_button()
        if st["dirty"] and not st["busy"]:
            draw()
        p = t.point() if not st["busy"] else None
        if p:
            for b in st["buttons"]:
                x, y, w, h, key = b[:5]
                if key and x - 4 <= p[0] < x + w + 4 and y - 4 <= p[1] < y + h + 4:
                    target.press(s, b, True)            # lit up at once: the press is seen before anything else
                    while t.pressed():
                        await asyncio.sleep_ms(15)
                    target.press(s, b, False)
                    await tap(key)
                    break
        v = st["v"]
        if st["msg"] and st["screen"] == "target" and time.ticks_diff(time.ticks_ms(), st.get("msg_until", 0)) >= 0:
            st["msg"] = st["msg_drawn"] = None             # the answer has been read: the top bar again
            if v:
                target.top(s, v)
                target.claim_band(s, v, now_server())
        if v and st["screen"] == "target" and (v.get("match") or (v.get("claim") and not st["msg"])):
            now = now_server()
            if now // 1000 != last["sec"]:
                last["sec"] = now // 1000
                if v.get("match"):
                    target.clock(s, v, now)
                if not st["msg"]:
                    target.claim_band(s, v, now)

    async def both():
        asyncio.create_task(screen())
        await net()

    asyncio.run(both())


def set_clock():
    """HTTPS needs the right time (certificates have dates): from NTP, several servers (pool.ntp.org did not answer on
    one home network). False: the person chose Set up."""
    import ntptime
    while True:
        page.show([("SETTING THE CLOCK", 2, ui.FG)], footer=FOOTER)
        for host in ("time.cloudflare.com", "time.google.com", "pool.ntp.org"):
            try:
                ntptime.host = host
                ntptime.settime()
                return True
            except OSError:
                pass
        page.show([("CAN'T SET THE CLOCK", 2, ui.BAD), ("No time server answered (UDP port 123).", 1, ui.WARN)],
                  [("TRY AGAIN", "again"), ("SET UP", "setup")], footer=FOOTER)
        if page.wait(30_000, tick) == "setup":
            return False


def reach(cfg):
    """Check that FeT Monitor answers; then show it. False: the person chose Set up."""
    host, _, port = cfg["server"].partition(":")
    while True:
        try:
            st, body = http.request("GET", host, int(port), "/api/v1/health", timeout=5)
            ok = st == 200 and body and body.get("ok")
            if ok:
                return True
            lines = [("FeT Monitor answered %d" % st, 2, ui.WARN), (cfg["server"], 2, ui.FG)]
        except OSError as exc:
            ok = False
            lines = [("CAN'T REACH", 2, ui.BAD), ("FeT Monitor " + cfg["server"], 2, ui.FG), (str(exc)[:38], 1, ui.WARN)]
        page.show(lines, [("TRY AGAIN", "again"), ("SET UP", "setup")], footer=FOOTER)
        if page.wait(30_000, tick) == "setup":
            return False
        if not sta.isconnected():
            return True                                # the WiFi went: join it again


def run():
    page.show([("FeT Display", 3, ui.FG), (VERSION, 2, ui.DIM)], footer=FOOTER)
    cfg = config.load()
    if t.cal is None or t.pressed():                   # a new display, or a finger on the screen at power-on
        import calibrate
        calibrate.calibrate(s, t, tick)
    if quick or cfg is None:
        setup(cfg)
    while True:
        home = cfg.get("home")                         # deepTENS: the clock (HTTPS checks dates); a range: FeT Monitor answers
        if not join(cfg) or not ((cfg.get("plain_http") or set_clock()) if home else reach(cfg)):
            setup(cfg)
        if sta.isconnected():
            paired(cfg)                                # returns when the WiFi is lost: join it again


run()
