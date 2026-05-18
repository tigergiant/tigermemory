from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_answer  # type: ignore[import-not-found]


def _search_result(hit: dict | None = None) -> dict:
    hits = [hit] if hit else []
    return {
        "query": "q",
        "scope": "wiki",
        "strategy": "grouped-intent-budget-v1",
        "primary_scope": "wiki",
        "primary_results": hits,
        "groups": {"wiki": hits},
        "warnings": [],
    }


def test_memory_answer_core_expands_evidence_and_generates_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/agent-write-toolkit.md",
            "title": "Agent 写入工具 tm_io",
            "snippet": "tm_io write_memory",
            "score": 10.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Use write_memory through the toolkit.",
            "summary": "Answered from toolkit evidence.",
            "claims": [{"id": "c1", "text": "write_memory is documented.", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core("write_memory toolkit", scope="wiki", run_id="unit-run-1")

    assert result["status"] == "ok"
    assert result["run_id"] == "unit-run-1"
    assert result["claims"][0]["support"] == ["e1"]
    assert result["evidence"][0]["id"] == "e1"
    assert result["evidence"][0]["authority"] >= 90.0
    assert result["evidence"][0]["source_role"] == "canonical_wiki"
    assert result["trace_id"]
    assert result["trace"]["run_id"] == "unit-run-1"
    assert (tmp_path / "trace.jsonl").exists()
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert trace_row["run_id"] == "unit-run-1"


def test_memory_answer_core_not_found_skips_llm(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("no such memory", scope="wiki", include_trace=False, run_id="unit-run-hidden")

    assert result["status"] == "not_found"
    assert result["evidence"] == []
    assert result["trace"] is None
    assert calls == []
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert trace_row["run_id"] == "unit-run-hidden"
    assert trace_row["trace"]["run_id"] == "unit-run-hidden"
    assert trace_row["trace"]["query_class"] == "recall"


def test_memory_answer_core_drops_unsupported_claims(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:abc",
            "title": "systems / codex",
            "snippet": "durable memory record",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "One valid claim remains.",
            "summary": "Unsupported claim is removed.",
            "claims": [
                {"id": "c1", "text": "valid", "support": ["e1"], "confidence": 0.8},
                {"id": "c2", "text": "invalid", "support": ["e99"], "confidence": 0.8},
            ],
        }),
    )

    result = tm_answer.memory_answer_core("memory record", scope="mem0")

    assert result["status"] == "ok"
    assert [claim["id"] for claim in result["claims"]] == ["c1"]
    assert any("dropped unsupported claim" in warning for warning in result["warnings"])


def test_memory_answer_core_llm_failure_preserves_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/agent-write-toolkit.md",
            "title": "Agent 写入工具 tm_io",
            "snippet": "toolkit evidence",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda _q, _e: (False, "offline"))

    result = tm_answer.memory_answer_core("toolkit", scope="wiki")

    assert result["status"] == "error"
    assert result["answer"] == ""
    assert result["evidence"]
    assert any("LLM failed" in warning for warning in result["warnings"])


def test_memory_answer_core_redacts_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:secret",
            "title": "systems / codex",
            "snippet": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Token was Bearer abcdefghijklmnopqrstuvwxyz",
            "summary": "Secret redacted.",
            "claims": [{"id": "c1", "text": "Bearer abcdefghijklmnopqrstuvwxyz", "support": ["e1"], "confidence": 0.5}],
        }),
    )

    result = tm_answer.memory_answer_core("secret", scope="mem0")

    assert "[REDACTED]" in result["answer"]
    assert "[REDACTED]" in result["claims"][0]["text"]
    assert "[REDACTED]" in result["evidence"][0]["excerpt"]


def test_memory_answer_core_filters_weak_evidence_before_llm(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "lessons",
            "path": "wiki/self-evolution/lessons/2026-05-10-retrieval-eval-query-pollution.md",
            "title": "retrieval eval guard",
            "snippet": "generic tigermemory lesson",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("zzzz impossible 7f3b2c9d", scope="lessons")

    assert result["status"] == "not_found"
    assert result["evidence"] == []
    assert calls == []
    assert any("weak-evidence guard" in warning for warning in result["warnings"])


def test_memory_answer_core_conflict_scan_short_circuits_llm(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:conflict",
            "title": "systems / codex",
            "snippet": "P5.2 小额规则内自动下单 与 P5.2 不自动下单 两种说法冲突",
            "score": 5.0,
        }),
    )
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("P5.2 自动下单 冲突", scope="mem0")

    assert result["status"] == "conflict"
    assert result["claims"][0]["support"] == ["e1"]
    assert calls == []
    assert result["trace"]["conflict_scan"]["conflict"] is True
