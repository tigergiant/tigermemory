"""MCP runtime integration tests for tm_mcp.py tools.

Requires: pip install -r deploy/mcp/requirements.txt  (mcp>=1.0)
Run:      cd d:\tigermemory && python3 -m pytest tests/test_tm_mcp_runtime.py -v

These tests verify that the MCP server-side tools behave correctly
independently of transport (stdio / HTTP) and do NOT touch external
services (Mem0, Git write, DeepSeek) — they only exercise deterministic
paths or assert rejection before side effects.
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import pytest

# Skip entire file if MCP SDK is not installed (CI can install it).
pytest.importorskip(
    "mcp.server.fastmcp",
    reason="mcp package not installed; run: pip install -r deploy/mcp/requirements.txt",
)

# Safe to import now — tm_core is stdlib-only, tm_persona is stdlib-only.
import tm_mcp  # type: ignore[import-not-found]


# ------------------------------------------------------------------
# get_agent_onboarding — contract tests per depth
# ------------------------------------------------------------------


def test_get_agent_onboarding_30s():
    result = tm_mcp.get_agent_onboarding("30s")
    assert isinstance(result, dict)
    assert result["depth"] == "30s"
    content = result["content"]
    # Canonical keywords from agent-onboarding.md
    assert "git pull --ff-only origin master" in content
    assert "tm_lessons.py search" in content
    assert "selfevolution" in content
    assert "write_memory" in content or "write_inbox" in content
    assert "commit + push" in content or "同回合 push" in content
    assert "--no-verify" in content


def test_get_agent_onboarding_5min():
    result = tm_mcp.get_agent_onboarding("5min")
    assert result["depth"] == "5min"
    content = result["content"]
    assert "## 1. 开工顺序" in content
    assert "## 2. 写入权限边界" in content
    assert "## 3. 工具入口" in content
    assert "## 4. Live-state 优先原则" in content
    assert "## 5. 必须避免的 lesson" in content


def test_get_agent_onboarding_full():
    result = tm_mcp.get_agent_onboarding("full")
    assert result["depth"] == "full"
    content = result["content"]
    assert "## 6. Agent 接入边界" in content
    assert "## 7. 完整 lesson 清单" in content
    assert "## 8. v0.2 范围" in content
    assert "## 来源" in content
    assert len(content) > len(tm_mcp.get_agent_onboarding("5min")["content"])


def test_get_agent_onboarding_invalid_depth_raises():
    with pytest.raises(ValueError):
        tm_mcp.get_agent_onboarding("1s")


# ------------------------------------------------------------------
# Sources contract — every depth must list the same SOURCE_PATHS
# ------------------------------------------------------------------


def test_get_agent_onboarding_includes_sources():
    for depth in ("30s", "5min", "full"):
        result = tm_mcp.get_agent_onboarding(depth)
        assert "sources" in result
        assert isinstance(result["sources"], list)
        assert "AGENTS.md" in result["sources"]
        assert "wiki/systems/tigermemory-agent-access.md" in result["sources"]


# ------------------------------------------------------------------
# Role boundary — reader must be rejected before any side effect
# ------------------------------------------------------------------


def test_reader_role_blocks_write_inbox():
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        with pytest.raises(PermissionError):
            tm_mcp.write_inbox("claude-code", "systems", "test", "body", "reason")
    finally:
        tm_mcp._ROLE = old


def test_reader_role_blocks_propose_wiki_page():
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        with pytest.raises(PermissionError):
            tm_mcp.propose_wiki_page(
                "claude-code",
                "systems",
                "test-page",
                "---\n",
                "body",
            )
    finally:
        tm_mcp._ROLE = old


def test_reader_role_blocks_write_memory():
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        with pytest.raises(PermissionError):
            tm_mcp.write_memory("claude-code", "systems", "test text")
    finally:
        tm_mcp._ROLE = old


def test_reader_role_allows_read_tools():
    """Reader role must NOT block read-only tools (get_agent_onboarding)."""
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        result = tm_mcp.get_agent_onboarding("30s")
        assert result["depth"] == "30s"
    finally:
        tm_mcp._ROLE = old


# ------------------------------------------------------------------
# Writer role sanity — default path, no rejection
# ------------------------------------------------------------------


def test_writer_role_allows_get_agent_onboarding():
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "writer"
        result = tm_mcp.get_agent_onboarding("30s")
        assert result["depth"] == "30s"
    finally:
        tm_mcp._ROLE = old
