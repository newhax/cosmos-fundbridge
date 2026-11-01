"""Relational store for the two-sided platform (SQLite).

Accounts, sessions, profiles, pitches and the pitch event log live here, not in
Meko. The event log is the source of truth for dates and follow-up
rules; Meko receives a copy of every event for shared agent memory.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ngo', 'company', 'admin', 'donor')),  -- admins are created by scripts/admin.py
    email_verified INTEGER NOT NULL DEFAULT 0, is_demo INTEGER NOT NULL DEFAULT 0,
    consent_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('verify', 'reset')), expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS login_failures (key TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_login_failures ON login_failures(key, at);

CREATE TABLE IF NOT EXISTS ngo_profiles (
    ngo_id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, state TEXT DEFAULT '', district TEXT DEFAULT '', causes TEXT DEFAULT '[]',
    summary TEXT DEFAULT '', crn TEXT DEFAULT '', darpan_id TEXT DEFAULT '',
    has_12a INTEGER DEFAULT 0, has_80g INTEGER DEFAULT 0,
    credential_source TEXT DEFAULT '{}',          -- field -> "self"
    listed_publicly INTEGER NOT NULL DEFAULT 0,   -- opt-in, default off
    listed_fields TEXT DEFAULT '["state","district","causes","summary"]',
    is_demo INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS company_profiles (
    company_id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, cin TEXT DEFAULT '', csr_contact_email TEXT DEFAULT '',
    focus_sectors TEXT DEFAULT '[]', focus_states TEXT DEFAULT '[]',
    min_ticket REAL, max_ticket REAL,
    -- Unused: companies can use the app as soon as they sign up (kept so older databases still load).
    approval_status TEXT NOT NULL DEFAULT 'approved' CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    is_demo INTEGER NOT NULL DEFAULT 0, updated_at TEXT);

CREATE TABLE IF NOT EXISTS pitches (
    id TEXT PRIMARY KEY, ngo_id TEXT NOT NULL REFERENCES ngo_profiles(ngo_id),
    company_id TEXT NOT NULL REFERENCES company_profiles(company_id),
    subject TEXT NOT NULL, body TEXT NOT NULL, evidence_refs TEXT DEFAULT '[]',
    status TEXT NOT NULL,                         -- sent | viewed | interested | info_requested | declined
    ask_inr REAL, sectors TEXT DEFAULT '[]', state TEXT DEFAULT '', district TEXT DEFAULT '',
    created_at TEXT NOT NULL, sent_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_pitches_ngo ON pitches(ngo_id);
CREATE INDEX IF NOT EXISTS ix_pitches_company ON pitches(company_id);
CREATE TABLE IF NOT EXISTS pitch_events (
    id TEXT PRIMARY KEY, pitch_id TEXT NOT NULL REFERENCES pitches(id), type TEXT NOT NULL,
    actor_role TEXT NOT NULL, visibility TEXT NOT NULL CHECK (visibility IN ('shared', 'company', 'ngo')),
    payload TEXT DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_pitch_events ON pitch_events(pitch_id, created_at);

CREATE TABLE IF NOT EXISTS donor_profiles (
    donor_id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL, state TEXT DEFAULT '', city TEXT DEFAULT '', causes TEXT DEFAULT '[]',
    giving_range TEXT DEFAULT '', frequency TEXT DEFAULT '',
    anonymous_by_default INTEGER NOT NULL DEFAULT 1, is_demo INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS donor_shortlist (
    donor_id TEXT NOT NULL, ngo_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (donor_id, ngo_id));
CREATE TABLE IF NOT EXISTS donor_follows (
    donor_id TEXT NOT NULL, ngo_id TEXT NOT NULL, share_name INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
    PRIMARY KEY (donor_id, ngo_id));
CREATE TABLE IF NOT EXISTS donor_gifts (
    id TEXT PRIMARY KEY, donor_id TEXT NOT NULL, ngo_id TEXT NOT NULL, amount_inr REAL NOT NULL, date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'self-reported' CHECK (status IN ('self-reported', 'confirmed by NGO')),
    share_name INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ngo_updates (
    id TEXT PRIMARY KEY, ngo_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ngo_reports (
    id TEXT PRIMARY KEY, reporter_role TEXT NOT NULL, reporter_id TEXT NOT NULL, ngo_id TEXT NOT NULL,
    reason TEXT, created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS blocks (
    company_id TEXT NOT NULL, ngo_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (company_id, ngo_id));
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, company_id TEXT NOT NULL, ngo_id TEXT NOT NULL, pitch_id TEXT, reason TEXT,
    created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
"""

