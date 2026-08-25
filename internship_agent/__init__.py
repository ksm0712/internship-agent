"""Internship Agent: a search -> contact resolution -> drafting pipeline with
a human-in-the-loop Gmail send step, backed by SQLite instead of flat JSON
files.
"""
from internship_agent.config import Config, load_config
from internship_agent.paths import (
    DB_PATH,
    DEFAULT_CREDENTIALS_FILE,
    DEFAULT_TOKEN_FILE,
    ROOT,
    UPLOAD_DIR,
    WEB_TOKEN_FILE,
    ensure_dirs,
)

__all__ = [
    "Config",
    "load_config",
    "DB_PATH",
    "DEFAULT_CREDENTIALS_FILE",
    "DEFAULT_TOKEN_FILE",
    "ROOT",
    "UPLOAD_DIR",
    "WEB_TOKEN_FILE",
    "ensure_dirs",
]
