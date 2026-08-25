from __future__ import annotations

import sys

import pytest

from internship_agent import cli


def _run(monkeypatch, argv, tmp_path):
    monkeypatch.setattr(cli, "DB_PATH", tmp_path / "cli-test.db")
    monkeypatch.setattr(sys, "argv", ["internship-agent", *argv])
    cli.main()


class TestCliDispatch:
    def test_search_command_calls_pipeline_and_prints_summary(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cli, "load_config", lambda: object())
        monkeypatch.setattr(
            cli, "search_internships", lambda limit, config, repo: [{"company": "Acme"}]
        )
        _run(monkeypatch, ["search", "--limit", "5"], tmp_path)
        assert "Found 1 new opportunities" in capsys.readouterr().out

    def test_contacts_command_calls_pipeline(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cli, "load_config", lambda: object())
        monkeypatch.setattr(cli, "find_contacts", lambda opportunities, config, repo: [{"email": "a@b.com"}])
        _run(monkeypatch, ["contacts"], tmp_path)
        assert "Resolved 1 contacts" in capsys.readouterr().out

    def test_draft_command_prompts_for_resume_and_drafts(self, monkeypatch, tmp_path, capsys, resume_file):
        monkeypatch.setattr(cli, "load_config", lambda: object())
        monkeypatch.setattr(cli, "prompt_for_resume", lambda path: resume_file)
        monkeypatch.setattr(
            cli, "draft_emails", lambda resume, contacts, limit, config, repo, user: [{"id": 1}]
        )
        _run(monkeypatch, ["draft", "--limit", "3"], tmp_path)
        assert "Drafted 1 emails" in capsys.readouterr().out

    def test_send_command_reviews_pending_drafts(self, monkeypatch, tmp_path, capsys):
        _run(monkeypatch, ["send"], tmp_path)
        assert "No pending drafts." in capsys.readouterr().out

    def test_stats_command_prints_aggregate_stats(self, monkeypatch, tmp_path, capsys):
        _run(monkeypatch, ["stats"], tmp_path)
        assert "Total runs: 0" in capsys.readouterr().out

    def test_unexpected_error_exits_nonzero(self, monkeypatch, tmp_path):
        def boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(cli, "load_config", boom)
        with pytest.raises(SystemExit) as exc_info:
            _run(monkeypatch, ["search"], tmp_path)
        assert exc_info.value.code == 1

    def test_review_and_send_sends_only_on_yes(self, monkeypatch, tmp_path):
        from internship_agent.db import Database
        from internship_agent.repository import Repository

        db = Database(tmp_path / "review.db")
        repo = Repository(db)
        draft_id = repo.add_draft(cli.CLI_USER_EMAIL, {"company": "Acme", "role": "AI Intern", "to": "hr@acme.com", "subject": "s", "body": "b"})

        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        monkeypatch.setattr(cli, "gmail_service", lambda creds, token: object())
        monkeypatch.setattr(cli, "send_message", lambda *a, **k: {"id": "msg-1"})

        cli.review_and_send(repo, tmp_path / "creds.json", tmp_path / "token.json")

        updated = repo.get_draft(cli.CLI_USER_EMAIL, draft_id)
        assert updated["status"] == "sent"
        assert updated["gmail_message_id"] == "msg-1"
        db.close()

    def test_review_and_send_skip_on_no(self, monkeypatch, tmp_path):
        from internship_agent.db import Database
        from internship_agent.repository import Repository

        db = Database(tmp_path / "review2.db")
        repo = Repository(db)
        draft_id = repo.add_draft(cli.CLI_USER_EMAIL, {"company": "Acme", "role": "AI Intern", "to": "hr@acme.com", "subject": "s", "body": "b"})

        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        cli.review_and_send(repo, tmp_path / "creds.json", tmp_path / "token.json")

        assert repo.get_draft(cli.CLI_USER_EMAIL, draft_id)["status"] == "skipped"
        db.close()
