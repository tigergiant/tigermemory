from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

pytest.importorskip("fastapi")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_http  # type: ignore[import-not-found]


def test_suggest_wiki_patches_save_schedules_digest_refresh(monkeypatch):
    calls = []
    patch = tm_http.WikiPatchItem(
        page="wiki/systems/example.md",
        type="append",
        section="Notes",
        content="patch content",
        rationale="test",
    )

    monkeypatch.setattr(tm_http, "_load_wiki_catalog", lambda _partition: ["wiki/systems/example.md"])
    monkeypatch.setattr(tm_http.tm_core, "suggest_wiki_patches", lambda *_args, **_kwargs: [patch])
    monkeypatch.setattr(
        tm_http.tm_core,
        "save_wiki_patches_to_inbox",
        lambda *_args, **_kwargs: "inbox/2026-05-16-0000-codex-cross.md",
    )
    monkeypatch.setattr(tm_http.tm_memory_ops, "schedule_digest_refresh", lambda: calls.append("digest"))

    req = tm_http.SuggestPatchesRequest(
        summary="x" * 30,
        partition="systems",
        max_patches=1,
        save=True,
        source="codex",
    )
    result = asyncio.run(tm_http.suggest_wiki_patches(req))

    assert result.inbox_path == "inbox/2026-05-16-0000-codex-cross.md"
    assert calls == ["digest"]


def test_memory_answer_endpoint_delegates_to_core(monkeypatch):
    captured = {}

    def fake_core(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return {
            "status": "not_found",
            "answer": "",
            "summary": "no evidence",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-http",
            "trace": None,
        }

    monkeypatch.setattr(tm_http.tm_answer, "memory_answer_core", fake_core)

    req = tm_http.MemoryAnswerRequest(
        query="missing query",
        scope="wiki",
        top_k=3,
        max_evidence=2,
        include_trace=False,
        run_id="http-run-1",
    )
    result = asyncio.run(tm_http.memory_answer(req))

    assert result["trace_id"] == "trace-http"
    assert result["run_id"] == "http-run-1"
    assert captured == {
        "query": "missing query",
        "scope": "wiki",
        "top_k": 3,
        "max_evidence": 2,
        "include_trace": False,
        "run_id": "http-run-1",
        "evidence_char_budget": 2000,
        "task_context": None,
    }


def test_write_memory_endpoint_forwards_force_inbox(monkeypatch):
    captured = {}

    def fake_write_memory_with_review(agent, topic, text, force_inbox=False, light=False):
        captured["agent"] = agent
        captured["topic"] = topic
        captured["text"] = text
        captured["force_inbox"] = force_inbox
        captured["light"] = light
        return {"route": "inbox", "path": "inbox/x.md"}

    monkeypatch.setattr(tm_http, "_write_memory_with_review", fake_write_memory_with_review)

    req = tm_http.WriteMemoryRequest(
        agent="codex",
        topic="systems",
        text="needs human review",
        force_inbox=True,
        light=False,
    )
    result = asyncio.run(tm_http.write_memory(req))

    assert result["route"] == "inbox"
    assert captured == {
        "agent": "codex",
        "topic": "systems",
        "text": "needs human review",
        "force_inbox": True,
        "light": False,
    }


def test_write_memory_endpoint_rejects_force_inbox_light_conflict(monkeypatch):
    calls = []
    monkeypatch.setattr(tm_http, "_write_memory_with_review", lambda *_args, **_kwargs: calls.append("called"))

    req = tm_http.WriteMemoryRequest(
        agent="codex",
        topic="systems",
        text="needs human review",
        force_inbox=True,
        light=True,
    )
    with pytest.raises(tm_http.HTTPException) as exc:
        asyncio.run(tm_http.write_memory(req))

    assert exc.value.status_code == 400
    assert calls == []


def test_mem0_api_probe_reports_latency_and_error(monkeypatch):
    calls = {}

    def fake_request(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return '{"items":[],"total":0}'

    monkeypatch.setattr(tm_http.tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_http.tm_core, "mem0_user_id", lambda: "tiger")
    monkeypatch.setattr(tm_http.tm_core, "mem0_request", fake_request)

    ok = tm_http._probe_mem0_api(timeout=3)

    assert ok["reachable"] is True
    assert ok["latency_ms"] >= 0
    assert ok["error"] is None
    assert calls["url"] == "http://localhost:8765/api/v1/memories/?user_id=tiger&page=1&size=1&match_mode=id_first"
    assert calls["kwargs"]["timeout"] == 3

    monkeypatch.setattr(
        tm_http.tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Mem0 timeout: timed out")),
    )

    fail = tm_http._probe_mem0_api(timeout=3)

    assert fail["reachable"] is False
    assert "timed out" in fail["error"]


def test_agent_doctor_endpoint_delegates(monkeypatch):
    captured = {}

    def fake_doctor(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "checks": []}

    monkeypatch.setattr(tm_http.tm_agent_doctor, "run_agent_doctor", fake_doctor)

    req = tm_http.AgentDoctorRequest(query="connect", include_l2=False, http_url="http://127.0.0.1:8790")
    result = asyncio.run(tm_http.agent_doctor(req))

    assert result["status"] == "ok"
    assert captured == {
        "query": "connect",
        "include_l2": False,
        "http_url": "http://127.0.0.1:8790",
    }


def test_retention_audit_endpoint_delegates(monkeypatch):
    captured = {}

    def fake_audit(**kwargs):
        captured.update(kwargs)
        return {"dry_run": True, "candidates": []}

    monkeypatch.setattr(tm_http.tm_retention_audit, "run_retention_audit", fake_audit)

    req = tm_http.RetentionAuditRequest(max_items=12)
    result = asyncio.run(tm_http.retention_audit(req))

    assert result["dry_run"] is True
    assert captured == {"max_items": 12}
