# SPDX-License-Identifier: Apache-2.0
"""Orchestrator tests: full walkthrough, budgets, redirects, flush, and chaos handling."""

from __future__ import annotations

import pytest

from chaoslab.core.engine import build_orchestrator
from chaoslab.lab.chaos import ChaosError
from chaoslab.settings import Settings

LESSON_IDS = ["switches_explained", "inside_sonic", "bgp_reconvergence", "mtu_mismatch"]


def _settings(**overrides) -> Settings:
    base = {"provider": "fake", "lab_mode": "mock"}
    base.update(overrides)
    return Settings(**base)


def _orch(repo_root, lesson_id="bgp_reconvergence", **settings_overrides):
    return build_orchestrator(
        lesson_id,
        settings=_settings(**settings_overrides),
        lessons_dir=repo_root / "lessons",
        fixtures_dir=repo_root / "tests" / "fixtures" / "mock",
    )


def _goto(orch, kind):
    while orch.current_step.kind != kind and not orch.finished:
        orch.advance()


@pytest.mark.parametrize("lesson_id", LESSON_IDS)
def test_full_walk_completes(repo_root, lesson_id):
    orch = _orch(repo_root, lesson_id)
    guard = 0
    while not orch.finished and guard < 100:
        guard += 1
        step = orch.current_step
        if step.kind == "teach":
            assert orch.teach_text()
        elif step.kind == "observe":
            assert orch.observe().explanation
        elif step.kind == "qna":
            questions = orch.suggested_questions()
            if questions and orch.questions_left > 0:
                assert orch.ask(questions[0]).answered
        elif step.kind == "chaos_select":
            item = next(menu for menu in orch.chaos_menu() if menu.enabled)
            orch.select_chaos(item.id)
        elif step.kind == "restore":
            orch.restore()
        orch.advance()
    assert orch.finished


def test_bgp_chaos_diff_and_restore(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "chaos_select")
    outcome = orch.select_chaos("c_shut_one_link")
    assert outcome.changed_facts
    assert any("bgp:10.0.12.1" in fact for fact in outcome.changed_facts)
    assert outcome.explanation
    restore = orch.restore()
    assert restore.healed


def test_double_inject_refused(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "chaos_select")
    orch.select_chaos("c_shut_one_link")
    with pytest.raises(ChaosError):
        orch.select_chaos("c_shut_both_links")


def test_disabled_chaos_refused(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "chaos_select")
    with pytest.raises(ChaosError):
        orch.select_chaos("c_withdraw_only")  # enabled: false in the lesson


def test_question_budget_enforced(repo_root):
    orch = _orch(repo_root, demo_mode=True)  # budget = 3
    _goto(orch, "qna")
    assert orch.questions_left == 3
    for _ in range(3):
        result = orch.ask("What does bgp established mean?")
        assert result.answered
    exhausted = orch.ask("And what about the bgp route?")
    assert exhausted.budget_exhausted
    assert exhausted.should_advance
    assert not exhausted.answered


def test_redirect_cap_enforced(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "qna")
    first = orch.ask("please share a recipe for pasta carbonara")
    assert first.redirected and not first.should_advance
    second = orch.ask("what is the weather like in paris today")
    assert second.redirected and second.should_advance
    assert orch.questions_left == orch.question_budget  # redirects never spend budget


def test_memory_flush_on_advance(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "qna")
    orch.ask("What does bgp established mean?")
    assert orch.memory.pairs
    orch.advance()
    assert orch.memory.pairs == []


def test_experiment_command_path(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "qna")
    result = orch.experiment("leaf1: show bgp summary", explain=True)
    assert result.executed and result.relevant and result.safe
    assert "10.0.12.1" in result.output
    assert result.budget_spent


def test_experiment_rejects_mutation(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "qna")
    result = orch.experiment("leaf1: sudo config interface shutdown Ethernet0")
    assert not result.executed and not result.safe


def test_experiment_nl_proposes_then_confirms(repo_root):
    orch = _orch(repo_root)
    _goto(orch, "qna")
    proposal = orch.experiment("please check the bgp neighbors", confirm=False)
    assert not proposal.executed and proposal.proposed_command == "leaf1: show bgp summary"
    confirmed = orch.experiment("please check the bgp neighbors", explain=False, confirm=True)
    assert confirmed.executed


def test_config_set_batch_allowlist(repo_root):
    orch = _orch(repo_root)
    ok = orch.config_set("leaf1", ["config vlan add 200", "config vlan member add 200 Ethernet4"])
    assert ok.accepted and ok.rejected_line is None
    bad = orch.config_set("leaf1", ["config vlan add 200", "systemctl stop bgp"])
    assert not bad.accepted and bad.rejected_line == "systemctl stop bgp"
