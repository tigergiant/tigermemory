from __future__ import annotations

import datetime as dt

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
