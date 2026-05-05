"""Tests for tm_memory_eval.py.

The eval runner is deterministic and read-only. These tests cover parsing,
scoring, and Mem0 failure behavior without requiring a live Mem0 service.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_eval  # type: ignore[import-not-found]


def test_load_cases_parses_fixture():
    cases = tm_memory_eval.load_cases(REPO_ROOT / "tests" / "fixtures" / "memory_eval_cases.jsonl")
    assert len(cases) == 25
    assert {case.scope for case in cases} >= {"wiki", "lessons", "onboarding", "mem0", "all"}
    assert all(case.id and case.query for case in cases)


def test_load_cases_missing_field_reports_line_and_case(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"id": "bad", "query": "x", "scope": "wiki"}, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        tm_memory_eval.load_cases(path)

    msg = str(exc.value)
    assert "line 1" in msg
    assert "bad" in msg
    assert "missing fields" in msg


def test_invalid_scope_reports_clear_error(tmp_path):
    path = tmp_path / "bad-scope.jsonl"
    path.write_text(
        json.dumps({
            "id": "bad-scope",
            "query": "x",
            "scope": "vector",
            "expected_paths": [],
            "must_contain": [],
            "notes": "",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc:
        tm_memory_eval.load_cases(path)

    assert "invalid scope" in str(exc.value)
    assert "vector" in str(exc.value)


def test_score_case_prefers_expected_paths_over_must_contain():
    case = tm_memory_eval.EvalCase(
        id="x",
        query="query",
        scope="wiki",
        expected_paths=["wiki/systems/target.md"],
        must_contain=["needle"],
        notes="",
    )
    wrong_hit = tm_memory_eval.SearchHit(
        path="wiki/systems/wrong.md",
        title="needle",
        snippet="needle",
        score=10,
        source="wiki",
    )

    assert not tm_memory_eval.score_case(case, [wrong_hit], 1)


def test_score_case_uses_must_contain_when_no_expected_paths():
    case = tm_memory_eval.EvalCase(
        id="x",
        query="query",
        scope="mem0",
        expected_paths=[],
        must_contain=["needle", "topic"],
        notes="",
    )
    hit = tm_memory_eval.SearchHit(
        path="mem0:1",
        title="topic",
        snippet="contains needle",
        score=1,
        source="mem0",
    )

    assert tm_memory_eval.score_case(case, [hit], 1)


def test_mem0_unavailable_is_reported_not_raised(monkeypatch):
    def boom(_query: str, size: int = 5) -> str:
        raise RuntimeError("offline")

    monkeypatch.setattr(tm_memory_eval.tm_core, "mem0_search", boom)
    hits, errors = tm_memory_eval.search_mem0_case("anything", 3)

    assert hits == []
    assert errors and "mem0 unavailable" in errors


def test_eval_report_shape_with_stubbed_search(monkeypatch):
    case = tm_memory_eval.EvalCase(
        id="ok",
        query="query",
        scope="wiki",
        expected_paths=["wiki/systems/target.md"],
        must_contain=[],
        notes="",
    )
    hit = tm_memory_eval.SearchHit(
        path="wiki/systems/target.md",
        title="Target",
        snippet="query",
        score=1,
        source="wiki",
    )

    monkeypatch.setattr(tm_memory_eval, "run_search", lambda _scope, _query, _top_k: ([hit], []))
    report = tm_memory_eval.evaluate([case], top_k=3)

    assert report["case_count"] == 1
    assert report["hit1"] == 1
    assert report["hit3"] == 1
    assert report["results"][0]["top_results"][0]["path"] == "wiki/systems/target.md"
