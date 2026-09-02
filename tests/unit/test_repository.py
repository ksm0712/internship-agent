from __future__ import annotations


class TestOpportunities:
    def test_upsert_inserts_new_rows(self, repo):
        inserted = repo.upsert_opportunities(
            [{"company": "Acme", "role": "ML Intern"}, {"company": "Globex", "role": "SWE Intern"}]
        )
        assert inserted == 2
        assert repo.count_opportunities() == 2

    def test_upsert_is_idempotent_by_company_and_role(self, repo):
        item = {"company": "Acme", "role": "ML Intern", "description": "v1"}
        repo.upsert_opportunities([item])
        inserted_again = repo.upsert_opportunities([{**item, "description": "v2"}])
        assert inserted_again == 0
        assert repo.count_opportunities() == 1
        # ON CONFLICT DO NOTHING: the original row survives untouched.
        assert repo.list_opportunities()[0]["description"] == "v1"

    def test_skips_items_missing_company_or_role(self, repo):
        inserted = repo.upsert_opportunities([{"company": "", "role": "ML Intern"}, {"company": "Acme", "role": ""}])
        assert inserted == 0

    def test_list_opportunities_respects_limit(self, repo):
        repo.upsert_opportunities([{"company": f"C{i}", "role": "Intern"} for i in range(5)])
        assert len(repo.list_opportunities(limit=2)) == 2


class TestContacts:
    def test_upsert_and_get_roundtrip(self, repo):
        repo.upsert_contact(
            {"company": "Acme", "role": "ML Intern", "domain": "acme.com", "email": "hr@acme.com"}
        )
        found = repo.get_contact("Acme", "ML Intern")
        assert found["email"] == "hr@acme.com"
        assert found["domain"] == "acme.com"

    def test_get_contact_is_case_and_punctuation_insensitive_on_company(self, repo):
        repo.upsert_contact({"company": "Acme, Inc.", "role": "ML Intern", "email": "hr@acme.com"})
        assert repo.get_contact("ACME INC", "ML Intern") is not None

    def test_upsert_updates_existing_contact(self, repo):
        repo.upsert_contact({"company": "Acme", "role": "ML Intern", "email": "old@acme.com"})
        repo.upsert_contact({"company": "Acme", "role": "ML Intern", "email": "new@acme.com"})
        assert repo.count_contacts() == 1
        assert repo.get_contact("Acme", "ML Intern")["email"] == "new@acme.com"

    def test_get_contact_missing_returns_none(self, repo):
        assert repo.get_contact("Nope", "Role") is None


class TestUsersAndApiKeys:
    def test_save_and_get_api_keys_roundtrip_decrypted(self, repo):
        repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key="sk-gemini", tavily_api_key="sk-tavily")
        user = repo.get_user("a@example.com")
        assert user["gemini_api_key"] == "sk-gemini"
        assert user["tavily_api_key"] == "sk-tavily"
        assert user["hunter_api_key"] is None

    def test_api_keys_are_encrypted_at_rest(self, repo, db):
        repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key="sk-super-secret")
        with db.cursor() as cur:
            cur.execute("SELECT gemini_api_key_enc FROM users WHERE email = ?", ("a@example.com",))
            raw = cur.fetchone()["gemini_api_key_enc"]
        assert b"sk-super-secret" not in bytes(raw)

    def test_saving_blank_key_does_not_overwrite_existing(self, repo):
        repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key="sk-gemini")
        repo.save_api_keys("a@example.com", "a_example_com", gemini_api_key=None)
        assert repo.get_user("a@example.com")["gemini_api_key"] == "sk-gemini"

    def test_get_user_missing_returns_none(self, repo):
        assert repo.get_user("nobody@example.com") is None

    def test_save_resume_path(self, repo):
        repo.save_resume_path("a@example.com", "a_example_com", "/uploads/resume.pdf")
        assert repo.get_user("a@example.com")["resume_path"] == "/uploads/resume.pdf"


class TestGmailToken:
    def test_roundtrip(self, repo):
        token = {"token": "abc", "refresh_token": "xyz"}
        repo.save_gmail_token("a@example.com", "a_example_com", token)
        assert repo.get_gmail_token("a@example.com") == token

    def test_missing_token_returns_none(self, repo):
        assert repo.get_gmail_token("nobody@example.com") is None

    def test_two_users_have_independent_tokens(self, repo):
        repo.save_gmail_token("a@example.com", "a_example_com", {"token": "a-token"})
        repo.save_gmail_token("b@example.com", "b_example_com", {"token": "b-token"})
        assert repo.get_gmail_token("a@example.com")["token"] == "a-token"
        assert repo.get_gmail_token("b@example.com")["token"] == "b-token"

    def test_clear_gmail_token(self, repo):
        repo.save_gmail_token("a@example.com", "a_example_com", {"token": "abc"})
        repo.clear_gmail_token("a@example.com")
        assert repo.get_gmail_token("a@example.com") is None

    def test_token_is_encrypted_at_rest(self, repo, db):
        repo.save_gmail_token("a@example.com", "a_example_com", {"refresh_token": "super-secret-refresh"})
        with db.cursor() as cur:
            cur.execute("SELECT gmail_oauth_token_enc FROM users WHERE email = ?", ("a@example.com",))
            raw = cur.fetchone()["gmail_oauth_token_enc"]
        assert b"super-secret-refresh" not in bytes(raw)


