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
Run tigermemory-config manager status --runtime openclaw --runtime hermes --json and pass runtime_config_manager into the machine summary.
Write the human-facing daily report and final closeout as 中文优先 / Chinese-first content; if English is needed, use 中英双文.
Run py tools/tm_answer_eval.py eval --run-id daily-health-YYYY-MM-DD, py tools/tm_answer_trace.py summary --run-id daily-health-YYYY-MM-DD, and py tools/tm_answer_trace.py failures --status error --run-id daily-health-YYYY-MM-DD.
Run py tools/tm_daily_health_summary.py prompt-audit --json to audit agent role prompts, agent identity coverage, ChatGPT facade prompt, requested_topic handling, and topic taxonomy.
Run py tools/tm_memory_eval.py eval and py tools/tm_memory_eval.py eval --recall hybrid --embedding-base-url http://127.0.0.1:19190/v1.
Run py tools/tm_daily_health_summary.py trend --json --days 14 > .tmp/daily-health/YYYY-MM-DD/daily-trend.json and pass daily_trend into the machine summary.
Run py tools/tm_daily_health_summary.py assemble with the health JSON and daily-trend.json, then place the result under ## 机器可读摘要.
Run py tools/tm_daily_health_summary.py validate-report --require-daily-trend --require-runtime-config-manager and confirm schema_version, health_probe, runtime_config_manager, and daily_trend are present.
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


def test_load_json_report_tolerates_utf8_bom(tmp_path):
    report = tmp_path / "bom.json"
    report.write_text("\ufeff{\"ok\": true}", encoding="utf-8")

    assert tm_daily_health_summary.load_json_report(report) == {"ok": True}


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


def test_prompt_audit_targets_follow_package_split():
    targets = {target["id"]: target["path"] for target in tm_daily_health_summary.PROMPT_AUDIT_TARGETS}

    assert targets["agent-onboarding-snapshot"] == (
        "packages/tigermemory-persona/src/tigermemory_persona/__init__.py"
    )
    assert targets["memory-routing-llm"] == (
        "packages/tigermemory-route/src/tigermemory_route/__init__.py"
    )


def test_audit_role_prompts_reports_marker_status(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_daily_health_summary, "PROMPT_AUDIT_TARGETS", (
        {
            "id": "ok-file",
            "path": "ok.md",
            "description": "complete",
            "markers": ("alpha", "beta"),
        },
        {
            "id": "missing-marker",
            "path": "bad.md",
            "description": "incomplete",
            "markers": ("gamma", "delta"),
        },
    ))
    role_doc = tmp_path / tm_daily_health_summary.ROLE_IDENTITY_COVERAGE_PATH
    role_doc.parent.mkdir(parents=True)
    role_doc.write_text(" ".join(sorted(tm_daily_health_summary.tm_core.AGENTS)), encoding="utf-8")
    (tmp_path / "ok.md").write_text("alpha beta", encoding="utf-8")
    (tmp_path / "bad.md").write_text("gamma only", encoding="utf-8")

    report = tm_daily_health_summary.audit_role_prompts(root=tmp_path)

    assert report["schema_version"] == "daily-health-prompt-audit-v1"
    assert report["status"] == "fail"
    assert report["passed_count"] == 2
    assert report["missing_count"] == 1
    assert report["agent_count"] == len(tm_daily_health_summary.tm_core.AGENTS)
    assert report["missing"][0]["id"] == "missing-marker"
    assert report["missing"][0]["missing_markers"] == ["delta"]


def test_compact_prompt_audit_omits_full_marker_details():
    compact = tm_daily_health_summary.compact_prompt_audit({
        "schema_version": "daily-health-prompt-audit-v1",
        "status": "fail",
        "check_count": 2,
        "passed_count": 1,
        "missing_count": 1,
        "missing": [{"id": "role-a", "missing_markers": ["secret prompt text"]}],
    })

    assert compact == {
        "schema_version": "daily-health-prompt-audit-v1",
        "status": "fail",
        "check_count": 2,
        "passed_count": 1,
        "missing_count": 1,
        "missing_ids": ["role-a"],
    }


