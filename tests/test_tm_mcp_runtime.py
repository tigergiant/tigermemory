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
import json

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
    assert "## 4. Agent 生态地图（一句话定位）" in content
    assert "## 5. Live-state 优先原则" in content
    assert "## 6. 必须避免的 lesson" in content


def test_get_agent_onboarding_full():
    result = tm_mcp.get_agent_onboarding("full")
    assert result["depth"] == "full"
    content = result["content"]
    assert "## 7. Agent 接入边界" in content
    assert "## 8. 完整 lesson 清单" in content
    assert "## 9. v0.2 范围" in content
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


def test_write_memory_uses_shared_memory_ops(monkeypatch):
    captured = {}

    def fake_write_memory_with_review(agent, topic, text, **kwargs):
        captured.update({"agent": agent, "topic": topic, "text": text, "kwargs": kwargs})
        return {"route": "mem0", "id": "fd65b298-05bd-493c-83ce-e37d84447362"}

    monkeypatch.setattr(tm_mcp.tm_memory_ops, "write_memory_with_review", fake_write_memory_with_review)
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "writer"
        result = tm_mcp.write_memory("codex", "systems", "body", force_inbox=True)
    finally:
        tm_mcp._ROLE = old

    assert result["route"] == "mem0"
    assert captured["agent"] == "codex"
    assert captured["topic"] == "systems"
    assert captured["text"] == "body"
    assert captured["kwargs"]["force_inbox"] is True
    assert captured["kwargs"]["total_budget_s"] is None
    assert captured["kwargs"]["include_readback"] is True


