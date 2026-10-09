"""Database storage engine: schema, connections, and small portability shims.

Local development uses SQLite by default. Hosted deployments can set
INTERNSHIP_AGENT_DATABASE_URL to a Postgres/Supabase connection string and keep
the same repository API.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

SQLITE_SCHEMA = """
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
    resume_filename TEXT,
    resume_content_type TEXT,
    resume_blob BLOB,
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

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id BIGSERIAL PRIMARY KEY,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT NOT NULL,
    role_key TEXT NOT NULL,
    description TEXT,
    location TEXT,
    official_url TEXT,
    source_url TEXT,
    evidence TEXT,
    confidence DOUBLE PRECISION,
    created_at TEXT NOT NULL,
    UNIQUE (company_key, role_key)
);
CREATE INDEX IF NOT EXISTS idx_opportunities_company_key ON opportunities(company_key);

CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    role TEXT NOT NULL,
    description TEXT,
    location TEXT,
    official_url TEXT,
    source_url TEXT,
    evidence TEXT,
    confidence DOUBLE PRECISION,
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
    resume_filename TEXT,
    resume_content_type TEXT,
    resume_blob BYTEA,
    gemini_api_key_enc BYTEA,
    tavily_api_key_enc BYTEA,
    hunter_api_key_enc BYTEA,
    gmail_oauth_token_enc BYTEA,
    search_locations TEXT,
    search_roles TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id BIGSERIAL PRIMARY KEY,
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
    fit_score DOUBLE PRECISION,
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
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL,
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


class CursorLike(Protocol):
    rowcount: int
    lastrowid: int | None

    def execute(self, sql: str, params: Any = None) -> Any: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def close(self) -> None: ...


def _is_postgres_url(value: str | None) -> bool:
    return bool(value and value.startswith(("postgres://", "postgresql://")))


def _translate_placeholders(sql: str) -> str:
    """Translate sqlite-style `?` placeholders to psycopg `%s` placeholders.

    The repository never uses literal question marks inside SQL strings, so a
    plain replacement keeps query code readable without coupling every caller
    to one database driver's paramstyle.
    """
    return sql.replace("?", "%s")


class PostgresCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return None

    def execute(self, sql: str, params: Any = None) -> Any:
        return self._cursor.execute(_translate_placeholders(sql), params)

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()


class Database:
    """Owns one lazily-created connection per thread.

    SQLite connections aren't safe to share across threads, and keeping the same
    pattern for Postgres lets the pipeline's worker threads use the repository
    without a global lock.
    """

    def __init__(self, path: Path | str, database_url: str | None = None) -> None:
        self.path = Path(path)
        self.database_url = database_url or os.getenv("INTERNSHIP_AGENT_DATABASE_URL")
        self.engine = "postgres" if _is_postgres_url(self.database_url) else "sqlite"
        if self.engine == "sqlite" and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        # Every connection ever opened (one per thread that has used this
        # Database), tracked so close() can clean up connections made by
        # worker threads, not just whichever thread calls close().
        self._all_connections: list[Any] = []
        self._connections_lock = threading.Lock()

    def _new_connection(self) -> Any:
        if self.engine == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - only hit in misconfigured deploys
                raise RuntimeError(
                    "INTERNSHIP_AGENT_DATABASE_URL is set, but psycopg is not installed. "
                    "Install requirements.txt before running with Postgres."
                ) from exc
            conn = psycopg.connect(self.database_url, row_factory=dict_row)
            with self._connections_lock:
                self._all_connections.append(conn)
            return conn

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

    def _ensure_schema(self, conn: Any) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                if self.engine == "postgres":
                    with conn.cursor() as cur:
                        cur.execute(POSTGRES_SCHEMA)
                    self._apply_postgres_column_migrations(conn)
                else:
                    conn.executescript(SQLITE_SCHEMA)
                    self._apply_sqlite_column_migrations(conn)
                conn.commit()
                self._schema_ready = True

    @staticmethod
    def _apply_sqlite_column_migrations(conn: sqlite3.Connection) -> None:
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
                ("resume_filename", "TEXT"),
                ("resume_content_type", "TEXT"),
                ("resume_blob", "BLOB"),
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

    @staticmethod
    def _apply_postgres_column_migrations(conn: Any) -> None:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS search_locations TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS search_roles TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS resume_filename TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS resume_content_type TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS resume_blob BYTEA")
            cur.execute("ALTER TABLE drafts ADD COLUMN IF NOT EXISTS fit_score DOUBLE PRECISION")

    def connection(self) -> Any:
        conn: Any | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def cursor(self) -> Iterator[CursorLike]:
        conn = self.connection()
        raw_cur = conn.cursor()
        cur = PostgresCursor(raw_cur) if self.engine == "postgres" else raw_cur
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
