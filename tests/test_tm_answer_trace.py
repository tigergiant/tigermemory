from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_answer_trace  # type: ignore[import-not-found]


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _row(trace_id: str, status: str, query: str = "daily-health known debt") -> dict:
    return {
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
            "duration_ms": 123.4,
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


def test_failures_include_non_ok_without_query_text_by_default(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok"), _row("t-not-found", "not_found", query="secret query")])

    rows, _invalid = tm_answer_trace.load_trace_rows(log)
    failures = [tm_answer_trace.compact_row(row) for row in tm_answer_trace.failure_rows(rows)]

    assert [item["trace_id"] for item in failures] == ["t-not-found"]
    assert failures[0]["status"] == "not_found"
    assert "query" not in failures[0]
    assert failures[0]["query_hash"]


def test_summary_latest_zero_omits_latest_rows(tmp_path):
    log = tmp_path / "trace.jsonl"
    _write_jsonl(log, [_row("t-ok", "ok")])

    rows, invalid = tm_answer_trace.load_trace_rows(log)
    report = tm_answer_trace.summarize_rows(rows, invalid, latest=0)

    assert report["row_count"] == 1
    assert report["latest"] == []


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