def test_audit_session_handoff_cards_requires_codex_and_windsurf_cards():
    report = tm_daily_health_summary.audit_session_handoff_cards([
        {
            "id": "codex-1",
            "content": (
                "---\n"
                "memory_type: session-handoff\n"
                "session_id: codex-20260601-0900\n"
                "repo: D:\\tigermemory\n"
                "ide: codex\n"
                "agent: codex\n"
                "confidence: high\n"
                "source: agent\n"
                "---\n"
                "\n"
                "## Evidence Refs\n"
                "- commit: abc123\n"
                "- files: tools/x.py\n"
                "- test: pass\n"
                "- canvas_patch: none\n"
            ),
            "verified": {"direct_readback_ok": True},
        },
        {
            "id": "windsurf-1",
            "content": (
                "---\n"
                "memory_type: session-handoff\n"
                "session_id: windsurf-20260601-0900\n"
                "repo: D:\\tigermemory\n"
                "ide: windsurf\n"
                "agent: cascade\n"
                "confidence: high\n"
                "source: hook_auto\n"
                "---\n"
                "\n"
                "## Evidence Refs\n"
                "- commit: def456\n"
                "- files: hooks/x.ps1\n"
                "- test: pass\n"
                "- canvas_patch: none\n"
            ),
            "verified": {"direct_readback_ok": True},
        },
    ])

    assert report["schema_version"] == "session-handoff-audit-v1"
    assert report["status"] == "ok"
    assert report["passed_count"] == 2
    assert report["missing_ids"] == []
    assert report["by_ide"] == {"codex": 1, "windsurf": 1}
    assert report["coverage_slo"]["handoff_coverage_rate"] == 1.0
    assert report["coverage_slo"]["agent_written_rate"] == 0.5
    assert report["coverage_slo"]["fallback_rate"] == 0.0
    assert report["coverage_slo"]["verified_write_rate"] == 1.0
    assert report["metadata_contract"]["missing_count"] == 0
    assert report["evidence_refs"]["low_quality_count"] == 0


def test_audit_session_handoff_cards_strict_evidence_flags_low_quality():
    report = tm_daily_health_summary.audit_session_handoff_cards(
        [
            {
                "id": "codex-1",
                "content": (
                    "---\n"
                    "memory_type: session-handoff\n"
                    "session_id: codex-20260601-0900\n"
                    "repo: D:\\tigermemory\n"
                    "ide: codex\n"
                    "agent: codex\n"
                    "confidence: high\n"
                    "source: agent\n"
                    "---\n"
                    "\n"
                    "## Evidence Refs\n"
                    "- files: tools/x.py\n"
                ),
            },
            {
                "id": "windsurf-1",
                "content": (
                    "---\n"
                    "memory_type: session-handoff\n"
                    "session_id: windsurf-20260601-0900\n"
                    "repo: D:\\tigermemory\n"
                    "ide: windsurf\n"
                    "agent: cascade\n"
                    "confidence: high\n"
                    "source: hook_auto\n"
                    "---\n"
                    "\n"
                    "## Evidence Refs\n"
                    "- commit: def456\n"
                    "- files: hooks/x.ps1\n"
                    "- test: pass\n"
                    "- canvas_patch: none\n"
                ),
            },
        ],
        strict_evidence=True,
    )

    assert report["status"] == "fail"
    assert report["evidence_refs"]["strict"] is True
    assert report["evidence_refs"]["low_quality_count"] == 1
    low_quality = [row for row in report["evidence_refs"]["items"] if row["quality"] == "low"]
    assert low_quality[0]["card_id"] == "codex-1"
    assert "has_commit_ref" in low_quality[0]["missing"]


def test_audit_session_handoff_cards_reports_missing_windsurf_card():
    report = tm_daily_health_summary.audit_session_handoff_cards([
        {
            "id": "codex-1",
            "content": (
                "---\n"
                "memory_type: session-handoff\n"
                "ide: codex\n"
                "agent: codex\n"
                "source: agent\n"
                "---\n"
            ),
        },
    ])

    assert report["status"] == "fail"
    assert report["passed_count"] == 1
    assert report["missing_ids"] == ["windsurf_hook_card"]


