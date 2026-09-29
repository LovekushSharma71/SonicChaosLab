# SPDX-License-Identifier: Apache-2.0
"""Loader and converter tests: happy path, cross-file rules, and round-trip."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import convert_lessons
from chaoslab.lessons import loader
from chaoslab.lessons.loader import LessonValidationError

EXPECTED_ORDER = ["switches_explained", "inside_sonic", "bgp_reconvergence", "mtu_mismatch"]


def _valid_lesson() -> dict:
    return {
        "id": "demo_lesson",
        "title": "Demo",
        "difficulty": "intro",
        "teach_file": "./teach.md",
        "card_file": "./card.yaml",
        "steps": [
            {"id": "t1", "kind": "teach", "title": "T", "optional": False},
            {"id": "o1", "kind": "observe", "title": "O", "optional": False},
            {"id": "q1", "kind": "qna", "title": "Q", "optional": False},
            {"id": "chaos", "kind": "chaos_select", "title": "C", "optional": False},
            {"id": "restore", "kind": "restore", "title": "R", "optional": False},
        ],
        "commands": {
            "baseline": ["leaf1: show x"],
            "after_chaos": ["leaf1: show x"],
            "per_step": {"o1": ["leaf1: show x"]},
            "vocabulary": ["show *", "ping*", "redis-cli *"],
        },
        "chaos_options": [
            {
                "id": "c_demo",
                "label": "Demo",
                "type": "config",
                "inject": ["leaf1: sudo config x"],
                "restore": ["leaf1: sudo config y"],
                "expected_effects": ["something happens"],
                "risk": "low",
                "enabled": True,
            }
        ],
        "observe": {"facts": ["ping_loss"], "measure_recovery": False},
        "qna": {
            "scope_keywords": ["a", "b", "c", "d", "e"],
            "suggested_questions": {"q1": ["one", "two", "three"]},
        },
        "meta": {"verified_on": "UNVERIFIED — x", "card_version": 1},
    }


def _valid_card() -> dict:
    return {
        "objective": "obj",
        "in_scope_subtopics": ["a", "b", "c"],
        "key_concepts": ["1", "2", "3", "4", "5"],
        "command_field_meanings": {"show x": {"meaning": "m"}},
        "healthy_state_expectations": "healthy",
        "expected_chaos_effects": {"c_demo": "effect"},
        "common_misconceptions": [
            {"misconception": f"m{i}", "why_wrong": "w", "correct_model": "c"} for i in range(5)
        ],
        "out_of_scope": ["z"],
        "redirect_line": "redirect",
    }


def _materialize(tmp_path: Path, lesson: dict, card: dict) -> Path:
    lessons_dir = tmp_path / "lessons"
    folder = lessons_dir / lesson["id"]
    folder.mkdir(parents=True)
    (folder / "lesson.yaml").write_text(yaml.safe_dump(lesson, allow_unicode=True))
    (folder / "card.yaml").write_text(yaml.safe_dump(card, allow_unicode=True))
    (folder / "teach.md").write_text("### SECTION: t1 — T\n\nbody text\n")
    (lessons_dir / "curriculum.yaml").write_text(
        yaml.safe_dump({"course": "c", "version": 1, "lessons": [f"{lesson['id']}/lesson.yaml"]})
    )
    return lessons_dir


def test_load_all_real(real_lessons_dir):
    loaded = loader.load_all(real_lessons_dir)
    assert [lesson.id for lesson in loaded] == EXPECTED_ORDER
    bgp = next(lesson for lesson in loaded if lesson.id == "bgp_reconvergence")
    assert bgp.teach_sections  # teach sections split by SECTION headers
    assert any(step.kind == "chaos_select" for step in bgp.lesson.steps)
    assert any(option.enabled for option in bgp.lesson.chaos_options)


def test_minimal_valid_loads(tmp_path):
    lessons_dir = _materialize(tmp_path, _valid_lesson(), _valid_card())
    loaded = loader.load_all(lessons_dir)
    assert len(loaded) == 1
    assert loaded[0].card.redirect_line == "redirect"


def test_id_folder_mismatch(tmp_path):
    lesson = _valid_lesson()
    lesson["id"] = "other_name"
    lessons_dir = tmp_path / "lessons"
    folder = lessons_dir / "demo_lesson"
    folder.mkdir(parents=True)
    (folder / "lesson.yaml").write_text(yaml.safe_dump(lesson, allow_unicode=True))
    (folder / "card.yaml").write_text(yaml.safe_dump(_valid_card(), allow_unicode=True))
    (folder / "teach.md").write_text("### SECTION: t1 — T\n\nbody\n")
    (lessons_dir / "curriculum.yaml").write_text(
        yaml.safe_dump({"course": "c", "version": 1, "lessons": ["demo_lesson/lesson.yaml"]})
    )
    with pytest.raises(LessonValidationError, match="folder name"):
        loader.load_all(lessons_dir)


def test_no_enabled_chaos(tmp_path):
    lesson = _valid_lesson()
    lesson["chaos_options"][0]["enabled"] = False
    lessons_dir = _materialize(tmp_path, lesson, _valid_card())
    with pytest.raises(LessonValidationError, match="no chaos option is enabled"):
        loader.load_all(lessons_dir)


def test_suggested_questions_bad_key(tmp_path):
    lesson = _valid_lesson()
    lesson["qna"]["suggested_questions"] = {"not_a_step": ["one", "two", "three"]}
    lessons_dir = _materialize(tmp_path, lesson, _valid_card())
    with pytest.raises(LessonValidationError, match="suggested_questions"):
        loader.load_all(lessons_dir)


def test_per_step_bad_key(tmp_path):
    lesson = _valid_lesson()
    lesson["commands"]["per_step"] = {"not_observe": ["leaf1: show x"]}
    lessons_dir = _materialize(tmp_path, lesson, _valid_card())
    with pytest.raises(LessonValidationError, match="per_step"):
        loader.load_all(lessons_dir)


def test_expected_effects_mismatch(tmp_path):
    card = _valid_card()
    card["expected_chaos_effects"] = {"c_wrong": "effect"}
    lessons_dir = _materialize(tmp_path, _valid_lesson(), card)
    with pytest.raises(LessonValidationError, match="expected_chaos_effects"):
        loader.load_all(lessons_dir)


def test_missing_chaos_select_step(tmp_path):
    lesson = _valid_lesson()
    lesson["steps"] = [s for s in lesson["steps"] if s["kind"] != "chaos_select"]
    lessons_dir = _materialize(tmp_path, lesson, _valid_card())
    with pytest.raises(LessonValidationError, match="chaos_select"):
        loader.load_all(lessons_dir)


def test_schema_violation_vocabulary(tmp_path):
    lesson = _valid_lesson()
    lesson["commands"]["vocabulary"] = ["only", "two"]
    lessons_dir = _materialize(tmp_path, lesson, _valid_card())
    with pytest.raises(LessonValidationError):
        loader.load_all(lessons_dir)


def test_converter_roundtrip(tmp_path, real_lessons_dir):
    staged = tmp_path / "lessons"
    staged.mkdir()
    for lesson_id in EXPECTED_ORDER:
        shutil.copy(real_lessons_dir / f"{lesson_id}.md", staged / f"{lesson_id}.md")
    assert convert_lessons.main(staged) == 0
    loaded = loader.load_all(staged)
    assert [lesson.id for lesson in loaded] == EXPECTED_ORDER
    for lesson_id in EXPECTED_ORDER:
        assert (staged / lesson_id / "lesson.yaml").exists()
        assert (staged / lesson_id / "card.yaml").exists()
        assert (staged / lesson_id / "teach.md").exists()
