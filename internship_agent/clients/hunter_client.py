"""Hunter.io domain-search client: retryable, rate-limited contact lookup."""
from __future__ import annotations

from typing import Any

import requests

from internship_agent.rate_limiter import TokenBucketRateLimiter
from internship_agent.retry import retry_with_backoff

HUNTER_URL = "https://api.hunter.io/v2/domain-search"

# Hunter's free tier is capped low enough that uncoordinated concurrent workers
# will start hitting 429s; this keeps every worker under one shared ceiling.
_default_limiter = TokenBucketRateLimiter(rate=2.0, capacity=2.0)


class HunterClient:
    def __init__(self, api_key: str | None, *, limiter: TokenBucketRateLimiter | None = None) -> None:
        self._api_key = api_key
        self._limiter = limiter or _default_limiter

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    @retry_with_backoff(max_attempts=3, base_delay=1.0, exceptions=(requests.RequestException,))
    def domain_search(self, domain: str) -> list[dict[str, Any]]:
        if not self._api_key:
            return []
        self._limiter.acquire()
        response = requests.get(
            HUNTER_URL,
            params={"domain": domain, "api_key": self._api_key},
            timeout=30,
        )
        if response.status_code != 200:
            return []
        return response.json().get("data", {}).get("emails", [])


def score_contact(contact: dict[str, Any]) -> int:
    position = str(contact.get("position", "")).lower()
    email = str(contact.get("value", "")).lower()
    score = int(contact.get("confidence") or 0)
    for keyword, boost in [
        ("talent", 35),
        ("recruit", 35),
        ("people", 25),
        ("founder", 25),
        ("cto", 25),
        ("engineering", 20),
        ("engineer", 15),
        ("hr", 15),
        ("career", 15),
    ]:
        if keyword in position or keyword in email:
            score += boost
    return score
