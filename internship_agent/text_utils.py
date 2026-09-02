"""Pure text/URL helpers: normalization, relevance filtering, and fuzzy dedup."""
from __future__ import annotations

import difflib
import json
import re
from typing import Any
from urllib.parse import urlparse

# Used only as a fallback when the user hasn't typed any role keywords, so
# search still has *some* relevance signal instead of accepting anything
# with the word "intern" in it.
DEFAULT_ROLE_TERMS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "ml",
    "data science",
    "software",
    "engineering",
    "computer science",
    "robotics",
    "quant",
    "developer",
]


def _word_boundary_pattern(terms: list[str]) -> re.Pattern[str]:
    # Word-boundary matching, not bare substring containment: short terms
    # like "ai" and "ml" otherwise match inside ordinary words ("campAIgn",
    # "avAIlable", "htML"), which quietly waves through irrelevant leads.
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms if t.strip()) + r")\b")

# Near-duplicate company names below this similarity are treated as different
# companies. Tuned against real Tavily extraction noise (e.g. "Acme Inc." vs
# "Acme, Inc" vs "ACME") in tests/unit/test_dedup.py.
FUZZY_DEDUP_THRESHOLD = 0.92


def clean_json(text: str) -> str:
    """Strip a ```json ... ``` markdown fence an LLM sometimes wraps JSON in."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    return raw.strip()


def parse_llm_json(text: str) -> Any:
    return json.loads(clean_json(text))


def normalized_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower()


def bare_domain(url_or_domain: str | None) -> str | None:
    if not url_or_domain:
        return None
    value = url_or_domain.strip()
    if value.lower().startswith("http"):
        host = urlparse(value).netloc
    else:
        host = value.split("/")[0]
    host = host.lower().replace("www.", "")
    if "." not in host or "linkedin.com" in host:
        return None
    return host


def company_key(company: str | None) -> str:
    return re.sub(r"\W+", "", (company or "").lower())


def role_key(role: str | None) -> str:
    return re.sub(r"\W+", "", (role or "").lower())


def valid_email(value: str | None) -> bool:
    if not value or value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    return bool(local and "." in domain and not domain.startswith("."))


def is_relevant_role(
    item: dict[str, Any],
    locations: list[str] | None = None,
    roles: list[str] | None = None,
) -> bool:
    """Whether a Gemini-extracted listing matches the user's locations/roles.

    Empty `locations` accepts any location; empty `roles` falls back to
    DEFAULT_ROLE_TERMS.
    """
    role_text = " ".join(
        str(item.get(k, "")).lower() for k in ("role", "description", "evidence")
    )
    full_text = " ".join(
        str(item.get(k, "")).lower()
        for k in ("company", "role", "description", "location", "source_url")
    )
    location = str(item.get("location", "")).lower()
    role = str(item.get("role", "")).strip().lower()
    if role in {"careers", "jobs", "open roles", "internships"}:
        return False

    location_terms = [loc.lower() for loc in (locations or []) if loc.strip()]
    if not location_terms:
        location_matches = True
    else:
        location_matches = any(
            term in location or term in full_text or "remote" in location
            for term in location_terms
        )

    role_terms = [r.lower() for r in (roles or []) if r.strip()] or DEFAULT_ROLE_TERMS
    role_pattern = _word_boundary_pattern(role_terms)

    return (
        re.search(r"\bintern(ship)?\b", role_text) is not None
        and location_matches
        and role_pattern.search(role_text) is not None
    )


def company_similarity(a: str | None, b: str | None) -> float:
    """Similarity of two company names after key-normalization, in [0, 1]."""
    key_a, key_b = company_key(a), company_key(b)
    if not key_a or not key_b:
        return 0.0
    if key_a == key_b:
        return 1.0
    return difflib.SequenceMatcher(None, key_a, key_b).ratio()


def find_fuzzy_duplicate(
    company: str | None, known_companies: list[str], threshold: float = FUZZY_DEDUP_THRESHOLD
) -> str | None:
    """First known company name that's a near-duplicate of `company`, if any.

    Catches variants exact-key dedup misses, e.g. "Acme Inc." vs "ACME Pte Ltd".
    """
    best_match: str | None = None
    best_score = threshold
    for known in known_companies:
        score = company_similarity(company, known)
        if score >= best_score:
            best_score = score
            best_match = known
    return best_match
