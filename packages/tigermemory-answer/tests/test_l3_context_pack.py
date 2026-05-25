from __future__ import annotations

from typing import Any

import tigermemory_answer as answer


def search_result(hit: dict[str, Any] | None = None) -> dict[str, Any]:
    hits = [hit] if hit else []
    return {
        "query": "toolkit evidence",
        "scope": "wiki",
        "primary_scope": "wiki",
        "primary_results": hits,
        "groups": {"wiki": hits},
        "warnings": [],
    }


def high_authority_hit() -> dict[str, Any]:
    return {
        "source": "wiki",
        "path": "wiki/systems/agent-write-toolkit.md",
        "title": "Agent toolkit",
        "snippet": "toolkit evidence supports the requested answer",
        "score": 10.0,
    }


def ok_llm(_query: str, _evidence: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    return True, {
        "status": "ok",
        "answer": "Use the toolkit.",
        "summary": "Toolkit evidence was found.",
        "claims": [{"id": "c1", "text": "Use the toolkit.", "support": ["e1"], "confidence": 0.9}],
        "warnings": [],
    }


def test_memory_answer_no_task_context_returns_empty_context_arrays(monkeypatch) -> None:
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: search_result())

    result = answer.memory_answer_core("missing", scope="wiki", write_trace=False)

    assert result["status"] == "not_found"
    assert result["must_read"] == []
    assert result["risks"] == []
    assert result["missing_context"] == []
    assert result["applied_policies"] == []


def test_memory_answer_task_context_populates_must_read_from_authority(monkeypatch) -> None:
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: search_result(high_authority_hit()))
    monkeypatch.setattr(answer, "_call_memory_answer_llm", ok_llm)

    result = answer.memory_answer_core(
        "toolkit evidence",
        scope="wiki",
        write_trace=False,
        task_context={"task": "review", "intent": "recall"},
    )

    assert result["status"] == "ok"
    assert result["must_read"][0]["path"] == "wiki/systems/agent-write-toolkit.md"
    assert result["must_read"][0]["reason"].startswith("authority_score=")
    assert result["applied_policies"] == []


def test_memory_answer_task_context_maps_conflict_hits_to_high_risk(monkeypatch) -> None:
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: search_result(high_authority_hit()))
    monkeypatch.setattr(
        answer,
        "scan_conflicts",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "conflict": True,
            "checks": [],
            "conflicts": [
                {
                    "name": "status_conflict",
                    "evidence_ids": ["e1", "e2", "e3"],
                }
            ],
        },
    )
    monkeypatch.setattr(answer, "_call_memory_answer_llm", ok_llm)

    result = answer.memory_answer_core(
        "toolkit evidence conflict",
        scope="wiki",
        write_trace=False,
        task_context={"task": "review", "intent": "decide"},
    )

    assert result["status"] == "conflict"
    assert result["risks"] == [{"risk": "status_conflict", "severity": "high"}]


def test_memory_answer_task_context_collects_missing_context_from_gate(monkeypatch) -> None:
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: search_result({
        "source": "wiki",
        "path": "wiki/systems/unrelated.md",
        "title": "Unrelated",
        "snippet": "no matching terms",
        "score": 1.0,
    }))

    result = answer.memory_answer_core(
        "needle-only",
        scope="wiki",
        write_trace=False,
        task_context={"task": "review", "intent": "recall"},
    )

    assert result["status"] == "not_found"
    assert "all candidate evidence filtered by weak-evidence guard" in result["missing_context"]
