from __future__ import annotations

import pytest

import web_app
from internship_agent.db import Database
from internship_agent.repository import Repository


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = Database(tmp_path / "test.db")
    test_repo = Repository(test_db)
    monkeypatch.setattr(web_app, "db", test_db)
    monkeypatch.setattr(web_app, "repo", test_repo)
    monkeypatch.setattr(web_app, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(web_app, "ensure_dirs", lambda: (tmp_path / "uploads").mkdir(exist_ok=True))
    web_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with web_app.app.test_client() as test_client:
        yield test_client
    test_db.close()


@pytest.fixture
def signed_in_client(client):
    with client.session_transaction() as session:
        session["user"] = {"email": "a@example.com", "name": "A User"}
    return client
