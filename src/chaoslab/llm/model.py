# SPDX-License-Identifier: Apache-2.0
"""Model client: a deterministic fake plus a provider chain with timeout, retry, and failover.

``safe_generate``/``safe_stream`` implement the resilience contract of PRODUCT.md §8.6: an 8s
timeout, one retry, provider failover, and ``FallbackExhausted`` when nothing succeeds (the
caller then substitutes a labelled card fallback). The default provider is ``fake`` so tests and
the out-of-the-box experience never touch the network.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator

import httpx
from pydantic import BaseModel

from ..settings import Settings, api_key

_TIMEOUT_S = 8.0
_RETRIES = 1


class GenerationRequest(BaseModel):
    """A fixed-shape request. ``context`` holds the assembled blocks (§8.4); ``task`` the suffix."""

    system: str
    context: str
    task: str
    temperature: float = 0.0
    max_tokens: int = 512


class ModelError(Exception):
    """A provider call failed."""


class FallbackExhausted(ModelError):
    """Every provider in the chain failed; the caller must use the card fallback."""


class ModelClient(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, request: GenerationRequest) -> str: ...

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        for token in self.generate(request).split(" "):
            yield token + " "


def _state_lines(context: str) -> list[str]:
    lines: list[str] = []
    capture = False
    for line in context.splitlines():
        if line.strip().startswith("[state]"):
            capture = True
            continue
        if capture and line.startswith("["):
            break
        if capture and line.strip():
            lines.append(line.strip())
    return lines


class FakeModelClient(ModelClient):
    """Deterministic client that grounds its reply in the assembled state block."""

    name = "fake"

    def generate(self, request: GenerationRequest) -> str:
        facts = _state_lines(request.context)
        highlighted = "; ".join(facts[:3]) if facts else "no changed facts were observed"
        question = ""
        if request.task.startswith("QUESTION:"):
            question = request.task.split("QUESTION:", 1)[1].strip()
        lead = f"Regarding '{question}': " if question else ""
        return (
            f"{lead}Grounded in the observed lab state ({highlighted}), "
            "this reflects the lesson's expected behaviour. "
            "(fake provider — deterministic explanation for offline runs.)"
        )


class AnthropicClient(ModelClient):
    name = "anthropic"

    def __init__(self, model: str, key: str) -> None:
        self.model = model
        self.key = key

    def _client(self):
        import anthropic

        # Default (non-workspace-scoped) keys require this header; set it when provided.
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        headers = {"anthropic-workspace-id": workspace} if workspace else None
        return anthropic.Anthropic(api_key=self.key, timeout=_TIMEOUT_S, default_headers=headers)

    def _kwargs(self, method: object, request: GenerationRequest) -> dict:
        import inspect

        kwargs: dict = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": f"{request.context}\n\n{request.task}"}],
        }
        try:  # SDKs >=1.9 dropped top-level temperature in favour of output_config.effort
            if "temperature" in inspect.signature(method).parameters:
                kwargs["temperature"] = request.temperature
        except (TypeError, ValueError):
            pass
        return kwargs

    def generate(self, request: GenerationRequest) -> str:
        client = self._client()
        message = client.messages.create(**self._kwargs(client.messages.create, request))
        return "".join(block.text for block in message.content if block.type == "text")

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        client = self._client()
        with client.messages.stream(**self._kwargs(client.messages.stream, request)) as stream:
            yield from stream.text_stream


class _HttpClient(ModelClient):
    """Shared base for OpenAI-style/Ollama HTTP providers."""

    def _prompt(self, request: GenerationRequest) -> str:
        return f"{request.system}\n\n{request.context}\n\n{request.task}"


class GeminiClient(_HttpClient):
    name = "gemini"

    def __init__(self, model: str, key: str) -> None:
        self.model = model
        self.key = key

    def generate(self, request: GenerationRequest) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )
        response = httpx.post(
            url,
            params={"key": self.key},
            json={
                "contents": [{"parts": [{"text": self._prompt(request)}]}],
                "generationConfig": {"temperature": request.temperature},
            },
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class GroqClient(_HttpClient):
    name = "groq"

    def __init__(self, model: str, key: str) -> None:
        self.model = model
        self.key = key

    def generate(self, request: GenerationRequest) -> str:
        response = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "temperature": request.temperature,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": f"{request.context}\n\n{request.task}"},
                ],
            },
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        """Live token stream over the OpenAI-style SSE endpoint (PRODUCT.md §8.6)."""
        with httpx.stream(
            "POST",
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "temperature": request.temperature,
                "stream": True,
                "messages": [
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": f"{request.context}\n\n{request.task}"},
                ],
            },
            timeout=_TIMEOUT_S,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


class OllamaClient(_HttpClient):
    name = "ollama"

    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self.host = host.rstrip("/")

    def generate(self, request: GenerationRequest) -> str:
        response = httpx.post(
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "prompt": self._prompt(request),
                "stream": False,
                "options": {"temperature": request.temperature},
            },
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.json()["response"]

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        """Live token stream over Ollama's newline-delimited JSON responses."""
        with httpx.stream(
            "POST",
            f"{self.host}/api/generate",
            json={
                "model": self.model,
                "prompt": self._prompt(request),
                "stream": True,
                "options": {"temperature": request.temperature},
            },
            timeout=_TIMEOUT_S,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    token = json.loads(line).get("response")
                except json.JSONDecodeError:
                    continue
                if token:
                    yield token


def build_chain(settings: Settings) -> list[ModelClient]:
    """Build the ordered provider chain from settings and available env keys."""
    if settings.provider == "fake":
        return [FakeModelClient()]

    chain: list[ModelClient] = []
    anthropic_key = api_key("anthropic")
    gemini_key = api_key("gemini")
    groq_key = api_key("groq")

    primaries = {
        "anthropic": lambda: (
            AnthropicClient(settings.model, anthropic_key) if anthropic_key else None
        ),
        "gemini": lambda: GeminiClient(settings.gemini_model, gemini_key) if gemini_key else None,
        "groq": lambda: GroqClient(settings.groq_model, groq_key) if groq_key else None,
        "ollama": lambda: OllamaClient(settings.ollama_model, settings.ollama_host),
    }
    primary = primaries.get(settings.provider, lambda: None)()
    if primary:
        chain.append(primary)
    if gemini_key and settings.provider != "gemini":
        chain.append(GeminiClient(settings.gemini_model, gemini_key))
    if groq_key and settings.provider != "groq":
        chain.append(GroqClient(settings.groq_model, groq_key))
    if os.environ.get("OLLAMA_HOST") and settings.provider != "ollama":
        chain.append(OllamaClient(settings.ollama_model, os.environ["OLLAMA_HOST"]))
    if not chain:
        chain.append(FakeModelClient())
    return chain


def safe_generate(request: GenerationRequest, settings: Settings) -> str:
    """Generate with retry and provider failover. Raises FallbackExhausted if all fail."""
    errors: list[str] = []
    for client in build_chain(settings):
        for _ in range(_RETRIES + 1):
            try:
                return client.generate(request)
            except Exception as exc:  # noqa: BLE001 - failover is intentional
                errors.append(f"{client.name}: {exc}")
    raise FallbackExhausted("; ".join(errors) or "no providers configured")


def safe_stream(request: GenerationRequest, settings: Settings) -> Iterator[str]:
    """Stream tokens from the first working provider, else raise FallbackExhausted."""
    errors: list[str] = []
    for client in build_chain(settings):
        try:
            yield from client.stream(request)
            return
        except Exception as exc:  # noqa: BLE001 - failover is intentional
            errors.append(f"{client.name}: {exc}")
    raise FallbackExhausted("; ".join(errors) or "no providers configured")


def provider_reachable(settings: Settings) -> bool:
    """Whether the configured provider can be used (fake always; others need a key/host)."""
    if settings.provider == "fake":
        return True
    if settings.provider == "ollama":
        return True
    return api_key(settings.provider) is not None
