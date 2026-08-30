from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    model: str
    model_cheap: str
    github_token: str | None


_cached: Config | None = None


def load_config() -> Config:
    """Load config from the environment. Cached after first call."""
    global _cached
    if _cached is not None:
        return _cached
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    _cached = Config(
        openai_api_key=key,
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        model_cheap=os.environ.get("OPENAI_MODEL_CHEAP", "gpt-4o-mini"),
        github_token=os.environ.get("GITHUB_TOKEN"),
    )
    return _cached
