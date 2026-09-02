"""SQLite storage engine: schema, connections, WAL mode.

Each thread gets its own connection to the database file — SQLite
connections aren't safe to share across threads. WAL mode lets those
connections read concurrently without blocking writers.
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
    search_locations TEXT,
    search_roles TEXT,
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
    fit_score REAL,
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
            # busy_timeout before journal_mode, not after — otherwise the
            # WAL-mode switch itself has nothing making it retry a lock and
            # two processes opening a fresh db at once hit "database is locked".
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
        with self._connections_lock:
            self._all_connections.append(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                conn.executescript(SCHEMA)
                self._apply_column_migrations(conn)
                conn.commit()
                self._schema_ready = True

    @staticmethod
    def _apply_column_migrations(conn: sqlite3.Connection) -> None:
        """Add columns introduced after a database file already existed.

        `CREATE TABLE IF NOT EXISTS` above only covers brand-new databases;
        an existing one (e.g. from before `search_locations`/`search_roles`
        were added) needs an explicit ALTER TABLE, or those columns simply
        never show up and every query referencing them errors.
        """
        added_columns = {
            "users": [
                ("search_locations", "TEXT"),
                ("search_roles", "TEXT"),
            ],
            "drafts": [
                ("fit_score", "REAL"),
            ],
        }
        for table, columns in added_columns.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, col_type in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")

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
