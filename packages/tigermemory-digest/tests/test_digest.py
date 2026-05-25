from __future__ import annotations

import datetime

from tigermemory_digest import digest


def test_date_window_uses_china_day_boundaries():
    start, end = digest._date_window("2026-05-16")

    assert start.isoformat() == "2026-05-16T00:00:00+08:00"
    assert end.isoformat() == "2026-05-16T23:59:59.999999+08:00"


def test_created_at_local_accepts_epoch_seconds():
    parsed = digest._created_at_local(1778860800)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.strftime("%Y-%m-%d") == "2026-05-16"


def test_read_inbox_summary_strips_frontmatter_and_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "INBOX_SUMMARY_MAX_CHARS", 10)
    path = tmp_path / "item.md"
    path.write_text("---\nowner: codex\n---\nalpha beta gamma delta", encoding="utf-8")

    assert digest._read_inbox_summary(path) == "alpha beta…"


def test_render_digest_markdown_includes_source_ids_and_fact_text():
    rendered = digest._render_digest_markdown(
        "2026-05-16",
        {
            "tldr": "今日摘要",
            "facts": [{"fact_id": "fact-001", "topic": "systems", "text": "完成抽包", "source_type": "mem0", "source_id": "abc"}],
            "audit_suggestions": ["检查重复"],
        },
        [{"id": "abc", "content": "完成抽包", "created_at": "2026-05-16T10:00:00+08:00", "metadata_": {}}],
        [],
    )

    assert "完成抽包" in rendered
    assert "abc" in rendered
    assert "检查重复" in rendered


def test_generate_daily_digest_dry_run_skips_when_no_activity(monkeypatch):
    monkeypatch.setattr(digest, "_fetch_memories_for_date", lambda _date: [])
    monkeypatch.setattr(digest, "_list_inbox_for_date", lambda _date: [])

    result = digest.generate_daily_digest("2026-05-16", dry_run=True)

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "no_activity"
