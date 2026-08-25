"""Benchmark: sequential vs. concurrent contact resolution.

Runs the real `pipeline.contacts.find_contacts` function against fake Tavily/
Gemini/Hunter clients that sleep for a fixed, realistic per-call latency
instead of hitting the network, so the comparison isolates the effect of
`max_workers` and is reproducible without API keys or live rate limits.

This does not benchmark real API latency (that varies by provider, region,
and load) — it isolates and measures the one thing this rewrite changed:
how much wall-clock time a bounded thread pool saves over the original
agent's sequential loop, for a given per-call latency and batch size.

Usage:
    python -m benchmarks.bench_concurrency
    python -m benchmarks.bench_concurrency --companies 40 --latency 0.35
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from internship_agent.config import Config
from internship_agent.db import Database
from internship_agent.pipeline import contacts as contacts_module
from internship_agent.repository import Repository


class SlowFakeTavily:
    def __init__(self, latency: float) -> None:
        self._latency = latency

    def search(self, query, **kwargs):
        time.sleep(self._latency)
        return {"results": [{"url": "https://example.com"}]}


class SlowFakeGemini:
    def __init__(self, latency: float) -> None:
        self._latency = latency

    def generate_json(self, prompt):
        time.sleep(self._latency)
        return {"domain": "example.com"}


class SlowFakeHunter:
    def __init__(self, latency: float) -> None:
        self._latency = latency
        self.enabled = True

    def domain_search(self, domain):
        time.sleep(self._latency)
        return [{"value": "hr@example.com", "position": "HR", "confidence": 80}]


def _opportunities(n: int) -> list[dict]:
    return [
        {
            "company": f"Company{i}",
            "role": "AI Intern",
            "official_url": None,
            "source_url": "https://example.com",
        }
        for i in range(n)
    ]


def run_once(repo: Repository, n_companies: int, latency: float, max_workers: int) -> float:
    tavily, gemini, hunter = SlowFakeTavily(latency), SlowFakeGemini(latency), SlowFakeHunter(latency)
    fakes = {"TavilySearchClient": lambda api_key: tavily, "GeminiClient": lambda api_key: gemini, "HunterClient": lambda api_key: hunter}

    original = {name: getattr(contacts_module, name) for name in fakes}
    for name, fake in fakes.items():
        setattr(contacts_module, name, fake)
    try:
        config = Config(tavily_api_key="x", gemini_api_key="x", hunter_api_key="x")
        opportunities = _opportunities(n_companies)
        started = time.perf_counter()
        contacts_module.find_contacts(opportunities, config, repo, max_workers=max_workers)
        return time.perf_counter() - started
    finally:
        for name, fn in original.items():
            setattr(contacts_module, name, fn)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", type=int, default=20, help="opportunities to resolve contacts for")
    parser.add_argument("--latency", type=float, default=0.3, help="simulated seconds per API call")
    parser.add_argument("--workers", type=int, default=5, help="thread pool size for the concurrent run")
    args = parser.parse_args()

    with TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "bench.db")
        repo = Repository(db)
        sequential = run_once(repo, args.companies, args.latency, max_workers=1)

    with TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "bench.db")
        repo = Repository(db)
        concurrent = run_once(repo, args.companies, args.latency, max_workers=args.workers)

    speedup = sequential / concurrent if concurrent else float("inf")
    print(f"companies:            {args.companies}")
    print(f"simulated latency:    {args.latency:.2f}s/call")
    print(f"workers (concurrent): {args.workers}")
    print(f"sequential (1 worker): {sequential:.2f}s")
    print(f"concurrent ({args.workers} workers): {concurrent:.2f}s")
    print(f"speedup:              {speedup:.2f}x")


if __name__ == "__main__":
    main()
