"""Range Control authentication: one password, set on the Pi, and short-lived login sessions.

* The password is set with `python3 -m fetserver set-password` (needs a shell on the Pi), so nobody on the LAN can
  claim it first. It is stored as a salted scrypt hash in <data-dir>/admin_password (mode 0600); the file is read on
  every login, so changing it needs no restart and ends every session.
* The LAN is plain HTTP: the password and the session cookie can be sniffed there. Sessions are therefore short
  (ADMIN_IDLE_S idle, ADMIN_MAX_S at most), live only in memory (a restart logs everyone out), and the cookie is
  HttpOnly, SameSite=Strict and sent only to /api/v1/admin.
* Failed logins are limited per address (FAIL_LIMIT within FAIL_WINDOW_S locks that address for LOCK_S).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

PASSWORD_FILE = "admin_password"
MIN_PASSWORD = 8
ADMIN_IDLE_S = 2 * 3600
ADMIN_MAX_S = 12 * 3600
FAIL_LIMIT = 5
FAIL_WINDOW_S = 600
LOCK_S = 600
COOKIE = "fet_admin"
COOKIE_PATH = "/api/v1/admin"
MATCH_COOKIE = "fet_match"
MATCH_COOKIE_PATH = "/api/v1/match"
ROLES = ("director", "officer")          # Match Control: director (formats, matches, decisions) and range officer (clock)
USERNAME_RE = __import__("re").compile(r"^[A-Za-z0-9._-]{2,32}$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}   # ~16 MB and well under a second on a Raspberry Pi
PBKDF2_ITER = 310_000                     # fallback where Python lacks scrypt (built without OpenSSL 1.1+)


def _derive(password: str, rec: dict) -> bytes:
    salt = bytes.fromhex(rec["salt"])
    if rec["alg"] == "scrypt":
        return hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024,
                              n=int(rec["n"]), r=int(rec["r"]), p=int(rec["p"]))
    if rec["alg"] == "pbkdf2-sha256":
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rec["iter"]), dklen=32)
    raise ValueError("unknown algorithm")


def hash_password(password: str, salt: Optional[bytes] = None, alg: Optional[str] = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    alg = alg or ("scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2-sha256")
    rec = {"alg": "scrypt", **SCRYPT} if alg == "scrypt" else {"alg": "pbkdf2-sha256", "iter": PBKDF2_ITER}
    rec["salt"] = salt.hex()
    rec["hash"] = _derive(password, rec).hex()
    return rec


def verify_password(password: str, rec: dict) -> bool:
    if not isinstance(rec, dict):
        return False
    try:
        dk = _derive(password, rec)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), str(rec.get("hash", "")))


def write_password(data_dir: Path, password: str) -> Path:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"the password must be at least {MIN_PASSWORD} characters")
    rec = hash_password(password)                # before touching any file
    path = Path(data_dir) / PASSWORD_FILE
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


class Admin:
    def __init__(self, data_dir: Optional[Path], now: Callable[[], float] = time.monotonic):
        self.path = Path(data_dir) / PASSWORD_FILE if data_dir is not None else None
        self.now = now
        self._lock = threading.Lock()
        self._sessions: Dict[str, dict] = {}     # sha256(token) -> {created, last, pw}
        self._fails: Dict[str, list] = {}        # address -> [failure times]
        self._locked: Dict[str, float] = {}      # address -> locked until

    def _record(self) -> Optional[dict]:
        if self.path is None:
            return None
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None

    def password_set(self) -> bool:
        return self._record() is not None

    def login(self, password, addr: str):
        """Returns ("ok", token) | ("unset", None) | ("locked", seconds) | ("bad", None)."""
        rec = self._record()
        if rec is None:
            return "unset", None
        now = self.now()
        with self._lock:
            until = self._locked.get(addr, 0)
            if until > now:
                return "locked", int(until - now) + 1
        ok = isinstance(password, str) and verify_password(password, rec)
        with self._lock:
            if not ok:
                fails = [t for t in self._fails.get(addr, []) if t > now - FAIL_WINDOW_S] + [now]
                self._fails[addr] = fails
                if len(fails) >= FAIL_LIMIT:
                    self._locked[addr] = now + LOCK_S
                    self._fails.pop(addr, None)
                return "bad", None
            self._fails.pop(addr, None)
            token = secrets.token_urlsafe(32)
            self._sessions[self._key(token)] = {"created": now, "last": now, "pw": rec.get("hash"), "kind": "rc",
                                                "user": "admin", "role": "admin"}
            return "ok", token

    def _throttled(self, addr: str, now: float):
        with self._lock:
            until = self._locked.get(addr, 0)
            return int(until - now) + 1 if until > now else 0

    def _failed(self, addr: str, now: float) -> None:
        with self._lock:
            fails = [t for t in self._fails.get(addr, []) if t > now - FAIL_WINDOW_S] + [now]
            self._fails[addr] = fails
            if len(fails) >= FAIL_LIMIT:
                self._locked[addr] = now + LOCK_S
                self._fails.pop(addr, None)

    def login_match(self, username, password, addr: str, users: Callable[[str], Optional[dict]]):
        """Match Control login: a Match Control user (users(name) -> {"role", "pw"} or None), or "admin" with the Range
        Control password (role admin, can do everything). Same lockout as Range Control. Returns like login()."""
        now = self.now()
        wait = self._throttled(addr, now)
        if wait:
            return "locked", wait
        if not isinstance(username, str) or not isinstance(password, str):
            self._failed(addr, now)
            return "bad", None
        if username == "admin":
            rec, role = self._record(), "admin"
        else:
            u = users(username) if USERNAME_RE.match(username) else None
            rec, role = (u["pw"], u["role"]) if u else (None, None)
        if rec is None or not verify_password(password, rec):
            if rec is None:
                verify_password(password, hash_password("x" * MIN_PASSWORD))   # same work either way: no user probing
            self._failed(addr, now)
            return "bad", None
        with self._lock:
            self._fails.pop(addr, None)
            token = secrets.token_urlsafe(32)
            self._sessions[self._key(token)] = {"created": now, "last": now, "pw": rec.get("hash"), "kind": "match",
                                                "user": username, "role": role}
            return "ok", token

    def match_session(self, token: Optional[str], users: Callable[[str], Optional[dict]]) -> Optional[dict]:
        """{"user", "role"} for a valid Match Control session made with the user's current password, else None."""
        if not token:
            return None
        now = self.now()
        with self._lock:
            self._expire(now)
            s = self._sessions.get(self._key(token))
            if s is None or s["kind"] != "match":
                return None
            user, role, pw = s["user"], s["role"], s["pw"]
        if role == "admin":
            rec = self._record()
            current = rec.get("hash") if rec else None
        else:
            u = users(user)
            current = u["pw"].get("hash") if u and u["role"] == role else None
        if current is None or current != pw:
            return None
        with self._lock:
            if self._key(token) in self._sessions:
                self._sessions[self._key(token)]["last"] = now
        return {"user": user, "role": role}

    def drop_user(self, username: str) -> None:
        """End every session of a Match Control user (removed, or role changed)."""
        with self._lock:
            for k in [k for k, s in self._sessions.items() if s["kind"] == "match" and s["user"] == username]:
                del self._sessions[k]

    def _expire(self, now: float) -> None:
        for k in [k for k, s in self._sessions.items() if now - s["last"] > ADMIN_IDLE_S or now - s["created"] > ADMIN_MAX_S]:
            del self._sessions[k]

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def check(self, token: Optional[str]) -> bool:
        """A valid, unexpired session made with the current password. Renews the idle timer."""
        if not token:
            return False
        rec = self._record()
        now = self.now()
        with self._lock:
            self._expire(now)
            s = self._sessions.get(self._key(token))
            if s is None or s["kind"] != "rc" or rec is None or s["pw"] != rec.get("hash"):
                return False
            s["last"] = now
            return True

    def logout(self, token: Optional[str]) -> None:
        if token:
            with self._lock:
                self._sessions.pop(self._key(token), None)
