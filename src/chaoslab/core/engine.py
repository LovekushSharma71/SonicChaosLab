# SPDX-License-Identifier: Apache-2.0
"""Factory helpers to build orchestrators and read the catalogue (shared by CLI and API)."""

from __future__ import annotations

from pathlib import Path

from ..lab.adapter import CommandResult, make_adapter
from ..lessons import loader
from ..lessons.models import LoadedLesson
from ..llm.guards import config_set_validate
from ..settings import Settings, load_settings, settings_dir
from .models import ConfigSetResult
from .orchestrator import Orchestrator


def catalogue(lessons_dir: Path | str | None = None) -> list[LoadedLesson]:
    """Load the full curriculum in catalogue order."""
    return loader.load_all(lessons_dir)


def build_orchestrator(
    lesson_id: str,
    settings: Settings | None = None,
    lessons_dir: Path | str | None = None,
    fixtures_dir: Path | None = None,
) -> Orchestrator:
    """Load a lesson and wire an orchestrator with the adapter selected by settings."""
    settings = settings or load_settings()
    lessons = loader.load_all(lessons_dir)
    loaded = next((lesson for lesson in lessons if lesson.id == lesson_id), None)
    if loaded is None:
        raise KeyError(f"unknown lesson: {lesson_id}")
    adapter = make_adapter(settings, fixtures_dir)
    return Orchestrator(loaded, adapter, settings)


def config_get(
    node: str, settings: Settings | None = None, fixtures_dir: Path | None = None
) -> CommandResult:
    """Dump a node's running configuration (CONFIG_DB view). Switch-level, not lesson-scoped."""
    settings = settings or load_settings()
    adapter = make_adapter(settings, fixtures_dir)
    return adapter.run(f"{node}: show runningconfiguration all")


def config_set(
    node: str,
    lines: list[str],
    settings: Settings | None = None,
    confirm: bool = True,
    fixtures_dir: Path | None = None,
) -> ConfigSetResult:
    """Validate a SONiC config set-family batch (one bad line rejects it) and apply on confirm."""
    settings = settings or load_settings()
    ok, bad = config_set_validate(lines)
    if not ok:
        return ConfigSetResult(node=node, accepted=False, rejected_line=bad, applied=[])
    if not confirm:
        return ConfigSetResult(node=node, accepted=True, rejected_line=None, applied=[])
    adapter = make_adapter(settings, fixtures_dir)
    applied = adapter.run_many([f"{node}: {line.strip()}" for line in lines])
    return ConfigSetResult(node=node, accepted=True, rejected_line=None, applied=applied)


def save_last_lesson(lesson_id: str) -> None:
    directory = settings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "last_lesson").write_text(lesson_id)


def load_last_lesson() -> str | None:
    path = settings_dir() / "last_lesson"
    return path.read_text().strip() if path.exists() else None
