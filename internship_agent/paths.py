"""Filesystem layout shared across the CLI, web app, and pipeline modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"
UPLOAD_DIR = ROOT / "uploads"

DB_PATH = DATA_DIR / "internship_agent.db"
DEFAULT_TOKEN_FILE = ROOT / "token.json"
DEFAULT_CREDENTIALS_FILE = ROOT / "credentials.json"
WEB_TOKEN_FILE = DATA_DIR / "web_google_token.json"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
