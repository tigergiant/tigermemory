from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_daily_health_summary  # type: ignore[import-not-found]


GOOD_AUTOMATION_PROMPT = """
Run git pull --ff-only origin master, full git status line count, and py tools/tm_lessons.py search.
Run py tools/tm_daily_health_summary.py automation-contract --json.
Persist .tmp/daily-health/YYYY-MM-DD/ files.
Run tm_http /health and persist the JSON to .tmp/daily-health/YYYY-MM-DD/health.json.
Use mem0_reachable, mem0_api_reachable, mem0_api_latency_ms, and mem0_api_error as first-class health signals.
Run py tools/tm_answer_eval.py eval, py tools/tm_answer_trace.py summary, and py tools/tm_answer_trace.py failures.
Run py tools/tm_memory_eval.py eval and py tools/tm_memory_eval.py eval --recall hybrid --embedding-base-url http://127.0.0.1:19190/v1.
Run py tools/tm_daily_health_summary.py assemble with the health JSON and place the result under ## 机器可读摘要.
Run py tools/tm_daily_health_summary.py validate-report and confirm schema_version and health_probe are present.
Read wiki/operations/daily-health-known-debt.md and classify findings as new / known / resolved / worsened.
Use git commit, git push, git pull --ff-only origin master, and write_memory at closeout.
"""


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


def test_audit_automation_contract_passes_complete_prompt():
    report = tm_daily_health_summary.audit_automation_contract(
        GOOD_AUTOMATION_PROMPT,
        path="automation.toml",
    )

    assert report["schema_version"] == "daily-health-automation-contract-v1"
    assert report["status"] == "ok"
    assert report["missing"] == []
    assert report["passed_count"] == report["check_count"]


def test_audit_automation_contract_reports_missing_markers():
    report = tm_daily_health_summary.audit_automation_contract(
        "Run git status only.",
        path="automation.toml",
    )

    assert report["status"] == "fail"
    assert report["missing_count"] > 0
    missing_ids = {item["id"] for item in report["missing"]}
    assert "answer_quality" in missing_ids
    assert "machine_summary" in missing_ids


def test_cmd_automation_contract_json_exit_codes(tmp_path, monkeypatch):
    automation = tmp_path / "automation.toml"
    automation.write_text(GOOD_AUTOMATION_PROMPT, encoding="utf-8")
    captured: list[str] = []
    monkeypatch.setattr(tm_daily_health_summary.sys.stdout, "write", captured.append)

    args = type("Args", (), {"path": str(automation), "json": True})()

    assert tm_daily_health_summary.cmd_automation_contract(args) == 0
    report = json.loads("".join(captured))
    assert report["status"] == "ok"


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
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "ok": True,
        "mem0_reachable": True,
        "mem0_api_reachable": True,
        "mem0_api_latency_ms": 123.4,
        "mem0_api_error": None,
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
        "health_json": "health.json",
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
    assert summary["health_probe"]["mem0_api_reachable"] is True
    assert summary["health_probe"]["mem0_api_latency_ms"] == 123.4
    assert summary["answer_eval"]["status_correct"] == 25
    assert summary["answer_trace"]["failure_count"] == 1
    assert summary["retrieval_eval_lexical"]["hit3"] == 67
    assert summary["retrieval_eval_hybrid"]["hit1"] == 80
    assert summary["commit_sha"] == "abc123"


def _daily_summary() -> dict:
    return {
        "schema_version": "daily-health-summary-v1",
        "health_color": "yellow",
        "blocking_count": 0,
        "known_debt_count": 1,
        "new_problem_count": 0,
        "health_probe": {
            "mem0_reachable": True,
            "mem0_api_reachable": True,
            "mem0_api_latency_ms": 123.4,
            "mem0_api_error": None,
        },
        "known_debt_changes": {"new": 0, "known": 1, "resolved": 0, "worsened": 0},
        "answer_eval": {"case_count": 25, "status_correct": 25},
        "answer_trace": {"row_count": 10, "failure_count": 0},
        "retrieval_eval_lexical": {"case_count": 80, "hit3": 67},
        "retrieval_eval_hybrid": {"case_count": 80, "hit3": 80},
        "commit_sha": "abc123",
        "push_result": "pushed",
    }


def test_validate_daily_report_accepts_machine_summary_before_sources():
    text = "\n".join([
        "# daily",
        "## 机器可读摘要",
        json.dumps(_daily_summary(), ensure_ascii=False, sort_keys=True),
        "## 来源",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(text, path="report.md")

    assert report["schema_version"] == "daily-health-report-validation-v1"
    assert report["status"] == "ok"
    assert report["summary_present"] is True
    assert report["missing_fields"] == []


def test_validate_daily_report_rejects_missing_health_probe():
    summary = _daily_summary()
    summary.pop("health_probe")
    text = "\n".join([
        "# daily",
        "## 机器可读摘要",
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "## 来源",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(text, path="report.md")

    assert report["status"] == "fail"
    assert "health_probe" in report["missing_fields"]


def test_cmd_validate_report_json_exit_codes(tmp_path, monkeypatch):
    report_path = tmp_path / "daily.md"
    report_path.write_text(
        "\n".join([
            "# daily",
            "## 机器可读摘要",
            json.dumps(_daily_summary(), ensure_ascii=False, sort_keys=True),
            "## 来源",
            "- live checks",
        ]),
        encoding="utf-8",
    )
    captured: list[str] = []
    monkeypatch.setattr(tm_daily_health_summary.sys.stdout, "write", captured.append)

    args = type("Args", (), {"path": str(report_path), "today": None, "json": True})()

    assert tm_daily_health_summary.cmd_validate_report(args) == 0
    assert json.loads("".join(captured))["status"] == "ok"
