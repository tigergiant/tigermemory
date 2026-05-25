from __future__ import annotations

import datetime as dt
import json
from argparse import Namespace

from tigermemory_doctor import retention


NOW = dt.datetime(2026, 5, 25, tzinfo=dt.timezone.utc)


def test_load_mem0_json_handles_missing_file(tmp_path):
    missing = tmp_path / "missing.json"

    try:
        retention.load_mem0_json(str(missing))
    except FileNotFoundError as exc:
        assert "missing.json" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_score_item_flags_duplicate_fingerprint_review():
    item = {"id": "dup-1", "text": "duplicate useful memory text", "metadata": {"topic": "systems", "source": "codex"}}
    fp = retention._normalized_fingerprint("duplicate useful memory text")

    scored = retention.score_item(item, rank=1, now=NOW, duplicate_counts={fp: 4}, recent_hits=set(), promotion_ids=set())

    assert scored["duplicate_count"] == 4
    assert any("duplicate_fingerprint_count:4" == risk for risk in scored["risks"])
    assert scored["recommended_action"] == "review"


def test_score_item_marks_sensitive_text_for_review_sensitive():
    item = {"id": "secret-1", "text": "api_key=abc123456 should be reviewed", "metadata": {"topic": "systems", "source": "codex"}}

    scored = retention.score_item(item, rank=1, now=NOW, duplicate_counts={}, recent_hits=set(), promotion_ids=set())

    assert any(risk.startswith("sensitive:") for risk in scored["risks"])
    assert scored["retention_score"] >= 75


def test_run_retention_audit_sorts_review_candidates_by_score(tmp_path):
    data = [
        {"id": "keep-recent", "text": "fresh memory with enough detail", "created_at": "2026-05-20T00:00:00Z", "metadata": {"topic": "systems", "source": "codex", "last_accessed_at": "2026-05-24T00:00:00Z"}},
        {"id": "stale-risk", "text": "api_key=abc123", "created_at": "2025-01-01T00:00:00Z", "metadata": {"topic": "systems", "source": "codex", "last_accessed_at": "2025-01-01T00:00:00Z"}},
    ]
    source = tmp_path / "mem0.json"
    source.write_text(json.dumps(data), encoding="utf-8")

    report = retention.run_retention_audit(source="mem0-json", input_path=str(source), now=NOW)

    assert report["ok"] is True
    assert report["candidates"][0]["id"] == "stale-risk"
    assert report["candidates"][0]["retention_score"] >= report["candidates"][1]["retention_score"]


def test_render_markdown_includes_summary_and_actions():
    report = retention.run_retention_audit(source="sample", max_items=1, now=NOW)

    rendered = retention.render_markdown(report, limit=1)

    assert "# Tigermemory Retention Dry-Run Audit" in rendered
    assert "No records were deleted or updated." in rendered
    assert "| score | action | id | topic | source | reasons | risks | preview |" in rendered


def test_cmd_audit_writes_json_report_to_stdout(capsys):
    args = Namespace(source="sample", input=None, output=None, max_items=1, limit=1, json=True)

    exit_code = retention.cmd_audit(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["dry_run"] is True
