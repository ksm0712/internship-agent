"""Tavily search/extract wrapper: retry-with-backoff plus a circuit breaker.

The breaker check has to sit *outside* the retried call, not inside it: if
`before_call()` raised from within the retried function, the retry decorator
would treat `CircuitOpenError` as just another retryable exception and burn a
full backoff sleep before giving up — retrying against a circuit that just
told you not to defeats the point of failing fast.
"""
from __future__ import annotations

from typing import Any

from tavily import TavilyClient

from internship_agent.retry import CircuitBreaker, retry_with_backoff


class TavilySearchClient:
    def __init__(self, api_key: str, *, circuit_breaker: CircuitBreaker | None = None) -> None:
        self._client = TavilyClient(api_key=api_key)
        self._breaker = circuit_breaker or CircuitBreaker(failure_threshold=5, reset_timeout=30.0)

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self._breaker.before_call()
        try:
            result = self._retryable_search(query, **kwargs)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return result

    @retry_with_backoff(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    def _retryable_search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._client.search(query=query, **kwargs)

    def extract(self, urls: list[str]) -> dict[str, Any]:
        self._breaker.before_call()
        try:
            result = self._retryable_extract(urls)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return result

    @retry_with_backoff(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    def _retryable_extract(self, urls: list[str]) -> dict[str, Any]:
        return self._client.extract(urls=urls)
