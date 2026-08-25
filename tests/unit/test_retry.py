from __future__ import annotations

import pytest

from internship_agent.retry import CircuitBreaker, CircuitOpenError, retry_with_backoff


def _no_sleep(_seconds: float) -> None:
    return None


class TestRetryWithBackoff:
    def test_returns_result_on_first_success(self):
        calls = []

        @retry_with_backoff(max_attempts=3, sleeper=_no_sleep)
        def fn():
            calls.append(1)
            return "ok"

        assert fn() == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        attempts = {"n": 0}

        @retry_with_backoff(max_attempts=4, sleeper=_no_sleep)
        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("flaky")
            return "ok"

        assert flaky() == "ok"
        assert attempts["n"] == 3

    def test_raises_after_max_attempts(self):
        attempts = {"n": 0}

        @retry_with_backoff(max_attempts=3, sleeper=_no_sleep)
        def always_fails():
            attempts["n"] += 1
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            always_fails()
        assert attempts["n"] == 3

    def test_only_retries_matching_exception_types(self):
        @retry_with_backoff(max_attempts=3, exceptions=(ConnectionError,), sleeper=_no_sleep)
        def raises_value_error():
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            raises_value_error()

    def test_backoff_delays_grow_and_are_bounded(self):
        delays: list[float] = []

        @retry_with_backoff(
            max_attempts=5,
            base_delay=1.0,
            max_delay=4.0,
            jitter=0.0,
            sleeper=delays.append,
        )
        def always_fails():
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            always_fails()
        # 1.0, 2.0, 4.0 (capped), 4.0 (capped) — 4 sleeps between 5 attempts
        assert delays == [1.0, 2.0, 4.0, 4.0]


class TestCircuitBreaker:
    def test_opens_after_threshold_failures(self):
        clock = {"t": 0.0}
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=lambda: clock["t"])
        breaker.before_call()  # closed, no error
        breaker.record_failure()
        breaker.before_call()  # still closed after 1 failure
        breaker.record_failure()
        with pytest.raises(CircuitOpenError):
            breaker.before_call()

    def test_half_opens_after_reset_timeout(self):
        clock = {"t": 0.0}
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=lambda: clock["t"])
        breaker.record_failure()
        with pytest.raises(CircuitOpenError):
            breaker.before_call()
        clock["t"] = 11.0
        breaker.before_call()  # should not raise once reset_timeout has elapsed

    def test_success_resets_failure_count(self):
        clock = {"t": 0.0}
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=lambda: clock["t"])
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        breaker.before_call()  # only 1 consecutive failure since the reset; still closed
