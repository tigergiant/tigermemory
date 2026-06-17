from __future__ import annotations

import json

from tigermemory_answer import eval as answer_eval


def test_load_cases_parses_jsonl_and_skips_blanks(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "\n".join([
            "",
            "# comment",
            json.dumps({"id": "case-1", "query": "what is p3-d?"}, ensure_ascii=False),
        ]),
        encoding="utf-8",
    )

    loaded = answer_eval.load_cases(str(cases))

    assert loaded == [{"id": "case-1", "query": "what is p3-d?"}]


def test_eval_case_scores_retrieval_expected_evidence(monkeypatch):
    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "ok",
            "summary": "P3-D extracted trace",
            "answer": "trace module extracted",
            "claims": [{"id": "c1", "support": ["e1"]}],
            "evidence": [{"path": "wiki/systems/p3-d.md", "excerpt": "trace module extracted"}],
            "warnings": [],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-1",
        }

    monkeypatch.setattr(answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = answer_eval.eval_case({
        "id": "retrieval",
        "query": "P3-D",
        "expected_status": "ok",
        "expected_evidence_paths": ["wiki/systems/p3-d.md"],
        "must_contain": ["trace module"],
    }, run_id="run-1")

    assert result["status_ok"] is True
    assert result["expected_evidence_hit"] is True
    assert result["must_contain_hit"] is True
    assert result["supported_claim_count"] == 1


def test_eval_case_scores_trace_numeric_expressions(monkeypatch):
    def fake_memory_answer_core(_query: str, **kwargs):
        assert kwargs["include_trace"] is True
        return {
            "status": "ok",
            "summary": "freshness guard fired",
            "answer": "freshness guard fired",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-freshness",
            "trace": {
                "validity": {
                    "state_counts": {
                        "obsolete_ignored": 2,
                        "unresolved_conflict": 0,
                    },
                    "guard_active": "true",
                },
            },
        }

    monkeypatch.setattr(answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = answer_eval.eval_case({
        "id": "freshness-trace",
        "query": "current state?",
        "expected_trace_flags": [
            "validity.state_counts.obsolete_ignored >= 1",
            "validity.state_counts.unresolved_conflict == 0",
            "validity.guard_active == true",
        ],
    }, run_id="run-trace")

    assert result["trace_flags_hit"] is True


def test_eval_case_fails_trace_numeric_expression_when_value_missing(monkeypatch):
    def fake_memory_answer_core(_query: str, **kwargs):
        return {
            "status": "ok",
            "summary": "no guard",
            "answer": "no guard",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-missing",
            "trace": {"validity": {"state_counts": {}}},
        }

    monkeypatch.setattr(answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = answer_eval.eval_case({
        "id": "freshness-trace-missing",
        "query": "current state?",
        "expected_trace_flags": ["validity.state_counts.obsolete_ignored >= 1"],
    }, run_id="run-trace")

    assert result["trace_flags_hit"] is False


def test_eval_case_preserves_legacy_trace_key_presence(monkeypatch):
    def fake_memory_answer_core(_query: str, **kwargs):
        assert kwargs["include_trace"] is True
        return {
            "status": "ok",
            "summary": "legacy flag",
            "answer": "legacy flag",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-legacy",
            "trace": {"outer": {"stale_guard": {"active": True}}},
        }

    monkeypatch.setattr(answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = answer_eval.eval_case({
        "id": "legacy-trace-flag",
        "query": "current state?",
        "expected_trace_flags": ["stale_guard"],
    }, run_id="run-trace")

    assert result["trace_flags_hit"] is True


def test_eval_case_scores_quality_status_and_claims(monkeypatch):
    def fake_memory_answer_core(_query: str, **kwargs):
        return {
            "status": "conflict",
            "summary": "conflict found",
            "answer": "",
            "claims": [{"id": "c1", "support": ["e1"]}, {"id": "c2", "support": []}],
            "evidence": [{"path": "wiki/systems/conflict.md", "excerpt": "conflict found"}],
            "warnings": ["conflict"],
            "run_id": kwargs["run_id"],
            "trace_id": "trace-conflict",
        }

    monkeypatch.setattr(answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = answer_eval.eval_case({
        "id": "quality",
        "query": "conflict?",
        "expected_status": "conflict",
        "must_contain": ["conflict found"],
    }, run_id="run-2")

    assert result["status"] == "conflict"
    assert result["status_ok"] is True
    assert result["claim_count"] == 2
    assert result["supported_claim_count"] == 1


def test_summarize_counts_status_evidence_and_quality_metrics():
    summary = answer_eval.summarize([
        {
            "status_ok": True,
            "expected_evidence_paths": ["wiki/a.md"],
            "expected_evidence_hit": True,
            "must_contain": ["A"],
            "must_contain_hit": True,
            "claim_count": 2,
            "supported_claim_count": 2,
            "status": "ok",
            "expected_status": "ok",
        },
        {
            "status_ok": True,
            "expected_evidence_paths": [],
            "expected_evidence_hit": True,
            "must_contain": [],
            "must_contain_hit": True,
            "claim_count": 1,
            "supported_claim_count": 0,
            "status": "not_found",
            "expected_status": "not_found",
        },
        {
            "status_ok": True,
            "expected_evidence_paths": [],
            "expected_evidence_hit": True,
            "must_contain": [],
            "must_contain_hit": True,
            "claim_count": 0,
            "supported_claim_count": 0,
            "status": "conflict",
            "expected_status": "conflict",
        },
    ])

    assert summary["case_count"] == 3
    assert summary["status_correct"] == 3
    assert summary["expected_evidence_hit"] == 1
    assert summary["must_contain_hit"] == 1
    assert summary["claim_support_rate"] == 2 / 3
    assert summary["not_found_precision"] == 1.0
    assert summary["conflict_correct"] == 1
