"""Retry-with-backoff and a circuit breaker for flaky external APIs."""
from __future__ import annotations

import functools
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """Raised when a circuit breaker is open and is short-circuiting calls."""


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic)

    def __post_init__(self) -> None:
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            return (self.clock() - self._opened_at) < self.reset_timeout

    def before_call(self) -> None:
        if self.is_open:
            raise CircuitOpenError(
                f"circuit open after {self._failures} consecutive failures"
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self.clock()


def retry_with_backoff(
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: float = 0.1,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    circuit_breaker: CircuitBreaker | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> T:
            attempt = 0
            while True:
                attempt += 1
                if circuit_breaker is not None:
                    circuit_breaker.before_call()
                try:
                    result = func(*args, **kwargs)
                except exceptions:
                    if circuit_breaker is not None:
                        circuit_breaker.record_failure()
                    if attempt >= max_attempts:
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, jitter)
                    sleeper(delay)
                    continue
                if circuit_breaker is not None:
                    circuit_breaker.record_success()
                return result

        return wrapper

    return decorator
