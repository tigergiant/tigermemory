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


def test_openai_facade_exposes_only_first_step_tools():
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
    assert names == ["search", "fetch", "get_agent_onboarding"]
    assert all(tool.outputSchema for tool in tools)
    assert "write_memory" not in names
    assert "propose_wiki_page" not in names
    assert "expense_write" not in names