class TestDrafts:
    def _draft(self, **overrides):
        base = {
            "company": "Acme",
            "role": "ML Intern",
            "to": "hr@acme.com",
            "subject": "Hi",
            "body": "Hello there",
        }
        base.update(overrides)
        return base

    def test_add_and_list(self, repo):
        draft_id = repo.add_draft("a@example.com", self._draft())
        drafts = repo.list_drafts("a@example.com")
        assert len(drafts) == 1
        assert drafts[0]["id"] == draft_id
        assert drafts[0]["to"] == "hr@acme.com"
        assert drafts[0]["status"] == "pending_approval"

    def test_list_excludes_removed_by_default(self, repo):
        draft_id = repo.add_draft("a@example.com", self._draft())
        repo.update_draft_status("a@example.com", draft_id, "removed")
        assert repo.list_drafts("a@example.com") == []
        assert len(repo.list_drafts("a@example.com", exclude_removed=False)) == 1

    def test_drafts_are_scoped_per_user(self, repo):
        repo.add_draft("a@example.com", self._draft())
        assert repo.list_drafts("b@example.com") == []

    def test_get_draft_wrong_user_returns_none(self, repo):
        draft_id = repo.add_draft("a@example.com", self._draft())
        assert repo.get_draft("b@example.com", draft_id) is None

    def test_update_draft_status_sets_gmail_message_id(self, repo):
        draft_id = repo.add_draft("a@example.com", self._draft())
        updated = repo.update_draft_status("a@example.com", draft_id, "sent", gmail_message_id="msg-123")
        assert updated["status"] == "sent"
        assert updated["gmail_message_id"] == "msg-123"

    def test_existing_draft_company_keys(self, repo):
        repo.add_draft("a@example.com", self._draft(company="Acme"))
        keys = repo.existing_draft_company_keys("a@example.com")
        assert "acme" in keys

    def test_removed_drafts_excluded_from_company_keys(self, repo):
        draft_id = repo.add_draft("a@example.com", self._draft(company="Acme"))
        repo.update_draft_status("a@example.com", draft_id, "removed")
        assert "acme" not in repo.existing_draft_company_keys("a@example.com")


class TestCompanyHistory:
    def test_remember_and_list(self, repo):
        repo.remember_company("a@example.com", {"company": "Acme", "role": "ML Intern", "to": "hr@acme.com"}, "sent")
        history = repo.history("a@example.com")
        assert len(history) == 1
        assert history[0]["status"] == "sent"

    def test_history_company_keys_used_for_dedup(self, repo):
        repo.remember_company("a@example.com", {"company": "Acme Inc."}, "drafted")
        assert "acmeinc" in repo.history_company_keys("a@example.com")

    def test_remember_company_upserts_by_company(self, repo):
        repo.remember_company("a@example.com", {"company": "Acme", "status": "drafted"}, "drafted")
        repo.remember_company("a@example.com", {"company": "Acme", "status": "sent"}, "sent")
        history = repo.history("a@example.com")
        assert len(history) == 1
        assert history[0]["status"] == "sent"

    def test_remember_company_preserves_gmail_message_id_when_not_provided(self, repo):
        repo.remember_company(
            "a@example.com",
            {"company": "Acme", "gmail_message_id": "msg-1"},
            "sent",
        )
        repo.remember_company("a@example.com", {"company": "Acme"}, "removed")
        history = repo.history("a@example.com")
        assert history[0]["gmail_message_id"] == "msg-1"

    def test_remember_company_ignores_blank_company(self, repo):
        repo.remember_company("a@example.com", {"company": ""}, "drafted")
        assert repo.history("a@example.com") == []

    def test_history_scoped_per_user(self, repo):
        repo.remember_company("a@example.com", {"company": "Acme"}, "drafted")
        assert repo.history("b@example.com") == []


class TestTrainingExamples:
    def _draft(self, **overrides):
        base = {
            "company": "Acme",
            "role": "Backend Intern",
            "to": "hr@acme.com",
            "subject": "s",
            "body": "b",
            "status": "sent",
        }
        base.update(overrides)
        return base

    def test_only_includes_decided_drafts(self, repo):
        repo.add_draft("a@example.com", self._draft(status="sent"))
        repo.add_draft("a@example.com", self._draft(company="Globex", status="pending_approval"))
        repo.add_draft("a@example.com", self._draft(company="Initech", status="needs_contact"))
        examples = repo.training_examples("a@example.com")
        assert len(examples) == 1

    def test_sent_maps_to_positive_label(self, repo):
        repo.add_draft("a@example.com", self._draft(status="sent"))
        examples = repo.training_examples("a@example.com")
        assert examples[0]["label"] == 1

    def test_removed_and_skipped_map_to_negative_label(self, repo):
        repo.add_draft("a@example.com", self._draft(status="removed"))
        repo.add_draft("a@example.com", self._draft(company="Globex", status="skipped"))
        examples = repo.training_examples("a@example.com")
        assert all(ex["label"] == 0 for ex in examples)

    def test_joins_opportunity_and_contact_fields(self, repo):
        repo.upsert_opportunities(
            [{"company": "Acme", "role": "Backend Intern", "description": "Build APIs", "confidence": 0.8}]
        )
        repo.upsert_contact({"company": "Acme", "role": "Backend Intern", "contact_source": "hunter.io"})
        repo.add_draft("a@example.com", self._draft(status="sent"))

        examples = repo.training_examples("a@example.com")
        assert examples[0]["description"] == "Build APIs"
        assert examples[0]["confidence"] == 0.8
        assert examples[0]["contact_source"] == "hunter.io"

    def test_missing_opportunity_or_contact_fills_in_defaults(self, repo):
        repo.add_draft("a@example.com", self._draft(status="sent"))
        examples = repo.training_examples("a@example.com")
        assert examples[0]["description"] == ""
        assert examples[0]["contact_source"] == ""

    def test_scoped_per_user(self, repo):
        repo.add_draft("a@example.com", self._draft(status="sent"))
        assert repo.training_examples("b@example.com") == []
