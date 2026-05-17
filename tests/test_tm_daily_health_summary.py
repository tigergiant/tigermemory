from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_daily_health_summary  # type: ignore[import-not-found]


def test_load_json_report_tolerates_llm_log_lines(tmp_path):
    report = tmp_path / "answer.json"
    report.write_text(
        "\n".join([
            '{"ts":"2026-05-18T00:00:00Z","kind":"llm_call","ok":true}',
            "{",
            '  "case_count": 25,',
            '  "status_correct": 25,',
            '  "failures": []',
            "}",
        ]),
        encoding="utf-8",
    )

    data = tm_daily_health_summary.load_json_report(report)

    assert data["case_count"] == 25
    assert data["status_correct"] == 25
    assert data["failures"] == []


def test_summarize_known_debt_counts_status_and_review_dates():
    rows = [
        {"id": "a", "status": "active", "review_by_date": "2026-05-17"},
        {"id": "b", "status": "resolved", "review_by_date": "2026-05-17"},
        {"id": "c", "status": "inventory_pending", "review_by_date": "2026-05-20"},
        {"id": "d", "status": "needs_id_review", "review_by_date": "not-a-date"},
    ]

    report = tm_daily_health_summary.summarize_known_debt(
        rows,
        today=tm_daily_health_summary.dt.date(2026, 5, 18),
    )

    assert report["total"] == 4
    assert report["active_count"] == 3
    assert report["resolved_count"] == 1
    assert report["by_status"]["active"] == 1
    assert report["review_overdue_ids"] == ["a"]
    assert report["review_due_soon_ids"] == ["c"]


def test_compact_answer_eval_omits_success_rows():
    report = {
        "case_count": 2,
        "status_correct": 1,
        "expected_evidence_case_count": 2,
        "expected_evidence_hit": 1,
        "claim_support_rate": 1.0,
        "not_found_precision": 1.0,
        "expected_conflict_case_count": 1,
        "conflict_correct": 1,
        "failures": [{"id": "case-a", "query": "raw query should not leak"}],
        "results": [{"id": "case-ok", "query": "raw query should not leak"}],
    }

    compact = tm_daily_health_summary.compact_answer_eval(report)

    assert compact == {
        "case_count": 2,
        "status_correct": 1,
        "expected_evidence_case_count": 2,
        "expected_evidence_hit": 1,
        "claim_support_rate": 1.0,
        "not_found_precision": 1.0,
        "expected_conflict_case_count": 1,
        "conflict_correct": 1,
        "failure_count": 1,
        "failure_ids": ["case-a"],
    }


def test_assemble_summary_from_fixture_jsons(tmp_path, monkeypatch):
    answer = tmp_path / "answer.json"
    trace = tmp_path / "trace.json"
    failures = tmp_path / "failures.json"
    lexical = tmp_path / "lexical.json"
    hybrid = tmp_path / "hybrid.json"
    debt = tmp_path / "known-debt.md"

    answer.write_text(json.dumps({
        "case_count": 25,
        "status_correct": 25,
        "expected_evidence_case_count": 23,
        "expected_evidence_hit": 23,
        "claim_support_rate": 1.0,
        "not_found_precision": 1.0,
        "expected_conflict_case_count": 2,
        "conflict_correct": 2,
        "failures": [],
    }), encoding="utf-8")
    trace.write_text(json.dumps({
        "row_count": 10,
        "invalid_row_count": 0,
        "trace_present_count": 3,
        "status_counts": {"ok": 9, "not_found": 1},
        "llm_counts": {"ok": 3, "missing": 7},
    }), encoding="utf-8")
    failures.write_text(json.dumps({"failure_count": 1, "failures": [{"trace_id": "x"}]}), encoding="utf-8")
    lexical.write_text(json.dumps({"case_count": 80, "hit1": 61, "hit3": 67, "recall": "lexical", "top_k": 3}), encoding="utf-8")
    hybrid.write_text(json.dumps({"case_count": 80, "hit1": 80, "hit3": 80, "recall": "hybrid", "top_k": 3}), encoding="utf-8")
    debt.write_text(
        "\n".join([
            "| id | status | review_by_date |",
            "|---|---|---|",
            "| debt-a | active | 2026-05-20 |",
            "| debt-b | resolved | 2026-05-20 |",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_daily_health_summary, "REPO_ROOT", tmp_path)

    args = type("Args", (), {
        "known_debt_file": "known-debt.md",
        "today": "2026-05-18",
        "health_color": "yellow",
        "blocking_count": 0,
        "new_problem_count": 0,
        "known_debt_new": 0,
        "known_debt_known": 1,
        "known_debt_resolved": 0,
        "known_debt_worsened": 0,
        "answer_eval": "answer.json",
        "answer_trace_summary": "trace.json",
        "answer_trace_failures": "failures.json",
        "retrieval_lexical": "lexical.json",
        "retrieval_hybrid": "hybrid.json",
        "commit_sha": "abc123",
        "push_result": "pushed",
    })()

    captured: list[str] = []
    monkeypatch.setattr(tm_daily_health_summary.sys.stdout, "write", captured.append)

    assert tm_daily_health_summary.cmd_assemble(args) == 0
    summary = json.loads("".join(captured))

    assert summary["schema_version"] == "daily-health-summary-v1"
    assert summary["known_debt_count"] == 1
    assert summary["known_debt_changes"]["known"] == 1
    assert summary["answer_eval"]["status_correct"] == 25
    assert summary["answer_trace"]["failure_count"] == 1
    assert summary["retrieval_eval_lexical"]["hit3"] == 67
    assert summary["retrieval_eval_hybrid"]["hit1"] == 80
    assert summary["commit_sha"] == "abc123"
