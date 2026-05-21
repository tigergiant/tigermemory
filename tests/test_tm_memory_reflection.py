from __future__ import annotations

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_reflection  # type: ignore[import-not-found]


def _write_inbox(path: pathlib.Path, body: str) -> None:
    text = "\n".join([
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-01",
        "topic: systems",
        "---",
        "",
        body,
        "",
    ])
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _strip_details(text: str) -> list[str]:
    visible: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip() == "<details>":
            inside = True
            continue
        if line.strip() == "</details>":
            inside = False
            continue
        if not inside:
            visible.append(line)
    return visible


def test_daily_digest_decision_block_and_frontmatter_counts(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", "stale archive me")
    pdir = tmp_path / "cron-proposals" / "2026-05-15" / "proposal-2026-05-15-001"
    pdir.mkdir(parents=True)
    (pdir / "proposal.json").write_text(
        json.dumps({"type": "wiki-doc", "trigger": "fixture", "impact": "wiki/operations/x.md"}),
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "stale_archive_count: 1" in report
    assert "promote_candidate_count: 0" in report
    frontmatter_end = report.splitlines().index("---", 1)
    first_30 = "\n".join(report.splitlines()[frontmatter_end + 1 : frontmatter_end + 31])
    assert "## ⚡ 今日要决策" in first_30
    assert "🔴 14 天兜底 archive 候选：1 条" in first_30
    assert "🟡 promote_to_mem0 / promote_to_wiki 候选：0 条" in first_30
    assert "🔵 Proposed Changes：1 条" in first_30
    assert "⚪ discard 误判候选：0 条" in first_30


def test_daily_digest_groups_inbox_actions_and_wraps_keep_rows(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", "old")
    _write_inbox(inbox / "2026-05-14-1200-codex-systems.md", "new")

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "### 🔴 建议 archive" in report
    assert "### 🟡 建议 promote" in report
    assert "### ⚪ 仅观察 keep_in_inbox" in report
    assert "<summary>展开 1 条 keep_in_inbox</summary>" in report
    assert "2026-05-01-1200-codex-systems.md` **高亮：14 天兜底 archive**" in report


def test_preview_is_capped_at_eighty_characters():
    text = "a" * 120
    assert len(tm_memory_reflection._preview(text)) == 80


def test_daily_digest_raw_lists_are_in_appendix_details(tmp_path):
    discard_file = tmp_path / "discard-root" / "2026-05-15" / "discard" / "events.jsonl"
    discard_file.parent.mkdir(parents=True)
    discard_file.write_text(
        json.dumps({
            "event_id": "abc123",
            "score": 15,
            "agent": "codex",
            "requested_topic": "systems",
            "topic_inferred": "systems",
            "is_transient": True,
            "is_sensitive": False,
            "reasons": "low value",
            "text_excerpt": "discard raw text",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[{
            "id": "m1",
            "created_at": "2026-05-15T08:00:00+08:00",
            "content": "mem0 raw text",
            "metadata": {"topic": "systems", "source": "codex"},
        }],
        inbox_dir=tmp_path / "inbox",
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "## 📚 附录" in report
    assert "<summary>Mem0 当日正式写入（1 条，最多显示 40 条）</summary>" in report
    assert "<summary>discard quarantine（1 条，最多显示 40 条）</summary>" in report
    visible = "\n".join(_strip_details(report))
    assert "mem0 raw text" not in visible
    assert "discard raw text" not in visible


def test_daily_digest_visible_lines_stay_compact(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for idx in range(40):
        _write_inbox(inbox / f"2026-05-14-12{idx:02d}-codex-systems.md", f"keep {idx}")

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[
            {
                "id": f"m{idx}",
                "created_at": "2026-05-15T08:00:00+08:00",
                "content": f"memory {idx}",
                "metadata": {"topic": "systems", "source": "codex"},
            }
            for idx in range(40)
        ],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    outside_details = [line for line in _strip_details(report) if line.strip()]
    assert len(outside_details) <= 100
    assert not re.search(r"memory \d+", "\n".join(outside_details))
