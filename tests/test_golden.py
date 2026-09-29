# SPDX-License-Identifier: Apache-2.0
"""Golden-set harness test: the fake provider must route every case correctly."""

from __future__ import annotations

from run_golden import run_golden


def test_golden_set_routes_correctly(repo_root):
    results = run_golden(lessons_dir=repo_root / "lessons")
    assert len(results) == 20
    failed = [result.question for result in results if not result.passed]
    assert not failed, failed
