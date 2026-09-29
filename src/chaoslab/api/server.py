# SPDX-License-Identifier: Apache-2.0
"""FastAPI server implementing the §5.2 surface. A thin transport over the orchestrator.

Every endpoint returns orchestrator-owned state (single source of truth). Budgets are enforced
server-side; demo mode is a server-side flag so both clients inherit it. Long-running lab ops
return a job id. Q&A and experiment explanations stream over SSE.
"""

from __future__ import annotations

import contextlib
import io
import threading
import uuid

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..core.engine import build_orchestrator, catalogue, config_get, config_set
from ..core.orchestrator import Orchestrator
from ..lab import topology
from ..lab.chaos import ChaosError
from ..llm.model import provider_reachable
from ..settings import load_env, load_settings, set_value
from .models import (
    ChaosBody,
    ConfigBody,
    ExperimentBody,
    QuestionBody,
    SessionCreate,
    SettingsUpdate,
)

app = FastAPI(title="SONiC ChaosLab API", version="0.1.0")

_SESSIONS: dict[str, Orchestrator] = {}
_JOBS: dict[str, dict] = {}


def _get(session_id: str) -> Orchestrator:
    orch = _SESSIONS.get(session_id)
    if orch is None:
        raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
    return orch


def _start_job(action: str, func) -> str:
    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {"id": job_id, "action": action, "status": "running", "output": ""}

    def worker() -> None:
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = func()
            _JOBS[job_id]["status"] = "done" if code == 0 else "error"
        except Exception as exc:  # noqa: BLE001 - report any failure to the job
            _JOBS[job_id]["status"] = "error"
            buffer.write(str(exc))
        _JOBS[job_id]["output"] = buffer.getvalue()

    threading.Thread(target=worker, daemon=True).start()
    return job_id


# ------------------------------------------------------------------ lessons/session
@app.get("/lessons")
def lessons() -> list[dict]:
    return [
        {
            "id": lesson.id,
            "title": lesson.title,
            "difficulty": lesson.lesson.difficulty,
            "chaos_options": [
                {"id": c.id, "label": c.label, "enabled": c.enabled, "risk": c.risk}
                for c in lesson.lesson.chaos_options
            ],
        }
        for lesson in catalogue()
    ]


@app.post("/session")
def create_session(body: SessionCreate) -> dict:
    try:
        orch = build_orchestrator(body.lesson_id, load_settings())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _SESSIONS[orch.session_id] = orch
    return {"session_id": orch.session_id, "state": orch.status().model_dump()}


@app.get("/session/{session_id}/state")
def session_state(session_id: str) -> dict:
    return _get(session_id).status().model_dump()


@app.post("/session/{session_id}/advance")
def advance(session_id: str) -> dict:
    return _get(session_id).advance().model_dump()


@app.get("/session/{session_id}/teach")
def teach(session_id: str) -> dict:
    return {"text": _get(session_id).teach_text()}


@app.get("/session/{session_id}/snapshot")
def snapshot(session_id: str) -> dict:
    snap = _get(session_id).snapshot()
    return {"values": snap.values, "structured": snap.structured}


@app.get("/session/{session_id}/diff")
def diff(session_id: str) -> dict:
    return {"changed_facts": _get(session_id).last_diff}


@app.post("/session/{session_id}/explain")
def explain(session_id: str) -> dict:
    orch = _get(session_id)
    if orch.current_step.kind == "observe":
        result = orch.observe()
        return {
            "explanation": result.explanation,
            "facts": result.facts,
            "fallback": result.fallback,
        }
    return {
        "explanation": orch.last_explanation,
        "changed_facts": orch.last_diff,
        "fallback": orch.last_fallback,
    }


@app.get("/session/{session_id}/suggested-questions")
def suggested_questions(session_id: str) -> dict:
    return {"questions": _get(session_id).suggested_questions()}


@app.post("/session/{session_id}/question")
async def question(session_id: str, body: QuestionBody) -> EventSourceResponse:
    orch = _get(session_id)

    def generate():
        for event in orch.stream_question(body.text):
            yield {"event": event.kind, "data": event.model_dump_json()}

    return EventSourceResponse(generate())


@app.post("/session/{session_id}/chaos")
def chaos(session_id: str, body: ChaosBody) -> dict:
    orch = _get(session_id)
    try:
        return orch.select_chaos(body.option_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChaosError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/session/{session_id}/restore")
def restore(session_id: str) -> dict:
    return _get(session_id).restore().model_dump()


@app.post("/session/{session_id}/experiment")
async def experiment(session_id: str, body: ExperimentBody) -> EventSourceResponse:
    orch = _get(session_id)
    result = orch.experiment(body.command, explain=body.explain, confirm=body.confirm)

    def generate():
        if result.proposed_command and not result.executed:
            yield {"event": "proposed", "data": result.model_dump_json()}
            return
        if result.redirected:
            yield {"event": "redirect", "data": result.output}
            return
        if not result.executed:
            yield {"event": "refused", "data": result.output}
            return
        yield {"event": "output", "data": result.output}
        for token in result.explanation.split(" "):
            if token:
                yield {"event": "token", "data": token + " "}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(generate())


# ------------------------------------------------------------------------- lab
@app.get("/lab/status")
def lab_status() -> dict:
    settings = load_settings()
    if settings.lab_mode == "mock":
        return {"mode": "mock", "deployed": False, "nodes": {}}
    ok, message = topology.preflight()
    nodes = {node: topology.node_ready(node) for node in topology.LEAVES} if ok else {}
    return {"mode": settings.lab_mode, "deployed": ok, "detail": message, "nodes": nodes}


@app.post("/lab/up")
def lab_up() -> dict:
    return {"job_id": _start_job("up", topology.deploy)}


@app.post("/lab/down")
def lab_down() -> dict:
    return {"job_id": _start_job("down", topology.destroy)}


@app.get("/jobs/{job_id}")
def job(job_id: str) -> dict:
    result = _JOBS.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return result


@app.get("/lab/topology")
def lab_topology() -> dict:
    return topology.describe()


@app.get("/lab/config/{node}")
def get_config(node: str) -> dict:
    return {"raw": config_get(node, load_settings()).raw}


@app.post("/lab/config/{node}")
def set_config(node: str, body: ConfigBody) -> dict:
    return config_set(node, body.lines, load_settings(), confirm=body.confirm).model_dump()


# --------------------------------------------------------------- settings/health
@app.get("/settings")
def get_settings() -> dict:
    return load_settings().model_dump()


@app.put("/settings")
def put_settings(body: SettingsUpdate) -> dict:
    for key, value in body.values.items():
        set_value(key, str(value))
    return load_settings().model_dump()


@app.get("/health")
def health() -> dict:
    settings = load_settings()
    return {
        "status": "ok",
        "provider": settings.provider,
        "provider_reachable": provider_reachable(settings),
    }


@app.on_event("startup")
def _startup() -> None:
    load_env()
