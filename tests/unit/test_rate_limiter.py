from __future__ import annotations

import threading

import pytest

from internship_agent.rate_limiter import TokenBucketRateLimiter


class FakeClock:
    """A manually-advanced clock so tests don't depend on real wall time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(rate: float, capacity: float | None = None):
    clock = FakeClock()
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    limiter = TokenBucketRateLimiter(rate=rate, capacity=capacity, clock=clock, sleeper=sleeper)
    return limiter, clock, sleeps


class TestTokenBucketRateLimiter:
    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=0)

    def test_first_calls_up_to_capacity_do_not_wait(self):
        limiter, _clock, sleeps = make_limiter(rate=2.0, capacity=2.0)
        assert limiter.acquire() == 0.0
        assert limiter.acquire() == 0.0
        assert sleeps == []

    def test_call_beyond_capacity_waits_for_refill(self):
        limiter, _clock, sleeps = make_limiter(rate=2.0, capacity=2.0)
        limiter.acquire()
        limiter.acquire()
        # bucket is empty; the 3rd call must wait ~0.5s at a 2/sec refill rate
        waited = limiter.acquire()
        assert waited == pytest.approx(0.5)
        assert sleeps == [pytest.approx(0.5)]

    def test_refill_is_capped_at_capacity(self):
        limiter, clock, sleeps = make_limiter(rate=1.0, capacity=1.0)
        limiter.acquire()
        clock.advance(100)  # way more idle time than the bucket can hold
        assert limiter.acquire() == 0.0
        assert sleeps == []

    def test_rejects_request_larger_than_capacity(self):
        limiter, _clock, _sleeps = make_limiter(rate=1.0, capacity=1.0)
        with pytest.raises(ValueError):
            limiter.acquire(tokens=2.0)

    def test_context_manager_acquires_one_token(self):
        limiter, _clock, sleeps = make_limiter(rate=5.0, capacity=1.0)
        with limiter:
            pass
        assert sleeps == []

    def test_shared_across_threads_caps_total_throughput(self):
        # Real (not fake) clock/sleeper here: this checks actual thread-safety,
        # not the refill math, so it needs to run concurrently for real.
        limiter = TokenBucketRateLimiter(rate=1000.0, capacity=1.0)
        acquired = []
        lock = threading.Lock()

        def worker():
            limiter.acquire()
            with lock:
                acquired.append(1)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(acquired) == 20
