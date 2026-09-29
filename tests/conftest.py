# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures. Keeps tests hermetic: no network, no lab, no keys."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def real_lessons_dir() -> Path:
    return REPO_ROOT / "lessons"


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    """Point settings and lessons resolution at throwaway locations by default."""
    monkeypatch.setenv("CHAOSLAB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CHAOSLAB_LESSONS", raising=False)
