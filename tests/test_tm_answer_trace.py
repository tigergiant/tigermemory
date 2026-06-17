from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_answer_trace  # type: ignore[import-not-found]


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _row(
    trace_id: str,
    status: str,
    query: str = "daily-health known debt",
    run_id: str | None = None,
    duration_ms: float | None = 123.4,
) -> dict:
    row = {
        "ts": "2026-05-17T21:30:00+08:00",
        "trace_id": trace_id,
        "query": query,
        "status": status,
        "summary": "summary",
        "claims": [{"id": "c1", "support": ["e1"]}] if status == "ok" else [],
        "warnings": ["warn"] if status != "ok" else [],
        "evidence": [{
            "id": "e1",
            "source": "wiki",
            "path": "wiki/operations/daily-health-known-debt.md",
            "title": "known debt",
            "excerpt": "this long excerpt must not appear in compact output",
            "score": 10.0,
            "authority": 98.0,
            "relevance": 2.0,
            "source_role": "canonical_wiki",
        }],
        "trace": {
            "query_class": "synthesis",
            "selected_evidence": ["e1"],
            "calls": [
                {"tool": "search_tigermemory", "query": query, "group_counts": {"wiki": 1}},
                {"tool": "DeepSeek", "purpose": "memory_answer", "ok": status != "error"},
            ],
            "evidence_gate": [{"path": "p1", "keep": True}, {"path": "p2", "keep": False}],
            "authority_scores": [{"id": "e1", "path": "p1", "authority": 98.0, "relevance": 2.0}],
            "conflict_scan": {"conflict": status == "conflict"},
        },
    }
    if duration_ms is not None:
        row["trace"]["duration_ms"] = duration_ms
    if run_id:
        row["run_id"] = run_id
        row["trace"]["run_id"] = run_id
    return row


def test_summary_counts_status_llm_and_gate(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok"), _row("t-error", "error"), _row("t-conflict", "conflict")])

    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid)

    assert report["row_count"] == 3
    assert report["trace_present_count"] == 3
    assert report["trace_missing_count"] == 0
    assert report["status_counts"] == {"conflict": 1, "error": 1, "ok": 1}
    assert report["llm_counts"] == {"failed": 1, "ok": 2}
    assert report["evidence_gate"] == {"kept": 3, "dropped": 3}
    assert report["duration_ms"]["count"] == 3
    assert report["duration_ms"]["avg"] == 123.4
    assert report["duration_ms"]["p50"] == 123.4
    assert report["duration_ms"]["p95"] == 123.4
    assert report["duration_ms"]["max"] == 123.4


