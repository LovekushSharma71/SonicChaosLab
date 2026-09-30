# SPDX-License-Identifier: Apache-2.0
"""LLM stack tests: fake client, context assembly, memory, and failover."""

from __future__ import annotations

import pytest

from chaoslab.lessons.models import Card, Misconception
from chaoslab.llm import model
from chaoslab.llm.context import ChatMemory, assemble_context
from chaoslab.llm.model import (
    FakeModelClient,
    FallbackExhausted,
    GenerationRequest,
    GroqClient,
    OllamaClient,
    safe_generate,
)
from chaoslab.settings import Settings


def _card() -> Card:
    return Card(
        objective="learn bgp",
        in_scope_subtopics=["a", "b", "c"],
        key_concepts=["one", "two", "three", "four", "five"],
        command_field_meanings={},
        healthy_state_expectations="both neighbors Established",
        expected_chaos_effects={"c_x": "drops"},
        common_misconceptions=[Misconception(misconception="m", why_wrong="w", correct_model="c")]
        * 5,
        out_of_scope=["iBGP"],
        redirect_line="back to lesson",
    )


class _Failing(model.ModelClient):
    name = "failing"

    def generate(self, request):
        raise RuntimeError("provider down")


def test_fake_client_grounds_in_state():
    request = GenerationRequest(
        system="s", context="[state]\n- bgp:10.0.12.1:state: Established\n", task="TASK: explain"
    )
    output = FakeModelClient().generate(request)
    assert "10.0.12.1" in output


def test_assemble_context_shape():
    memory = ChatMemory()
    request = assemble_context(
        _card(), ["bgp:10.0.12.1:state: Established"], memory, "explain_baseline"
    )
    assert "[card]" in request.context and "[state]" in request.context
    assert request.temperature == 0.0
    qna = assemble_context(_card(), [], memory, "qna", question="why?")
    assert qna.task.startswith("QUESTION:")
    assert qna.temperature == 0.25


def test_chat_memory_flush_keeps_summary():
    memory = ChatMemory(max_pairs=2)
    memory.add("q1", "a1")
    memory.add("q2", "a2")
    memory.add("q3", "a3")  # q1 folds into summary
    assert len(memory.pairs) == 2
    memory.flush_step()
    assert memory.pairs == []
    assert memory.summary  # rolling summary survives


def test_safe_generate_fake():
    request = GenerationRequest(system="s", context="[state]\n- fact one\n", task="TASK: x")
    assert safe_generate(request, Settings(provider="fake"))


def test_safe_generate_failover(monkeypatch):
    monkeypatch.setattr(model, "build_chain", lambda s: [_Failing(), FakeModelClient()])
    request = GenerationRequest(system="s", context="[state]\n- fact\n", task="TASK: x")
    assert safe_generate(request, Settings(provider="fake"))


def test_safe_generate_exhausted(monkeypatch):
    monkeypatch.setattr(model, "build_chain", lambda s: [_Failing()])
    request = GenerationRequest(system="s", context="[state]\n- fact\n", task="TASK: x")
    with pytest.raises(FallbackExhausted):
        safe_generate(request, Settings(provider="fake"))


class _FakeStreamResponse:
    """Minimal context-manager stand-in for httpx.stream responses."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> _FakeStreamResponse:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield from self._lines


def test_groq_client_streams_sse_deltas(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"Both "}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"neighbors "}}]}',
        'data: {"choices":[{"delta":{"content":"Established"}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(model.httpx, "stream", lambda *a, **k: _FakeStreamResponse(lines))
    request = GenerationRequest(system="s", context="[state]\n- fact\n", task="TASK: x")
    tokens = list(GroqClient("llama-3.1-8b-instant", "key").stream(request))
    assert "".join(tokens) == "Both neighbors Established"


def test_ollama_client_streams_ndjson(monkeypatch):
    lines = [
        '{"response":"route "}',
        '{"response":"count "}',
        '{"response":"drops","done":true}',
    ]
    monkeypatch.setattr(model.httpx, "stream", lambda *a, **k: _FakeStreamResponse(lines))
    request = GenerationRequest(system="s", context="[state]\n- fact\n", task="TASK: x")
    tokens = list(OllamaClient("llama3", "http://localhost:11434").stream(request))
    assert "".join(tokens) == "route count drops"