# NGO columns for the donor side (added in place on existing databases)
NGO_DONOR_COLUMNS = {
    "accepts_individual_donations": "INTEGER NOT NULL DEFAULT 0",
    "donation_url": "TEXT DEFAULT ''",
    "impact_updates_enabled": "INTEGER NOT NULL DEFAULT 0",
    "founded_year": "INTEGER",
    "suggested_gift_inr": "REAL",
}

NGO_VERIFICATION_COLUMNS = {
    "pan": "TEXT DEFAULT ''",
    "is_section8": "INTEGER NOT NULL DEFAULT 0",
    "section8_cin": "TEXT DEFAULT ''",
    "receives_foreign_funds": "INTEGER NOT NULL DEFAULT 0",
}
COMPANY_VERIFICATION_COLUMNS = {"pan": "TEXT DEFAULT ''"}
USER_COLUMNS = {"language": "TEXT NOT NULL DEFAULT 'en'"}

EVENT_TYPES = {"drafted", "sent", "viewed", "screened", "info_requested", "interested", "declined",
               "reply", "promise_made", "nudge_suggested"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class PlatformDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.tx() as con:
            self._migrate_users(con)
            con.executescript(SCHEMA)
            self._add_columns(con)
            # Companies use the app as soon as they sign up (no approval step).
            con.execute("UPDATE company_profiles SET approval_status = 'approved' WHERE approval_status != 'approved'")

    @staticmethod
    def _migrate_users(con: sqlite3.Connection) -> None:
        """Older databases only allowed ngo/company/admin; rebuild users to add the donor role."""
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        if row and "'donor'" not in row[0]:
            # SQLite's documented recipe: build the new table, copy, drop the old one, rename the new one.
            # (Renaming the OLD table instead would rewrite other tables' foreign keys to point at it.)
            con.execute("PRAGMA foreign_keys = OFF")
            users_ddl = SCHEMA.split("CREATE TABLE IF NOT EXISTS sessions")[0]
            con.executescript(users_ddl.replace("CREATE TABLE IF NOT EXISTS users (", "CREATE TABLE users_new ("))
            con.execute("INSERT INTO users_new SELECT * FROM users")
            con.execute("DROP TABLE users")
            con.execute("ALTER TABLE users_new RENAME TO users")
            con.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _add_columns(con: sqlite3.Connection) -> None:
        for table, columns in (("ngo_profiles", NGO_DONOR_COLUMNS | NGO_VERIFICATION_COLUMNS),
                               ("company_profiles", COMPANY_VERIFICATION_COLUMNS),
                               ("users", USER_COLUMNS)):
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in cols:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def tx(self):
        """Context manager: one connection, committed on success."""
        lock = self._lock

        class _Tx:
            def __enter__(s):
                lock.acquire()
                s.con = self.connect()
                return s.con

            def __exit__(s, exc_type, exc, tb):
                try:
                    if exc_type is None:
                        s.con.commit()
                    s.con.close()
                finally:
                    lock.release()

        return _Tx()

    def one(self, sql: str, params=()) -> dict | None:
        with self.tx() as con:
            r = con.execute(sql, params).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, params=()) -> list[dict]:
        with self.tx() as con:
            return [dict(r) for r in con.execute(sql, params)]

    def run(self, sql: str, params=()) -> int:
        with self.tx() as con:
            return con.execute(sql, params).rowcount


def loads(v, default):
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default
