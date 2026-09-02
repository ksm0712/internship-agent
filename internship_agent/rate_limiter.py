"""Thread-safe token-bucket rate limiter, shared across worker threads.

Clock/sleep are injectable so tests can fast-forward instead of sleeping.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucketRateLimiter:
    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = rate
        self._capacity = capacity if capacity is not None else max(rate, 1.0)
        self._tokens = self._capacity
        self._clock = clock
        self._sleeper = sleeper
        self._last_refill = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until `tokens` are available. Returns the time spent waiting."""
        if tokens > self._capacity:
            raise ValueError("cannot acquire more tokens than the bucket capacity")
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                sleep_for = deficit / self._rate
            self._sleeper(sleep_for)
            waited += sleep_for

    def __enter__(self) -> TokenBucketRateLimiter:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None
