# SPDX-License-Identifier: Apache-2.0
"""Typed models for loaded curriculum data (cross-module payloads)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

StepKind = Literal["teach", "observe", "qna", "chaos_select", "restore"]
ChaosType = Literal["link", "config", "service", "churn"]
RiskLevel = Literal["low", "medium", "high"]
Difficulty = Literal["intro", "core", "advanced"]


class Step(BaseModel):
    id: str
    kind: StepKind
    title: str
    optional: bool = False


class ChaosOption(BaseModel):
    id: str
    label: str
    type: ChaosType
    inject: list[str]
    restore: list[str]
    expected_effects: list[str]
    risk: RiskLevel
    enabled: bool
    plan_b: str | None = None


class Commands(BaseModel):
    baseline: list[str]
    after_chaos: list[str]
    vocabulary: list[str]
    per_step: dict[str, list[str]] = {}


class Observe(BaseModel):
    facts: list[str]
    measure_recovery: bool = False
    key_fields: list[str] = []


class Qna(BaseModel):
    scope_keywords: list[str]
    suggested_questions: dict[str, list[str]]


class Meta(BaseModel):
    verified_on: str
    card_version: int
    author: str | None = None


class Misconception(BaseModel):
    misconception: str
    why_wrong: str
    correct_model: str


class Card(BaseModel):
    objective: str
    in_scope_subtopics: list[str]
    key_concepts: list[str]
    command_field_meanings: dict[str, dict[str, str]]
    healthy_state_expectations: str
    expected_chaos_effects: dict[str, str]
    common_misconceptions: list[Misconception]
    out_of_scope: list[str]
    redirect_line: str
    source_urls: list[str] = []


class Lesson(BaseModel):
    id: str
    title: str
    difficulty: Difficulty
    teach_file: str
    card_file: str
    steps: list[Step]
    commands: Commands
    chaos_options: list[ChaosOption]
    observe: Observe
    qna: Qna
    meta: Meta
    requires: list[str] = []
    demo_answers_file: str | None = None


class Curriculum(BaseModel):
    course: str
    version: int
    lessons: list[str]


class LoadedLesson(BaseModel):
    """A fully-resolved lesson: structure, knowledge card, and per-step teach text."""

    lesson: Lesson
    card: Card
    teach_sections: dict[str, str]
    directory: Path

    model_config = {"arbitrary_types_allowed": True}

    @property
    def id(self) -> str:
        return self.lesson.id

    @property
    def title(self) -> str:
        return self.lesson.title

    def chaos_by_id(self, chaos_id: str) -> ChaosOption | None:
        return next((c for c in self.lesson.chaos_options if c.id == chaos_id), None)

    def step_by_id(self, step_id: str) -> Step | None:
        return next((s for s in self.lesson.steps if s.id == step_id), None)
