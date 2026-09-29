#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Golden-set harness (§11 stub): eval the Q&A pipeline against tests/golden/golden_set.yaml.

For each case it drives the orchestrator to a Q&A step and checks routing: on-topic questions
must be answered, off-topic ones must be redirected by the code scope gate. Runs on the fake
provider by default (no network/keys). Prints a pass/fail summary; exit code 0 on all pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from chaoslab.core.engine import build_orchestrator  # noqa: E402
from chaoslab.settings import Settings  # noqa: E402

GOLDEN = REPO_ROOT / "tests" / "golden" / "golden_set.yaml"


class GoldenResult(BaseModel):
    lesson: str
    question: str
    on_topic: bool
    passed: bool
    detail: str


def _goto_qna(orch) -> None:
    while orch.current_step.kind != "qna" and not orch.finished:
        orch.advance()


def run_golden(
    settings: Settings | None = None, lessons_dir: Path | None = None
) -> list[GoldenResult]:
    settings = settings or Settings(provider="fake", lab_mode="mock")
    cases = yaml.safe_load(GOLDEN.read_text())["cases"]
    results: list[GoldenResult] = []
    for case in cases:
        orch = build_orchestrator(case["lesson"], settings, lessons_dir=lessons_dir)
        _goto_qna(orch)
        outcome = orch.ask(case["question"])
        if case["on_topic"]:
            passed = outcome.answered and not outcome.redirected
            detail = "answered" if passed else "expected an answer but was redirected"
        else:
            passed = outcome.redirected and not outcome.answered
            detail = "redirected" if passed else "expected a redirect but was answered"
        results.append(
            GoldenResult(
                lesson=case["lesson"],
                question=case["question"],
                on_topic=case["on_topic"],
                passed=passed,
                detail=detail,
            )
        )
    return results


def main() -> int:
    results = run_golden()
    passed = sum(1 for result in results if result.passed)
    print(f"golden set: {passed}/{len(results)} passed")
    for result in results:
        if not result.passed:
            print(f"  FAIL [{result.lesson}] {result.question} — {result.detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
