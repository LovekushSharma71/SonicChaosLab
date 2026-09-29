# SPDX-License-Identifier: Apache-2.0
"""Schema-validated loader for the curriculum: manifest, lessons, and knowledge cards."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

from .models import Card, Curriculum, Lesson, LoadedLesson
from .schema import SCHEMA_YAML

_SECTION_RE = re.compile(
    r"^###\s+SECTION:\s*(?P<id>[a-z0-9_]+)\s*—\s*(?P<title>.*)$",
    re.MULTILINE,
)


class LessonValidationError(Exception):
    """Raised when a curriculum file violates the schema or a cross-file rule."""


def resolve_lessons_dir(explicit: Path | str | None = None) -> Path:
    """Locate the curriculum directory: explicit arg, env, then walk up from CWD."""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_dir():
            raise LessonValidationError(f"lessons dir not found: {path}")
        return path
    env = os.environ.get("CHAOSLAB_LESSONS")
    if env:
        return Path(env)
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "lessons"
        if candidate.is_dir():
            return candidate
    raise LessonValidationError("could not locate a 'lessons/' directory")


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return yaml.safe_load(SCHEMA_YAML)


def _validator(defn: str) -> Draft7Validator:
    schema = _schema()
    return Draft7Validator({"$ref": f"#/$defs/{defn}", "$defs": schema["$defs"]})


def _validate(instance: Any, defn: str, source: Path) -> None:
    errors = sorted(_validator(defn).iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise LessonValidationError(f"{source}: at '{location}': {first.message}")


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise LessonValidationError(f"missing file: {path}")
    return yaml.safe_load(path.read_text())


def parse_teach_sections(text: str) -> dict[str, str]:
    """Split a teach.md body into {step_id: section_text} by SECTION headers."""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group("title").strip()
        body = text[start:end].strip()
        sections[match.group("id")] = f"{title}\n\n{body}" if body else title
    return sections


def load_curriculum(lessons_dir: Path) -> Curriculum:
    data = _read_yaml(lessons_dir / "curriculum.yaml")
    _validate(data, "curriculum", lessons_dir / "curriculum.yaml")
    return Curriculum.model_validate(data)


def _check_cross_file_rules(lesson: Lesson, card: Card, rel_path: str, directory: Path) -> None:
    source = directory / "lesson.yaml"

    folder = directory.name
    if lesson.id != folder:
        raise LessonValidationError(f"{source}: lesson id '{lesson.id}' != folder name '{folder}'")

    if not (directory / lesson.teach_file).exists():
        raise LessonValidationError(f"{source}: teach_file does not resolve: {lesson.teach_file}")
    if not (directory / lesson.card_file).exists():
        raise LessonValidationError(f"{source}: card_file does not resolve: {lesson.card_file}")

    step_ids = {s.id for s in lesson.steps}
    observe_ids = {s.id for s in lesson.steps if s.kind == "observe"}
    qna_ids = {s.id for s in lesson.steps if s.kind == "qna"}

    stray_sq = set(lesson.qna.suggested_questions) - qna_ids
    if stray_sq:
        raise LessonValidationError(
            f"{source}: suggested_questions keys not qna steps: {sorted(stray_sq)}"
        )

    stray_ps = set(lesson.commands.per_step) - observe_ids
    if stray_ps:
        raise LessonValidationError(
            f"{source}: commands.per_step keys not observe steps: {sorted(stray_ps)}"
        )

    chaos_ids = {c.id for c in lesson.chaos_options}
    effect_ids = set(card.expected_chaos_effects)
    if chaos_ids != effect_ids:
        raise LessonValidationError(
            f"{source}: expected_chaos_effects keys {sorted(effect_ids)} "
            f"!= chaos ids {sorted(chaos_ids)}"
        )

    if not any(c.enabled for c in lesson.chaos_options):
        raise LessonValidationError(f"{source}: no chaos option is enabled")

    kinds = [s.kind for s in lesson.steps]
    for required in ("teach", "observe", "qna"):
        if kinds.count(required) < 1:
            raise LessonValidationError(f"{source}: steps need at least one '{required}'")
    if kinds.count("chaos_select") != 1:
        raise LessonValidationError(f"{source}: steps need exactly one 'chaos_select'")
    if kinds.count("restore") != 1:
        raise LessonValidationError(f"{source}: steps need exactly one 'restore'")

    _ = step_ids, rel_path  # referenced for clarity; validation above covers them


def load_lesson(lessons_dir: Path, rel_path: str) -> LoadedLesson:
    """Load and fully validate one lesson referenced by the manifest."""
    lesson_path = lessons_dir / rel_path
    directory = lesson_path.parent

    lesson_data = _read_yaml(lesson_path)
    _validate(lesson_data, "lesson", lesson_path)
    lesson = Lesson.model_validate(lesson_data)

    card_path = directory / lesson.card_file
    card_data = _read_yaml(card_path)
    _validate(card_data, "card", card_path)
    card = Card.model_validate(card_data)

    _check_cross_file_rules(lesson, card, rel_path, directory)

    teach_text = (directory / lesson.teach_file).read_text()
    return LoadedLesson(
        lesson=lesson,
        card=card,
        teach_sections=parse_teach_sections(teach_text),
        directory=directory,
    )


def load_all(lessons_dir: Path | str | None = None) -> list[LoadedLesson]:
    """Load every lesson in catalogue (manifest) order, fully validated."""
    directory = resolve_lessons_dir(lessons_dir)
    curriculum = load_curriculum(directory)
    seen: set[str] = set()
    loaded: list[LoadedLesson] = []
    for rel_path in curriculum.lessons:
        lesson = load_lesson(directory, rel_path)
        if lesson.id in seen:
            raise LessonValidationError(f"duplicate lesson id across manifest: {lesson.id}")
        seen.add(lesson.id)
        loaded.append(lesson)
    return loaded
