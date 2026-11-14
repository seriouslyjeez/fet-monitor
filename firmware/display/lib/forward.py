# FeT Display as a portable kit: its target's stored lines sent on to deepTENS (user 2026-11-14, docs/PLAN-cyd.md step
# 5; deepTENS devices/ingest.py handle_kit_lines), whenever the display reaches the internet. The kit pairs with
# deepTENS like a home display (a code typed at deeptens.com/display) and sends with its own token; deepTENS stores
# each line once by its identity (device_id, boot_count, seq), so sending again after a power cut is harmless.
#
# What has been sent: a byte offset into kit.LOG (kit-data/sent), moved past a batch once deepTENS answered for every
# line in it (acknowledged, or refused: a refused line is counted, not sent again: a target that is not the owner's on
# deepTENS would otherwise stop everything behind it).
# Time: a line's target boot started at (the real time it arrived) - (the request's uptime) when the display knew the
# real time then, or learned it later in the same start (ticks then, offset now); otherwise deepTENS keeps its time as
# unknown, or takes it from another line of the same boot.
import json
import os

STATE = "/kit-data/sent"
TYPES = ("shot", "target_shot", "target_hello", "arduino_boot")
BATCH = 20                       # lines a request (bodies of a few KB: TLS and the screen share the memory)


class Forwarder:
    def __init__(self, log, state=STATE, cyd_boot=0, ticks=None, epoch=None):
        self.log, self.state, self.cyd_boot = log, state, cyd_boot
        self.ticks = ticks or (lambda: 0)
        self.epoch = epoch or (lambda: None)
        self.offset = 0
        self.sent = self.refused = 0
        self.last_refusal = None
        try:
            with open(state) as fh:
                v = json.loads(fh.read())
            self.offset, self.sent, self.refused = int(v["offset"]), int(v.get("sent", 0)), int(v.get("refused", 0))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _boot_start(self, rec):
        if rec.get("ru") is None:
            return None                                    # stored from another boot than the request's
        ep = rec.get("ep")
        if ep is None and rec.get("cb") == self.cyd_boot:
            now = self.epoch()
            if now is not None:
                ep = now - self.ticks() + rec["rx"]          # the clock was set later in this start
        return None if ep is None else ep - rec["ru"]

    def line(self, rec):
        """A stored record as deepTENS takes it, or None (not a line to send)."""
        if rec.get("t") not in TYPES or not isinstance(rec.get("r"), str):
            return None
        out = {"device_id": rec["d"], "boot_count": rec["b"], "seq": rec["q"], "type": rec["t"], "uptime_ms": rec["u"], "raw": rec["r"]}
        if rec.get("f"):
            out["face_id"] = rec["f"]
        start = self._boot_start(rec)
        if start is not None and start >= 0:
            out["boot_start_ms"] = start
        return out

    def batch(self, n=BATCH):
        """(lines, offset after them) from what has not been sent; ([], offset) when there is nothing (lines that are
        not sent, and a last line still being written, are passed over)."""
        lines, end = [], self.offset
        try:
            fh = open(self.log, "rb")
        except OSError:
            return [], self.offset
        with fh:
            fh.seek(self.offset)
            while len(lines) < n:
                raw = fh.readline()
                if not raw or not raw.endswith(b"\n"):
                    break                                  # the end (or a line cut short: never acknowledged)
                end += len(raw)
                try:
                    ln = self.line(json.loads(raw))
                except (ValueError, KeyError, TypeError):
                    ln = None
                if ln is not None:
                    lines.append(ln)
        return lines, end

    def done(self, end, answer=None):
        """deepTENS answered for a batch (or it had nothing to send): move past it."""
        rej = (answer or {}).get("rejected") or []
        self.sent += len((answer or {}).get("acked") or [])
        self.refused += len(rej)
        if rej:
            self.last_refusal = str(rej[-1].get("why") or "refused")[:60]
        self.offset = end
        tmp = self.state + ".new"
        with open(tmp, "w") as fh:
            fh.write(json.dumps({"offset": end, "sent": self.sent, "refused": self.refused}))
        os.rename(tmp, self.state)                         # one step: a power cut leaves the old or the new

    def waiting(self):
        """Lines not yet sent (a count, for the screen)."""
        try:
            return max(0, os.stat(self.log)[6] - self.offset)
        except OSError:
            return 0


async def run(fw, client, online, on_code, on_state, wake, kit_token_saved):
    """Forever: when online(), pair if needed (on_code(code) shows it), then send every batch; wait for wake (an
    asyncio.Event set by new lines) or 60 s when nothing is left. on_state(text) says how it goes (for the screen).
    kit_token_saved(token or None): keep the token across restarts."""
    import asyncio
    import client as C
    backoff = 5
    while True:
        try:
            if not online():
                on_state(None)
                await asyncio.sleep(10)
                continue
            if not client.token:
                on_code(await client.pair(kit=True))
            lines, end = fw.batch()
            if not lines and end == fw.offset:
                await client.lines([])                     # paired? (401 pending until the code is typed)
                kit_token_saved(client.token)
                on_code(None)
                on_state("deepTENS: all sent")
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), 60)
                except asyncio.TimeoutError:
                    pass
                continue
            ans = await client.lines(lines) if lines else {}
            kit_token_saved(client.token)
            on_code(None)
            fw.done(end, ans)
            on_state("deepTENS: sending" if fw.batch(1)[0] else "deepTENS: all sent")
            backoff = 5
        except C.NotAssigned as e:
            if e.pairing == "pending":
                on_state("deepTENS: type the code")
                await asyncio.sleep(5)
            else:                                          # removed on deepTENS, or the code expired: a new code
                client.token = None
                kit_token_saved(None)
        except ValueError as exc:                          # the request refused as a whole: try later
            on_state("deepTENS: " + str(exc)[:40])
            await asyncio.sleep(60)
        except OSError:
            on_state("deepTENS: no answer")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