def test_compact_runtime_config_manager_summarizes_targets():
    compact = tm_daily_health_summary.compact_runtime_config_manager({
        "ok": True,
        "action": "status",
        "canonical_sha256": "b5725f03c7989b7ea",
        "preference_ids": ["read_wiki_first", "commit_prefix"],
        "errors": [],
        "runtimes": [
            {
                "runtime": "openclaw",
                "mode": "apply",
                "apply_supported": True,
                "targets": [
                    {
                        "target_id": "workspace-agents",
                        "status": "ok",
                        "write_policy": "managed_block",
                        "readable": True,
                        "has_managed_block": True,
                        "canonical_match": True,
                        "missing_preference_ids": [],
                    },
                    {
                        "target_id": "runtime-tools",
                        "status": "missing_block",
                        "write_policy": "managed_block",
                        "readable": True,
                        "has_managed_block": False,
                        "canonical_match": None,
                        "missing_preference_ids": ["read_wiki_first"],
                    },
                ],
            },
            {
                "runtime": "hermes",
                "mode": "apply",
                "apply_supported": True,
                "targets": [
                    {
                        "target_id": "profile-config",
                        "status": "backup_only_readable",
                        "write_policy": "backup_only",
                        "readable": True,
                        "has_managed_block": False,
                        "canonical_match": None,
                        "missing_preference_ids": [],
                    }
                ],
            },
        ],
    })

    assert compact["status"] == "fail"
    assert compact["canonical_sha12"] == "b5725f03c798"
    assert compact["preference_count"] == 2
    assert compact["runtime_count"] == 2
    assert compact["target_count"] == 3
    assert compact["bad_target_count"] == 1
    assert compact["bad_target_ids"] == ["openclaw:runtime-tools"]
    assert compact["runtimes"][0]["status_counts"]["missing_block"] == 1
    assert compact["runtimes"][0]["missing_block_count"] == 1


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
        "run_id": "daily-health-test",
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
        "run_id": "daily-health-test",
        "failure_count": 1,
        "failure_ids": ["case-a"],
    }


def test_compact_trace_summary_preserves_answer_trace_recommendation_quality():
    compact = tm_daily_health_summary.compact_trace_summary({
        "row_count": 12,
        "trace_present_count": 12,
        "status_counts": {"ok": 10, "not_found": 2},
        "recommendation_quality": {
            "recommendation_shown_count": 4,
            "recommendation_candidate_count": 9,
            "recommendation_boost_attempted_count": 2,
            "recommendation_used_as_evidence_count": 3,
            "recommendation_blocked_by_gate_count": 1,
            "status_counts": {
                "sidecar": {"ok": 2, "no_selected_evidence": 2},
                "boost": {"ok": 1, "missing": 1},
            },
            "top_noisy_reasons": [{"reason_category": "recency", "count": 1}],
        },
    }, {"failure_count": 1})

    assert compact["recommendation_quality"]["recommendation_shown_count"] == 4
    assert compact["recommendation_quality"]["recommendation_candidate_count"] == 9
    assert compact["recommendation_quality"]["recommendation_boost_attempted_count"] == 2
    assert compact["recommendation_quality"]["recommendation_used_as_evidence_count"] == 3
    assert compact["recommendation_quality"]["recommendation_blocked_by_gate_count"] == 1
    assert compact["recommendation_quality"]["status_counts"]["sidecar"]["no_selected_evidence"] == 2
    assert compact["recommendation_quality"]["top_noisy_reasons"][0]["reason_category"] == "recency"


def test_compact_trace_summary_allowlists_recommendation_quality_fields():
    raw_payload = "raw_query_or_reason_should_not_leak"
    short_private_token = "tiger_name"
    compact = tm_daily_health_summary.compact_trace_summary({
        "row_count": 1,
        "recommendation_quality": {
            "recommendation_shown_count": 1,
            "recommendation_candidate_count": 2,
            "recommendation_boost_attempted_count": 1,
            "recommendation_used_as_evidence_count": 1,
            "recommendation_blocked_by_gate_count": 1,
            "rows": [{"query": raw_payload, "title": raw_payload, "excerpt": raw_payload}],
            "query": raw_payload,
            "title": raw_payload,
            "excerpt": raw_payload,
            "raw_reason": raw_payload,
            "status_counts": {
                "sidecar": {raw_payload: 1, short_private_token: 1, "ok": 1},
                "boost": {"ok": 1},
            },
            "top_noisy_reasons": [
                {"reason_category": raw_payload, "count": 1, "reason": raw_payload},
                {"reason_category": short_private_token, "count": 1},
                {"reason_category": "recency", "count": 2},
            ],
        },
    }, None)

    encoded = json.dumps(compact, ensure_ascii=False)

    assert raw_payload not in encoded
    assert short_private_token not in encoded
    quality = compact["recommendation_quality"]
    assert "rows" not in quality
    assert "query" not in quality
    assert "title" not in quality
    assert "excerpt" not in quality
    assert quality["status_counts"]["sidecar"] == {"ok": 1, "unknown": 2}
    assert quality["top_noisy_reasons"] == [
        {"reason_category": "recency", "count": 2},
        {"reason_category": "unknown", "count": 2},
    ]


