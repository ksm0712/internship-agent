"""Unit tests for the client wrappers' resilience wiring (retry + circuit
breaker composition), with the underlying SDKs replaced by fakes.

Retry timing itself is already covered fast and deterministically in
test_retry.py with an injected sleeper; here the `_retryable_*` methods are
monkeypatched directly so these tests exercise the breaker-composition logic
(the bug fixed in tavily_client.py / gemini_client.py: `before_call()` must
sit outside the retried call) without waiting on real backoff sleeps.
"""
from __future__ import annotations

import pytest

from internship_agent.clients.gemini_client import GeminiClient
from internship_agent.clients.hunter_client import HunterClient, score_contact
from internship_agent.clients.tavily_client import TavilySearchClient
from internship_agent.retry import CircuitBreaker, CircuitOpenError


class FakeTavilySDK:
    def __init__(self):
        self.search_calls = 0

    def search(self, query, **kwargs):
        self.search_calls += 1
        return {"results": [{"url": "https://acme.com"}]}


class TestTavilySearchClient:
    def test_search_delegates_to_underlying_sdk(self, monkeypatch):
        fake = FakeTavilySDK()
        monkeypatch.setattr("internship_agent.clients.tavily_client.TavilyClient", lambda api_key: fake)

        client = TavilySearchClient("key")
        result = client.search("query")

        assert result["results"][0]["url"] == "https://acme.com"
        assert fake.search_calls == 1

    def test_breaker_opens_after_threshold_and_short_circuits_without_retrying(self, monkeypatch):
        monkeypatch.setattr("internship_agent.clients.tavily_client.TavilyClient", lambda api_key: object())
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout=30.0)
        client = TavilySearchClient("key", circuit_breaker=breaker)

        calls = {"n": 0}

        def always_fails(*args, **kwargs):
            calls["n"] += 1
            raise ConnectionError("tavily down")

        monkeypatch.setattr(client, "_retryable_search", always_fails)

        with pytest.raises(ConnectionError):
            client.search("q")
        with pytest.raises(ConnectionError):
            client.search("q")
        assert calls["n"] == 2

        # circuit is now open: the 3rd call must fail fast via the breaker,
        # not by calling (and retrying) the underlying search again
        with pytest.raises(CircuitOpenError):
            client.search("q")
        assert calls["n"] == 2

    def test_success_resets_breaker_failure_count(self, monkeypatch):
        monkeypatch.setattr("internship_agent.clients.tavily_client.TavilyClient", lambda api_key: object())
        breaker = CircuitBreaker(failure_threshold=2, reset_timeout=30.0)
        client = TavilySearchClient("key", circuit_breaker=breaker)

        monkeypatch.setattr(
            client, "_retryable_search", lambda *a, **k: (_ for _ in ()).throw(ConnectionError())
        )
        with pytest.raises(ConnectionError):
            client.search("q")

        monkeypatch.setattr(client, "_retryable_search", lambda *a, **k: {"results": []})
        client.search("q")  # success

        monkeypatch.setattr(
            client, "_retryable_search", lambda *a, **k: (_ for _ in ()).throw(ConnectionError())
        )
        with pytest.raises(ConnectionError):
            client.search("q")
        # only 1 consecutive failure since the reset — breaker should still be closed
        with pytest.raises(ConnectionError):
            client.search("q")  # would raise CircuitOpenError instead if the breaker were (wrongly) open


class TestGeminiClient:
    def test_generate_json_parses_markdown_fenced_response(self, monkeypatch):
        class FakeResponse:
            text = '```json\n{"subject": "Hi", "body": "Hello"}\n```'

        class FakeModel:
            def generate_content(self, prompt):
                return FakeResponse()

        monkeypatch.setattr("internship_agent.clients.gemini_client.genai.configure", lambda api_key: None)
        monkeypatch.setattr(
            "internship_agent.clients.gemini_client.genai.GenerativeModel", lambda model_name: FakeModel()
        )

        client = GeminiClient("key")
        assert client.generate_json("prompt") == {"subject": "Hi", "body": "Hello"}

    def test_breaker_opens_without_calling_model_again(self, monkeypatch):
        monkeypatch.setattr("internship_agent.clients.gemini_client.genai.configure", lambda api_key: None)
        monkeypatch.setattr(
            "internship_agent.clients.gemini_client.genai.GenerativeModel", lambda model_name: object()
        )
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30.0)
        client = GeminiClient("key", circuit_breaker=breaker)

        calls = {"n": 0}

        def always_fails(prompt):
            calls["n"] += 1
            raise RuntimeError("quota exceeded")

        monkeypatch.setattr(client, "_retryable_generate", always_fails)

        with pytest.raises(RuntimeError):
            client.generate_text("prompt")
        assert calls["n"] == 1

        with pytest.raises(CircuitOpenError):
            client.generate_text("prompt")
        assert calls["n"] == 1  # breaker short-circuited; underlying call not retried


class TestHunterClient:
    def test_disabled_without_api_key(self):
        assert HunterClient(None).enabled is False

    def test_domain_search_returns_empty_list_without_api_key(self):
        assert HunterClient(None).domain_search("acme.com") == []

    def test_enabled_with_api_key(self):
        assert HunterClient("key").enabled is True


class TestScoreContact:
    def test_boosts_recruiting_titles(self):
        recruiter = {"position": "Talent Acquisition Lead", "value": "a@acme.com", "confidence": 50}
        engineer = {"position": "Software Engineer", "value": "b@acme.com", "confidence": 50}
        assert score_contact(recruiter) > score_contact(engineer)

    def test_uses_confidence_as_base_score(self):
        assert score_contact({"position": "", "value": "", "confidence": 80}) == 80
