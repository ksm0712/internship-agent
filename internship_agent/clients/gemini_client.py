"""Gemini drafting/extraction client: retryable JSON generation.

See tavily_client.py's module docstring for why the circuit-breaker check
sits outside the retried call rather than inside it.
"""
from __future__ import annotations

import warnings
from typing import Any

# google-generativeai is EOL upstream in favor of google-genai; tracked as a
# follow-up in ARCHITECTURE.md rather than a mid-rewrite SDK migration here.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import google.generativeai as genai

from internship_agent.config import MODEL_NAME
from internship_agent.retry import CircuitBreaker, retry_with_backoff
from internship_agent.text_utils import parse_llm_json


class GeminiClient:
    def __init__(
        self, api_key: str, *, model_name: str = MODEL_NAME, circuit_breaker: CircuitBreaker | None = None
    ) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name)
        self._breaker = circuit_breaker or CircuitBreaker(failure_threshold=5, reset_timeout=30.0)

    def generate_text(self, prompt: str) -> str:
        self._breaker.before_call()
        try:
            response = self._retryable_generate(prompt)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return response.text

    @retry_with_backoff(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    def _retryable_generate(self, prompt: str) -> Any:
        return self._model.generate_content(prompt)

    def generate_json(self, prompt: str) -> Any:
        return parse_llm_json(self.generate_text(prompt))
