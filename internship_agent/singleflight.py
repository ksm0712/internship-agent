"""Single-flight call coalescing: at most one in-flight computation per key.

Dispatching a batch of opportunities to a thread pool means two workers can
land on the same cache key (e.g. two roles at the same company) before either
has written a result back — both miss the cache and both do the same
Tavily+Gemini domain lookup. This makes concurrent callers for the same key
queue behind whichever one gets there first, so the work — and the API
calls it costs — only happens once per key per batch.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class SingleFlight:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def do(self, key: str, compute: Callable[[], T]) -> T:
        """Run `compute()` for `key`, serializing concurrent calls for the
        same key so only the first caller actually executes it — the rest
        block until it's done, then run `compute()` themselves too (it's
        expected to be cache-backed and cheap on the second pass)."""
        with self._lock_for(key):
            return compute()
