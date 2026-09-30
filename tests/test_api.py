# SPDX-License-Identifier: Apache-2.0
"""API tests: §5.2 endpoints and SSE streaming on the fake provider (no lab, no keys)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from chaoslab.api.server import app


@pytest.fixture
def client(repo_root, monkeypatch):
    monkeypatch.chdir(repo_root)  # fixtures + lessons resolve from repo root
    os.environ.setdefault("CHAOSLAB_FIXTURES", str(repo_root / "tests" / "fixtures" / "mock"))
    return TestClient(app)


def _new_session(client, lesson_id="bgp_reconvergence") -> str:
    response = client.post("/session", json={"lesson_id": lesson_id})
    assert response.status_code == 200
    return response.json()["session_id"]


def test_lessons_catalogue(client):
    lessons = client.get("/lessons").json()
    assert [lesson["id"] for lesson in lessons] == [
        "switches_explained",
        "inside_sonic",
        "bgp_reconvergence",
        "mtu_mismatch",
    ]
    assert lessons[2]["chaos_options"]


def test_health_and_topology(client):
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["provider"] == "fake"
    topo = client.get("/lab/topology").json()
    assert {node["name"] for node in topo["nodes"]} == {"leaf1", "leaf2", "h1", "h2", "h3", "h4"}


def test_lab_status_mock(client):
    status = client.get("/lab/status").json()
    assert status["mode"] == "mock"


def test_session_lifecycle(client):
    session_id = _new_session(client)
    state = client.get(f"/session/{session_id}/state").json()
    assert state["lesson_id"] == "bgp_reconvergence"
    assert client.get(f"/session/{session_id}/teach").json()["text"]
    assert client.get(f"/session/{session_id}/suggested-questions").json()["questions"] == []
    advanced = client.post(f"/session/{session_id}/advance").json()
    assert advanced["step_index"] == 1


def test_snapshot_and_chaos_flow(client):
    session_id = _new_session(client)
    for _ in range(40):
        state = client.get(f"/session/{session_id}/state").json()
        if state["step_kind"] == "chaos_select":
            break
        client.post(f"/session/{session_id}/advance")
    outcome = client.post(
        f"/session/{session_id}/chaos", json={"option_id": "c_shut_one_link"}
    ).json()
    assert outcome["changed_facts"]
    diff = client.get(f"/session/{session_id}/diff").json()
    assert diff["changed_facts"]
    restore = client.post(f"/session/{session_id}/restore").json()
    assert restore["healed"] is True


def test_double_inject_conflict(client):
    session_id = _new_session(client)
    for _ in range(40):
        if client.get(f"/session/{session_id}/state").json()["step_kind"] == "chaos_select":
            break
        client.post(f"/session/{session_id}/advance")
    client.post(f"/session/{session_id}/chaos", json={"option_id": "c_shut_one_link"})
    conflict = client.post(f"/session/{session_id}/chaos", json={"option_id": "c_shut_both_links"})
    assert conflict.status_code == 409


def test_question_streams(client):
    session_id = _new_session(client)
    response = client.post(
        f"/session/{session_id}/question", json={"text": "what is a bgp neighbor?"}
    )
    assert response.status_code == 200
    assert "verdict" in response.text


def test_experiment_stream(client):
    session_id = _new_session(client)
    response = client.post(
        f"/session/{session_id}/experiment",
        json={"command": "leaf1: show bgp summary", "explain": False},
    )
    assert response.status_code == 200
    assert "10.0.12.1" in response.text


def test_config_endpoints(client):
    ok = client.post(
        "/lab/config/leaf1", json={"lines": ["config vlan add 200"], "confirm": False}
    ).json()
    assert ok["accepted"] is True
    bad = client.post("/lab/config/leaf1", json={"lines": ["rm -rf /"], "confirm": True}).json()
    assert bad["accepted"] is False


def test_settings_roundtrip(client):
    client.put("/settings", json={"values": {"max_questions": 7}})
    assert client.get("/settings").json()["max_questions"] == 7