def test_compact_trace_summary_allowlists_feedback_summary_fields():
    raw_payload = "raw_feedback_payload_should_not_leak"
    compact = tm_daily_health_summary.compact_trace_summary({
        "row_count": 1,
        "recommendation_quality": {
            "feedback_summary": {
                "schema_version": "memory-answer-feedback-summary-v1",
                "event_count": 3,
                "trace_count": 2,
                "invalid_row_count": 1,
                "action_counts": {"clicked": 1, "ignored": 1, "selected": 1, raw_payload: 9},
                "surface_counts": {"cli": 2, raw_payload: 1},
                "score_bucket_counts": {"high": 1, "mid": 1, "low": 1, raw_payload: 4},
                "use_hint_counts": {"read_next": 1, "candidate_for_evidence": 1, "background_only": 1, raw_payload: 2},
                "reason_category_counts": {"policy": 1, raw_payload: 2},
                "rows": [{"target_path": raw_payload, "query_hash": raw_payload}],
                "target_path": raw_payload,
                "query_hash": raw_payload,
            },
        },
    }, None)

    encoded = json.dumps(compact, ensure_ascii=False)

    assert raw_payload not in encoded
    feedback = compact["recommendation_quality"]["feedback_summary"]
    assert feedback["event_count"] == 3
    assert feedback["trace_count"] == 2
    assert feedback["invalid_row_count"] == 1
    assert feedback["action_counts"] == {"clicked": 1, "ignored": 1, "selected": 1, "unknown": 9}
    assert feedback["surface_counts"] == {"cli": 2, "unknown": 1}
    assert feedback["score_bucket_counts"] == {"high": 1, "low": 1, "mid": 1, "unknown": 4}
    assert feedback["use_hint_counts"] == {"background_only": 1, "candidate_for_evidence": 1, "read_next": 1, "unknown": 2}
    assert feedback["reason_category_counts"] == {"policy": 1, "unknown": 2}


def _write_daily_report(path: pathlib.Path, summary: dict) -> None:
    path.write_text(
        "\n".join([
            "# daily",
            "## 中文总览",
            "已验证：ok。推断：none。待确认：none。规划：continue。",
            "## 机器可读摘要",
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            "## 来源",
            "- fixture",
        ]),
        encoding="utf-8",
    )


def test_daily_health_trend_summarizes_historical_machine_summaries(tmp_path, monkeypatch):
    reports = tmp_path / "wiki/operations/daily-health"
    reports.mkdir(parents=True)
    monkeypatch.setattr(tm_daily_health_summary, "REPO_ROOT", tmp_path)
    base = _daily_summary()
    day1 = {
        **base,
        "health_color": "green",
        "blocking_count": 0,
        "new_problem_count": 0,
        "known_debt_count": 0,
        "health_probe": {**base["health_probe"], "mem0_api_latency_ms": 100.0},
        "answer_trace": {"row_count": 25, "failure_count": 0, "duration_ms": {"p95": 300.0}},
        "retrieval_eval_hybrid": {"case_count": 80, "hit3": 80},
    }
    day2 = {
        **base,
        "health_color": "red",
        "blocking_count": 2,
        "new_problem_count": 1,
        "known_debt_count": 2,
        "known_debt_changes": {"new": 1, "known": 0, "resolved": 0, "worsened": 1},
        "health_probe": {**base["health_probe"], "mem0_api_reachable": False, "mem0_api_latency_ms": 250.0},
        "answer_eval": {"case_count": 25, "status_correct": 24, "failure_count": 1},
        "answer_trace": {"row_count": 25, "failure_count": 1, "duration_ms": {"p95": 900.0}},
        "retrieval_eval_lexical": {"case_count": 80, "hit3": 79},
        "retrieval_eval_hybrid": {"case_count": 80, "hit3": 80},
    }
    _write_daily_report(reports / "2026-05-18.md", day1)
    _write_daily_report(reports / "2026-05-19.md", day2)

    trend = tm_daily_health_summary.build_daily_health_trend(reports, days=14)

    assert trend["schema_version"] == "daily-health-trend-v1"
    assert trend["report_count"] == 2
    assert trend["date_range"] == {"start": "2026-05-18", "end": "2026-05-19"}
    assert trend["health_color_counts"] == {"green": 1, "red": 1}
    assert trend["latest"]["date"] == "2026-05-19"
    assert trend["totals"]["blocking_count"] == 2
    assert trend["totals"]["known_debt_new"] == 1
    assert trend["answer_eval"]["min_status_rate"] == 0.96
    assert trend["answer_trace"]["max_p95_ms"] == 900.0
    assert trend["health_probe"]["unreachable_days"] == ["2026-05-19"]
    assert trend["runtime_config_manager"]["latest_status"] == "ok"
    assert trend["runtime_config_manager"]["bad_days"] == []
    assert trend["problem_days"] == ["2026-05-19"]


