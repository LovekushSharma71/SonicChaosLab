# SPDX-License-Identifier: Apache-2.0
"""The lesson orchestrator: a deterministic state machine over a lesson's steps.

It owns all business logic (CLI and API are thin clients): the step cursor, the question budget
(11/lesson, 3 in demo mode), the per-step redirect allowance (2), step-scoped memory flushing,
chaos injection via the engine, and the four LLM touchpoints (baseline/impact explanations, Q&A,
and experiment explanations). The model never counts, gates, or decides flow (PRODUCT.md §4).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

from ..lab.adapter import CommandResult, DeviceAdapter, parse_spec
from ..lab.chaos import ChaosEngine, ChaosError
from ..lab.metrics import Snapshot, collect_snapshot, diff_snapshots
from ..lessons.models import LoadedLesson
from ..llm.context import ChatMemory, assemble_context
from ..llm.guards import (
    check_scripted,
    config_set_validate,
    experiment_relevance,
    experiment_safety,
    scope_gate,
    tiered_fallback,
)
from ..llm.model import FallbackExhausted, safe_generate, safe_stream
from ..settings import Settings
from .demo import DemoStore, load_demo_store
from .models import (
    ChaosMenuItem,
    ChaosOutcome,
    ConfigSetResult,
    ExperimentResult,
    ObserveResult,
    QnAResult,
    RestoreOutcome,
    SessionStatus,
    StreamEvent,
)

_TARGET_PREFIX = re.compile(r"^(leaf1|leaf2|h1|h2):")
_PROPOSAL_MAP = [
    ("bgp", "leaf1: show bgp summary"),
    ("neighbor", "leaf1: show bgp summary"),
    ("route", "leaf1: show ip route"),
    ("ecmp", "leaf1: show ip route 10.0.2.0/24"),
    ("mac", "leaf1: show mac"),
    ("vlan", "leaf1: show vlan brief"),
    ("lldp", "leaf1: show lldp table"),
    ("interface", "leaf1: show interfaces status"),
    ("port", "leaf1: show interfaces status"),
    ("mtu", "leaf1: show interfaces status"),
    ("feature", "leaf1: show feature status"),
    ("container", "leaf1: docker ps"),
]


def _snapshot_lines(snapshot: Snapshot) -> list[str]:
    return [f"{key}: {value}" for key, value in sorted(snapshot.values.items())]


class Orchestrator:
    """Drives one lesson end to end; the single source of truth for session state."""

    def __init__(
        self,
        loaded: LoadedLesson,
        adapter: DeviceAdapter,
        settings: Settings,
        session_id: str | None = None,
    ) -> None:
        self.loaded = loaded
        self.lesson = loaded.lesson
        self.card = loaded.card
        self.adapter = adapter
        self.settings = settings
        self.chaos = ChaosEngine(adapter)
        self.memory = ChatMemory()
        self.demo: DemoStore | None = load_demo_store() if settings.demo_mode else None
        self.session_id = session_id or uuid.uuid4().hex
        self.step_index = 0
        self.questions_used = 0
        self.redirects_used = 0
        self.before_snapshot: Snapshot | None = None
        self.last_diff: list[str] = []
        self.last_state_lines: list[str] = []
        self.last_explanation: str = ""
        self.last_fallback: bool = False
        self.finished = False

    # ------------------------------------------------------------------ state
    @property
    def current_step(self):
        return self.lesson.steps[self.step_index]

    @property
    def question_budget(self) -> int:
        return self.settings.question_budget

    @property
    def questions_left(self) -> int:
        return max(0, self.question_budget - self.questions_used)

    @property
    def redirect_cap(self) -> int:
        return self.settings.redirects_per_step

    def status(self) -> SessionStatus:
        step = self.current_step
        return SessionStatus(
            session_id=self.session_id,
            lesson_id=self.lesson.id,
            lesson_title=self.lesson.title,
            step_index=self.step_index,
            step_count=len(self.lesson.steps),
            step_id=step.id,
            step_kind=step.kind,
            step_title=step.title,
            step_optional=step.optional,
            questions_left=self.questions_left,
            redirects_used=self.redirects_used,
            redirects_cap=self.redirect_cap,
            active_chaos=self.chaos.active_id,
            finished=self.finished,
        )

    def advance(self) -> SessionStatus:
        """Move to the next step, flushing step-scoped memory and resetting redirects."""
        self.memory.flush_step()
        self.redirects_used = 0
        if self.step_index < len(self.lesson.steps) - 1:
            self.step_index += 1
        else:
            self.finished = True
        return self.status()

    # ------------------------------------------------------------------ teach
    def teach_text(self) -> str:
        return self.loaded.teach_sections.get(self.current_step.id, self.current_step.title)

    def _demo_observe(self, step_id: str) -> str | None:
        if not self.demo:
            return None
        return self.demo.observe(self.lesson.id, step_id, self.lesson.meta.card_version)

    def _demo_impact(self, chaos_id: str) -> str | None:
        if not self.demo:
            return None
        return self.demo.impact(self.lesson.id, chaos_id, self.lesson.meta.card_version)

    # ---------------------------------------------------------------- observe
    def _commands_for_step(self, step_id: str) -> list[str]:
        return self.lesson.commands.per_step.get(step_id, self.lesson.commands.baseline)

    def observe(self) -> ObserveResult:
        step = self.current_step
        commands = self._commands_for_step(step.id)
        outputs = self.adapter.run_many(commands)
        snapshot = collect_snapshot(outputs)
        state_lines = _snapshot_lines(snapshot)
        self.last_state_lines = state_lines
        canned = self._demo_observe(step.id)
        if canned is not None:
            explanation, fallback = canned, False
        else:
            explanation, fallback = self._scripted_explain(state_lines, "explain_baseline")
        self.last_explanation = explanation
        self.last_fallback = fallback
        return ObserveResult(
            step_id=step.id,
            commands=commands,
            outputs=outputs,
            facts=snapshot.values,
            explanation=explanation,
            fallback=fallback,
        )

    def _scripted_explain(
        self,
        state_lines: list[str],
        mode: str,
        chaos_id: str | None = None,
        chaos_effect: str | None = None,
    ) -> tuple[str, bool]:
        request = assemble_context(
            self.card, state_lines, self.memory, mode, chaos_effect=chaos_effect
        )
        try:
            text = safe_generate(request, self.settings)
        except FallbackExhausted:
            return tiered_fallback(self.card, mode, chaos_id), True
        ok, _ = check_scripted(text, state_lines)
        if not ok:
            return tiered_fallback(self.card, mode, chaos_id), True
        return text, False

    # -------------------------------------------------------------------- qna
    def suggested_questions(self) -> list[str]:
        return self.lesson.qna.suggested_questions.get(self.current_step.id, [])

    def _gate_question(self, text: str) -> str:
        vetted = text.strip() in self.suggested_questions()
        if not vetted and not scope_gate(text, self.lesson.qna.scope_keywords):
            self.redirects_used += 1
            return "redirect"
        if self.questions_left <= 0:
            return "budget"
        return "answer"

    def stream_question(self, text: str) -> Iterator[StreamEvent]:
        decision = self._gate_question(text)
        if decision == "redirect":
            yield StreamEvent(
                kind="redirect",
                text=self.card.redirect_line,
                should_advance=self.redirects_used >= self.redirect_cap,
            )
            return
        if decision == "budget":
            yield StreamEvent(
                kind="budget",
                text="Question budget reached for this lesson; moving on.",
                should_advance=True,
            )
            return

        request = assemble_context(
            self.card, self.last_state_lines, self.memory, "qna", question=text
        )
        accumulated = ""
        fallback = False
        try:
            for token in safe_stream(request, self.settings):
                accumulated += token
                yield StreamEvent(kind="token", text=token)
        except FallbackExhausted:
            accumulated = tiered_fallback(self.card, "qna")
            fallback = True
            yield StreamEvent(kind="token", text=accumulated)

        self.questions_used += 1
        self.memory.add(text, accumulated)
        ok, reason = check_scripted(accumulated, [])
        verdict = "validated" if ok else f"unverified: {reason}"
        if fallback:
            verdict = "from lesson notes (model unavailable)"
        yield StreamEvent(
            kind="verdict",
            text=verdict,
            ok=ok,
            questions_left=self.questions_left,
            should_advance=self.questions_left <= 0,
        )

    def ask(self, text: str) -> QnAResult:
        redirected = budget = answered = fallback = should_advance = False
        parts: list[str] = []
        for event in self.stream_question(text):
            if event.kind == "redirect":
                redirected = True
                parts = [event.text]
                should_advance = bool(event.should_advance)
            elif event.kind == "budget":
                budget = True
                parts = [event.text]
                should_advance = True
            elif event.kind == "token":
                parts.append(event.text)
            elif event.kind == "verdict":
                answered = not redirected and not budget
                should_advance = bool(event.should_advance)
                fallback = "lesson notes" in event.text
        return QnAResult(
            answered=answered,
            redirected=redirected,
            text="".join(parts).strip(),
            questions_left=self.questions_left,
            budget_exhausted=budget or self.questions_left <= 0,
            should_advance=should_advance,
            fallback=fallback,
        )

    # ------------------------------------------------------------------ chaos
    def snapshot(self) -> Snapshot:
        """Capture current facts using the after-chaos probe set (baseline or post-chaos)."""
        return collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))

    def chaos_menu(self) -> list[ChaosMenuItem]:
        return [
            ChaosMenuItem(id=c.id, label=c.label, type=c.type, risk=c.risk, enabled=c.enabled)
            for c in self.lesson.chaos_options
        ]

    def select_chaos(self, option_id: str) -> ChaosOutcome:
        option = self.loaded.chaos_by_id(option_id)
        if option is None:
            raise KeyError(f"unknown chaos option: {option_id}")
        before = collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))
        self.chaos.inject(option)
        after = collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))
        changed = diff_snapshots(before, after)
        self.before_snapshot = before
        self.last_diff = changed
        self.last_state_lines = changed or _snapshot_lines(after)
        effect = self.card.expected_chaos_effects.get(option_id)
        canned = self._demo_impact(option_id)
        if canned is not None:
            explanation, fallback = canned, False
        else:
            explanation, fallback = self._scripted_explain(
                changed, "explain_impact", chaos_id=option_id, chaos_effect=effect
            )
        self.last_explanation = explanation
        self.last_fallback = fallback
        return ChaosOutcome(
            chaos_id=option_id,
            label=option.label,
            inject_commands=option.inject,
            changed_facts=changed,
            explanation=explanation,
            fallback=fallback,
        )

    def restore(self) -> RestoreOutcome:
        active = self.chaos.active
        if active is None:
            return RestoreOutcome(
                chaos_id="", restore_commands=[], residual_changes=[], healed=True
            )
        self.chaos.restore(active)
        verify = collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))
        residual = diff_snapshots(self.before_snapshot, verify) if self.before_snapshot else []
        self.last_state_lines = _snapshot_lines(verify)
        return RestoreOutcome(
            chaos_id=active.id,
            restore_commands=active.restore,
            residual_changes=residual,
            healed=not residual,
        )

    def reset_lab(self) -> None:
        """Restore any active chaos and clear engine/adapter state (baseline restore)."""
        if self.chaos.active is not None:
            try:
                self.chaos.restore(self.chaos.active)
            except ChaosError:
                pass
        self.chaos.reset()

    # ------------------------------------------------------------- experiment
    def _propose_command(self, instruction: str) -> str | None:
        low = instruction.lower()
        for keyword, command in _PROPOSAL_MAP:
            if keyword in low:
                return command
        return None

    def experiment(
        self, text: str, explain: bool = True, confirm: bool = False
    ) -> ExperimentResult:
        text = text.strip()
        proposed: str | None = None
        if not _TARGET_PREFIX.match(text):
            proposed = self._propose_command(text)
            if proposed is None:
                return ExperimentResult(
                    command=text,
                    relevant=False,
                    safe=False,
                    executed=False,
                    redirected=True,
                    output=self.card.redirect_line,
                    questions_left=self.questions_left,
                )
            if not confirm:
                return ExperimentResult(
                    command=text,
                    proposed_command=proposed,
                    relevant=True,
                    safe=True,
                    executed=False,
                    redirected=False,
                    output="Proposed command shown; re-run with confirmation to execute.",
                    questions_left=self.questions_left,
                )
            text = proposed

        target, command = parse_spec(text)
        relevant = experiment_relevance(command, self.lesson.commands.vocabulary)
        safe = experiment_safety(command)
        if not relevant:
            self.redirects_used += 1
            return ExperimentResult(
                command=text,
                proposed_command=proposed,
                relevant=False,
                safe=safe,
                executed=False,
                redirected=True,
                output=self.card.redirect_line,
                questions_left=self.questions_left,
            )
        if not safe:
            return ExperimentResult(
                command=text,
                proposed_command=proposed,
                relevant=True,
                safe=False,
                executed=False,
                redirected=False,
                output="Refused: experiment mode runs read-only diagnostics only.",
                questions_left=self.questions_left,
            )

        result = self.adapter.run(text)
        explanation = ""
        budget_spent = False
        fallback = False
        if explain and self.questions_left > 0:
            state_lines = _snapshot_lines(collect_snapshot([result]))
            explanation, fallback = self._scripted_explain(state_lines, "experiment")
            self.questions_used += 1
            budget_spent = True
        return ExperimentResult(
            command=text,
            proposed_command=proposed,
            relevant=True,
            safe=True,
            executed=True,
            redirected=False,
            output=result.raw,
            explanation=explanation,
            budget_spent=budget_spent,
            questions_left=self.questions_left,
            fallback=fallback,
        )

    # ----------------------------------------------------------------- config
    def config_get(self, node: str) -> CommandResult:
        return self.adapter.run(f"{node}: show runningconfiguration all")

    def config_set(self, node: str, lines: list[str], confirm: bool = True) -> ConfigSetResult:
        ok, bad = config_set_validate(lines)
        if not ok:
            return ConfigSetResult(node=node, accepted=False, rejected_line=bad, applied=[])
        if not confirm:
            return ConfigSetResult(node=node, accepted=True, rejected_line=None, applied=[])
        applied = self.adapter.run_many([f"{node}: {line.strip()}" for line in lines])
        return ConfigSetResult(node=node, accepted=True, rejected_line=None, applied=applied)
