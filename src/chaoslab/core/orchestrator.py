# SPDX-License-Identifier: Apache-2.0
"""The lesson orchestrator: a deterministic state machine over a lesson's steps.

It owns all business logic (CLI and API are thin clients): the step cursor, the question budget
(11/lesson, 3 in demo mode), the per-step redirect allowance (2), lesson-scoped chat memory
(last 3 Q&A persist across steps, flushed between lessons), chaos injection via the engine, and
the four LLM touchpoints (baseline/impact explanations, Q&A, and experiment explanations). The
model never counts, gates, or decides flow (PRODUCT.md §4).
"""

from __future__ import annotations

import re
import time
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

_TARGET_PREFIX = re.compile(r"^(leaf1|leaf2|h1|h2|h3|h4):")


def _ping_residual(snapshot: Snapshot) -> list[str]:
    """Ping facts that are not clean (non-zero loss) — health check for escalated heals."""
    return [
        f"{key}: {value}"
        for key, value in sorted(snapshot.values.items())
        if ":ping:" in key and not value.startswith("0%")
    ]


def _true_residual(before: Snapshot | None, verify: Snapshot) -> list[str]:
    """Diff vs before, minus pings that landed clean — a degraded 'before' (cold ARP,
    wedged vs pump) must not count 0% loss as residue and force escalation."""
    if before is None:
        return []
    return [
        change
        for change in diff_snapshots(before, verify)
        if not (":ping:" in change and change.endswith("→ 0% loss"))
    ]
# Experiment gates are code-disabled until the real-LLM milestone (user decision 2026-09-30);
# guards.py keeps the implementations unit-tested for the re-enable.
_GATES_ENABLED = False
_NOT_IMPLEMENTED = (
    "(not implemented — grounded explanations arrive with a real LLM provider; "
    "try: settings set provider anthropic)"
)
_PROPOSAL_MAP = [
    ("bgp", 'leaf1: vtysh -c "show bgp summary"'),
    ("neighbor", 'leaf1: vtysh -c "show bgp summary"'),
    ("route", "leaf1: show ip route"),
    ("ecmp", "leaf1: show ip route 10.0.2.0/24"),
    ("mac", "leaf1: show mac"),
    ("vlan", "leaf1: show vlan brief"),
    ("lldp", "leaf1: show lldp table"),
    ("interface", "leaf1: show interfaces status"),
    ("port", "leaf1: show interfaces status"),
    ("mtu", "leaf1: show interfaces status"),
    ("feature", "leaf1: show feature status"),
    ("container", "leaf1: supervisorctl status"),
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
        """Move to the next step, resetting redirects. Chat memory (last 3 Q&A) persists across
        steps within the lesson; it is flushed between lessons by building a fresh orchestrator."""
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
            explanation, fallback = self._scripted_explain(
                state_lines, "explain_baseline", focus_commands=commands
            )
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
        focus_commands: list[str] | None = None,
    ) -> tuple[str, bool]:
        if self.settings.provider == "fake":
            return _NOT_IMPLEMENTED, False
        request = assemble_context(
            self.card, state_lines, self.memory, mode, chaos_effect=chaos_effect
        )
        try:
            text = safe_generate(request, self.settings)
        except FallbackExhausted:
            return tiered_fallback(self.card, mode, chaos_id, focus_commands), True
        ok, _ = check_scripted(text, state_lines)
        if not ok:
            return tiered_fallback(self.card, mode, chaos_id, focus_commands), True
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

        if self.settings.provider == "fake":
            self.questions_used += 1
            self.memory.add(text, _NOT_IMPLEMENTED)
            yield StreamEvent(kind="token", text=_NOT_IMPLEMENTED)
            yield StreamEvent(
                kind="verdict",
                text="placeholder (no LLM provider configured)",
                ok=True,
                questions_left=self.questions_left,
                should_advance=self.questions_left <= 0,
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
        verdict = (
            "validated"
            if ok
            else f"unverified ({reason}) — treat with suspicion and cross-check the facts above"
        )
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
        if self.settings.lab_mode != "mock":
            # Heal-then-warm: a wedged vs pump or cold ARP/FDB must not contaminate the
            # baseline snapshot (a degraded 'before' fakes residuals after restore).
            from ..lab.topology import verify_dataplane

            verify_dataplane(self.adapter)
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
        started = time.monotonic()
        self.chaos.restore(active)
        verify = collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))
        residual = _true_residual(self.before_snapshot, verify)
        # Measured reconvergence: on a live lab, poll until the baseline facts return.
        if residual and self.lesson.observe.measure_recovery and self.settings.lab_mode != "mock":
            deadline = time.monotonic() + 30
            while residual and time.monotonic() < deadline:
                time.sleep(2)
                verify = collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))
                residual = _true_residual(self.before_snapshot, verify)
        recovery_seconds = round(time.monotonic() - started, 2)
        escalated = False
        if self.settings.lab_mode != "mock" and _ping_residual(verify):
            # A contaminated 'before' snapshot (lab broken pre-chaos) makes 100%→100%
            # diff as "no change" — dirty pings must force escalation regardless.
            residual = residual + [
                r for r in _ping_residual(verify) if r not in residual
            ]
        if residual and self.settings.lab_mode != "mock":
            # §7 invariant: restore must land back at the lab-up baseline. Escalate in
            # tiers: cheap dataplane un-wedge first (a sonic-vs port flap wedges syncd's
            # tap→veth pump one-way; only a veth bounce re-arms it), then the full
            # shared apply_baseline primitive.
            from ..lab.topology import apply_baseline, verify_dataplane

            escalated = True
            verify_dataplane(self.adapter)
            verify = collect_snapshot(self.adapter.run_many(self.lesson.commands.after_chaos))
            residual = _true_residual(self.before_snapshot, verify)
            if residual:
                apply_baseline(self.adapter)
                # apply_baseline asserts config consumed + BGP Established. The canonical
                # baseline is now the reference — the session's 'before' snapshot may
                # itself have captured a degraded lab. config-load churn re-wedges vs
                # pumps and re-arms can land late, so keep re-bouncing dead paths until
                # every lesson ping probe is clean.
                deadline = time.monotonic() + 180
                while True:
                    verify = collect_snapshot(
                        self.adapter.run_many(self.lesson.commands.after_chaos)
                    )
                    residual = _ping_residual(verify)
                    if not residual or time.monotonic() > deadline:
                        break
                    verify_dataplane(self.adapter)
                if not residual:
                    self.before_snapshot = verify
            recovery_seconds = round(time.monotonic() - started, 2)
        self.last_state_lines = _snapshot_lines(verify)
        return RestoreOutcome(
            chaos_id=active.id,
            restore_commands=active.restore,
            residual_changes=residual,
            healed=not residual,
            recovery_seconds=recovery_seconds,
            escalated=escalated,
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
                self.redirects_used += 1  # off-topic experiments count against the redirect cap
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

        try:
            target, command = parse_spec(text)
        except ValueError as exc:
            return ExperimentResult(
                command=text,
                proposed_command=proposed,
                relevant=False,
                safe=False,
                executed=False,
                redirected=False,
                output=f"Refused: {exc}",
                questions_left=self.questions_left,
            )
        relevant = experiment_relevance(command, self.lesson.commands.vocabulary)
        safe = experiment_safety(command)
        if _GATES_ENABLED and not relevant:
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
        if _GATES_ENABLED and not safe:
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
            relevant=relevant,
            safe=safe,
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