def test_assemble_summary_from_fixture_jsons(tmp_path, monkeypatch):
    answer = tmp_path / "answer.json"
    trace = tmp_path / "trace.json"
    failures = tmp_path / "failures.json"
    lexical = tmp_path / "lexical.json"
    hybrid = tmp_path / "hybrid.json"
    prompt_audit = tmp_path / "prompt-audit.json"
    manager_status = tmp_path / "manager-status.json"
    trend = tmp_path / "daily-trend.json"
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
        "run_id": "daily-health-2026-05-18",
        "failures": [],
    }), encoding="utf-8")
    trace.write_text(json.dumps({
        "row_count": 10,
        "invalid_row_count": 0,
        "trace_present_count": 3,
        "selected_run_id": "daily-health-2026-05-18",
        "run_id_counts": {"daily-health-2026-05-18": 10},
        "run_id_missing_count": 0,
        "status_counts": {"ok": 9, "not_found": 1},
        "llm_counts": {"ok": 3, "missing": 7},
        "duration_ms": {"count": 10, "avg": 220.0, "p50": 180.0, "p95": 420.0, "max": 450.0},
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
    prompt_audit.write_text(json.dumps({
        "schema_version": "daily-health-prompt-audit-v1",
        "status": "ok",
        "check_count": 8,
        "passed_count": 8,
        "missing_count": 0,
        "agent_count": 13,
        "missing": [],
    }), encoding="utf-8")
    manager_status.write_text(json.dumps({
        "ok": True,
        "action": "status",
        "canonical_sha256": "b5725f03c7989b7ea",
        "preference_ids": ["read_wiki_first"],
        "errors": [],
        "runtimes": [
            {
                "runtime": "openclaw",
                "mode": "apply",
                "apply_supported": True,
                "targets": [
                    {
                        "target_id": "workspace-agents",
                        "status": "ok",
                        "write_policy": "managed_block",
                        "readable": True,
                        "has_managed_block": True,
                        "canonical_match": True,
                        "missing_preference_ids": [],
                    }
                ],
            }
        ],
    }), encoding="utf-8")
    trend.write_text(json.dumps({
        "schema_version": "daily-health-trend-v1",
        "report_count": 2,
        "date_range": {"start": "2026-05-17", "end": "2026-05-18"},
        "latest": {"date": "2026-05-18", "health_color": "yellow"},
        "health_color_counts": {"yellow": 2},
        "totals": {"blocking_count": 1},
        "answer_eval": {"latest_status_rate": 1.0},
        "answer_trace": {"latest_p95_ms": 420.0},
        "retrieval_eval": {"hybrid_latest_hit3_rate": 1.0},
        "health_probe": {"latest_mem0_api_reachable": True},
        "runtime_config_manager": {"latest_status": "ok", "bad_days": []},
        "problem_day_count": 1,
        "problem_days": ["2026-05-18"],
        "errors": [],
    }), encoding="utf-8")
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
        "runtime_config_manager": "manager-status.json",
        "answer_eval": "answer.json",
        "answer_trace_summary": "trace.json",
        "answer_trace_failures": "failures.json",
        "prompt_audit": "prompt-audit.json",
        "retrieval_lexical": "lexical.json",
        "retrieval_hybrid": "hybrid.json",
        "daily_trend": "daily-trend.json",
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
    assert summary["runtime_config_manager"]["status"] == "ok"
    assert summary["runtime_config_manager"]["target_count"] == 1
    assert summary["answer_eval"]["status_correct"] == 25
    assert summary["answer_trace"]["failure_count"] == 1
    assert summary["answer_trace"]["duration_ms"]["p95"] == 420.0
    assert summary["prompt_audit"]["status"] == "ok"
    assert summary["prompt_audit"]["check_count"] == 8
    assert summary["prompt_audit"]["agent_count"] == 13
    assert summary["retrieval_eval_lexical"]["hit3"] == 67
    assert summary["retrieval_eval_hybrid"]["hit1"] == 80
    assert summary["daily_trend"]["report_count"] == 2
    assert summary["daily_trend"]["latest"]["date"] == "2026-05-18"
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
        "runtime_config_manager": {
            "status": "ok",
            "runtime_count": 2,
            "target_count": 6,
            "bad_target_count": 0,
            "error_count": 0,
        },
        "known_debt_changes": {"new": 0, "known": 1, "resolved": 0, "worsened": 0},
        "answer_eval": {"case_count": 25, "status_correct": 25},
        "answer_trace": {"row_count": 10, "failure_count": 0},
        "prompt_audit": {
            "schema_version": "daily-health-prompt-audit-v1",
            "status": "ok",
            "check_count": 8,
            "passed_count": 8,
            "missing_count": 0,
            "agent_count": 13,
            "missing_ids": [],
        },
        "retrieval_eval_lexical": {"case_count": 80, "hit3": 67},
        "retrieval_eval_hybrid": {"case_count": 80, "hit3": 80},
        "commit_sha": "abc123",
        "push_result": "pushed",
    }


def test_validate_daily_report_accepts_machine_summary_before_sources():
    text = "\n".join([
        "# daily",
        "## 中文总览",
        "已验证：ok。推断：none。待确认：none。规划：continue。",
        "## 已验证现状",
        "ok",
        "## 推断",
        "none",
        "## 待确认",
        "none",
        "## 规划",
        "continue",
        "## 机器可读摘要",
        json.dumps(_daily_summary(), ensure_ascii=False, sort_keys=True),
        "## 来源",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(text, path="report.md", require_runtime_config_manager=True)

    assert report["schema_version"] == "daily-health-report-validation-v1"
    assert report["status"] == "ok"
    assert report["summary_present"] is True
    assert report["missing_fields"] == []


def test_validate_daily_report_rejects_missing_health_probe():
    summary = _daily_summary()
    summary.pop("health_probe")
    text = "\n".join([
        "# daily",
        "## 中文总览",
        "已验证：ok。推断：none。待确认：none。规划：continue。",
        "## 机器可读摘要",
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "## 来源",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(
        text,
        path="report.md",
        require_runtime_config_manager=True,
    )

    assert report["status"] == "fail"
    assert "health_probe" in report["missing_fields"]


def test_validate_daily_report_rejects_missing_runtime_config_manager():
    summary = _daily_summary()
    summary.pop("runtime_config_manager")
    text = "\n".join([
        "# daily",
        "## 中文总览",
        "已验证：ok。推断：none。待确认：none。规划：continue。",
        "## 机器可读摘要",
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "## 来源",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(
        text,
        path="report.md",
        require_runtime_config_manager=True,
    )

    assert report["status"] == "fail"
    assert "runtime_config_manager" in report["missing_fields"]


def test_validate_daily_report_can_require_daily_trend():
    summary = _daily_summary()
    text = "\n".join([
        "# daily",
        "## 中文总览",
        "已验证：ok。推断：none。待确认：none。规划：continue。",
        "## 机器可读摘要",
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        "## 来源",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(text, path="report.md", require_daily_trend=True)

    assert report["status"] == "fail"
    assert "daily_trend" in report["missing_fields"]


def test_validate_daily_report_rejects_english_only_human_report():
    text = "\n".join([
        "# daily",
        "## Verified State",
        "ok",
        "## Machine-readable Summary",
        json.dumps(_daily_summary(), ensure_ascii=False, sort_keys=True),
        "## Sources",
        "- live checks",
    ])

    report = tm_daily_health_summary.validate_daily_report(text, path="report.md")

    assert report["status"] == "fail"
    assert "chinese-first language contract" in report["missing_sections"]
    assert "## 中文总览" in report["missing_language_markers"]


def test_cmd_validate_report_json_exit_codes(tmp_path, monkeypatch):
    report_path = tmp_path / "daily.md"
    report_path.write_text(
        "\n".join([
            "# daily",
            "## 中文总览",
            "已验证：ok。推断：none。待确认：none。规划：continue。",
            "## 机器可读摘要",
            json.dumps(_daily_summary(), ensure_ascii=False, sort_keys=True),
            "## 来源",
            "- live checks",
        ]),
        encoding="utf-8",
    )
    captured: list[str] = []
    monkeypatch.setattr(tm_daily_health_summary.sys.stdout, "write", captured.append)

    args = type("Args", (), {
        "path": str(report_path),
        "today": None,
        "require_daily_trend": False,
        "require_runtime_config_manager": False,
        "json": True,
    })()

    assert tm_daily_health_summary.cmd_validate_report(args) == 0
    assert json.loads("".join(captured))["status"] == "ok"
