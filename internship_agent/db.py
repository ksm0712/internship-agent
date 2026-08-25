"""SQLite storage engine.

Replaces the flat JSON files the original app read and rewrote wholesale on
every mutation (`data/internships.json`, `data/contacts.json`,
`data/drafts_<user>.json`, `data/history/<user>.json`, `data/web_state.json`).
That approach doesn't have real query support, keys, or safe concurrent
writers — every save serialized the *entire* collection. SQLite in WAL mode
gives indexed lookups, foreign keys, and safe multi-threaded/multi-process
access without adding infrastructure (Postgres/etc.) this single-host app
doesn't need.

It also fixes a real bug in the old layout: the Gmail OAuth token lived in one
shared `data/web_google_token.json` for every signed-in user, so a second
Google account signing in on the same server would overwrite the first
account's send credentials — any pending "approve and send" from the first
user would then send through the second user's Gmail. `users.gmail_oauth_token_enc`
scopes that token per account, encrypted like the BYO API keys.

Each thread gets its own connection to the same database file (SQLite
connections aren't safe to share across threads); WAL mode lets those
connections read concurrently and serializes writers without blocking readers.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT NOT NULL,
    role_key TEXT NOT NULL,
    description TEXT,
    location TEXT,
    official_url TEXT,
    source_url TEXT,
    evidence TEXT,
    confidence REAL,
    created_at TEXT NOT NULL,
    UNIQUE (company_key, role_key)
);
CREATE INDEX IF NOT EXISTS idx_opportunities_company_key ON opportunities(company_key);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT NOT NULL,
    description TEXT,
    location TEXT,
    official_url TEXT,
    source_url TEXT,
    evidence TEXT,
    confidence REAL,
    domain TEXT,
    contact_name TEXT,
    contact_position TEXT,
    email TEXT,
    contact_source TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (company_key, role)
);
CREATE INDEX IF NOT EXISTS idx_contacts_company_key ON contacts(company_key);

CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    user_key TEXT NOT NULL,
    resume_path TEXT,
    gemini_api_key_enc BLOB,
    tavily_api_key_enc BLOB,
    hunter_api_key_enc BLOB,
    gmail_oauth_token_enc BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT NOT NULL,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT,
    recipient_name TEXT,
    to_email TEXT,
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL,
    resume_path TEXT,
    source_url TEXT,
    contact_source TEXT,
    gmail_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drafts_user_status ON drafts(user_email, status);
CREATE INDEX IF NOT EXISTS idx_drafts_user_company ON drafts(user_email, company_key);

CREATE TABLE IF NOT EXISTS company_history (
    user_email TEXT NOT NULL,
    company_key TEXT NOT NULL,
    company TEXT,
    role TEXT,
    to_email TEXT,
    subject TEXT,
    status TEXT,
    gmail_message_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_email, company_key)
);

CREATE TABLE IF NOT EXISTS run_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    items_in INTEGER NOT NULL DEFAULT 0,
    items_out INTEGER NOT NULL DEFAULT 0,
    api_calls INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    user_email TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_metrics_stage ON run_metrics(stage);
CREATE INDEX IF NOT EXISTS idx_run_metrics_run_id ON run_metrics(run_id);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


class Database:
    """Owns one lazily-created SQLite connection per thread for a given file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        # Every connection ever opened (one per thread that has used this
        # Database), tracked so close() can clean up connections made by
        # worker threads, not just whichever thread calls close().
        self._all_connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
        with self._connections_lock:
            self._all_connections.append(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                conn.executescript(SCHEMA)
                conn.commit()
                self._schema_ready = True

    def connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self.connection()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def close(self) -> None:
        """Close every connection this Database has opened, across all threads."""
        with self._connections_lock:
            connections, self._all_connections = self._all_connections, []
        for conn in connections:
            conn.close()
        self._local.conn = None
