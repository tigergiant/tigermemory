"""Contract tests for the ChatGPT/OpenAI-facing tigermemory MCP facade."""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

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


def test_slice_fetch_text_reports_chunk_metadata():
    text = "# Title\nalpha\n## Detail\n" + ("x" * 20)

    chunk, meta = tm_mcp_openai._slice_fetch_text(
        text,
        start=0,
        max_chars=12,
        default_limit=20,
    )

    assert chunk == text[:12]
    assert meta["start"] == 0
    assert meta["end"] == 12
    assert meta["total_chars"] == len(text)
    assert meta["truncated"] is True
    assert meta["partial"] is True
    assert meta["next_start"] == 12
    assert meta["sections"][:2] == [
        {"line": 1, "char_start": 0, "level": 1, "title": "Title"},
        {"line": 3, "char_start": 14, "level": 2, "title": "Detail"},
    ]
    assert "start=12" in meta["chunk_hint"]


def test_slice_fetch_text_caps_requested_size_to_default_limit():
    text = "abcdef"

    chunk, meta = tm_mcp_openai._slice_fetch_text(
        text,
        start=1,
        max_chars=99,
        default_limit=3,
    )

    assert chunk == "bcd"
    assert meta["requested_max_chars"] == 99
    assert meta["max_fetch_chars"] == 3
    assert meta["recommended_max_chars"] == 3
    assert meta["next_start"] == 4


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
    assert tm_mcp_openai._should_fast_path_extra_docs("git rebase conflict abort inbox", results)


def test_extra_docs_do_not_fast_path_domain_queries():
    results = tm_mcp_openai._search_extra_doc_results("IPFB", 3)
    assert results
    assert not tm_mcp_openai._should_fast_path_extra_docs("IPFB", results)


def test_chatgpt_is_regular_agent_without_person_partition_access():
    assert "chatgpt" in tm_core.AGENTS
    assert "chatgpt" in tm_core.PARTITION_OWNERS["systems"]
    assert "chatgpt" not in tm_core.PARTITION_OWNERS["person"]


def test_normalize_chatgpt_memory_text_adds_date_and_warns_on_long_text():
    text, warnings = tm_mcp_openai._normalize_chatgpt_memory_text("memory body", today="2026-05-18")

    assert text == "2026-05-18 memory body"
    assert warnings == ["text lacked YYYY-MM-DD prefix; added server-side date prefix"]

    existing, existing_warnings = tm_mcp_openai._normalize_chatgpt_memory_text(
        "2026-05-17 already dated",
        today="2026-05-18",
    )

    assert existing == "2026-05-17 already dated"
    assert existing_warnings == []

    _long_text, long_warnings = tm_mcp_openai._normalize_chatgpt_memory_text(
        "x" * 1201,
        today="2026-05-18",
    )

    assert "text is long for Mem0; consider promoting stable rules to wiki/brand" in long_warnings


