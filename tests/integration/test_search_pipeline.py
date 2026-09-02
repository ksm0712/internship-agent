"""Integration tests for the search stage, with Tavily/Gemini replaced by fakes.

No real network calls: `TavilySearchClient` and `GeminiClient` are monkeypatched
at the point `pipeline.search` looks them up, so these exercise the real
dedup/relevance/persistence logic against canned responses.
"""
from __future__ import annotations

from internship_agent.config import Config
from internship_agent.pipeline import search as search_module


class TestBuildQueries:
    def test_defaults_when_nothing_specified(self):
        queries = search_module._build_queries([], [], 2026)
        assert queries == [
            "Remote internship intern summer 2026",
            "site:jobs.lever.co Remote intern 2026",
            "site:greenhouse.io Remote intern 2026",
            "site:linkedin.com/jobs Remote intern 2026",
        ]

    def test_combines_each_location_with_each_role(self):
        queries = search_module._build_queries(["Berlin", "Tokyo"], ["backend", "ml"], 2026)
        combo_queries = [q for q in queries if "site:" not in q]
        assert set(combo_queries) == {
            "Berlin backend intern summer 2026",
            "Berlin ml intern summer 2026",
            "Tokyo backend intern summer 2026",
            "Tokyo ml intern summer 2026",
        }

    def test_caps_total_query_count(self):
        locations = [f"City{i}" for i in range(10)]
        roles = [f"Role{i}" for i in range(10)]
        queries = search_module._build_queries(locations, roles, 2026)
        assert len(queries) == search_module.MAX_SEARCH_QUERIES

    def test_ignores_blank_entries(self):
        queries = search_module._build_queries(["  ", "Berlin"], ["", "backend"], 2026)
        assert queries[0] == "Berlin backend intern summer 2026"


class FakeTavily:
    def __init__(self, search_results=None, extract_results=None):
        self._search_results = search_results or []
        self._extract_results = extract_results or []
        self.search_calls = 0
        self.extract_calls = 0

    def search(self, query, **kwargs):
        self.search_calls += 1
        return {"results": self._search_results}

    def extract(self, urls):
        self.extract_calls += 1
        return {"results": [p for p in self._extract_results if p["url"] in urls]}


class FakeGemini:
    def __init__(self, items):
        self._items = items
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        return self._items


def _config():
    return Config(tavily_api_key="tavily-key", gemini_api_key="gemini-key", hunter_api_key=None)


def _patch_clients(monkeypatch, tavily, gemini):
    monkeypatch.setattr(search_module, "TavilySearchClient", lambda api_key: tavily)
    monkeypatch.setattr(search_module, "GeminiClient", lambda api_key: gemini)