def test_summary_recommendation_quality_counts_for_sidecar_and_boost_rows(tmp_path):
    log = tmp_path / "trace.jsonl"
    rows = []

    shown_row = _row("t-show", "ok")
    shown_row["trace"]["related_evidence_candidates"] = {
        "status": "ok",
        "candidate_count": 3,
    }
    shown_row["trace"]["recommendation_boosted_candidates"] = {
        "status": "ok",
        "candidate_count": 2,
        "accepted_count": 1,
        "rejected_count": 0,
        "candidates": [
            {
                "action": "accepted_to_evidence",
                "target_title": "target title should stay internal",
                "reason_category": "policy",
                "reason_text": "raw rejected reason text should be hidden",
            },
            {
                "action": "rejected_by_gate",
                "target_title": "target title should stay internal",
                "reason_category": "recency",
                "reason_text": "raw rejected reason text should be hidden",
            },
        ],
    }
    rows.append(shown_row)

    blocked_row = _row("t-blocked", "not_found")
    blocked_row["trace"]["related_evidence_candidates"] = {
        "status": "no_selected_evidence",
        "candidate_count": 0,
    }
    blocked_row["trace"]["recommendation_boosted_candidates"] = {
        "status": "error",
        "candidate_count": 1,
        "candidates": [
            {
                "action": "rejected_by_gate",
                "target_title": "target title should stay internal",
                "reason_category": "policy",
                "reason_text": "raw rejected reason text should be hidden",
            },
            {
                "action": "rejected_by_gate",
                "target_title": "target title should stay internal",
                "reason_category": "policy",
            },
        ],
    }
    rows.append(blocked_row)

    fallback_row = _row("t-fallback", "ok")
    fallback_row["trace"]["related_evidence_candidates"] = {
        "status": "fallback",
        "candidate_count": 2,
    }
    fallback_row["trace"]["recommendation_boosted_candidates"] = {
        "status": "missing",
        "candidate_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "candidates": [
            {"action": "ignored", "target_title": "target title should stay internal"},
        ],
    }
    rows.append(fallback_row)

    _write_jsonl(log, rows)
    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid)
    quality = report["recommendation_quality"]

    assert quality["recommendation_shown_count"] == 2
    assert quality["recommendation_candidate_count"] == 5
    assert quality["recommendation_boost_attempted_count"] == 2
    assert quality["recommendation_used_as_evidence_count"] == 1
    assert quality["recommendation_blocked_by_gate_count"] == 3
    assert quality["status_counts"]["sidecar"] == {"fallback": 1, "no_selected_evidence": 1, "ok": 1}
    assert quality["status_counts"]["boost"] == {"error": 1, "missing": 1, "ok": 1}
    assert quality["top_noisy_reasons"][0] == {"reason_category": "policy", "count": 2}


def test_summary_json_has_no_raw_recommendation_payload_leaks(tmp_path):
    log = tmp_path / "trace.jsonl"
    row = _row("t-privacy", "ok", query="raw query text should not leak")
    row["query_expanded"] = "expanded query should not leak"
    row["evidence"][0]["excerpt"] = "evidence excerpt should not leak"
    row["trace"]["related_evidence_candidates"] = {"status": "ok", "candidate_count": 1}
    row["trace"]["recommendation_boosted_candidates"] = {
        "status": "ok",
        "candidate_count": 1,
        "accepted_count": 0,
        "rejected_count": 0,
        "candidates": [
            {
                "action": "rejected_by_gate",
                "target_title": "target title should not leak",
                "reason_category": "policy",
                "reason": "raw rejected reason text should not leak",
                "reason_text": "raw rejected reason text should not leak",
            }
        ],
    }

    _write_jsonl(log, [row])
    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid)
    encoded = json.dumps(report, ensure_ascii=False)

    assert "raw query text should not leak" not in encoded
    assert "expanded query should not leak" not in encoded
    assert "raw rejected reason text should not leak" not in encoded
    assert "target title should not leak" not in encoded
    assert "evidence excerpt should not leak" not in encoded
    assert "raw_query_text_should_not_leak" not in encoded


def test_summary_recommendation_quality_sanitizes_metric_tokens(tmp_path):
    log = tmp_path / "trace.jsonl"
    raw_status = "tiger_name"
    raw_reason = "phone"
    row = _row("t-token-safety", "ok")
    row["trace"]["related_evidence_candidates"] = {
        "status": raw_status,
        "candidate_count": 1,
    }
    row["trace"]["recommendation_boosted_candidates"] = {
        "status": raw_status,
        "candidate_count": 1,
        "candidates": [
            {
                "action": "rejected_by_gate",
                "reason_category": raw_reason,
            }
        ],
    }

    _write_jsonl(log, [row])
    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid)
    encoded = json.dumps(report, ensure_ascii=False)

    assert raw_status not in encoded
    assert raw_reason not in encoded
    assert report["recommendation_quality"]["status_counts"]["sidecar"] == {"unknown": 1}
    assert report["recommendation_quality"]["status_counts"]["boost"] == {"unknown": 1}
    assert report["recommendation_quality"]["top_noisy_reasons"] == [{"reason_category": "unknown", "count": 1}]


