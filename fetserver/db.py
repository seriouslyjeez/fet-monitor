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
    """
    -- Match Control: users (two roles), custom formats, matches with their lanes, a timeline per match (from which
    -- out-of-time shots are worked out), and the match director's decisions on flagged shots.
    CREATE TABLE match_users (
        username   TEXT PRIMARY KEY,
        role       TEXT NOT NULL,
        pw_json    TEXT NOT NULL,
        created_ms INTEGER NOT NULL
    );
    CREATE TABLE match_formats (
        id         TEXT PRIMARY KEY,
        json       TEXT NOT NULL,
        created_by TEXT,
        updated_ms INTEGER NOT NULL
    );
    CREATE TABLE matches (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT NOT NULL,
        format_json      TEXT NOT NULL,
        state            TEXT NOT NULL,
        created_ms       INTEGER NOT NULL,
        created_by       TEXT,
        started_ms       INTEGER,
        ended_ms         INTEGER,
        phase            INTEGER NOT NULL DEFAULT -1,
        phase_started_ms INTEGER,
        phase_used_ms    INTEGER NOT NULL DEFAULT 0,
        phase_extra_ms   INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE match_lanes (
        match_id   INTEGER NOT NULL,
        device_id  TEXT NOT NULL,
        lane       INTEGER NOT NULL,
        competitor TEXT,
        series_id  INTEGER,
        PRIMARY KEY (match_id, device_id)
    );
    CREATE TABLE match_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id  INTEGER NOT NULL,
        at_ms     INTEGER NOT NULL,
        kind      TEXT NOT NULL,
        phase     INTEGER,
        device_id TEXT,
        data      TEXT
    );
    CREATE INDEX match_log_match ON match_log (match_id, id);
    CREATE TABLE shot_decisions (
        shot_id    INTEGER PRIMARY KEY,
        match_id   INTEGER NOT NULL,
        decision   TEXT NOT NULL,
        decided_by TEXT,
        at_ms      INTEGER NOT NULL
    );
    """,
    """
    -- Match Control pacing: 'auto' (phases follow each other) or 'officer' (the range officer reads each command and
    -- presses Next). A separate migration because v9 had already run on a Pi before this column existed.
    ALTER TABLE matches ADD COLUMN paced TEXT NOT NULL DEFAULT 'auto';
    """,
    """
    -- Competitions: a format, competitors (family/given name, optional class and team) and one or more relays; each
    -- relay is a match (its own clock) linked here; results and teams combine all relays.
    CREATE TABLE competitions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        format_json TEXT NOT NULL,
        team_size   INTEGER NOT NULL,
        best_count  INTEGER NOT NULL DEFAULT 0,
        paced       TEXT NOT NULL DEFAULT 'spoken',
        created_ms  INTEGER NOT NULL,
        created_by  TEXT
    );
    CREATE TABLE competitors (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        competition_id INTEGER NOT NULL,
        family         TEXT NOT NULL,
        given          TEXT,
        class          TEXT,
        team           TEXT,
        created_ms     INTEGER NOT NULL
    );
    CREATE INDEX competitors_competition ON competitors (competition_id);
    ALTER TABLE matches ADD COLUMN competition_id INTEGER;
    ALTER TABLE matches ADD COLUMN relay INTEGER;
    ALTER TABLE match_lanes ADD COLUMN competitor_id INTEGER;
    """,
    """
    -- A shooter moved to another target during a match (ISSF 6.10.9.2, 6.11.3.2; CMP 7.18.3; NRA 9.2 b, 10.3.2):
    -- the lane's own clock. moving_*: their time stands still from the move until the range officer says they are
    -- ready (moving_left_ms = what they had left). extra_ms: their own extra time in the current phase. own_*: the
    -- match has gone on to the next phase while they finish theirs (own_left_ms left at own_since_ms; own_since_ms
    -- NULL = not counting). sighting: after a move, sighters until they start record again.
    ALTER TABLE match_lanes ADD COLUMN moving_since_ms INTEGER;
    ALTER TABLE match_lanes ADD COLUMN moving_left_ms INTEGER;
    ALTER TABLE match_lanes ADD COLUMN extra_ms INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE match_lanes ADD COLUMN own_phase INTEGER;
    ALTER TABLE match_lanes ADD COLUMN own_left_ms INTEGER;
    ALTER TABLE match_lanes ADD COLUMN own_since_ms INTEGER;
    ALTER TABLE match_lanes ADD COLUMN sighting INTEGER NOT NULL DEFAULT 0;
    """,
    """
    -- Deleting matches and results independently. results_hidden: the results were deleted (no longer on the
    -- Results page, TV, print or CSV); the match stays in Match Control and they can be shown again. results_archive:
    -- a frozen copy of the results of a match or competition that was deleted from Match Control.
    ALTER TABLE competitions ADD COLUMN results_hidden INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE matches ADD COLUMN results_hidden INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE results_archive (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        kind       TEXT NOT NULL CHECK (kind IN ('competition','match')),
        source_id  INTEGER NOT NULL,
        name       TEXT NOT NULL,
        json       TEXT NOT NULL,
        created_ms INTEGER NOT NULL,
        created_by TEXT
    );
    """,
    """
    -- Corrections by the range officials (fetserver/corrections.py): reviews and protests, overrides, annulled and
    -- added shots, crossfires, penalties, disqualifications. The shots themselves are never changed. lane = the
    -- firing point number in the match (it stays when a shooter is moved to another target).
    CREATE TABLE shot_corrections (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id    INTEGER NOT NULL,
        lane        INTEGER NOT NULL,
        shot_id     INTEGER,
        kind        TEXT NOT NULL,
        status      TEXT NOT NULL,
        data        TEXT NOT NULL,
        created_by  TEXT,
        created_ms  INTEGER NOT NULL,
        resolved_by TEXT,
        resolved_ms INTEGER
    );
    CREATE INDEX shot_corrections_match ON shot_corrections (match_id, lane);
    """,
    """
    -- Official results (the match director, once every flag and protest is settled), and CMP 7.18: a shooter whose
    -- target failed finishes the match on a later relay. On the later relay's lane: start_phase (the phase they rejoin
    -- at), carry_shots (record shots already fired in it), carry_ms (time they had left there), from_match (the relay
    -- they came from). On the earlier relay's lane: left_ms (when they left), to_match (where they went).
    ALTER TABLE matches ADD COLUMN official_ms INTEGER;
    ALTER TABLE matches ADD COLUMN official_by TEXT;
    ALTER TABLE match_lanes ADD COLUMN start_phase INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE match_lanes ADD COLUMN carry_shots INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE match_lanes ADD COLUMN carry_ms INTEGER;
    ALTER TABLE match_lanes ADD COLUMN from_match INTEGER;
    ALTER TABLE match_lanes ADD COLUMN left_ms INTEGER;
    ALTER TABLE match_lanes ADD COLUMN to_match INTEGER;
    """,
    """
    -- Preliminary results and the protest time (ISSF 6.14.1, 6.16.5.1; CMP 8.2; NRA 16.1 b): when the match director
    -- posted them and when protests close. NRA's challenge time is set per competition (minutes; NULL: 30).
    ALTER TABLE matches ADD COLUMN results_posted_ms INTEGER;
    ALTER TABLE matches ADD COLUMN protest_until_ms INTEGER;
    ALTER TABLE competitions ADD COLUMN challenge_min INTEGER;
    """,
    """
    -- Finals (CMP 10): a final is a match of a competition with final_kind set ('standard', 'sfz_standing', 'sfz_3p');
    -- final_for: the relay it follows ("Final for All Athletes", CMP 10.1.2 a), else NULL; final_class: the class it is
    -- for. It is not a relay: its shots never count in the qualification. match_lanes.final_pos: A, B, C ...
    ALTER TABLE matches ADD COLUMN final_kind TEXT;
    ALTER TABLE matches ADD COLUMN final_for INTEGER;
    ALTER TABLE matches ADD COLUMN final_class TEXT;
    ALTER TABLE match_lanes ADD COLUMN final_pos TEXT;
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
