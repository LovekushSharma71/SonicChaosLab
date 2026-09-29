# SPDX-License-Identifier: Apache-2.0
"""Context assembly (§8.4) and step-scoped chat memory (§8.5).

The assembler builds a fixed-shape prompt so lesson N can never bleed into lesson N+1: a shared
system persona with grounding rules, the lesson card, the current structured facts, a rolling
summary, and a short chat buffer. Live facts are authoritative; the card overrides model memory.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..lessons.models import Card
from .model import GenerationRequest

Mode = Literal["explain_baseline", "explain_impact", "qna", "experiment"]

SYSTEM_PERSONA = (
    "You are a precise SONiC networking tutor embedded in a live lab. Grounding rules: the live "
    "facts provided are the source of truth and must never be contradicted; the lesson card "
    "overrides your own memory on lab specifics; general networking knowledge is allowed within "
    "the lesson topic and must be labelled 'general background' when it goes beyond the card; if "
    "input is off-topic, steer back with the card's redirect line; keep answers to at most four "
    "sentences unless asked to go deeper; always reference the exact observed values."
)

_MAX_CONTEXT_CHARS = 3600


class QAPair(BaseModel):
    question: str
    answer: str


class ChatMemory:
    """Step-scoped buffer: last few Q&A verbatim plus a rolling summary that crosses steps."""

    def __init__(self, max_pairs: int = 3) -> None:
        self.max_pairs = max_pairs
        self.pairs: list[QAPair] = []
        self.summary: list[str] = []

    def add(self, question: str, answer: str) -> None:
        self.pairs.append(QAPair(question=question, answer=answer))
        while len(self.pairs) > self.max_pairs:
            old = self.pairs.pop(0)
            self.summary.append(f"earlier: {old.question.strip()[:80]}")
            self.summary = self.summary[-3:]

    def flush_step(self) -> None:
        """Drop the verbatim buffer at a step change; the rolling summary survives."""
        for pair in self.pairs:
            self.summary.append(f"earlier: {pair.question.strip()[:80]}")
        self.summary = self.summary[-3:]
        self.pairs = []

    def render_chat(self) -> str:
        return "\n".join(f"Q: {p.question}\nA: {p.answer}" for p in self.pairs)

    def render_summary(self) -> str:
        return "\n".join(f"- {line}" for line in self.summary)


def _card_block(card: Card, chaos_effect: str | None) -> str:
    parts = [f"objective: {card.objective}", "key concepts:"]
    parts += [f"- {concept}" for concept in card.key_concepts[:6]]
    parts.append(f"healthy expectations: {card.healthy_state_expectations}")
    if chaos_effect:
        parts.append(f"expected chaos effect: {chaos_effect}")
    parts.append(f"redirect line: {card.redirect_line}")
    return "\n".join(parts)


def assemble_context(
    card: Card,
    state_lines: list[str],
    memory: ChatMemory,
    mode: Mode,
    question: str | None = None,
    chaos_effect: str | None = None,
) -> GenerationRequest:
    """Build the fixed-shape request for one LLM touchpoint."""
    blocks = [
        "[card]",
        _card_block(card, chaos_effect),
        "[state]",
        "\n".join(f"- {line}" for line in state_lines) if state_lines else "- (no facts captured)",
        "[summary]",
        memory.render_summary() or "- (none)",
        "[chat]",
        memory.render_chat() or "(none)",
    ]
    context = "\n".join(blocks)
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS] + "\n…(truncated)"

    task = _task_for(mode, question)
    temperature = 0.25 if mode == "qna" else 0.0
    return GenerationRequest(
        system=SYSTEM_PERSONA, context=context, task=task, temperature=temperature
    )


def _task_for(mode: Mode, question: str | None) -> str:
    if mode == "qna":
        return (
            f"QUESTION: {question or ''}\n"
            "Answer grounded in the facts and card above; label anything beyond the card as "
            "'general background'."
        )
    if mode == "explain_impact":
        return (
            "TASK: Explain what changed and why, field by field, grounded strictly in the changed "
            "facts above. Reference exact values."
        )
    if mode == "experiment":
        return (
            "TASK: Explain this command's output field by field, grounded strictly in the facts "
            "above. Reference exact values."
        )
    return (
        "TASK: Explain the healthy baseline state field by field, grounded strictly in the facts "
        "above. Reference exact values."
    )
