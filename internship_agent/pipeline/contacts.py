"""Stage 2: resolve a recipient contact for each opportunity.

Companies resolve concurrently on a bounded thread pool. Hunter.io calls
share a rate limiter (clients.hunter_client) so concurrency doesn't turn
into a burst of 429s; domain lookups are cached since a company's domain
doesn't change between runs.

Workers return their own call/cache/error counts rather than mutating a
shared StageTimer directly; the main thread aggregates as futures complete.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from internship_agent.cache import TTLCache
from internship_agent.clients.gemini_client import GeminiClient
from internship_agent.clients.hunter_client import HunterClient, score_contact
from internship_agent.clients.tavily_client import TavilySearchClient
from internship_agent.config import Config
from internship_agent.metrics import StageTimer, new_run_id
from internship_agent.repository import Repository
from internship_agent.singleflight import SingleFlight
from internship_agent.text_utils import bare_domain, company_key

CONTACTS_MAX_WORKERS = 5
DOMAIN_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DOMAIN_LOOKUP_FAILURE_TTL_SECONDS = 60 * 60


def _empty_contact(opp: dict[str, Any]) -> dict[str, Any]:
    return {
        **opp,
        "domain": None,
        "contact_name": "",
        "contact_position": "",
        "email": "",
        "contact_source": "",
    }


def _choose_domain(
    tavily: TavilySearchClient,
    gemini: GeminiClient,
    company: str,
    official_url: str | None,
    cache: TTLCache,
    single_flight: SingleFlight,
) -> tuple[str | None, int]:
    """Returns (domain, api_calls_made).

    Concurrent callers for the same company are serialized through
    `single_flight` so only the first one actually calls Tavily/Gemini; the
    rest block until it's done and then hit the now-populated cache instead
    of racing it (see internship_agent.singleflight).
    """
    direct = bare_domain(official_url)
    if direct:
        return direct, 0

    cache_key = f"domain:{company_key(company)}"

    def compute() -> tuple[str | None, int]:
        cached = cache.get(cache_key)
        if cached is not None:
            return (cached or None), 0

        api_calls = 0
        try:
            response = tavily.search(query=f"{company} official website", max_results=5)
            api_calls += 1
            urls = [r.get("url") for r in response.get("results", [])]
            prompt = f"""
Pick the official company website domain for "{company}" from this list.
Reject LinkedIn, job boards, Crunchbase, social media, and news sites.

URLs: {urls}

Return ONLY JSON:
{{"domain": "example.com or null"}}
"""
            chosen = gemini.generate_json(prompt)
            api_calls += 1
        except Exception:
            cache.set(cache_key, "", ttl_seconds=DOMAIN_LOOKUP_FAILURE_TTL_SECONDS)
            return None, api_calls

        domain = bare_domain(chosen.get("domain"))
        cache.set(cache_key, domain or "", ttl_seconds=DOMAIN_CACHE_TTL_SECONDS)
        return domain, api_calls

    return single_flight.do(cache_key, compute)


@dataclass
class ContactResolution:
    contact: dict[str, Any]
    api_calls: int = 0
    error: bool = False
    log: list[str] = field(default_factory=list)


def _resolve_contact(
    opp: dict[str, Any],
    tavily: TavilySearchClient,
    gemini: GeminiClient,
    hunter: HunterClient,
    cache: TTLCache,
    single_flight: SingleFlight,
) -> ContactResolution:
    try:
        domain, api_calls = _choose_domain(
            tavily, gemini, opp.get("company", ""), opp.get("official_url"), cache, single_flight
        )
    except Exception:
        return ContactResolution(contact=_empty_contact(opp), error=True)

    contact = _empty_contact(opp) | {"domain": domain}
    if domain:
        try:
            emails = hunter.domain_search(domain)
        except Exception:
            emails = []
        if hunter.enabled:
            api_calls += 1
        if emails:
            chosen = sorted(emails, key=score_contact, reverse=True)[0]
            contact.update(
                {
                    "contact_name": " ".join(
                        str(chosen.get(part, "")).strip() for part in ("first_name", "last_name")
                    ).strip(),
                    "contact_position": chosen.get("position") or "",
                    "email": chosen.get("value") or "",
                    "contact_source": "hunter.io",
                }
            )
        else:
            contact["email"] = f"careers@{domain}"
            contact["contact_source"] = "guessed generic careers address"
    return ContactResolution(contact=contact, api_calls=api_calls)


def find_contacts(
    opportunities: list[dict[str, Any]],
    config: Config,
    repo: Repository,
    *,
    run_id: str | None = None,
    user_email: str | None = None,
    max_workers: int = CONTACTS_MAX_WORKERS,
) -> list[dict[str, Any]]:
    run_id = run_id or new_run_id()
    tavily = TavilySearchClient(config.tavily_api_key)
    gemini = GeminiClient(config.gemini_api_key)
    hunter = HunterClient(config.hunter_api_key)
    cache = TTLCache(repo.db)
    single_flight = SingleFlight()

    with StageTimer(repo.db, run_id=run_id, stage="contacts", user_email=user_email) as timer:
        timer.items_in = len(opportunities)

        results: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for opp in opportunities:
            existing = repo.get_contact(opp.get("company"), opp.get("role"))
            if existing and existing.get("email"):
                results.append(existing)
                continue
            pending.append(opp)

        if pending:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_resolve_contact, opp, tavily, gemini, hunter, cache, single_flight): opp
                    for opp in pending
                }
                for future in as_completed(futures):
                    resolution = future.result()
                    timer.api_calls += resolution.api_calls
                    if resolution.error:
                        timer.record_error()
                    repo.upsert_contact(resolution.contact)
                    results.append(resolution.contact)

        timer.cache_hits = cache.hits
        timer.items_out = len(results)

    return results
