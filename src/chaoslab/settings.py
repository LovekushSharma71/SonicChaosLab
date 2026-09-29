# SPDX-License-Identifier: Apache-2.0
"""Application settings: persisted config (never secrets) plus env-only API keys."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

Provider = Literal["fake", "anthropic", "gemini", "groq", "ollama"]
LabMode = Literal["mock", "local", "ssh"]

DEMO_QUESTION_BUDGET = 3

_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


class Settings(BaseModel):
    """User-tunable application settings. Secrets are read from the environment only."""

    provider: Provider = "fake"
    model: str = "claude-sonnet-4-5"
    lab_mode: LabMode = "mock"
    lab_host: str = ""
    max_questions: int = Field(default=11, ge=1)
    redirects_per_step: int = Field(default=2, ge=0)
    temperature_scripted: float = Field(default=0.0, ge=0.0, le=1.0)
    temperature_qna: float = Field(default=0.25, ge=0.0, le=1.0)
    demo_mode: bool = False
    gemini_model: str = "gemini-1.5-flash"
    groq_model: str = "llama-3.1-8b-instant"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    @property
    def question_budget(self) -> int:
        """Answered-question allowance for a lesson; demo mode forces a small budget."""
        return DEMO_QUESTION_BUDGET if self.demo_mode else self.max_questions


def load_env() -> None:
    """Load a local .env into the process environment if one is present."""
    load_dotenv()


def settings_dir() -> Path:
    """Directory holding persisted settings; overridable via CHAOSLAB_HOME (used in tests)."""
    override = os.environ.get("CHAOSLAB_HOME")
    return Path(override) if override else Path.home() / ".chaoslab"


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def load_settings() -> Settings:
    """Load persisted settings, falling back to defaults when none exist."""
    path = settings_path()
    if path.exists():
        return Settings.model_validate(json.loads(path.read_text()))
    return Settings()


def save_settings(settings: Settings) -> None:
    """Persist settings to disk. API keys are never included."""
    directory = settings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(settings.model_dump_json(indent=2))


def set_value(key: str, value: str) -> Settings:
    """Coerce and apply one setting, validate the result, then persist it."""
    settings = load_settings()
    data: dict[str, Any] = settings.model_dump()
    if key not in data:
        raise KeyError(key)
    data[key] = value
    updated = Settings.model_validate(data)
    save_settings(updated)
    return updated


def api_key(provider: str) -> str | None:
    """Return the environment-provided API key for a provider, if any."""
    var = _ENV_KEYS.get(provider)
    return os.environ.get(var) if var else None
