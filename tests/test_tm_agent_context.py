from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_agent_context as ctx  # type: ignore[import-not-found]


def _write_sources(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    canvas = tmp_path / "project-canvas.md"
    memory_plan = tmp_path / "memory-answer-development-plan.md"
    handoff = tmp_path / "session-handoff-protocol.md"
    canvas.write_text(
        """---
updated: 2026-06-18
---

# Project Canvas

| 模块 | 状态 | 最后更新 | 负责 |
|---|---|---|---|
| Memory Answer Natural QA | 🟡 P5 active；P3.23 已关闭；packer 仍 opt-in | 2026-06-18 | codex |
| Development Supervisor | 🟡 wrapper / context pack / official review 可用 | 2026-06-18 | codex |
""",
        encoding="utf-8",
    )
    memory_plan.write_text(
        """---
updated: 2026-06-18
---

# Memory Answer 开发计划

## 摘要

P5 起只做运营硬化、真实失败反馈和小步增量，不回头重做 P1-P4 主链路。

- P3.23 已关闭：map arm 已是私有长驻服务默认能力。
- evidence prompt packer 仍为 opt-in，不默认开启。
- 旧 100 问 evidence-hit 不能再作为主 release metric。
""",
        encoding="utf-8",
    )
    handoff.write_text(
        """# Session Handoff Protocol

## 摘要

Session Handoff Card uses memory_type: session-handoff.
""",
        encoding="utf-8",
    )
    return canvas, memory_plan, handoff


def _patch_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    canvas, memory_plan, handoff = _write_sources(tmp_path)
    out_dir = tmp_path / "runtime" / "agent-context"
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ctx, "PROJECT_CANVAS", canvas)
    monkeypatch.setattr(ctx, "MEMORY_ANSWER_PLAN", memory_plan)
    monkeypatch.setattr(ctx, "SESSION_HANDOFF_PROTOCOL", handoff)
    monkeypatch.setattr(ctx, "OUT_DIR", out_dir)
    monkeypatch.setattr(ctx, "LATEST_JSON", out_dir / "latest.json")
    monkeypatch.setattr(ctx, "LATEST_MD", out_dir / "latest.md")
    monkeypatch.setattr(ctx, "EVENTS_JSONL", out_dir / "events.jsonl")
    monkeypatch.setattr(ctx, "_load_onboarding_30s", lambda: "30s onboarding summary")
    return out_dir


def test_build_context_pack_writes_runtime_files(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    _patch_paths(monkeypatch, tmp_path)

    data = ctx.build_context_pack(profile="codex", task_hint="do not store this raw task")
    json_path, md_path = ctx.write_context_pack(data)

    assert json_path.exists()
    assert md_path.exists()
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["profile"] == "codex"
    assert saved["pack_hash"]
    assert "do not store this raw task" not in json_path.read_text(encoding="utf-8")
    assert "Memory Answer P5" in saved["current_phase"] or "P5" in saved["summary"]
    assert ctx.validate_context_pack(saved)["ok"] is True


def test_policy_path_mentions_do_not_block_when_not_output(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    canvas, memory_plan, handoff = _write_sources(tmp_path)
    canvas.write_text(
        canvas.read_text(encoding="utf-8") + "\nPolicy note: never export wiki/person/tiger.md.\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "runtime" / "agent-context"
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ctx, "PROJECT_CANVAS", canvas)
    monkeypatch.setattr(ctx, "MEMORY_ANSWER_PLAN", memory_plan)
    monkeypatch.setattr(ctx, "SESSION_HANDOFF_PROTOCOL", handoff)
    monkeypatch.setattr(ctx, "OUT_DIR", out_dir)
    monkeypatch.setattr(ctx, "LATEST_JSON", out_dir / "latest.json")
    monkeypatch.setattr(ctx, "LATEST_MD", out_dir / "latest.md")
    monkeypatch.setattr(ctx, "EVENTS_JSONL", out_dir / "events.jsonl")
    monkeypatch.setattr(ctx, "_load_onboarding_30s", lambda: "30s onboarding summary")

    data = ctx.build_context_pack(profile="codex")

    assert "wiki/person" not in json.dumps(data, ensure_ascii=False)


def test_privacy_guard_blocks_person_path_in_output(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    canvas, memory_plan, handoff = _write_sources(tmp_path)
    canvas.write_text(
        """---
updated: 2026-06-18
---

# Project Canvas

| 模块 | 状态 | 最后更新 | 负责 |
|---|---|---|---|
| Memory Answer Natural QA | accidentally points at wiki/person/tiger.md | 2026-06-18 | codex |
""",
        encoding="utf-8",
    )
    out_dir = tmp_path / "runtime" / "agent-context"
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ctx, "PROJECT_CANVAS", canvas)
    monkeypatch.setattr(ctx, "MEMORY_ANSWER_PLAN", memory_plan)
    monkeypatch.setattr(ctx, "SESSION_HANDOFF_PROTOCOL", handoff)
    monkeypatch.setattr(ctx, "OUT_DIR", out_dir)
    monkeypatch.setattr(ctx, "LATEST_JSON", out_dir / "latest.json")
    monkeypatch.setattr(ctx, "LATEST_MD", out_dir / "latest.md")
    monkeypatch.setattr(ctx, "EVENTS_JSONL", out_dir / "events.jsonl")
    monkeypatch.setattr(ctx, "_load_onboarding_30s", lambda: "30s onboarding summary")

    with pytest.raises(ValueError, match="privacy guard"):
        ctx.build_context_pack(profile="codex")


def test_validate_marks_stale_pack(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    _patch_paths(monkeypatch, tmp_path)
    data = ctx.build_context_pack(profile="codex", stale_after_minutes=1)
    old = datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(minutes=5)
    data["generated_at"] = old.isoformat()
    data["pack_hash"] = ctx._canonical_hash(data)
    ctx.write_context_pack(data)

    result = ctx.validate_context_pack()

    assert result["ok"] is True
    assert result["stale"] is True
    assert result["age_minutes"] >= 5


def test_stats_counts_events_without_prompt_text(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    _patch_paths(monkeypatch, tmp_path)
    data = ctx.build_context_pack(profile="codex", task_hint="private user prompt should not appear")
    ctx.write_context_pack(data)
    ctx.validate_context_pack()

    result = ctx.stats()
    events = ctx.EVENTS_JSONL.read_text(encoding="utf-8")

    assert result["event_counts"]["built"] == 1
    assert "private user prompt should not appear" not in events
