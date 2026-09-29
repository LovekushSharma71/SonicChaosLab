# SPDX-License-Identifier: Apache-2.0
"""Guardrails (§8.6): input gates, output checks, and tiered fallback.

All gating is deterministic code — the model never decides what is in scope, safe, or valid
(PRODUCT.md §4). Input: a regex/keyword scope gate, an experiment relevance + read-only safety
allowlist, and a line-by-line config-set allowlist where one bad line rejects the whole batch.
Output: length and grounding checks; on failure the caller falls back to a labelled card field.
"""

from __future__ import annotations

import fnmatch
import re

from ..lessons.models import Card

FALLBACK_PREFIX = "(from lesson notes — model unavailable/unverified) "

_ALLOWED_CONFIG_HEADS = ("config vlan", "config interface", "config portchannel", "config bgp")
_DENY_CONFIG = ("config reload", "config save", "config load", "config erase")
_SHELL_METACHARS = (";", "|", "&", "`", "$(", ">", "<", "\n")
_REDIS_READ_OPS = (
    " keys ",
    " hget ",
    " hgetall ",
    " get ",
    " dbsize",
    " lrange ",
    " smembers ",
    " scan ",
)
_REDIS_WRITE_OPS = (
    " set ",
    " hset ",
    " del ",
    " flushdb",
    " flushall",
    " lpush",
    " rpush",
    " expire ",
    " unlink ",
)
_STOPWORDS = {"the", "and", "via", "for", "with", "loss", "both", "one", "two"}


def scope_gate(text: str, scope_keywords: list[str]) -> bool:
    """True when the input touches at least one lesson scope keyword."""
    low = text.lower()
    return any(keyword.lower() in low for keyword in scope_keywords)


def experiment_relevance(command: str, vocabulary: list[str]) -> bool:
    """True when a command matches the lesson's experiment vocabulary globs."""
    return any(fnmatch.fnmatchcase(command, glob) for glob in vocabulary)


def experiment_safety(command: str) -> bool:
    """True only for read-only diagnostics (show/ping/vtysh show/redis reads)."""
    command = command.strip()
    lowered = f" {command.lower()} "
    if command.startswith("show ") or command == "show":
        return True
    if command.startswith("ping ") and "-c" in command:
        return True
    if command.startswith("vtysh"):
        return '"show' in command or "'show" in command
    if command.startswith(("sonic-db-cli", "redis-cli", "sonic-db-cli ")):
        if any(op in lowered for op in _REDIS_WRITE_OPS):
            return False
        return any(op in lowered for op in _REDIS_READ_OPS)
    if command.startswith(("ip neigh", "ip addr show", "ip link show", "ip route show")):
        return True
    return False


def config_set_validate(lines: list[str]) -> tuple[bool, str | None]:
    """Validate a config-set batch. One bad line rejects the whole batch (atomic intent)."""
    if not lines:
        return False, None
    for line in lines:
        candidate = line.strip()
        if candidate.startswith("sudo "):
            candidate = candidate[len("sudo ") :].strip()
        if any(meta in candidate for meta in _SHELL_METACHARS):
            return False, line
        if any(candidate.startswith(bad) for bad in _DENY_CONFIG):
            return False, line
        if not any(candidate.startswith(head) for head in _ALLOWED_CONFIG_HEADS):
            return False, line
    return True, None


def _fact_tokens(fact: str) -> list[str]:
    return [tok for tok in re.findall(r"[A-Za-z0-9_./]+", fact) if len(tok) >= 3]


def mentions_fact(text: str, fact_values: list[str]) -> bool:
    """True if the text references at least one token from the changed facts."""
    if not fact_values:
        return True
    low = text.lower()
    for fact in fact_values:
        for token in _fact_tokens(fact):
            if token.lower() in _STOPWORDS:
                continue
            if token.lower() in low:
                return True
    return False


def within_length(text: str, max_sentences: int = 8) -> bool:
    sentences = len(re.findall(r"[.!?]", text))
    return 0 < sentences <= max_sentences


def check_scripted(text: str, fact_values: list[str]) -> tuple[bool, str]:
    """Validate a scripted (grounded) answer. Returns (ok, reason)."""
    if not text.strip():
        return False, "empty response"
    if not within_length(text):
        return False, "length out of bounds"
    if not mentions_fact(text, fact_values):
        return False, "no grounded value referenced"
    return True, ""


def tiered_fallback(card: Card, mode: str, chaos_id: str | None = None) -> str:
    """A labelled fallback drawn from the best-matching card field when the model is unavailable."""
    if mode == "explain_impact" and chaos_id and chaos_id in card.expected_chaos_effects:
        body = card.expected_chaos_effects[chaos_id]
    elif mode in ("explain_baseline", "experiment"):
        body = card.healthy_state_expectations
    else:
        body = card.objective
    return FALLBACK_PREFIX + body
