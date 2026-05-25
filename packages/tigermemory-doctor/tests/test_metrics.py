from __future__ import annotations

from tigermemory_doctor import metrics


def test_collect_new_lessons_counts_recent_lesson_files(monkeypatch):
    monkeypatch.setattr(metrics, "_git", lambda *args: "wiki/self-evolution/lessons/a.md\nwiki/self-evolution/lessons/index.md\n")

    assert metrics.collect_new_lessons("2026-05-01", "2026-06-01") == 1


def test_collect_inbox_backlog_groups_pending_files(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    monkeypatch.setattr(metrics, "INBOX_DIR", tmp_path)

    assert metrics.collect_inbox_backlog() == 1


def test_collect_guards_modified_detects_guard_commits(monkeypatch):
    def fake_git(*args):
        joined = " ".join(args)
        if ".githooks/" in joined:
            return "hooksha\n"
        if "tools/" in joined:
            return "__SHA__toolsha\ntools/tm_io.py\n__SHA__ignored\ntools/tm_digest.py\n"
        return ""

    monkeypatch.setattr(metrics, "_git", fake_git)

    assert metrics.collect_guards_modified("2026-05-01", "2026-06-01") == 2


def test_compile_row_preserves_metric_identity_and_value(monkeypatch):
    monkeypatch.setattr(metrics, "_month_bounds", lambda month: ("2026-05-01", "2026-06-01"))
    monkeypatch.setattr(metrics, "collect_new_lessons", lambda *_args: 2)
    monkeypatch.setattr(metrics, "_count_jsonl_in_month", lambda *_args: 3)
    monkeypatch.setattr(metrics, "collect_lessons_references", lambda *_args: 4)
    monkeypatch.setattr(metrics, "collect_inbox_backlog", lambda: 5)
    monkeypatch.setattr(metrics, "collect_guards_modified", lambda *_args: 6)

    row = metrics.compile_row("2026-05")

    assert row == {
        "month": "2026-05",
        "new_lessons": 2,
        "hook_rejects": 3,
        "lessons_refs": 4,
        "preflight_hits": 3,
        "inbox_backlog": 5,
        "repeat_incidents": "—",
        "guards_modified": 6,
    }


def test_render_row_formats_markdown_table_line():
    row = {
        "month": "2026-05",
        "new_lessons": 1,
        "hook_rejects": 2,
        "lessons_refs": 3,
        "preflight_hits": 4,
        "inbox_backlog": 5,
        "repeat_incidents": "—",
        "guards_modified": 6,
    }

    assert metrics.render_row(row) == "| 2026-05 | 1 | 2 | 3 | 4 | 5 | — | 6 |"


def test_update_metrics_md_replaces_generated_block_only(tmp_path, monkeypatch):
    page = tmp_path / "metrics.md"
    page.write_text(
        "# Metrics\n\n| 月份 | A |\n|---|---|\n| 2026-05 | old |\n\n## Tail\nkeep\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(metrics, "METRICS_PAGE", page)

    changed, message = metrics.update_metrics_md("2026-05", "| 2026-05 | new |")

    assert changed is True
    assert message == "replaced row"
    assert "| 2026-05 | new |" in page.read_text(encoding="utf-8")
    assert "## Tail\nkeep" in page.read_text(encoding="utf-8")
