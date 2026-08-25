from __future__ import annotations

import os

import pytest

# Set before any test module (e.g. tests/web) imports web_app, which builds a
# module-level Repository/SecretBox at import time — otherwise that first
# import warns about the insecure dev-default encryption key.
os.environ.setdefault("INTERNSHIP_AGENT_SECRET_KEY", "unit-test-secret-key-do-not-use-in-prod")

from internship_agent.crypto import SecretBox
from internship_agent.db import Database
from internship_agent.repository import Repository


@pytest.fixture(autouse=True)
def _test_secret_key(monkeypatch):
    """A fixed, explicit key so encryption tests are deterministic and don't
    warn about the insecure dev default."""
    monkeypatch.setenv("INTERNSHIP_AGENT_SECRET_KEY", "unit-test-secret-key-do-not-use-in-prod")


@pytest.fixture
def db(tmp_path):
    """A file-backed SQLite database (not `:memory:`): pipeline stages use a
    thread pool, and each thread gets its own connection — `:memory:` would
    give each thread an isolated, empty database instead of a shared one."""
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def secret_box():
    return SecretBox()


@pytest.fixture
def repo(db, secret_box):
    return Repository(db, secret_box)


@pytest.fixture
def resume_file(tmp_path):
    path = tmp_path / "jane_doe_resume.txt"
    path.write_text(
        "Jane Doe\n\n"
        "Education\nB.S. Computer Science\n\n"
        "Professional Experience\n"
        "Built a Python/Flask service with RAG search over internal docs. "
        "Used React and TypeScript for the frontend and SQL for storage.\n",
        encoding="utf-8",
    )
    return path
