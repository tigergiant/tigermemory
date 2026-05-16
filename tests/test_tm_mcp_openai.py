"""Contract tests for the ChatGPT/OpenAI-facing tigermemory MCP facade."""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import pytest

pytest.importorskip(
    "mcp.server.fastmcp",
    reason="mcp package not installed; run: pip install -r deploy/mcp/requirements.txt",
)

import anyio
import tm_core  # type: ignore[import-not-found]
import tm_mcp_openai  # type: ignore[import-not-found]


def test_page_id_roundtrip():
    path = "wiki/systems/chatgpt-mcp-access.md"
    assert tm_mcp_openai._decode_page_id(tm_mcp_openai._page_id(path)) == path


def test_safe_text_file_rejects_non_knowledge_paths():
    with pytest.raises(ValueError):
        tm_mcp_openai._safe_text_file("runtime/openmemory/.env")
    with pytest.raises(ValueError):
        tm_mcp_openai._safe_text_file("../AGENTS.md")


def test_safe_text_file_allows_agents_md_as_readonly_root_doc():
    assert tm_mcp_openai._safe_text_file("AGENTS.md").name == "AGENTS.md"


def test_search_extra_docs_finds_agents_rebase_rule():
    results = tm_mcp_openai._search_extra_doc_results("git rebase conflict abort inbox", 3)
    assert results
    assert results[0].metadata["path"] == "AGENTS.md"
    assert "rebase" in results[0].metadata["snippet"].casefold()
    assert tm_mcp_openai._has_strong_extra_doc_match(results)


def test_chatgpt_is_regular_agent_without_person_partition_access():
    assert "chatgpt" in tm_core.AGENTS
    assert "chatgpt" in tm_core.PARTITION_OWNERS["systems"]
    assert "chatgpt" not in tm_core.PARTITION_OWNERS["person"]


def test_write_memory_via_router_uses_fixed_chatgpt_agent(monkeypatch):
    calls = []

    def fake_write_memory_with_review(agent, topic, text, **kwargs):
        calls.append((agent, topic, text, kwargs))
        return {
            "route": "mem0",
            "score": 88,
            "topic_inferred": "systems",
            "id": "mem-id",
            "reasons": "accepted",
            "verified": {"direct_readback_ok": True},
        }

    monkeypatch.setattr(
        tm_mcp_openai.tm_memory_ops,
        "write_memory_with_review",
        fake_write_memory_with_review,
    )

    result = tm_mcp_openai._write_memory_via_router("systems", "durable ChatGPT test note")

    assert result.route == "mem0"
    assert result.id == "mem-id"
    assert calls == [(
        "chatgpt",
        "systems",
        "durable ChatGPT test note",
        {"force_inbox": False, "total_budget_s": 25, "include_readback": True},
    )]


def test_write_memory_via_router_rejects_invalid_inputs(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("router should not be called")

    monkeypatch.setattr(tm_mcp_openai.tm_memory_ops, "write_memory_with_review", fail_if_called)

    with pytest.raises(ValueError):
        tm_mcp_openai._write_memory_via_router("invalid", "text")
    with pytest.raises(ValueError):
        tm_mcp_openai._write_memory_via_router("systems", " ")
    with pytest.raises(ValueError):
        tm_mcp_openai._write_memory_via_router("systems", "x" * 4001)


def test_openai_facade_exposes_second_step_tools_only():
    async def _list_names():
        server = tm_mcp_openai._build_mcp(
            auth_mode="none",
            public_base="https://tm-openai.doodiu.cloud",
            link_secret=None,
            store_path=REPO_ROOT / ".tmp" / "openai-mcp-test-oauth.json",
        )
        tm_mcp_openai.register_tools(server, max_fetch_chars=1000)
        return await server.list_tools()

    tools = anyio.run(_list_names)
    names = [tool.name for tool in tools]
    assert names == ["search", "fetch", "get_agent_onboarding", "write_memory"]
    assert all(tool.outputSchema for tool in tools)
    assert "propose_wiki_page" not in names
    assert "write_sources" not in names
    assert "expense_write" not in names
