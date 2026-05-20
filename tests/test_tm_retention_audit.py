from __future__ import annotations

import datetime as dt
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_retention_audit  # type: ignore[import-not-found]


def test_retention_audit_scores_pinned_duplicates_and_sensitive(monkeypatch):
    old = "2025-01-01T00:00:00+00:00"
    items = [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "content": "keep me",
            "created_at": old,
            "metadata": {"topic": "systems", "source": "codex", "is_pinned": True, "route_score": 95},
        },
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "content": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "created_at": old,
            "metadata": {"topic": "systems", "source": "codex", "route_score": 25},
        },
        {
            "id": "33333333-3333-4333-8333-333333333333",
            "content": "duplicate memory text",
            "created_at": old,
            "metadata": {"topic": "systems", "source": "codex", "route_score": 50},
        },
        {
            "id": "44444444-4444-4444-8444-444444444444",
            "content": "duplicate memory text",
            "created_at": old,
            "metadata": {"topic": "systems", "source": "codex", "route_score": 50},
        },
    ]
    now = dt.datetime(2026, 5, 21, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(tm_retention_audit, "fetch_mem0_items", lambda **_kwargs: items)
    monkeypatch.setattr(tm_retention_audit, "_promotion_marker_ids", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(tm_retention_audit, "_recent_mem0_hits", lambda *_args, **_kwargs: set())

    report = tm_retention_audit.run_retention_audit(now=now)

    assert report["dry_run"] is True
    assert report["item_count"] == 4
    by_id = {row["id"]: row for row in report["candidates"]}
    assert by_id["11111111-1111-4111-8111-111111111111"]["recommended_action"] == "keep_pinned"
    assert by_id["22222222-2222-4222-8222-222222222222"]["recommended_action"] == "review_sensitive"
    assert any("duplicate_fingerprint_count:2" in risk for risk in by_id["33333333-3333-4333-8333-333333333333"]["risks"])
    assert all("delete" not in warning.lower() or "no" in warning.lower() for warning in report["warnings"])


def test_retention_markdown_renders_audit_table(monkeypatch):
    report = {
        "generated_at": "2026-05-21T10:00:00+08:00",
        "dry_run": True,
        "item_count": 1,
        "action_counts": {"review": 1},
        "warnings": ["dry-run only"],
        "candidates": [{
            "retention_score": 55,
            "recommended_action": "review",
            "id": "abc",
            "topic": "systems",
            "source_agent": "codex",
            "reasons": ["aged"],
            "risks": ["missing_route_score"],
            "text_preview": "preview | with pipe",
        }],
    }

    markdown = tm_retention_audit.render_markdown(report)

    assert "# Tigermemory Retention Dry-Run Audit" in markdown
    assert "No records were deleted or updated." in markdown
    assert "preview \\| with pipe" in markdown


def test_retention_audit_returns_structured_failure_when_mem0_unavailable(monkeypatch):
    monkeypatch.setattr(
        tm_retention_audit,
        "fetch_mem0_items",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("missing env")),
    )

    report = tm_retention_audit.run_retention_audit()

    assert report["ok"] is False
    assert report["status"] == "fail"
    assert report["candidates"] == []
    assert "missing env" in report["error"]
