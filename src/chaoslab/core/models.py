# SPDX-License-Identifier: Apache-2.0
"""Typed payloads exchanged between the orchestrator and its clients (CLI, API)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..lab.adapter import CommandResult


class SessionStatus(BaseModel):
    session_id: str
    lesson_id: str
    lesson_title: str
    step_index: int
    step_count: int
    step_id: str
    step_kind: str
    step_title: str
    step_optional: bool
    questions_left: int
    redirects_used: int
    redirects_cap: int
    active_chaos: str | None
    finished: bool


class ObserveResult(BaseModel):
    step_id: str
    commands: list[str]
    outputs: list[CommandResult]
    facts: dict[str, str]
    explanation: str
    fallback: bool = False


class QnAResult(BaseModel):
    answered: bool
    redirected: bool
    text: str
    questions_left: int
    budget_exhausted: bool
    should_advance: bool
    fallback: bool = False


class ChaosMenuItem(BaseModel):
    id: str
    label: str
    type: str
    risk: str
    enabled: bool


class ChaosOutcome(BaseModel):
    chaos_id: str
    label: str
    inject_commands: list[str]
    changed_facts: list[str]
    explanation: str
    fallback: bool = False


class RestoreOutcome(BaseModel):
    chaos_id: str
    restore_commands: list[str]
    residual_changes: list[str]
    healed: bool
    recovery_seconds: float = 0.0


class ExperimentResult(BaseModel):
    command: str
    proposed_command: str | None = None
    relevant: bool
    safe: bool
    executed: bool
    redirected: bool
    output: str = ""
    explanation: str = ""
    budget_spent: bool = False
    questions_left: int = 0
    fallback: bool = False


class ConfigSetResult(BaseModel):
    node: str
    accepted: bool
    rejected_line: str | None
    applied: list[CommandResult] = []


class StreamEvent(BaseModel):
    """One SSE/console event while streaming a Q&A or experiment explanation."""

    kind: Literal["token", "redirect", "budget", "verdict", "done", "error"]
    text: str = ""
    ok: bool | None = None
    questions_left: int | None = None
    should_advance: bool | None = None
