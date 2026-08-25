"""Integration tests for the drafting stage: Gemini success, malformed output,
and hard failure, all falling through to the same offline template so a
Gemini outage never blocks the whole approval queue."""
from __future__ import annotations

import threading

from internship_agent.config import Config
from internship_agent.pipeline import drafting as drafting_module


class ScriptedGemini:
    """Returns a scripted response per call, keyed by call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._lock = threading.Lock()
        self.calls = 0

    def generate_json(self, prompt):
        with self._lock:
            index = self.calls
            self.calls += 1
        response = self._responses[index % len(self._responses)]
        if isinstance(response, Exception):
            raise response
        return response


def _config():
    return Config(tavily_api_key="t", gemini_api_key="g", hunter_api_key=None)


def _contact(company, role="AI Intern", **overrides):
    base = {
        "company": company,
        "role": role,
        "email": f"hr@{company.lower()}.com",
        "contact_name": "Jo Lee",
        "source_url": "https://x.com",
        "contact_source": "hunter.io",
    }
    base.update(overrides)
    return base


class TestDraftEmails:
    def test_successful_gemini_draft_is_saved(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([{"subject": "Hi Acme", "body": "Hello!"}])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)

        created = drafting_module.draft_emails(
            resume_file, [_contact("Acme")], 10, _config(), repo, "a@example.com"
        )

        assert len(created) == 1
        assert created[0]["subject"] == "Hi Acme"
        assert created[0]["status"] == "pending_approval"
        saved = repo.list_drafts("a@example.com")
        assert len(saved) == 1
        assert saved[0]["to"] == "hr@acme.com"

    def test_malformed_gemini_json_falls_back_to_template(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([{"not_subject_or_body": True}])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)

        created = drafting_module.draft_emails(
            resume_file, [_contact("Acme")], 10, _config(), repo, "a@example.com"
        )

        assert len(created) == 1
        assert "Acme" in created[0]["subject"]

    def test_gemini_exception_falls_back_to_template(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([RuntimeError("quota exceeded")])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)

        created = drafting_module.draft_emails(
            resume_file, [_contact("Acme")], 10, _config(), repo, "a@example.com"
        )

        assert len(created) == 1
        assert created[0]["body"]  # fallback still produces a usable body

    def test_skips_companies_already_drafted_for_this_user(self, repo, resume_file, monkeypatch):
        repo.add_draft("a@example.com", {"company": "Acme", "role": "AI Intern", "to": "hr@acme.com"})
        gemini = ScriptedGemini([{"subject": "s", "body": "b"}])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)

        created = drafting_module.draft_emails(
            resume_file, [_contact("Acme")], 10, _config(), repo, "a@example.com"
        )

        assert created == []
        assert gemini.calls == 0

    def test_respects_limit(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([{"subject": "s", "body": "b"}])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)
        contacts = [_contact(f"Company{i}") for i in range(5)]

        created = drafting_module.draft_emails(resume_file, contacts, 2, _config(), repo, "a@example.com")

        assert len(created) == 2

    def test_drafts_are_scoped_per_user(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([{"subject": "s", "body": "b"}])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)

        drafting_module.draft_emails(resume_file, [_contact("Acme")], 10, _config(), repo, "a@example.com")

        assert repo.list_drafts("a@example.com") != []
        assert repo.list_drafts("b@example.com") == []

    def test_concurrent_drafting_produces_one_draft_per_contact(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([{"subject": "s", "body": "b"}])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)
        contacts = [_contact(f"Company{i}") for i in range(10)]

        created = drafting_module.draft_emails(
            resume_file, contacts, 10, _config(), repo, "a@example.com", max_workers=5
        )

        assert len(created) == 10
        assert len({d["id"] for d in created}) == 10  # every draft got a distinct row id
        assert len(repo.list_drafts("a@example.com")) == 10

    def test_records_run_metrics_with_fallback_counted_as_error(self, repo, resume_file, monkeypatch):
        gemini = ScriptedGemini([RuntimeError("down")])
        monkeypatch.setattr(drafting_module, "GeminiClient", lambda api_key: gemini)

        drafting_module.draft_emails(
            resume_file, [_contact("Acme")], 10, _config(), repo, "a@example.com", run_id="run-3"
        )

        with repo.db.cursor() as cur:
            cur.execute("SELECT * FROM run_metrics WHERE run_id = 'run-3'")
            row = dict(cur.fetchone())
        assert row["items_out"] == 1
        assert row["errors"] == 1  # fallback path counts as a degraded Gemini call