def test_propose_wiki_page_owner_schedules_embed_refresh(tmp_path, monkeypatch):
    import tm_review  # type: ignore[import-not-found]

    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(tm_mcp.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(tm_mcp.tm_core.PARTITION_OWNERS, "systems", {"codex"})
    monkeypatch.setattr(
        tm_review,
        "review_draft",
        lambda _body: {"score": 80, "issues": [], "suggestions": [], "review_skipped": False},
    )
    monkeypatch.setattr(tm_mcp.tm_core, "git_commit_push", lambda _files, _msg: "abc123")
    monkeypatch.setattr(
        tm_mcp.tm_memory_ops,
        "schedule_embed_refresh",
        lambda **kwargs: calls.append(kwargs) or {
            "embed_refresh_scheduled": True,
            "embed_refresh_scope": kwargs["scope"],
        },
    )
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "writer"
        result = tm_mcp.propose_wiki_page(
            "codex",
            "systems",
            "bc-embed-test",
            "owner: codex\nstatus: draft",
            "## Summary\n\nbody\n\n## Sources\n\n- test",
        )
    finally:
        tm_mcp._ROLE = old

    assert result["path"] == "wiki/systems/bc-embed-test.md"
    assert result["embed_refresh_scheduled"] is True
    assert calls == [{
        "scope": "wiki",
        "reason": "propose_wiki_page",
        "paths": ["wiki/systems/bc-embed-test.md"],
    }]


def test_write_sources_schedules_wiki_embed_refresh(tmp_path, monkeypatch):
    (tmp_path / "sources").mkdir()
    calls = []
    monkeypatch.setattr(tm_mcp.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_mcp.tm_core, "git_commit_push", lambda _files, _msg: "abc123")
    monkeypatch.setattr(
        tm_mcp.tm_memory_ops,
        "schedule_embed_refresh",
        lambda **kwargs: calls.append(kwargs) or {
            "embed_refresh_scheduled": True,
            "embed_refresh_scope": kwargs["scope"],
        },
    )
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "writer"
        result = tm_mcp.write_sources(
            "codex",
            "bc",
            "source-test",
            "https://example.com/source",
            "codex-via-test",
            "source body",
        )
    finally:
        tm_mcp._ROLE = old

    assert result["path"] == "sources/bc/source-test.md"
    assert result["embed_refresh_scheduled"] is True
    assert calls == [{
        "scope": "wiki",
        "reason": "write_sources",
        "paths": ["sources/bc/source-test.md"],
    }]


def test_propose_wiki_page_l2_fallback_schedules_digest_refresh(tmp_path, monkeypatch):
    import tm_review  # type: ignore[import-not-found]

    (tmp_path / "inbox").mkdir()
    calls = []
    monkeypatch.setattr(tm_mcp.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(tm_mcp.tm_core.PARTITION_OWNERS, "systems", {"codex"})
    monkeypatch.setattr(
        tm_review,
        "review_draft",
        lambda _body: {"score": 10, "issues": [], "suggestions": [], "review_skipped": False},
    )
    monkeypatch.setattr(tm_mcp.tm_core, "git_commit_push", lambda _files, _msg: "abc123")
    monkeypatch.setattr(tm_mcp.tm_memory_ops, "schedule_digest_refresh", lambda: calls.append("digest"))
    monkeypatch.setattr(
        tm_mcp.tm_memory_ops,
        "schedule_embed_refresh",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fallback must not refresh embed index")),
    )
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "writer"
        result = tm_mcp.propose_wiki_page(
            "codex",
            "systems",
            "bc-l2-fallback",
            "owner: codex\nstatus: draft",
            "low score body",
        )
    finally:
        tm_mcp._ROLE = old

    assert result["path"].startswith("inbox/")
    assert result["digest_refresh_scheduled"] is True
    assert calls == ["digest"]


def test_propose_wiki_page_non_owner_fallback_schedules_digest_refresh(tmp_path, monkeypatch):
    import tm_review  # type: ignore[import-not-found]

    (tmp_path / "inbox").mkdir()
    calls = []
    monkeypatch.setattr(tm_mcp.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(tm_mcp.tm_core.PARTITION_OWNERS, "systems", {"claude-code"})
    monkeypatch.setattr(
        tm_review,
        "review_draft",
        lambda _body: {"score": 80, "issues": [], "suggestions": [], "review_skipped": False},
    )
    monkeypatch.setattr(tm_mcp.tm_core, "git_commit_push", lambda _files, _msg: "abc123")
    monkeypatch.setattr(tm_mcp.tm_memory_ops, "schedule_digest_refresh", lambda: calls.append("digest"))
    monkeypatch.setattr(
        tm_mcp.tm_memory_ops,
        "schedule_embed_refresh",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fallback must not refresh embed index")),
    )
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "writer"
        result = tm_mcp.propose_wiki_page(
            "codex",
            "systems",
            "bc-non-owner",
            "owner: codex\nstatus: draft",
            "## Summary\n\nbody",
        )
    finally:
        tm_mcp._ROLE = old

    assert result["path"].startswith("inbox/")
    assert "not an owner" in result["fallback_reason"]
    assert result["digest_refresh_scheduled"] is True
    assert calls == ["digest"]


def test_reader_role_allows_read_tools():
    """Reader role must NOT block read-only tools (get_agent_onboarding)."""
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        result = tm_mcp.get_agent_onboarding("30s")
        assert result["depth"] == "30s"
    finally:
        tm_mcp._ROLE = old


def test_reader_role_allows_verify_memory_id(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    monkeypatch.setattr(
        tm_mcp.tm_core,
        "verify_memory_id",
        lambda memory_id, key_terms=None, digest_date=None: {
            "id": memory_id,
            "status": "exists_active",
            "key_terms": key_terms,
            "digest_date": digest_date,
        },
    )
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        result = tm_mcp.verify_memory_id(mem_id, key_terms="T-X3.5", digest_date="2026-05-16")
        assert result["id"] == mem_id
        assert result["status"] == "exists_active"
        assert result["key_terms"] == "T-X3.5"
        assert result["digest_date"] == "2026-05-16"
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


# ------------------------------------------------------------------
# search_tigermemory — grouped unified search, no fused ranking
# ------------------------------------------------------------------


def _stub_mem0_empty(monkeypatch):
    monkeypatch.setattr(
        tm_mcp.tm_core,
        "mem0_search",
        lambda _query, size=5: json.dumps({"items": []}, ensure_ascii=False),
    )


def test_search_tigermemory_auto_groups_and_prefers_onboarding(monkeypatch):
    _stub_mem0_empty(monkeypatch)

    result = tm_mcp.search_tigermemory("git pull ff-only origin master preflight", top_k=3)

    assert result["strategy"] == "grouped-intent-budget-v1"
    assert result["primary_scope"] == "onboarding"
    assert set(result["groups"]) == {"wiki", "lessons", "onboarding", "mem0"}
    assert result["primary_results"]
    assert result["primary_results"][0]["source"] == "onboarding"
    assert result["primary_results"][0]["path"] == "wiki/systems/agent-onboarding.md"


def test_search_tigermemory_auto_prefers_lessons_for_failure_queries(monkeypatch):
    _stub_mem0_empty(monkeypatch)

    result = tm_mcp.search_tigermemory("hook reject no-verify routed_by", top_k=3)

    assert result["primary_scope"] == "lessons"
    assert result["primary_results"][0]["path"].endswith("2026-04-22-no-verify-bypass.md")


def test_search_tigermemory_wiki_scope_uses_canonical_search(monkeypatch):
    _stub_mem0_empty(monkeypatch)

    result = tm_mcp.search_tigermemory("agent write toolkit tm_io", scope="wiki", top_k=3)

    assert result["primary_scope"] == "wiki"
    assert set(result["groups"]) == {"wiki"}
    assert result["primary_results"][0]["path"] == "wiki/systems/agent-write-toolkit.md"
    assert all(hit["path"].startswith("wiki/") for hit in result["groups"]["wiki"])


def test_search_tigermemory_invalid_scope_raises():
    with pytest.raises(ValueError):
        tm_mcp.search_tigermemory("anything", scope="fused")


def test_search_tigermemory_mem0_failure_is_warning(monkeypatch):
    monkeypatch.setattr(
        tm_mcp.tm_core,
        "mem0_search",
        lambda _query, size=5: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    result = tm_mcp.search_tigermemory("tigermemory onboarding", scope="mem0")

    assert result["groups"]["mem0"] == []
    assert result["warnings"]
    assert "mem0 unavailable" in result["warnings"][0]


def test_search_tigermemory_writes_dogfood_log(monkeypatch, tmp_path):
    _stub_mem0_empty(monkeypatch)
    log_path = tmp_path / "search-tigermemory.jsonl"
    monkeypatch.setattr(tm_mcp, "_SEARCH_DOGFOOD_LOG", log_path)

    result = tm_mcp.search_tigermemory("commit push same turn", scope="lessons", top_k=2)

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["query"] == "commit push same turn"
    assert rows[0]["scope"] == "lessons"
    assert rows[0]["primary_scope"] == "lessons"
    assert rows[0]["primary_top_path"] == result["primary_results"][0]["path"]
    assert rows[0]["group_counts"] == {"lessons": 2}


def test_reader_role_allows_search_tigermemory(monkeypatch):
    _stub_mem0_empty(monkeypatch)
    old = tm_mcp._ROLE
    try:
        tm_mcp._ROLE = "reader"
        result = tm_mcp.search_tigermemory("commit push same turn", scope="lessons")
        assert result["primary_scope"] == "lessons"
        assert result["primary_results"][0]["path"].endswith("2026-04-23-commit-push-same-turn.md")
    finally:
        tm_mcp._ROLE = old
