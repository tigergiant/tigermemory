from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_reflection  # type: ignore[import-not-found]


def _write_inbox(path: pathlib.Path, body: str = "review me") -> None:
    text = "\n".join([
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-01",
        "topic: systems",
        "route_score: 55",
        "---",
        "",
        body,
        "",
    ])
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def test_inbox_aging_archives_after_fourteen_days(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md")

    rows = tm_memory_reflection.audit_inbox(
        date="2026-05-15",
        inbox_dir=inbox,
        proposal_root=tmp_path / "cron-proposals",
    )

    assert len(rows) == 1
    assert rows[0].age_days == 14
    assert rows[0].action == "archive"
    assert rows[0].stale_archive is True


def test_inbox_aging_keeps_file_with_applied_history(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    name = "2026-05-01-1200-codex-systems.md"
    _write_inbox(inbox / name)
    applied = tmp_path / "cron-proposals" / "2026-05-10" / "proposal-2026-05-10-001" / "applied.json"
    applied.parent.mkdir(parents=True)
    applied.write_text(
        json.dumps({"proposal_id": "proposal-2026-05-10-001", "paths": [f"inbox/{name}"]}),
        encoding="utf-8",
    )

    rows = tm_memory_reflection.audit_inbox(
        date="2026-05-20",
        inbox_dir=inbox,
        proposal_root=tmp_path / "cron-proposals",
    )

    assert rows[0].already_applied is True
    assert rows[0].action == "keep_in_inbox"
    assert rows[0].stale_archive is False


def test_daily_report_renders_proposals_and_required_sections(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-21-1200-codex-systems.md", body="daily inbox")

    discard_file = tmp_path / "discard-root" / "2026-05-21" / "discard" / "events.jsonl"
    discard_file.parent.mkdir(parents=True)
    discard_file.write_text(
        json.dumps({
            "event_id": "abc123",
            "score": 78,
            "agent": "codex",
            "requested_topic": "systems",
            "topic_inferred": "systems",
            "is_transient": True,
            "is_sensitive": False,
            "reasons": "transient content",
            "text_excerpt": "finished commit and push",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    pdir = tmp_path / "cron-proposals" / "2026-05-21" / "proposal-2026-05-21-001"
    pdir.mkdir(parents=True)
    (pdir / "proposal.json").write_text(
        json.dumps({"type": "prompt-tuning", "trigger": "discard candidate abc123", "impact": "tools/tm_route.py"}),
        encoding="utf-8",
    )
    (pdir / "patch").write_text("diff --git a/tools/tm_route.py b/tools/tm_route.py\n", encoding="utf-8")
    (pdir / "replay-result.json").write_text(
        json.dumps({"recommendation": "apply", "severe_count": 0, "matrix": {"discard": {"mem0": 1}}}),
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-21",
        now_iso="2026-05-21T23:55:00+08:00",
        mem0_items=[{
            "id": "m1",
            "created_at": "2026-05-21T08:00:00+08:00",
            "content": "durable memory",
            "metadata": {"topic": "systems", "source": "codex"},
        }],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    for section in (
        "## 摘要",
        "## 当日三源汇总",
        "## discard 误判候选",
        "## inbox audit",
        "## Proposed Changes",
        "## 已生效改动",
        "## 自评指标",
        "## 来源",
    ):
        assert section in report
    assert "#### proposal-2026-05-21-001" in report
    assert "- [ ] apply" in report
    assert "py tools\\tm_io.py cron-apply 2026-05-21 --proposal proposal-2026-05-21-001" in report


def test_weekly_drift_signal_detects_mem0_topic_spike(tmp_path):
    week_dates = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23", "2026-05-24"]
    previous_dates = ["2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15", "2026-05-16", "2026-05-17"]
    items = [
        {"id": f"m{i}", "created_at": f"2026-05-{18+i:02d}T08:00:00+08:00", "content": "x", "metadata": {"topic": "systems"}}
        for i in range(3)
    ]

    signals = tm_memory_reflection.detect_drift_signals(
        week_dates=week_dates,
        previous_dates=previous_dates,
        mem0_items=items,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert any(signal["type"] == "single_class_mem0_spike" for signal in signals)