def test_summary_duration_percentiles_ignore_missing_values(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [
        _row("t-1", "ok", duration_ms=100.0),
        _row("t-2", "ok", duration_ms=200.0),
        _row("t-3", "ok", duration_ms=300.0),
        _row("t-4", "ok", duration_ms=400.0),
        _row("t-missing", "ok", duration_ms=None),
    ])

    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid)

    assert report["duration_ms"]["count"] == 4
    assert report["duration_ms"]["avg"] == 250.0
    assert report["duration_ms"]["min"] == 100.0
    assert report["duration_ms"]["p50"] == 250.0
    assert report["duration_ms"]["p95"] == 385.0
    assert report["duration_ms"]["max"] == 400.0


def test_failures_include_non_ok_without_query_text_by_default(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok"), _row("t-not-found", "not_found", query="secret query")])

    rows, _invalid = tm_answer_trace.load_trace_rows(log)
    failures = [tm_answer_trace.compact_row(row) for row in tm_answer_trace.failure_rows(rows)]

    assert [item["trace_id"] for item in failures] == ["t-not-found"]
    assert failures[0]["status"] == "not_found"
    assert "query" not in failures[0]
    assert failures[0]["query_hash"]


def test_real_failure_intake_summarizes_p5_candidates_without_raw_query(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [
        _row("t-ok", "ok", query="raw ok query"),
        _row("t-not-found", "not_found", query="raw missing query", run_id="real-run"),
        _row("t-conflict", "conflict", query="raw conflict query", run_id="real-run"),
        _row("t-error", "error", query="raw error query", run_id="real-run"),
    ])

    rows, invalid = tm_answer_trace.load_trace_rows(log, run_id="real-run")
    report = tm_answer_trace.summarize_rows(rows, invalid, latest=5)
    intake = report["real_failure_intake"]
    encoded = json.dumps(intake, ensure_ascii=False)

    assert intake["schema_version"] == "memory-answer-real-failure-intake-v1"
    assert intake["candidate_count"] == 3
    assert intake["status_counts"] == {"conflict": 1, "error": 1, "not_found": 1}
    assert intake["run_id_counts"] == {"real-run": 3}
    assert intake["source_kind_counts"] == {"named_run": 3}
    assert len(intake["latest"]) == 3
    assert "query_hash" in intake["latest"][0]
    assert intake["latest"][0]["source_kind"] == "named_run"
    assert "raw missing query" not in encoded
    assert "raw conflict query" not in encoded
    assert "raw error query" not in encoded
    assert any("not_found" in action for action in intake["next_actions"])


def test_real_failure_intake_marks_diagnostic_runs_separately(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [
        _row("t-daily", "not_found", run_id="daily-health-2026-06-18"),
        _row("t-holdout", "not_found", run_id="p315-holdout-check"),
        _row("t-diagnosis", "error", run_id="p322-quality-packer"),
        _row("t-live", "conflict"),
    ])

    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid)
    intake = report["real_failure_intake"]

    assert intake["source_kind_counts"] == {
        "daily_health": 1,
        "diagnostic_or_eval": 1,
        "holdout": 1,
        "live_or_manual": 1,
    }
    assert any("diagnostic/eval" in action for action in intake["next_actions"])


def test_intake_cli_outputs_json_for_real_failures(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok", run_id="cli-run"), _row("t-error", "error", query="secret cli query", run_id="cli-run")])

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "tm_answer_trace.py"),
            "--log",
            str(log),
            "intake",
            "--json",
            "--run-id",
            "cli-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    report = json.loads(result.stdout)
    assert report["candidate_count"] == 1
    assert report["status_counts"] == {"error": 1}
    assert report["selected_run_id"] == "cli-run"
    assert "secret cli query" not in result.stdout