class TestSearchInternships:
    def test_relevant_leads_are_saved_and_returned(self, repo, monkeypatch):
        search_results = [{"url": "https://acme.com/careers/ml-intern", "title": "ML Intern", "content": "..."}]
        extracted = [{"url": "https://acme.com/careers/ml-intern", "raw_content": "Full JD..."}]
        gemini_items = [
            {
                "company": "Acme",
                "role": "AI Engineering Intern",
                "description": "Work on ML systems.",
                "location": "Singapore",
                "official_url": "https://acme.com",
                "source_url": "https://acme.com/careers/ml-intern",
                "evidence": "AI internship in Singapore",
                "confidence": 0.9,
            }
        ]
        tavily = FakeTavily(search_results=search_results, extract_results=extracted)
        gemini = FakeGemini(gemini_items)
        _patch_clients(monkeypatch, tavily, gemini)

        found = search_module.search_internships(10, _config(), repo)

        assert len(found) == 1
        assert found[0]["company"] == "Acme"
        assert repo.count_opportunities() == 1
        assert tavily.search_calls == 4  # default location/role -> 1 combo query + 3 site filters
        assert gemini.calls == 1

    def test_irrelevant_leads_are_filtered_out(self, repo, monkeypatch):
        gemini_items = [
            {
                "company": "Acme",
                "role": "Marketing Intern",
                "description": "Support campaigns.",
                "location": "Memphis, Tennessee",
                "source_url": "https://acme.com/careers/marketing",
                "evidence": "Marketing internship in the US",
            }
        ]
        tavily = FakeTavily()
        gemini = FakeGemini(gemini_items)
        _patch_clients(monkeypatch, tavily, gemini)

        found = search_module.search_internships(10, _config(), repo)

        assert found == []
        assert repo.count_opportunities() == 0

    def test_exact_duplicate_within_run_is_deduped(self, repo, monkeypatch):
        item = {
            "company": "Acme",
            "role": "AI Intern",
            "description": "x",
            "location": "Singapore",
            "source_url": "https://acme.com/careers",
            "evidence": "AI internship",
        }
        gemini = FakeGemini([item, dict(item)])
        _patch_clients(monkeypatch, FakeTavily(), gemini)

        found = search_module.search_internships(10, _config(), repo)
        assert len(found) == 1

    def test_fuzzy_duplicate_against_known_company_is_rejected(self, repo, monkeypatch):
        repo.upsert_opportunities([{"company": "Acme Corp", "role": "AI Intern"}])
        item = {
            "company": "Acme Corp.",  # near-duplicate of an already-known company
            "role": "AI Intern",
            "description": "x",
            "location": "Singapore",
            "source_url": "https://acme.com/careers",
            "evidence": "AI internship",
        }
        gemini = FakeGemini([item])
        _patch_clients(monkeypatch, FakeTavily(), gemini)

        found = search_module.search_internships(10, _config(), repo)
        assert found == []

    def test_user_supplied_locations_and_roles_filter_results(self, repo, monkeypatch):
        gemini_items = [
            {
                "company": "Acme",
                "role": "Backend Intern",
                "description": "Work on backend services.",
                "location": "Berlin, Germany",
                "source_url": "https://acme.com/careers/backend",
                "evidence": "backend internship",
            },
            {
                "company": "Globex",
                "role": "Marketing Intern",
                "description": "Support campaigns.",
                "location": "Berlin, Germany",
                "source_url": "https://globex.com/careers/marketing",
                "evidence": "marketing internship",
            },
        ]
        tavily = FakeTavily()
        gemini = FakeGemini(gemini_items)
        _patch_clients(monkeypatch, tavily, gemini)

        found = search_module.search_internships(
            10, _config(), repo, locations=["Berlin"], roles=["backend"]
        )

        assert [item["company"] for item in found] == ["Acme"]
        # 1 location x role combo query + 3 job-site filters for "Berlin"
        assert tavily.search_calls == 4

    def test_query_count_is_capped_for_large_location_and_role_selections(self, repo, monkeypatch):
        tavily = FakeTavily()
        gemini = FakeGemini([])
        _patch_clients(monkeypatch, tavily, gemini)

        search_module.search_internships(
            10,
            _config(),
            repo,
            locations=["Berlin", "Singapore", "Remote", "Tokyo", "New York"],
            roles=["backend", "frontend", "data", "ml", "product"],
        )

        assert tavily.search_calls == search_module.MAX_SEARCH_QUERIES

    def test_respects_limit(self, repo, monkeypatch):
        items = [
            {
                "company": f"Company{i}",
                "role": "AI Intern",
                "description": "x",
                "location": "Singapore",
                "source_url": f"https://company{i}.com/careers",
                "evidence": "AI internship",
            }
            for i in range(5)
        ]
        gemini = FakeGemini(items)
        _patch_clients(monkeypatch, FakeTavily(), gemini)

        found = search_module.search_internships(2, _config(), repo)
        assert len(found) == 2

    def test_records_run_metrics(self, repo, monkeypatch):
        gemini = FakeGemini([])
        _patch_clients(monkeypatch, FakeTavily(), gemini)

        search_module.search_internships(10, _config(), repo, run_id="run-1", user_email="a@example.com")

        with repo.db.cursor() as cur:
            cur.execute("SELECT * FROM run_metrics WHERE run_id = 'run-1'")
            row = dict(cur.fetchone())
        assert row["stage"] == "search"
        assert row["user_email"] == "a@example.com"
        assert row["api_calls"] >= 4  # 4 default search queries, no extract calls when there are no results
