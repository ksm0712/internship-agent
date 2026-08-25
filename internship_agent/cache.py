"""TTL cache for external lookups, backed by the `cache_entries` SQLite table.

`choose_domain()` and Hunter.io domain-search calls are pure functions of
(company name) / (domain) that don't change within a run, and rarely change
across runs of the same session. The original agent had no cache at all: two
searches five minutes apart re-spent the same Tavily/Gemini/Hunter calls.
Caching these keeps a slow-changing external answer around for `ttl_seconds`
so repeat lookups are a local read instead of a network round trip; hit/miss
counts feed `internship_agent.metrics` for the cache-hit-rate metric surfaced
in `/api/stats`.
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from internship_agent.db import Database

DEFAULT_TTL_SECONDS = 24 * 60 * 60


class TTLCache:
    def __init__(self, db: Database, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._db = db
        self._ttl_seconds = ttl_seconds
        self._counter_lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _record_hit(self) -> None:
        with self._counter_lock:
            self.hits += 1

    def _record_miss(self) -> None:
        with self._counter_lock:
            self.misses += 1

    def get(self, key: str) -> Any | None:
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT value, expires_at FROM cache_entries WHERE cache_key = ?",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            self._record_miss()
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at < datetime.now(UTC):
            self._record_miss()
            self.delete(key)
            return None
        self._record_hit()
        return json.loads(row["value"])

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = self._ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()
        with self._db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cache_entries (cache_key, value, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET value = excluded.value,
                    expires_at = excluded.expires_at
                """,
                (key, json.dumps(value), expires_at),
            )

    def delete(self, key: str) -> None:
        with self._db.cursor() as cur:
            cur.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))

    def get_or_set(self, key: str, compute: Any) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute()
        self.set(key, value)
        return value

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
