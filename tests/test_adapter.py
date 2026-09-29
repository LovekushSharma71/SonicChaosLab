# SPDX-License-Identifier: Apache-2.0
"""Adapter tests: spec parsing, slugs, and mock fixture selection (incl. chaos overrides)."""

from __future__ import annotations

import pytest

from chaoslab.lab.adapter import (
    MockAdapter,
    parse_spec,
    slugify_spec,
)

FIXTURES = None  # resolved from repo via default_fixtures_dir()


def _mock(repo_root):
    return MockAdapter(repo_root / "tests" / "fixtures" / "mock")


def test_parse_spec_ok():
    assert parse_spec("leaf1: show bgp summary") == ("leaf1", "show bgp summary")


@pytest.mark.parametrize("bad", ["show bgp summary", "nope: show x", "leaf1: "])
def test_parse_spec_rejects(bad):
    with pytest.raises(ValueError):
        parse_spec(bad)


def test_slugify_is_stable_and_distinct():
    assert slugify_spec("leaf1: show mac") == "leaf1_show_mac"
    assert slugify_spec("leaf1: show mac -c") == "leaf1_show_mac_c"
    assert slugify_spec("leaf1: show mac") != slugify_spec("leaf1: show mac -c")


def test_mock_returns_baseline_fixture(repo_root):
    adapter = _mock(repo_root)
    result = adapter.run("leaf1: show bgp summary")
    assert "Established" not in result.raw  # summary lists Up/Down, state parsed later
    assert "10.0.12.1" in result.raw
    assert result.ok


def test_mock_switches_to_chaos_override(repo_root):
    adapter = _mock(repo_root)
    baseline = adapter.run("leaf1: show interfaces status").raw
    adapter.on_inject("c_shut_one_link")
    injected = adapter.run("leaf1: show interfaces status").raw
    assert baseline != injected
    assert "down" in injected
    adapter.on_restore("c_shut_one_link")
    assert adapter.run("leaf1: show interfaces status").raw == baseline


def test_mock_missing_fixture_is_safe(repo_root):
    adapter = _mock(repo_root)
    result = adapter.run("leaf1: show something undefined")
    assert "no fixture" in result.raw


def test_mock_reset_clears_scenario(repo_root):
    adapter = _mock(repo_root)
    adapter.on_inject("c_shut_one_link")
    adapter.reset()
    assert adapter.active_chaos is None
