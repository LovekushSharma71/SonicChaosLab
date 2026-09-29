# SPDX-License-Identifier: Apache-2.0
"""Request bodies for the §5.2 API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SessionCreate(BaseModel):
    lesson_id: str


class QuestionBody(BaseModel):
    text: str


class ChaosBody(BaseModel):
    option_id: str


class ExperimentBody(BaseModel):
    command: str
    confirm: bool = False
    explain: bool = True


class ConfigBody(BaseModel):
    lines: list[str]
    confirm: bool = False


class SettingsUpdate(BaseModel):
    values: dict[str, Any]
