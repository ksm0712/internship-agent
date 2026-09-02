"""Single-flight call coalescing: at most one in-flight computation per key.

Serializes concurrent callers for the same key so only the first actually
runs the work; the rest wait and hit the now-populated cache instead of
racing it. See contacts.py's domain-lookup cache for why this exists.
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
