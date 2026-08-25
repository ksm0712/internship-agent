"""Runtime configuration: BYO API keys and the CLI's .env-backed fallback."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

MODEL_NAME = "gemini-2.5-flash-lite"


@dataclass(frozen=True)
class Config:
    tavily_api_key: str
    gemini_api_key: str
    hunter_api_key: str | None = None


def load_config(overrides: Config | None = None) -> Config:
    """Return explicit overrides (BYO-key web flow) or fall back to .env (CLI flow)."""
    if overrides:
        return overrides
    load_dotenv()
    tavily_key = os.getenv("TAVILY_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not tavily_key:
        raise RuntimeError("Missing TAVILY_API_KEY in .env")
    if not gemini_key:
        raise RuntimeError("Missing GEMINI_API_KEY in .env")
    return Config(
        tavily_api_key=tavily_key,
        gemini_api_key=gemini_key,
        hunter_api_key=os.getenv("HUNTER_API_KEY"),
    )
