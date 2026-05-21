from __future__ import annotations

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_reflection  # type: ignore[import-not-found]


def _write_inbox(path: pathlib.Path, body: str, summary_cn: str | None = None) -> None:
    summary_line = f"summary_cn: {summary_cn}" if summary_cn else None
    text = "\n".join([
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-01",
        "topic: systems",
        *(line for line in [summary_line] if line),
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
    assert "mem0_audit_candidate_count: 0" in report
    frontmatter_end = report.splitlines().index("---", 1)
    first_30 = "\n".join(report.splitlines()[frontmatter_end + 1 : frontmatter_end + 31])
    assert "## ⚡ 今日要决策" in first_30
    assert "🔴 14 天兜底 archive 候选：1 条" in first_30
    assert "🟡 promote_to_mem0 / promote_to_wiki 候选：0 条" in first_30
    assert "🔵 Proposed Changes：1 条" in first_30
    assert "🟢 Mem0 重复 / 误判候选：0 条" in first_30
    assert "⚪ discard 误判候选：0 条" in first_30


def test_daily_digest_renders_mem0_audit_candidates(tmp_path):
    audit_dir = tmp_path / "mem0-audit" / "2026-05-15"
    audit_dir.mkdir(parents=True)
    (audit_dir / "dedup_candidates.json").write_text(
        json.dumps([
            {
                "candidate_id": "mem-old",
                "canonical_id": "mem-new",
                "agent": "cascade",
                "topic": "systems",
                "created_at": "2026-05-15T08:00:00+08:00",
                "signature_distance": 3,
                "preview": "重复的 Cascade closeout 摘要",
                "reason": "signature_cluster_distance=3; canonical=mem-new",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=tmp_path / "inbox",
        audit_root=tmp_path / "discard-root",
        mem0_audit_root=tmp_path / "mem0-audit",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "mem0_audit_candidate_count: 1" in report
    assert "## 🟢 Mem0 重复 / 误判候选" in report
    assert "### 🟢 重复候选 (dedup)" in report
    assert "`mem-old` :: agent=cascade topic=systems dist=3" in report
    assert "canonical: `mem-new`" in report
    assert "虎哥裁决：[ ] confirm  [ ] reject" in report


def test_daily_digest_groups_inbox_actions_and_wraps_keep_rows(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(
        inbox / "2026-05-01-1200-codex-systems.md",
        "old",
        summary_cn="这是一条测试用的中文摘要。",
    )
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
    assert "中文标题：这是一条测试用的中文摘要。" in report
    assert "中文预览：这是一条测试用的中文摘要。" in report
    assert "原文预览：old" in report
    assert "Codex 推荐操作：归档" in report
    assert "Codex 推荐理由：已停留 14 天且没有 apply 记录" in report
    assert "<summary>展开 1 条 keep_in_inbox</summary>" in report
    assert "2026-05-01-1200-codex-systems.md` **高亮：14 天兜底 archive**" in report


def test_legacy_inbox_extracts_existing_chinese_line(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", "# Routed memory 80\n这条历史 inbox 已经自带中文说明。")

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "中文标题：这条历史 inbox 已经自带中文说明。" in report


def test_legacy_inbox_without_chinese_uses_raw_preview_instead_of_placeholder(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    body = "Routed closeout pushed commit and passed pytest with enough English details for review."
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", body)

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "未提供中文摘要" not in report
    assert "中文标题：Routed closeout pushed commit" in report


def test_preview_is_capped_at_one_hundred_sixty_characters():
    text = "a" * 200
    preview = tm_memory_reflection._preview(text)

    assert len(preview) == 160
    assert len(preview) >= 100


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
