# SPDX-License-Identifier: Apache-2.0
"""CLI tests: drive the guided loop headlessly with a scripted questionary fake."""

from __future__ import annotations

import pytest

from chaoslab import cli
from chaoslab.core.engine import build_orchestrator
from chaoslab.settings import Settings


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def _fake_select(message, choices=None, **kwargs):
    options = choices or []
    if any(isinstance(choice, str) and choice == "Continue" for choice in options):
        return _Answer("Continue")
    for choice in options:
        value = getattr(choice, "value", choice)
        if value == "__back__" or getattr(choice, "disabled", None):
            continue
        return _Answer(value)
    return _Answer(None)


@pytest.fixture
def scripted_questionary(monkeypatch):
    monkeypatch.setattr(cli.questionary, "select", _fake_select)
    monkeypatch.setattr(cli.questionary, "text", lambda *a, **k: _Answer(""))
    monkeypatch.setattr(cli.questionary, "confirm", lambda *a, **k: _Answer(True))


@pytest.mark.parametrize(
    "lesson_id", ["switches_explained", "inside_sonic", "bgp_reconvergence", "mtu_mismatch"]
)
def test_run_loop_completes(repo_root, scripted_questionary, lesson_id):
    orch = build_orchestrator(
        lesson_id,
        settings=Settings(provider="fake", lab_mode="mock"),
        lessons_dir=repo_root / "lessons",
        fixtures_dir=repo_root / "tests" / "fixtures" / "mock",
    )
    cli._run_loop(orch)
    assert orch.finished
