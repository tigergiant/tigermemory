from __future__ import annotations

import json

from tigermemory_answer import trace


def _write_trace(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) if not isinstance(row, str) else row for row in rows) + "\n",
        encoding="utf-8",
    )


def _row(trace_id: str, status: str = "ok", *, run_id: str | None = None, ts: str = "2026-05-25T09:00:00+08:00"):
    row = {
        "ts": ts,
        "trace_id": trace_id,
        "query": "Bearer secret-token should redact",
        "query_hash": "storedhash",
        "status": status,
        "summary": "summary token=hidden",
        "warnings": ["warn api_key=hidden"],
        "claims": [{"id": "c1", "support": ["e1"]}],
        "evidence": [{
            "id": "e1",
            "source": "wiki",
            "path": "wiki/systems/example.md",
            "title": "Example",
            "excerpt": "raw excerpt",
            "score": 3.0,
            "authority": 90.0,
            "relevance": 2.0,
            "source_role": "canonical_wiki",
        }],
        "trace": {
            "query_class": "recall",
            "duration_ms": 25.5,
            "calls": [{"purpose": "memory_answer", "ok": status != "error", "query": "Bearer call-token"}],
            "selected_evidence": ["e1"],
            "evidence_gate": [{"keep": True}, {"keep": False}],
            "authority_scores": [{"id": "e1", "authority": 90.0}],
            "conflict_scan": None,
        },
    }
    if run_id:
        row["run_id"] = run_id
        row["trace"]["run_id"] = run_id
    return row


def test_load_trace_rows_skips_invalid_jsonl(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_trace(log, [_row("ok-1"), "{not-json", ["not", "object"]])

    rows, invalid = trace.load_trace_rows(log)

    assert [row["trace_id"] for row in rows] == ["ok-1"]
    assert [item["line_no"] for item in invalid] == [2, 3]


def test_select_rows_filters_by_run_id():
    rows = [_row("a", run_id="run-a"), _row("b", run_id="run-b")]

    selected, selected_run_id = trace.select_rows(rows, run_id="run-b")

    assert selected_run_id == "run-b"
    assert [row["trace_id"] for row in selected] == ["b"]


def test_summarize_rows_groups_by_status():
    rows = [_row("ok", "ok"), _row("err", "error"), _row("nf", "not_found")]

    summary = trace.summarize_rows(rows, [{"line_no": 99}], latest=2)

    assert summary["row_count"] == 3
    assert summary["invalid_row_count"] == 1
    assert summary["status_counts"] == {"error": 1, "not_found": 1, "ok": 1}
    assert summary["evidence_gate"] == {"kept": 3, "dropped": 3}
    assert [item["trace_id"] for item in summary["latest"]] == ["err", "nf"]


def test_find_by_trace_id_returns_match_and_none():
    rows = [_row("first"), _row("second"), _row("first", "conflict")]

    assert trace.find_by_trace_id(rows, "first")["status"] == "conflict"
    assert trace.find_by_trace_id(rows, "missing") is None


def test_latest_run_id_handles_empty_trace_dir():
    assert trace.latest_run_id([]) is None
    assert trace.select_rows([], latest_run=True) == ([], None)


def test_failure_rows_preserves_trace_order_for_matching_statuses():
    rows = [_row("ok", "ok"), _row("old", "error"), _row("new", "conflict")]

    failures = trace.failure_rows(rows, statuses=("conflict", "error"))

    assert [row["trace_id"] for row in failures] == ["old", "new"]


def test_compact_row_omits_raw_evidence_and_query_by_default():
    compact = trace.compact_row(_row("compact", "not_found"))

    assert compact["trace_id"] == "compact"
    assert compact["selected_evidence"] == ["e1"]
    assert "query" not in compact
    assert "excerpt" not in compact


def test_replay_row_redacts_sensitive_query_text(monkeypatch):
    calls = []

    def fake_redact(value: str) -> str:
        calls.append(value)
        return value.replace("Bearer", "[REDACTED]")

    monkeypatch.setattr(trace, "redact_secrets", fake_redact)

    replay = trace.replay_row(_row("replay"), include_query=True)

    assert replay["query"].startswith("[REDACTED]")
    assert replay["calls"][0]["query"].startswith("[REDACTED]")
    assert any("api_key=hidden" in value for value in calls)
