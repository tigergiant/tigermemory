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
import tm_core  # type: ignore[import-not-found]


def test_load_cases_parses_fixture():
    cases = tm_memory_eval.load_cases(REPO_ROOT / "tests" / "fixtures" / "memory_eval_cases.jsonl")
    assert len(cases) >= 50
    assert {case.scope for case in cases} >= {"wiki", "lessons", "onboarding", "mem0", "all"}
    assert all(case.id and case.query for case in cases)
    # Default kind is "retrieval" when the field is omitted; the probe split
    # depends on at least one explicit runtime_probe case existing in fixture.
    assert any(case.kind == "retrieval" for case in cases)


def test_fixture_marks_mem0_diagnostic_as_runtime_probe():
    cases = tm_memory_eval.load_cases(REPO_ROOT / "tests" / "fixtures" / "memory_eval_cases.jsonl")
    by_id = {case.id: case for case in cases}
    probe = by_id.get("mem0-diagnostic-search")
    assert probe is not None, "mem0-diagnostic-search must stay in the fixture as a runtime probe"
    assert probe.kind == "runtime_probe"


def test_load_cases_rejects_unknown_kind(tmp_path):
    path = tmp_path / "bad-kind.jsonl"
    path.write_text(
        json.dumps({
            "id": "bad-kind",
            "kind": "benchmark",
            "query": "x",
            "scope": "wiki",
            "expected_paths": [],
            "must_contain": [],
            "notes": "",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc:
        tm_memory_eval.load_cases(path)

    assert "invalid kind" in str(exc.value)
    assert "benchmark" in str(exc.value)


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

    monkeypatch.setattr(tm_memory_eval, "run_search", lambda _scope, _query, _top_k, **_kw: ([hit], []))
    report = tm_memory_eval.evaluate([case], top_k=3)

    assert report["case_count"] == 1
    assert report["hit1"] == 1
    assert report["hit3"] == 1
    assert report["quality_case_count"] == 1
    assert report["runtime_unavailable_count"] == 0
    assert report["results"][0]["top_results"][0]["path"] == "wiki/systems/target.md"


def test_evaluate_splits_runtime_probe_out_of_retrieval_denominator(monkeypatch):
    retrieval = tm_memory_eval.EvalCase(
        id="ok",
        query="query",
        scope="wiki",
        expected_paths=["wiki/systems/target.md"],
        must_contain=[],
        notes="",
        kind="retrieval",
    )
    probe = tm_memory_eval.EvalCase(
        id="probe",
        query="tigermemory onboarding",
        scope="mem0",
        expected_paths=[],
        must_contain=["tigermemory"],
        notes="",
        kind="runtime_probe",
    )
    retrieval_hit = tm_memory_eval.SearchHit(
        path="wiki/systems/target.md",
        title="Target",
        snippet="query",
        score=1,
        source="wiki",
    )
    probe_hit = tm_memory_eval.SearchHit(
        path="mem0:1",
        title="tigermemory onboarding note",
        snippet="tigermemory",
        score=1,
        source="mem0",
    )

    def stub(scope, _query, _top_k, **_kw):
        if scope == "mem0":
            return [probe_hit], []
        return [retrieval_hit], []

    monkeypatch.setattr(tm_memory_eval, "run_search", stub)
    report = tm_memory_eval.evaluate([retrieval, probe], top_k=3)

    # Retrieval denominator excludes the probe.
    assert report["case_count"] == 1
    assert report["hit1"] == 1
    assert report["hit3"] == 1
    assert report["quality_case_count"] == 1
    assert report["runtime_unavailable_count"] == 0
    assert report["total_case_count"] == 2

    # Probe layer is reported separately with its own counters and rows.
    assert report["probe_case_count"] == 1
    assert report["probe_hit1"] == 1
    assert report["probe_hit3"] == 1
    assert report["probe_runtime_unavailable_count"] == 0
    assert len(report["probe_results"]) == 1
    assert report["probe_results"][0]["id"] == "probe"
    assert report["probe_results"][0]["kind"] == "runtime_probe"

    # Retrieval results do not contain the probe row.
    assert all(row["id"] != "probe" for row in report["results"])
    assert report["results"][0]["kind"] == "retrieval"


def test_evaluate_probe_miss_does_not_affect_retrieval_hits(monkeypatch):
    """An empty-store Mem0 probe that misses must not reduce retrieval hit@k."""
    retrieval = tm_memory_eval.EvalCase(
        id="ok",
        query="query",
        scope="wiki",
        expected_paths=["wiki/systems/target.md"],
        must_contain=[],
        notes="",
    )
    probe = tm_memory_eval.EvalCase(
        id="probe",
        query="tigermemory onboarding",
        scope="mem0",
        expected_paths=[],
        must_contain=["tigermemory"],
        notes="",
        kind="runtime_probe",
    )
    retrieval_hit = tm_memory_eval.SearchHit(
        path="wiki/systems/target.md",
        title="Target",
        snippet="query",
        score=1,
        source="wiki",
    )

    def stub(scope, _query, _top_k, **_kw):
        if scope == "mem0":
            return [], []  # empty store, but Mem0 is up (no unavailable error)
        return [retrieval_hit], []

    monkeypatch.setattr(tm_memory_eval, "run_search", stub)
    report = tm_memory_eval.evaluate([retrieval, probe], top_k=3)

    assert report["hit3"] == 1  # retrieval clean
    assert report["hit3_rate"] == 1.0
    assert report["probe_hit3"] == 0
    assert report["probe_runtime_unavailable_count"] == 0  # Mem0 up, just empty


def test_eval_excludes_runtime_unavailable_mem0_from_quality_denominator(monkeypatch):
    case = tm_memory_eval.EvalCase(
        id="mem0-down",
        query="tigermemory onboarding",
        scope="mem0",
        expected_paths=[],
        must_contain=["tigermemory"],
        notes="",
    )

    monkeypatch.setattr(
        tm_memory_eval,
        "run_search",
        lambda _scope, _query, _top_k, **_kw: ([], ["mem0 unavailable: offline"]),
    )
    report = tm_memory_eval.evaluate([case], top_k=3)

    assert report["hit3"] == 0
    assert report["runtime_unavailable_count"] == 1
    assert report["quality_case_count"] == 0
    assert report["results"][0]["runtime_unavailable"] is True


def test_grouped_search_uses_intent_primary_for_all_scope(monkeypatch):
    def offline(_query: str, size: int = 5) -> str:
        raise RuntimeError("offline")

    monkeypatch.setattr(tm_memory_eval.tm_core, "mem0_search", offline)
    hits, errors = tm_memory_eval.run_search(
        "all",
        "Mem0 promotion lifecycle delete duplicate",
        3,
        grouped=True,
    )

    assert errors and "mem0 unavailable" in errors[0]
    assert hits
    assert hits[0].source == "wiki"
    assert hits[0].path == "wiki/systems/mem0-wiki-compilation.md"


def test_grouped_search_prefers_lessons_for_failure_queries(monkeypatch):
    def offline(_query: str, size: int = 5) -> str:
        raise RuntimeError("offline")

    monkeypatch.setattr(tm_memory_eval.tm_core, "mem0_search", offline)
    hits, _errors = tm_memory_eval.run_search(
        "all",
        "hook reject no-verify routed_by",
        3,
        grouped=True,
    )

    assert hits
    assert hits[0].source == "lessons"
    assert hits[0].path == "wiki/self-evolution/lessons/2026-04-22-no-verify-bypass.md"


def test_evaluate_reports_grouped_mode(monkeypatch):
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

    monkeypatch.setattr(tm_memory_eval, "run_search", lambda _scope, _query, _top_k, **_kw: ([hit], []))
    report = tm_memory_eval.evaluate([case], top_k=3, grouped=True)

    assert report["grouped"] is True
    assert report["fuse"] is False
    assert report["hit3"] == 1


def test_wiki_search_prioritizes_slug_and_title_hits():
    results = tm_core.search_wiki("agent write toolkit tm_io", size=3, include_sources=False)

    assert results
    assert results[0]["path"] == "wiki/systems/agent-write-toolkit.md"


def test_wiki_search_demotes_aggregate_pages():
    """Contract: aggregate/dashboard pages must be demoted below canonical
    content pages. We assert top-3 contains the canonical page (not strictly
    rank 1) because other content pages legitimately reference the same
    keywords (e.g. memory-retrieval-eval.md Phase 2m extensively cites
    openmemory-ce-limits.md as a worked example). The demotion contract
    is checked via the score comparison against the dashboard, which is
    the actual intent of this test."""
    results = tm_core.search_wiki("OpenMemory CE search limits", size=5, include_sources=False)

    paths = [r["path"] for r in results]
    assert "wiki/systems/openmemory-ce-limits.md" in paths, (
        f"canonical page must appear in top-5; got {paths}"
    )
    canonical_idx = paths.index("wiki/systems/openmemory-ce-limits.md")
    if "wiki/operations/backlinks-dashboard.md" in paths:
        dashboard = results[paths.index("wiki/operations/backlinks-dashboard.md")]
        assert results[canonical_idx]["score"] > dashboard["score"]
