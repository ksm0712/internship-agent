"""Integration tests for the contacts stage: concurrency, caching, and the
existing-contact skip path, with Tavily/Gemini/Hunter replaced by fakes.
"""
from __future__ import annotations

import threading

from internship_agent.config import Config
from internship_agent.pipeline import contacts as contacts_module


class FakeTavily:
    def __init__(self):
        self.search_calls = 0
        self._lock = threading.Lock()

    def search(self, query, **kwargs):
        with self._lock:
            self.search_calls += 1
        return {"results": [{"url": "https://acme.com"}]}


class FakeGemini:
    def __init__(self, domain="acme.com"):
        self._domain = domain
        self.calls = 0
        self._lock = threading.Lock()

    def generate_json(self, prompt):
        with self._lock:
            self.calls += 1
        return {"domain": self._domain}


class FakeHunter:
    def __init__(self, emails_by_domain=None):
        self._emails_by_domain = emails_by_domain or {}
        self.enabled = True
        self.calls = 0
        self._lock = threading.Lock()

    def domain_search(self, domain):
        with self._lock:
            self.calls += 1
        return self._emails_by_domain.get(domain, [])


def _config():
    return Config(tavily_api_key="t", gemini_api_key="g", hunter_api_key="h")


def _patch_clients(monkeypatch, tavily, gemini, hunter):
    monkeypatch.setattr(contacts_module, "TavilySearchClient", lambda api_key: tavily)
    monkeypatch.setattr(contacts_module, "GeminiClient", lambda api_key: gemini)
    monkeypatch.setattr(contacts_module, "HunterClient", lambda api_key: hunter)


def _opp(company, role="AI Intern", **overrides):
    base = {"company": company, "role": role, "official_url": None, "source_url": "https://x.com"}
    base.update(overrides)
    return base


class TestFindContacts:
    def test_resolves_contact_via_hunter(self, repo, monkeypatch):
        hunter = FakeHunter({"acme.com": [{"first_name": "Jo", "last_name": "Lee", "position": "Talent Lead", "value": "jo@acme.com", "confidence": 90}]})
        _patch_clients(monkeypatch, FakeTavily(), FakeGemini(), hunter)

        results = contacts_module.find_contacts([_opp("Acme")], _config(), repo)

        assert len(results) == 1
        assert results[0]["email"] == "jo@acme.com"
        assert results[0]["contact_source"] == "hunter.io"
        assert repo.get_contact("Acme", "AI Intern")["email"] == "jo@acme.com"

    def test_falls_back_to_guessed_careers_address_when_hunter_has_no_emails(self, repo, monkeypatch):
        _patch_clients(monkeypatch, FakeTavily(), FakeGemini(), FakeHunter({}))

        results = contacts_module.find_contacts([_opp("Acme")], _config(), repo)

        assert results[0]["email"] == "careers@acme.com"
        assert results[0]["contact_source"] == "guessed generic careers address"

    def test_uses_official_url_directly_without_domain_lookup_calls(self, repo, monkeypatch):
        tavily, gemini = FakeTavily(), FakeGemini()
        hunter = FakeHunter({"acme.com": [{"value": "hr@acme.com", "position": "HR"}]})
        _patch_clients(monkeypatch, tavily, gemini, hunter)

        contacts_module.find_contacts([_opp("Acme", official_url="https://acme.com")], _config(), repo)

        assert tavily.search_calls == 0
        assert gemini.calls == 0
        assert hunter.calls == 1

    def test_skips_opportunities_that_already_have_a_resolved_contact(self, repo, monkeypatch):
        repo.upsert_contact({"company": "Acme", "role": "AI Intern", "email": "existing@acme.com"})
        tavily, gemini, hunter = FakeTavily(), FakeGemini(), FakeHunter()
        _patch_clients(monkeypatch, tavily, gemini, hunter)

        results = contacts_module.find_contacts([_opp("Acme")], _config(), repo)

        assert results[0]["email"] == "existing@acme.com"
        assert tavily.search_calls == 0
        assert hunter.calls == 0

    def test_domain_lookup_is_cached_across_roles_at_the_same_company(self, repo, monkeypatch):
        tavily, gemini = FakeTavily(), FakeGemini()
        hunter = FakeHunter({"acme.com": [{"value": "hr@acme.com", "position": "HR"}]})
        _patch_clients(monkeypatch, tavily, gemini, hunter)

        opportunities = [_opp("Acme", role="AI Intern"), _opp("Acme", role="Data Intern")]
        results = contacts_module.find_contacts(opportunities, _config(), repo)

        assert len(results) == 2
        # domain lookup (tavily search + gemini pick) only needed once; the
        # second role at the same company should hit the domain cache
        assert tavily.search_calls == 1
        assert gemini.calls == 1
        # each role still gets its own Hunter lookup — the cache is only for domains
        assert hunter.calls == 2

    def test_resolves_many_opportunities_concurrently(self, repo, monkeypatch):
        tavily, gemini = FakeTavily(), FakeGemini()
        hunter = FakeHunter({"acme.com": [{"value": "hr@acme.com", "position": "HR"}]})
        _patch_clients(monkeypatch, tavily, gemini, hunter)

        opportunities = [_opp(f"Company{i}", official_url=f"https://company{i}.com") for i in range(12)]
        results = contacts_module.find_contacts(opportunities, _config(), repo, max_workers=6)

        assert len(results) == 12
        assert repo.count_contacts() == 12
        assert {r["company"] for r in results} == {f"Company{i}" for i in range(12)}

    def test_domain_lookup_failure_does_not_crash_the_batch(self, repo, monkeypatch):
        class ExplodingGemini:
            def generate_json(self, prompt):
                raise RuntimeError("gemini down")

        _patch_clients(monkeypatch, FakeTavily(), ExplodingGemini(), FakeHunter())

        results = contacts_module.find_contacts([_opp("Acme")], _config(), repo)

        assert len(results) == 1
        assert results[0]["email"] == ""
        assert results[0]["domain"] is None

    def test_records_run_metrics_including_cache_hits(self, repo, monkeypatch):
        tavily, gemini = FakeTavily(), FakeGemini()
        hunter = FakeHunter({"acme.com": [{"value": "hr@acme.com", "position": "HR"}]})
        _patch_clients(monkeypatch, tavily, gemini, hunter)

        opportunities = [_opp("Acme", role="AI Intern"), _opp("Acme", role="Data Intern")]
        contacts_module.find_contacts(opportunities, _config(), repo, run_id="run-2")

        with repo.db.cursor() as cur:
            cur.execute("SELECT * FROM run_metrics WHERE run_id = 'run-2'")
            row = dict(cur.fetchone())
        assert row["items_in"] == 2
        assert row["items_out"] == 2
        assert row["cache_hits"] == 1
