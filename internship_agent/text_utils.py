"""Pure text/URL helpers: normalization, relevance filtering, and fuzzy dedup."""
from __future__ import annotations

import difflib
import json
import re
from typing import Any
from urllib.parse import urlparse

AI_TECH_TERMS = [
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
# Word-boundary matching, not bare substring containment: short terms like
# "ai" and "ml" otherwise match inside ordinary words ("campAIgn",
# "avAIlable", "htML"), which was quietly waving through irrelevant leads.
_AI_TECH_TERMS_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in AI_TECH_TERMS) + r")\b"
)

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


def is_relevant_role(item: dict[str, Any]) -> bool:
    role_text = " ".join(
        str(item.get(k, "")).lower() for k in ("role", "description", "evidence")
    )
    full_text = " ".join(
        str(item.get(k, "")).lower()
        for k in ("company", "role", "description", "location", "source_url")
    )
    location = str(item.get("location", "")).lower()
    source_url = str(item.get("source_url", "")).lower()
    role = str(item.get("role", "")).strip().lower()
    if role in {"careers", "jobs", "open roles", "internships"}:
        return False
    singaporeish = (
        "singapore" in location
        or ".sg" in source_url
        or "mycareersfuture.gov.sg" in source_url
        or ("remote" in location and "singapore" in full_text)
    )
    blocked_locations = ["memphis", "tennessee", "usa", "united states"]
    if any(place in location for place in blocked_locations) and "singapore" not in location:
        singaporeish = False
    return (
        re.search(r"\bintern(ship)?\b", role_text) is not None
        and singaporeish
        and _AI_TECH_TERMS_PATTERN.search(role_text) is not None
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
    """Return the first known company name that's a near-duplicate of `company`.

    Catches variants plain key-normalization misses, e.g. "Acme Inc." vs
    "Acme, Inc" vs "ACME Pte Ltd" — common noise in LLM-extracted company
    names that would otherwise slip past exact-key dedup and generate a
    duplicate outreach draft.
    """
    best_match: str | None = None
    best_score = threshold
    for known in known_companies:
        score = company_similarity(company, known)
        if score >= best_score:
            best_score = score
            best_match = known
    return best_match
