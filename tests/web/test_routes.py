from __future__ import annotations

import io

import web_app


class TestErrorHandling:
    def test_unknown_route_returns_404_not_500(self, client):
        # Regression check: the catch-all error handler used to swallow
        # Werkzeug's HTTPException (404/405/...) and turn every routing miss
        # — including a browser's routine /favicon.ico probe — into a 500.
        response = client.get("/no-such-route")
        assert response.status_code == 404

    def test_unknown_api_route_returns_404_json(self, client):
        response = client.get("/api/no-such-route")
        assert response.status_code == 404
        assert response.get_json()["ok"] is False


class TestIndexPage:
    def test_signed_out_shows_login(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"Sign in with Google" in response.data

    def test_signed_in_shows_dashboard(self, signed_in_client):
        response = signed_in_client.get("/")
        assert response.status_code == 200
        assert b"Review internship outreach" in response.data

    def test_history_page_redirects_when_signed_out(self, client):
        response = client.get("/history")
        assert response.status_code == 302

    def test_stats_page_signed_in(self, signed_in_client):
        response = signed_in_client.get("/stats")
        assert response.status_code == 200
        assert b"Run metrics" in response.data


class TestAuthGate:
    def test_api_routes_require_sign_in(self, client):
        response = client.post("/api/settings", data={"gemini_api_key": "sk-123"})
        assert response.status_code == 500
        assert response.get_json()["ok"] is False

    def test_logout_clears_session_and_gmail_token(self, signed_in_client):
        web_app.repo.save_gmail_token("a@example.com", "a_example_com", {"token": "abc"})
        response = signed_in_client.post("/api/logout")
        assert response.get_json() == {"ok": True}
        assert web_app.repo.get_gmail_token("a@example.com") is None
        # session is cleared: a follow-up authenticated call should now fail
        follow_up = signed_in_client.get("/api/drafts")
        assert follow_up.status_code == 500


class TestSettingsAndResume:
    def test_save_settings_updates_status_and_encrypts_at_rest(self, signed_in_client):
        response = signed_in_client.post("/api/settings", data={"gemini_api_key": "sk-secret-value"})
        body = response.get_json()
        assert body["ok"] is True
        assert body["api_key_status"] == {"gemini": True, "tavily": False, "hunter": False}

        with web_app.db.cursor() as cur:
            cur.execute("SELECT gemini_api_key_enc FROM users WHERE email = 'a@example.com'")
            raw = cur.fetchone()["gemini_api_key_enc"]
        assert b"sk-secret-value" not in bytes(raw)

    def test_upload_resume_requires_supported_extension(self, signed_in_client):
        data = {"resume": (io.BytesIO(b"binary"), "resume.docx")}
        response = signed_in_client.post("/api/upload", data=data, content_type="multipart/form-data")
        assert response.status_code == 400

    def test_upload_resume_saves_path(self, signed_in_client):
        data = {"resume": (io.BytesIO(b"Jane Doe resume text"), "resume.txt")}
        response = signed_in_client.post("/api/upload", data=data, content_type="multipart/form-data")
        assert response.status_code == 200
        assert response.get_json()["ok"] is True
        assert web_app.repo.get_user("a@example.com")["resume_path"].endswith("resume.txt")


class TestSearchRoute:
    def test_requires_api_keys(self, signed_in_client):
        response = signed_in_client.post("/api/search", data={"limit": "5"})
        assert response.status_code == 500
        assert "API key" in response.get_json()["error"]

    def test_search_updates_counts(self, signed_in_client, monkeypatch):
        web_app.repo.save_api_keys(
            "a@example.com", "a_example_com", gemini_api_key="g", tavily_api_key="t"
        )

        def fake_search(limit, config, repo, **kwargs):
            repo.upsert_opportunities([{"company": "Acme", "role": "AI Intern"}])
            return repo.list_opportunities()

        def fake_find_contacts(opportunities, config, repo, **kwargs):
            for opp in opportunities:
                repo.upsert_contact({**opp, "email": "hr@acme.com"})
            return repo.list_contacts()

        monkeypatch.setattr(web_app, "search_internships", fake_search)
        monkeypatch.setattr(web_app, "find_contacts", fake_find_contacts)

        response = signed_in_client.post("/api/search", data={"limit": "5"})
        body = response.get_json()
        assert body["ok"] is True
        assert body["internships_count"] == 1
        assert body["contacts_count"] == 1


class TestDraftRoute:
    def test_requires_resume(self, signed_in_client, monkeypatch):
        web_app.repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key="g", tavily_api_key="t")
        response = signed_in_client.post("/api/draft", data={"limit": "5"})
        assert response.status_code == 400
        assert "resume" in response.get_json()["error"].lower()

    def test_drafts_only_contacts_not_blocked_by_history(self, signed_in_client, monkeypatch, tmp_path):
        web_app.repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key="g", tavily_api_key="t")
        resume = tmp_path / "resume.txt"
        resume.write_text("Jane Doe")
        web_app.repo.save_resume_path("a@example.com", "a_example_com", str(resume))
        web_app.repo.upsert_contact({"company": "Acme", "role": "AI Intern", "email": "hr@acme.com"})
        web_app.repo.upsert_contact({"company": "Globex", "role": "SWE Intern", "email": "hr@globex.com"})
        web_app.repo.remember_company("a@example.com", {"company": "Globex"}, "removed")

        seen_companies = []

        def fake_draft_emails(resume_file, contacts, limit, config, repo, user_email, **kwargs):
            seen_companies.extend(c["company"] for c in contacts)
            created = []
            for c in contacts:
                draft = {
                    "status": "pending_approval",
                    "to": c["email"],
                    "company": c["company"],
                    "role": c["role"],
                    "subject": "s",
                    "body": "b",
                }
                draft_id = repo.add_draft(user_email, draft)
                created.append({**draft, "id": draft_id})
            return created

        monkeypatch.setattr(web_app, "draft_emails", fake_draft_emails)

        response = signed_in_client.post("/api/draft", data={"limit": "5"})
        assert response.status_code == 200
        assert seen_companies == ["Acme"]  # Globex is excluded via company history

    def test_no_sendable_contacts_returns_400(self, signed_in_client, tmp_path):
        web_app.repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key="g", tavily_api_key="t")
        resume = tmp_path / "resume.txt"
        resume.write_text("Jane Doe")
        web_app.repo.save_resume_path("a@example.com", "a_example_com", str(resume))

        response = signed_in_client.post("/api/draft", data={"limit": "5"})
        assert response.status_code == 400
        assert response.get_json()["ok"] is False