def test_compact_row_uses_stored_query_hash_without_raw_query(tmp_path):
    log = tmp_path / "trace.jsonl"
    row = _row("t-not-found", "not_found")
    row.pop("query", None)
    row["query_hash"] = "abc123hash"
    _write_jsonl(log, [row])

    rows, _invalid = tm_answer_trace.load_trace_rows(log)
    compact = tm_answer_trace.compact_row(rows[0], include_query=True)

    assert compact["query_hash"] == "abc123hash"
    assert "query" not in compact


def test_summary_latest_zero_omits_latest_rows(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok")])

    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid, latest=0)

    assert report["row_count"] == 1
    assert report["latest"] == []


def test_run_id_filter_and_summary_selection(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [
        _row("t-a1", "ok", run_id="run-a"),
        _row("t-b1", "not_found", run_id="run-b"),
        _row("t-b2", "error", run_id="run-b"),
    ])

    rows, invalid = tm_answer_trace.load_trace_rows(log, run_id="run-b")
    report = tm_answer_trace.summarize_rows(rows, invalid, selected_run_id="run-b")
    failures = tm_answer_trace.failure_rows(rows)

    assert [row["trace_id"] for row in rows] == ["t-b1", "t-b2"]
    assert report["selected_run_id"] == "run-b"
    assert report["run_id_counts"] == {"run-b": 2}
    assert report["run_id_missing_count"] == 0
    assert [row["trace_id"] for row in failures] == ["t-b1", "t-b2"]
    assert tm_answer_trace.compact_row(rows[0])["run_id"] == "run-b"


