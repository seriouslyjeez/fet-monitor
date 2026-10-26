"""SQLite storage: WAL, synchronous=FULL, explicit transactions, versioned migrations."""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager

log = logging.getLogger("fetserver.db")

# Each entry upgrades the schema by one version (PRAGMA user_version).
MIGRATIONS = [
    """
    CREATE TABLE targets (
        device_id       TEXT PRIMARY KEY,
        name            TEXT,
        arduino_sn      INTEGER,
        arduino_version TEXT,
        bridge_fw       TEXT,
        calibre_x10     INTEGER NOT NULL DEFAULT 45,
        align_x_h       INTEGER NOT NULL DEFAULT 0,
        align_y_h       INTEGER NOT NULL DEFAULT 0,
        first_seen_ms   INTEGER NOT NULL,
        last_seen_ms    INTEGER NOT NULL,
        last_ip         TEXT,
        rssi            INTEGER,
        queue_depth     INTEGER,
        overflow_count  INTEGER,
        boot_count      INTEGER,
        uptime_ms       INTEGER,
        config_json     TEXT
    );
    CREATE TABLE boots (
        device_id     TEXT NOT NULL,
        boot_count    INTEGER NOT NULL,
        boot_start_ms INTEGER NOT NULL,
        PRIMARY KEY (device_id, boot_count)
    );
    -- Append-only record of everything a target sent. Never updated except parse_error.
    CREATE TABLE events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id    TEXT NOT NULL,
        boot_count   INTEGER NOT NULL,
        seq          INTEGER NOT NULL,
        type         TEXT NOT NULL,
        event_ms     INTEGER NOT NULL,
        time_quality TEXT NOT NULL,
        recv_ms      INTEGER NOT NULL,
        uptime_ms    INTEGER,
        raw          TEXT,
        data_json    TEXT,
        parse_error  TEXT,
        UNIQUE (device_id, boot_count, seq)
    );
    CREATE INDEX events_time ON events (device_id, event_ms);
    -- Derived from events; can be rebuilt (rescore) without touching raw data.
    CREATE TABLE shots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id        INTEGER NOT NULL UNIQUE REFERENCES events(id),
        device_id       TEXT NOT NULL,
        event_ms        INTEGER NOT NULL,
        shot_number     INTEGER,
        is_miss         INTEGER NOT NULL,
        x_h             INTEGER,
        y_h             INTEGER,
        ax_h            INTEGER,
        ay_h            INTEGER,
        n INTEGER, e INTEGER, s INTEGER, w INTEGER,
        arduino_time_s  REAL,
        v_ref           REAL,
        temp_c          REAL,
        face            INTEGER,
        calibre_x10     INTEGER,
        scoring_version INTEGER NOT NULL,
        ring            INTEGER NOT NULL,
        tenths          INTEGER NOT NULL
    );
    CREATE INDEX shots_target ON shots (device_id, id);
    CREATE TABLE commands (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id  TEXT NOT NULL,
        type       TEXT NOT NULL,
        line       TEXT,
        created_ms INTEGER NOT NULL,
        sent_ms    INTEGER,
        acked_ms   INTEGER,
        status     TEXT NOT NULL DEFAULT 'queued',
        result     TEXT
    );
    CREATE INDEX commands_pending ON commands (device_id, status);
    """,
    """
    -- A segment is a stretch of time on one target in one mode (practice / sighter / record).
    CREATE TABLE segments (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id  TEXT NOT NULL,
        mode       TEXT NOT NULL,
        shooter    TEXT,
        started_ms INTEGER NOT NULL,
        ended_ms   INTEGER
    );
    CREATE INDEX segments_target ON segments (device_id, started_ms);
    ALTER TABLE shots ADD COLUMN segment_id INTEGER;
    CREATE INDEX shots_segment ON shots (segment_id);
    -- Who is currently allowed to change a target's mode. Expires unless renewed.
    CREATE TABLE leases (
        device_id   TEXT PRIMARY KEY,
        client_id   TEXT NOT NULL,
        holder_name TEXT NOT NULL,
        acquired_ms INTEGER NOT NULL,
        expires_ms  INTEGER NOT NULL
    );
    """,
    """
    -- An event whose (device, boot_count, seq) already exists but whose content differs.
    -- That means a target reused a boot_count (for example after a factory reset). Kept, never dropped silently.
    CREATE TABLE event_conflicts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id  TEXT NOT NULL,
        boot_count INTEGER NOT NULL,
        seq        INTEGER NOT NULL,
        type       TEXT,
        recv_ms    INTEGER NOT NULL,
        uptime_ms  INTEGER,
        raw        TEXT,
        data_json  TEXT
    );
    """,
    """
    -- A series is one practice run or one match. A match series holds sighter and record segments;
    -- record shots keep accumulating across sighter detours. Shots per bull (target face), per
    -- aggregate group and in total are the shooter's choice, remembered per target.
    ALTER TABLE targets ADD COLUMN bull_size INTEGER NOT NULL DEFAULT 10;
    ALTER TABLE targets ADD COLUMN group_size INTEGER NOT NULL DEFAULT 10;
    ALTER TABLE targets ADD COLUMN total_practice INTEGER NOT NULL DEFAULT 20;
    ALTER TABLE targets ADD COLUMN total_match INTEGER NOT NULL DEFAULT 60;
    CREATE TABLE series (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id  TEXT NOT NULL,
        kind       TEXT NOT NULL CHECK (kind IN ('practice','match')),
        shooter    TEXT,
        started_ms INTEGER NOT NULL,
        ended_ms   INTEGER,
        bull_size  INTEGER NOT NULL,
        group_size INTEGER NOT NULL,
        total      INTEGER NOT NULL
    );
    CREATE INDEX series_target ON series (device_id, started_ms);
    ALTER TABLE segments ADD COLUMN series_id INTEGER;
    ALTER TABLE shots ADD COLUMN series_id INTEGER;
    ALTER TABLE shots ADD COLUMN series_no INTEGER;
    ALTER TABLE shots ADD COLUMN stream TEXT;
    CREATE INDEX shots_series ON shots (series_id, stream, series_no);
    """,
    """
    -- Ammo test: an open-ended series scored relative to its first shot, with a projectile size.
    -- SQLite cannot change a CHECK constraint, so the series table is rebuilt.
    ALTER TABLE targets ADD COLUMN projectile_h INTEGER NOT NULL DEFAULT 450;
    CREATE TABLE series_new (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id    TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('practice','match','ammo')),
        shooter      TEXT,
        started_ms   INTEGER NOT NULL,
        ended_ms     INTEGER,
        bull_size    INTEGER NOT NULL,
        group_size   INTEGER NOT NULL,
        total        INTEGER NOT NULL,
        projectile_h INTEGER NOT NULL DEFAULT 450
    );
    INSERT INTO series_new (id, device_id, kind, shooter, started_ms, ended_ms, bull_size, group_size, total)
        SELECT id, device_id, kind, shooter, started_ms, ended_ms, bull_size, group_size, total FROM series;
    DROP TABLE series;
    ALTER TABLE series_new RENAME TO series;
    CREATE INDEX series_target ON series (device_id, started_ms);
    """,
    """
    -- An ammo test has a shooter (fixed once the test starts) and a free-text label (ammo, lot) that can change.
    ALTER TABLE series ADD COLUMN label TEXT;
    """,
    """
    -- Range Control: a removed target is hidden until it reports again (its history stays); a banned one is refused.
    ALTER TABLE targets ADD COLUMN removed INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE targets ADD COLUMN banned INTEGER NOT NULL DEFAULT 0;
    """,
    """
    -- Firmware updates from the Pi (bridge firmware 0.2.0+ reports these; 0 = it cannot update that way).
    ALTER TABLE targets ADD COLUMN ota_space INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE targets ADD COLUMN flash_size INTEGER NOT NULL DEFAULT 0;
    """,
]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self.write() as conn:
            self._migrate(conn)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")   # a commit is on disk before we acknowledge
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=10000")
            self._local.conn = c
        return c

    @contextmanager
    def write(self):
        """One atomic transaction. Everything inside commits together or not at all."""
        with self._write_lock:
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError(f"database is newer (v{version}) than this server (v{len(MIGRATIONS)})")
        for i in range(version, len(MIGRATIONS)):
            log.info("applying migration %d", i + 1)
            for stmt in _split(MIGRATIONS[i]):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version={i + 1}")

    def integrity_check(self) -> str:
        return self.conn().execute("PRAGMA integrity_check").fetchone()[0]


def _split(script: str):
    """Split a script into statements, ignoring comment-only lines."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        if stmt.strip():
            yield stmt