class TestDraftLifecycle:
    def _seed_draft(self, **overrides):
        draft = {"company": "Acme", "role": "AI Intern", "to": "hr@acme.com", "subject": "s", "body": "b"}
        draft.update(overrides)
        return web_app.repo.add_draft("a@example.com", draft)

    def test_skip_marks_removed_and_updates_history(self, signed_in_client):
        draft_id = self._seed_draft()
        response = signed_in_client.post(f"/api/drafts/{draft_id}/skip")
        assert response.get_json()["draft"]["status"] == "removed"
        assert web_app.repo.history("a@example.com")[0]["status"] == "removed"

    def test_remove_is_an_alias_for_skip(self, signed_in_client):
        draft_id = self._seed_draft()
        response = signed_in_client.post(f"/api/drafts/{draft_id}/remove")
        assert response.get_json()["draft"]["status"] == "removed"

    def test_send_missing_recipient_marks_missing_email(self, signed_in_client):
        draft_id = self._seed_draft(to="")
        response = signed_in_client.post(f"/api/drafts/{draft_id}/send")
        assert response.status_code == 400
        assert response.get_json()["draft"]["status"] == "missing_email"

    def test_send_success_marks_sent_and_records_message_id(self, signed_in_client, monkeypatch):
        draft_id = self._seed_draft()
        monkeypatch.setattr(web_app, "gmail_service", lambda: object())
        monkeypatch.setattr(web_app, "send_message", lambda *a, **k: {"id": "msg-123"})

        response = signed_in_client.post(f"/api/drafts/{draft_id}/send")
        body = response.get_json()
        assert body["draft"]["status"] == "sent"
        assert body["draft"]["gmail_message_id"] == "msg-123"
        assert web_app.repo.history("a@example.com")[0]["status"] == "sent"

    def test_sending_twice_is_a_no_op_on_the_second_call(self, signed_in_client, monkeypatch):
        draft_id = self._seed_draft()
        calls = {"n": 0}

        def fake_send(*a, **k):
            calls["n"] += 1
            return {"id": "msg-123"}

        monkeypatch.setattr(web_app, "gmail_service", lambda: object())
        monkeypatch.setattr(web_app, "send_message", fake_send)

        signed_in_client.post(f"/api/drafts/{draft_id}/send")
        signed_in_client.post(f"/api/drafts/{draft_id}/send")
        assert calls["n"] == 1

    def test_send_missing_draft_returns_404(self, signed_in_client):
        response = signed_in_client.post("/api/drafts/999/send")
        assert response.status_code == 404

    def test_needs_contact_drafts_are_excluded_from_current_but_listed_separately(self, signed_in_client):
        self._seed_draft(company="NoContact", to="")
        response = signed_in_client.get("/api/drafts")
        body = response.get_json()
        assert body["current_draft"] is None
        assert len(body["needs_contact"]) == 1


class TestStatsRoute:
    def test_returns_ok_with_empty_stats(self, signed_in_client):
        response = signed_in_client.get("/api/stats")
        body = response.get_json()
        assert body["ok"] is True
        assert body["stats"]["total_runs"] == 0