def test_latest_run_id_uses_latest_non_empty_run(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-a", "ok", run_id="run-a"), _row("t-no-run", "ok"), _row("t-b", "ok", run_id="run-b")])

    rows, _invalid = tm_answer_trace.load_trace_rows(log)
    selected, selected_run_id = tm_answer_trace.select_rows(rows, latest_run=True)

    assert selected_run_id == "run-b"
    assert [row["trace_id"] for row in selected] == ["t-b"]


def test_replay_is_compact_and_omits_evidence_excerpt(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-replay", "ok")])

    rows, _invalid = tm_answer_trace.load_trace_rows(log)
    row = tm_answer_trace.find_by_trace_id(rows, "t-replay")
    replay = tm_answer_trace.replay_row(row)

    assert replay["query"] == "daily-health known debt"
    assert replay["evidence"][0]["path"] == "wiki/operations/daily-health-known-debt.md"
    assert "excerpt" not in replay["evidence"][0]


def test_replay_no_query_omits_call_queries(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-replay", "ok")])

    rows, _invalid = tm_answer_trace.load_trace_rows(log)
    row = tm_answer_trace.find_by_trace_id(rows, "t-replay")
    replay = tm_answer_trace.replay_row(row, include_query=False)

    assert "query" not in replay
    assert "query" not in replay["calls"][0]


def test_tool_cli_shim_invokes_trace_main(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok", run_id="shim-smoke")])

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "tm_answer_trace.py"),
            "--log",
            str(log),
            "summary",
            "--json",
            "--run-id",
            "shim-smoke",
            "--latest",
            "0",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    report = json.loads(result.stdout)
    assert report["selected_run_id"] == "shim-smoke"
    assert report["row_count"] == 1
    assert result.stdout.strip()


def test_feedback_helpers_write_safely_and_summarize(tmp_path):
    log = tmp_path / "feedback.jsonl"
    event = tm_answer_trace.build_feedback_event(
        surface="review_ui",
        action="clicked",
        trace_id="trace-1",
        query="raw query text should not persist",
        run_id="run-1",
        target_path="wiki/operations/memory-answer.md",
        source_evidence_id="e-1",
        source_evidence_path="sources/ops/guide.md",
        candidate_rank=2,
        score_bucket="high",
        reason_categories=["policy", "recency"],
        use_hint="read_next",
    )
    stored = tm_answer_trace.append_feedback_event(event, path=log)
    tm_answer_trace.append_feedback_event(
        {
            "surface": "cli",
            "action": "selected",
            "trace_id": "trace-2",
            "target_path": "wiki/systems/memory-answer-p38-recommendation-plan.md",
            "candidate_rank": 1,
            "score_bucket": "low",
            "reason_categories": "relevance",
            "use_hint": "candidate_for_evidence",
        },
        path=log,
    )

    events, invalid = tm_answer_trace.load_feedback_events(log)
    report = tm_answer_trace.summarize_feedback_events(events, invalid)
    raw = log.read_text(encoding="utf-8")

    assert stored["query_hash"] == tm_answer_trace._query_hash("raw query text should not persist")
    assert "raw query text should not persist" not in raw
    assert "schema_version" in raw
    assert report["schema_version"] == "memory-answer-feedback-summary-v1"
    assert report["event_count"] == 2
    assert report["trace_count"] == 2
    assert report["invalid_row_count"] == 0
    assert report["action_counts"] == {"clicked": 1, "selected": 1}
    assert report["surface_counts"] == {"cli": 1, "review_ui": 1}
    assert report["score_bucket_counts"] == {"high": 1, "low": 1}
    assert report["use_hint_counts"] == {"candidate_for_evidence": 1, "read_next": 1}
    assert report["reason_category_counts"] == {"policy": 1, "recency": 1, "relevance": 1}
    assert report["reason_category_counts"].get("unknown") is None
    assert "target_path" in raw
    assert "wiki/person" not in raw


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/abs/path.md",
        "C:/Users/Giant/secret.md",
        "../wiki/systems/x.md",
        "https://example.com/x.md",
        "wiki/person/tiger.md",
        "sources/person/tiger.md",
        "wiki/systems/../person/tiger.md",
        "runtime/memory_answer_feedback/events.jsonl",
        ".tmp/memory-answer-feedback.jsonl",
        "tests/fixtures/example.md",
        "review-artifacts/p37-map-20260610/summary.json",
    ],
)
def test_feedback_event_rejects_unsafe_target_paths(unsafe_path):
    with pytest.raises(ValueError):
        tm_answer_trace.build_feedback_event(
            surface="cli",
            action="ignored",
            trace_id="trace-3",
            target_path=unsafe_path,
        )


def test_feedback_event_rejects_unsafe_source_evidence_paths():
    with pytest.raises(ValueError):
        tm_answer_trace.build_feedback_event(
            surface="cli",
            action="selected",
            trace_id="trace-source-path",
            source_evidence_path="wiki/person/tiger.md",
        )


def test_feedback_event_rejects_freeform_timestamp():
    with pytest.raises(ValueError):
        tm_answer_trace.build_feedback_event(
            surface="cli",
            action="clicked",
            trace_id="trace-4",
            ts="raw query text should not persist here",
        )


def test_tool_cli_shim_supports_feedback_record_and_summary(tmp_path):
    log = tmp_path / "feedback.jsonl"

    record = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "tm_answer_trace.py"),
            "--feedback-log",
            str(log),
            "feedback",
            "record",
            "--surface",
            "dashboard",
            "--action",
            "ignored",
            "--trace-id",
            "trace-cli",
            "--target-path",
            "wiki/operations/memory-answer-p38-recommendation-plan.md",
            "--reason-category",
            "policy",
            "--use-hint",
            "background_only",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    summary = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "tm_answer_trace.py"),
            "--feedback-log",
            str(log),
            "feedback",
            "summary",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    record_payload = json.loads(record.stdout)
    summary_payload = json.loads(summary.stdout)

    assert record_payload["ok"] is True
    assert record_payload["event"]["trace_id"] == "trace-cli"
    assert summary_payload["event_count"] == 1
    assert summary_payload["action_counts"] == {"ignored": 1}
    assert summary_payload["surface_counts"] == {"dashboard": 1}
    assert summary_payload["reason_category_counts"] == {"policy": 1}
    assert summary_payload["use_hint_counts"] == {"background_only": 1}
