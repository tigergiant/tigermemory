from __future__ import annotations

import datetime as dt
import json

from tigermemory_digest import reflection


def test_frontmatter_returns_mapping_and_body_after_yaml_fence():
    fm, body = reflection._frontmatter("---\nowner: codex\nstatus: active\n---\n正文\n")

    assert fm == {"owner": "codex", "status": "active"}
    assert body == "正文\n"


def test_parse_dt_converts_unix_timestamp_to_china_date():
    parsed = reflection._parse_dt("1767225600")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.strftime("%Y-%m-%d") == "2026-01-01"


def test_preview_collapses_whitespace_and_respects_limit():
    assert reflection._preview("alpha\n\n beta\tgamma", limit=12) == "alpha beta g"


def test_discard_review_candidates_keeps_high_signal_events_only():
    events = [
        {"event_id": "high", "score": 80, "is_transient": True, "text_excerpt": "important"},
        {"event_id": "normal", "score": 10, "is_transient": True, "text_excerpt": "ignore"},
    ]

    candidates = reflection.discard_review_candidates(events)

    assert len(candidates) == 1
    assert candidates[0]["event_id"] == "high"
    assert candidates[0]["reason"] == "high_score_discard"


def _write_discard_event(root, date: str, row: dict) -> None:
    path = root / date / "discard" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def test_discard_events_for_dates_merges_primary_and_wsl_roots(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    wsl = tmp_path / "wsl"
    _write_discard_event(primary, "2026-06-04", {"event_id": "d-root", "score": 10})
    _write_discard_event(wsl, "2026-06-04", {"event_id": "wsl-root", "score": 80})
    monkeypatch.setattr(reflection, "WSL_DISCARD_ROOT", wsl)

    rows = reflection.discard_events_for_dates(["2026-06-04"], audit_root=primary)

    assert [row["event_id"] for row in rows] == ["d-root", "wsl-root"]


def test_discard_events_for_dates_deduplicates_cross_root_events(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    wsl = tmp_path / "wsl"
    event = {"event_id": "same", "text_sha256_12": "abc123", "score": 90}
    _write_discard_event(primary, "2026-06-04", event)
    _write_discard_event(wsl, "2026-06-04", event)
    monkeypatch.setattr(reflection, "WSL_DISCARD_ROOT", wsl)

    rows = reflection.discard_events_for_dates(["2026-06-04"], audit_root=primary)

    assert len(rows) == 1
    assert rows[0]["event_id"] == "same"


def test_inbox_action_groups_split_archive_promote_and_keep_rows():
    rows = [
        reflection.InboxAuditRow("archive.md", "2026-01-01", 20, "codex", "systems", "A", "p", "s", "s", "archive", "old", "archive", "old", True),
        reflection.InboxAuditRow("promote.md", "2026-01-02", 1, "codex", "systems", "B", "p", "s", "s", "promote_to_mem0", "useful", "promote_to_mem0", "useful"),
        reflection.InboxAuditRow("keep.md", "2026-01-03", 1, "codex", "systems", "C", "p", "s", "s", "keep_in_inbox", "wait", "keep_in_inbox", "wait"),
    ]

    archive, promote, keep = reflection._inbox_action_groups(rows)

    assert [row.path for row in archive] == ["archive.md"]
    assert [row.path for row in promote] == ["promote.md"]
    assert [row.path for row in keep] == ["keep.md"]
