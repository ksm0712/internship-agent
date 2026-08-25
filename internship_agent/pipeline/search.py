"""Stage 1: find current internship postings and extract structured leads.

Search queries run sequentially (Tavily's search endpoint is already a single
round trip per query), but page-content extraction — the part that used to
loop over `range(0, len(urls), 20)` one batch at a time — is parallelized
across a small thread pool, since each batch is an independent network call.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

from internship_agent.clients.gemini_client import GeminiClient
from internship_agent.clients.tavily_client import TavilySearchClient
from internship_agent.config import Config
from internship_agent.metrics import StageTimer, new_run_id
from internship_agent.repository import Repository
from internship_agent.text_utils import (
    company_key,
    find_fuzzy_duplicate,
    is_relevant_role,
    normalized_url,
)

EXTRACT_BATCH_SIZE = 20
EXTRACT_MAX_WORKERS = 4


def _build_queries(current_year: int) -> list[str]:
    return [
        f"Singapore AI internship summer {current_year}",
        f"Singapore machine learning intern summer {current_year}",
        f"Singapore software engineering intern summer {current_year} startup",
        f"Singapore computer science internship {current_year} AI tech",
        f"site:jobs.lever.co Singapore AI intern {current_year}",
        f"site:greenhouse.io Singapore machine learning intern {current_year}",
        f"site:mycareersfuture.gov.sg AI intern Singapore {current_year}",
        f"site:linkedin.com/jobs Singapore AI intern {current_year}",
    ]


def _extract_pages(
    tavily: TavilySearchClient, urls: list[str], timer: StageTimer
) -> dict[str, str]:
    if not urls:
        return {}
    batches = [urls[i : i + EXTRACT_BATCH_SIZE] for i in range(0, len(urls), EXTRACT_BATCH_SIZE)]
    raw_by_url: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=EXTRACT_MAX_WORKERS) as pool:
        futures = {pool.submit(tavily.extract, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                extracted = future.result()
            except Exception:
                timer.record_error()
                continue
            timer.record_api_call()
            for page in extracted.get("results", []):
                raw_by_url[page.get("url", "")] = page.get("raw_content", "")
    return raw_by_url


def _extraction_prompt(snippets: list[dict[str, Any]]) -> str:
    import json

    return f"""
You are extracting real internship opportunities for a student applying for this summer in Singapore.

Today is {date.today().isoformat()}. Extract ONLY current or plausibly current AI/tech/computer-science-related internships in Singapore, Singapore-hybrid, or remote roles open to Singapore-based applicants.

Reject job boards as companies. Keep the source URL as the page where the role was found.

Return ONLY valid JSON:
[
  {{
    "company": "Company name",
    "role": "Exact internship role title",
    "description": "One sentence about the role and company",
    "location": "Singapore / Remote / Hybrid, as stated",
    "official_url": "Company website if visible, else null",
    "source_url": "URL for the job/source",
    "evidence": "Short evidence phrase from the source",
    "confidence": 0.0
  }}
]

Search and extracted content:
{json.dumps(snippets, ensure_ascii=False)}
"""


def search_internships(
    limit: int,
    config: Config,
    repo: Repository,
    *,
    run_id: str | None = None,
    user_email: str | None = None,
) -> list[dict[str, Any]]:
    run_id = run_id or new_run_id()
    tavily = TavilySearchClient(config.tavily_api_key)
    gemini = GeminiClient(config.gemini_api_key)

    with StageTimer(repo.db, run_id=run_id, stage="search", user_email=user_email) as timer:
        queries = _build_queries(date.today().year)
        seen_urls: set[str] = set()
        results: list[dict[str, Any]] = []
        for query in queries:
            response = tavily.search(
                query=query, max_results=6, search_depth="advanced", include_answer=False
            )
            timer.record_api_call()
            for result in response.get("results", []):
                url = normalized_url(result.get("url"))
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append(
                    {
                        "title": result.get("title"),
                        "url": result.get("url"),
                        "content": result.get("content", ""),
                        "score": result.get("score"),
                    }
                )
        timer.items_in = len(results)

        urls = [r["url"] for r in results[: max(8, min(len(results), 40))]]
        raw_by_url = _extract_pages(tavily, urls, timer)

        snippets = [
            {
                "title": result["title"],
                "url": result["url"],
                "search_snippet": result["content"][:900],
                "page_text": raw_by_url.get(result["url"], "")[:5000],
            }
            for result in results
        ]

        items = gemini.generate_json(_extraction_prompt(snippets))
        timer.record_api_call()

        known_companies = [row["company"] for row in repo.list_opportunities()]
        deduped: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        seen_generic_companies: set[str] = set()
        for item in items:
            if not is_relevant_role(item):
                continue
            ck = company_key(item.get("company"))
            if ck == "aiap":
                ck = "aisingapore"
                item["company"] = "AI Singapore"
            role_key_val = company_key(item.get("role"))
            generic_role = role_key_val in {"internship", "intern", "internshipprogramme"}
            if generic_role and ck in seen_generic_companies:
                continue
            key = (ck, role_key_val)
            if key in seen_keys:
                continue
            if find_fuzzy_duplicate(item.get("company"), known_companies):
                continue
            seen_keys.add(key)
            if generic_role or "internshipprogramme" in role_key_val:
                seen_generic_companies.add(ck)
            deduped.append(item)
            known_companies.append(item.get("company", ""))
            if len(deduped) >= limit:
                break

        timer.items_out = len(deduped)
        repo.upsert_opportunities(deduped)

    return deduped