def test_write_memory_via_router_uses_fixed_chatgpt_agent(monkeypatch):
    calls = []

    def fake_write_memory_with_review(agent, topic, text, **kwargs):
        calls.append((agent, topic, text, kwargs))
        return {
            "route": "mem0",
            "outcome": None,
            "score": 88,
            "topic": topic,
            "topic_inferred": "systems",
            "knowledge_target": "mem0",
            "target_confidence": 91,
            "id": "mem-id",
            "reasons": "accepted",
            "verified": {"direct_readback_ok": True},
        }

    monkeypatch.setattr(
        tm_mcp_openai.tm_memory_ops,
        "write_memory_with_review",
        fake_write_memory_with_review,
    )

    result = tm_mcp_openai._write_memory_via_router("systems", "2026-05-18 durable ChatGPT test note")

    assert result.route == "mem0"
    assert result.id == "mem-id"
    assert result.topic == "systems"
    assert result.knowledge_target == "mem0"
    assert result.target_confidence == 91
    assert result.warnings == []
    assert calls == [(
        "chatgpt",
        "systems",
        "2026-05-18 durable ChatGPT test note",
        {
            "force_inbox": False,
            "total_budget_s": 25,
            "include_readback": True,
            "preserve_requested_topic": True,
        },
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


def test_memory_answer_via_core_hides_trace_by_default(monkeypatch):
    calls = []

    def fake_memory_answer_core(query, **kwargs):
        calls.append((query, kwargs))
        return {
            "status": "ok",
            "answer": "Use the evidence-first answer.",
            "summary": "Answered from evidence.",
            "claims": [{"id": "c1", "text": "claim", "support": ["e1"], "confidence": 1.0}],
            "evidence": [{
                "id": "e1",
                "source": "wiki",
                "path": "wiki/systems/memory-answer-development-plan.md",
                "title": "Memory Answer",
                "excerpt": "compact evidence",
                "score": 9.0,
                "authority": 90.0,
                "source_role": "system_doc",
                "relevance": 2.0,
                "match_count": 2,
                "_snippet": "internal search snippet should not leak",
            }],
            "warnings": [],
            "trace_id": "trace-123",
            "trace": None,
        }

    monkeypatch.setattr(tm_mcp_openai.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_mcp_openai._memory_answer_via_core("how does memory_answer work?")

    assert result["trace_id"] == "trace-123"
    assert "trace" not in result
    assert result["evidence"] == [{
        "id": "e1",
        "source": "wiki",
        "path": "wiki/systems/memory-answer-development-plan.md",
        "title": "Memory Answer",
        "excerpt": "compact evidence",
        "score": 9.0,
        "authority": 90.0,
        "source_role": "system_doc",
        "relevance": 2.0,
        "match_count": 2,
    }]
    assert calls == [("how does memory_answer work?", {
        "scope": "auto",
        "top_k": 5,
        "max_evidence": 6,
        "include_trace": False,
        "task_context": None,
    })]


def test_memory_answer_via_core_can_include_trace_and_passes_arguments(monkeypatch):
    calls = []

    def fake_memory_answer_core(query, **kwargs):
        calls.append((query, kwargs))
        return {
            "status": "not_found",
            "answer": "",
            "summary": "No evidence.",
            "claims": [],
            "evidence": [],
            "warnings": ["all candidate evidence filtered"],
            "trace_id": "trace-456",
            "trace": {"calls": [{"tool": "search_tigermemory"}]},
        }

    monkeypatch.setattr(tm_mcp_openai.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_mcp_openai._memory_answer_via_core(
        "recent onboarding",
        scope="mem0",
        top_k=10,
        max_evidence=12,
        include_trace=True,
    )

    assert result["trace"] == {"calls": [{"tool": "search_tigermemory"}]}
    assert result["warnings"] == ["all candidate evidence filtered"]
    assert calls == [("recent onboarding", {
        "scope": "mem0",
        "top_k": 10,
        "max_evidence": 12,
        "include_trace": True,
        "task_context": None,
    })]


def test_memory_answer_via_core_rejects_invalid_arguments(monkeypatch):
    monkeypatch.setattr(
        tm_mcp_openai.tm_answer,
        "memory_answer_core",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("core should not be called")),
    )

    with pytest.raises(ValueError, match="query"):
        tm_mcp_openai._memory_answer_via_core(" ")
    with pytest.raises(ValueError, match="scope"):
        tm_mcp_openai._memory_answer_via_core("q", scope="bad")
    with pytest.raises(ValueError, match="top_k"):
        tm_mcp_openai._memory_answer_via_core("q", top_k=0)
    with pytest.raises(ValueError, match="max_evidence"):
        tm_mcp_openai._memory_answer_via_core("q", max_evidence=13)


def test_write_memory_scope_guard(monkeypatch):
    monkeypatch.setattr(tm_mcp_openai, "get_access_token", lambda: None)
    tm_mcp_openai._require_write_memory_scope()

    monkeypatch.setattr(tm_mcp_openai, "get_access_token", lambda: SimpleNamespace(scopes=["tm:read"]))
    with pytest.raises(PermissionError, match="reconnect"):
        tm_mcp_openai._require_write_memory_scope()

    monkeypatch.setattr(
        tm_mcp_openai,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["tm:read", "tm:write_memory"]),
    )
    tm_mcp_openai._require_write_memory_scope()


def test_write_memory_scope_guard_uses_oauth_store_fallback(tmp_path, monkeypatch):
    store = tmp_path / "oauth.json"
    store.write_text(
        """
{
  "clients": {
    "read-client": {"scope": "tm:read"},
    "write-client": {"scope": "tm:read tm:write_memory"}
  },
  "access_tokens": {},
  "refresh_tokens": {},
  "pending": {},
  "codes": {}
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_mcp_openai, "get_access_token", lambda: None)
    monkeypatch.setattr(tm_mcp_openai, "_oauth_store_path", lambda: store)

    with pytest.raises(PermissionError, match="reconnect"):
        tm_mcp_openai._require_write_memory_scope(SimpleNamespace(client_id="read-client"))

    tm_mcp_openai._require_write_memory_scope(SimpleNamespace(client_id="write-client"))


def test_readyz_payload_reports_dependency_state(tmp_path, monkeypatch):
    calls = []

    def fake_probe(name, url, **_kwargs):
        calls.append((name, url))
        return {"ok": name != "mem0", "name": name}

    monkeypatch.setattr(tm_mcp_openai.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_mcp_openai, "_oauth_store_path", lambda: tmp_path / "oauth.json")
    monkeypatch.setattr(tm_mcp_openai.tm_core, "mem0_base", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(tm_mcp_openai, "_probe_url", fake_probe)
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:19190/v1")

    status, payload = tm_mcp_openai._readyz_payload()

    assert status == 503
    assert payload["ok"] is False
    assert payload["checks"]["repo"]["ok"] is True
    assert payload["checks"]["mem0"]["ok"] is False
    assert calls == [
        ("mem0", "http://127.0.0.1:8765/api/v1/memories/?user_id=tiger&page=1&size=1"),
        ("embedding", "http://127.0.0.1:19190/v1/models"),
    ]


def test_openai_facade_exposes_narrow_chatgpt_tools_only():
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
    assert names == ["search", "fetch", "get_agent_onboarding", "memory_answer", "write_memory"]
    assert all(tool.outputSchema for tool in tools)
    assert "propose_wiki_page" not in names
    assert "write_sources" not in names
    assert "expense_write" not in names
    by_name = {tool.name: tool for tool in tools}
    assert by_name["search"].annotations.readOnlyHint is True
    assert by_name["fetch"].annotations.readOnlyHint is True
    assert by_name["get_agent_onboarding"].annotations.readOnlyHint is True
    assert by_name["memory_answer"].annotations.readOnlyHint is True
    assert by_name["write_memory"].annotations.readOnlyHint is False
    assert by_name["write_memory"].annotations.destructiveHint is False
    assert by_name["write_memory"].annotations.openWorldHint is False
    assert "Wiki proposal" in by_name["write_memory"].description
    assert "propose_wiki_page" not in by_name["write_memory"].description
