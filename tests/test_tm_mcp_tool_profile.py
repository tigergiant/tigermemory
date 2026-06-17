"""Tool profile contract tests for the general TigerMemory MCP server."""
from __future__ import annotations

import inspect
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import pytest

pytest.importorskip(
    "mcp.server.fastmcp",
    reason="mcp package not installed; run: pip install -r deploy/mcp/requirements.txt",
)

import tm_mcp  # type: ignore[import-not-found]


def test_memory_tool_profile_keeps_answer_first_surface() -> None:
    registered = set(tm_mcp._MEMORY_TOOL_PROFILE) | tm_mcp._OPS_EXTRA_TOOLS | {
        "minimax_image",
        "expense_read",
        "start_deep_dive",
    }

    visible = tm_mcp._visible_tool_names_for_profile("memory", "writer", registered)

    assert "memory_answer" in visible
    assert "search_tigermemory" in visible
    assert "search_memories" in visible
    assert "write_memory" in visible
    assert "minimax_image" not in visible
    assert "expense_read" not in visible
    assert "start_deep_dive" not in visible


def test_reader_role_hides_writer_tools_even_in_memory_profile() -> None:
    registered = set(tm_mcp._MEMORY_TOOL_PROFILE)

    visible = tm_mcp._visible_tool_names_for_profile("memory", "reader", registered)

    assert "memory_answer" in visible
    assert "write_memory" not in visible
    assert "write_sources" not in visible
    assert "propose_wiki_page" not in visible


def test_ops_and_full_profiles_are_explicit_opt_ins() -> None:
    registered = set(tm_mcp._MEMORY_TOOL_PROFILE) | tm_mcp._OPS_EXTRA_TOOLS | {
        "minimax_image",
        "expense_read",
    }

    ops_visible = tm_mcp._visible_tool_names_for_profile("ops", "writer", registered)
    full_visible = tm_mcp._visible_tool_names_for_profile("full", "writer", registered)

    assert "review_digest" in ops_visible
    assert "minimax_image" not in ops_visible
    assert "minimax_image" in full_visible
    assert "expense_read" in full_visible


def test_tool_docstrings_push_memory_answer_as_primary_entry() -> None:
    answer_doc = inspect.getdoc(tm_mcp.memory_answer) or ""
    grouped_doc = inspect.getdoc(tm_mcp.search_tigermemory) or ""
    mem0_doc = inspect.getdoc(tm_mcp.search_memories) or ""

    assert "主要入口" in answer_doc
    assert "普通自然语言记忆问答请先用 `memory_answer`" in grouped_doc
    assert "普通自然语言记忆问答请先用 `memory_answer`" in mem0_doc


def test_onboarding_reports_visible_and_hidden_tool_counts() -> None:
    old_status = dict(tm_mcp._TOOL_PROFILE_STATUS)
    try:
        tm_mcp._TOOL_PROFILE_STATUS = {}
        result = tm_mcp.get_agent_onboarding("30s")
    finally:
        tm_mcp._TOOL_PROFILE_STATUS = old_status

    profile = result["tool_profile"]
    assert profile["profile"] == "memory"
    assert profile["primary_entry"] == "memory_answer"
    assert profile["visible_count"] >= len(tm_mcp._MEMORY_TOOL_PROFILE)
    assert profile["hidden_count"] > 0
    assert "当前可见工具" in result["content"]
    assert "已折叠/隐藏工具" in result["content"]
    assert "`memory_answer`" in result["content"]


def test_agent_doctor_includes_tool_profile_summary(monkeypatch) -> None:
    monkeypatch.setattr(tm_mcp.tm_agent_doctor, "run_agent_doctor", lambda **_kwargs: {"status": "ok"})

    result = tm_mcp.agent_doctor(include_l2=False)

    assert result["status"] == "ok"
    assert result["tool_profile"]["profile"] == "memory"
    assert result["tool_profile"]["primary_entry"] == "memory_answer"
