"""Tests for _auto_wrap_handoff_card — server-side handoff wrapping for hookless agents."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "packages" / "tigermemory-core" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "tigermemory-search" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "tigermemory-config" / "src"))

from tm_memory_ops import _auto_wrap_handoff_card  # type: ignore


def test_closeout_text_gets_wrapped():
    """Text with closeout signals (commit + verified) must be wrapped."""
    text = "Codex completed task. commit b5469ac pushed. verified all tests passed."
    result = _auto_wrap_handoff_card("openclaw", text)
    assert "memory_type: session-handoff" in result
    assert "openclaw-" in result
    assert "## Task" in result
    assert "## Handoff" in result
    assert text in result  # original preserved


def test_already_formatted_text_passes_through():
    """If text already has memory_type: session-handoff, do not re-wrap."""
    text = (
        "---\nmemory_type: session-handoff\nsession_id: cascade-test\n---\n\n"
        "## Task\nfoo bar"
    )
    result = _auto_wrap_handoff_card("cascade", text)
    assert result == text


def test_short_non_closeout_passes_through():
    """Short text without enough closeout signals should not be wrapped."""
    text = "completed"
    result = _auto_wrap_handoff_card("hermes", text)
    assert result == text


def test_question_text_passes_through():
    """Conversational text without closeout markers passes through."""
    text = "just a quick question about the API endpoint behavior"
    result = _auto_wrap_handoff_card("openclaw", text)
    assert result == text


def test_chinese_closeout_gets_wrapped():
    """Chinese closeout text with 完成 + commit hash signals must wrap."""
    text = "完成了 P2 任务，commit a1b2c3d4 已推送，验证通过所有测试。"
    result = _auto_wrap_handoff_card("hermes", text)
    assert "memory_type: session-handoff" in result
    assert "hermes-" in result


def test_session_id_includes_agent():
    """Auto-generated session_id must use the agent name as prefix."""
    text = "completed migration. commit f00ba12 verified."
    result = _auto_wrap_handoff_card("openclaw", text)
    assert "session_id: openclaw-" in result


def test_source_marked_as_server_auto_wrap():
    """Wrapped cards should be marked source: server_auto_wrap for audit."""
    text = "task done. commit 1234567 pushed. verified ok."
    result = _auto_wrap_handoff_card("openclaw", text)
    assert "source: server_auto_wrap" in result
    assert "confidence: low" in result  # auto-wrapped = low confidence


def test_long_text_truncated_in_handoff():
    """Original text >1500 chars should be truncated in the Handoff section."""
    long_text = "task completed. commit abc1234 verified ok. " + ("x" * 2000)
    result = _auto_wrap_handoff_card("hermes", long_text)
    assert "memory_type: session-handoff" in result
    # The Handoff section caps at 1500 chars
    handoff_start = result.find("## Handoff\n")
    handoff_end = result.find("\n\n## Evidence")
    handoff_body = result[handoff_start:handoff_end]
    assert len(handoff_body) < 1600


def test_hermes_natural_chinese_wrap():
    """Real Hermes output: uses '提交' + commit hashes (645c067 etc). Should wrap."""
    text = (
        "2026-05-26 Hermes查阅tigermemory最近7天git log，"
        "筛出与investment相关的提交：645c067（刷新预览）、"
        "ea7f5b2（新增600887.SH决策日志）、204b7ef（批量归档）。"
        "口语化结论：最近一周确有多条投资相关提交。"
    )
    result = _auto_wrap_handoff_card("hermes", text)
    assert "memory_type: session-handoff" in result
    assert "hermes-" in result


def test_openclaw_natural_chinese_wrap():
    """Real OpenClaw output: uses '提交' + hash + '口语化结论'. Should wrap."""
    text = (
        "最近 5 个 commit：\n"
        "- `60cdff5` `[cascade] create: server-side auto-wrap`\n"
        "- `77ff508` `[codex] create: P2 development closeout plan`\n\n"
        "口语化结论：最近几次提交主要在收口 agent 运行规范。\n"
        "提交作者名显示为 raogiant66。"
    )
    result = _auto_wrap_handoff_card("openclaw", text)
    assert "memory_type: session-handoff" in result
    assert "openclaw-" in result


def test_no_wrap_for_pure_question():
    """Pure research text with hashes but no closeout words should NOT wrap."""
    text = (
        "看了 645c067 和 ea7f5b2 这两条，"
        "前者是刷新预览，后者是决策日志。"
        "我还需要你帮我看另外几个。"
    )
    result = _auto_wrap_handoff_card("hermes", text)
    assert result == text  # no wrap, only 1 signal (hash)
