from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

pytest.importorskip("fastapi")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_http  # type: ignore[import-not-found]


def test_suggest_wiki_patches_save_schedules_digest_refresh(monkeypatch):
    calls = []
    patch = tm_http.WikiPatchItem(
        page="wiki/systems/example.md",
        type="append",
        section="Notes",
        content="patch content",
        rationale="test",
    )

    monkeypatch.setattr(tm_http, "_load_wiki_catalog", lambda _partition: ["wiki/systems/example.md"])
    monkeypatch.setattr(tm_http.tm_core, "suggest_wiki_patches", lambda *_args, **_kwargs: [patch])
    monkeypatch.setattr(
        tm_http.tm_core,
        "save_wiki_patches_to_inbox",
        lambda *_args, **_kwargs: "inbox/2026-05-16-0000-codex-cross.md",
    )
    monkeypatch.setattr(tm_http.tm_memory_ops, "schedule_digest_refresh", lambda: calls.append("digest"))

    req = tm_http.SuggestPatchesRequest(
        summary="x" * 30,
        partition="systems",
        max_patches=1,
        save=True,
        source="codex",
    )
    result = asyncio.run(tm_http.suggest_wiki_patches(req))

    assert result.inbox_path == "inbox/2026-05-16-0000-codex-cross.md"
    assert calls == ["digest"]


def test_memory_answer_endpoint_delegates_to_core(monkeypatch):
    captured = {}

    def fake_core(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return {
            "status": "not_found",
            "answer": "",
            "summary": "no evidence",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "trace_id": "trace-http",
            "trace": None,
        }

    monkeypatch.setattr(tm_http.tm_answer, "memory_answer_core", fake_core)

    req = tm_http.MemoryAnswerRequest(
        query="missing query",
        scope="wiki",
        top_k=3,
        max_evidence=2,
        include_trace=False,
    )
    result = asyncio.run(tm_http.memory_answer(req))

    assert result["trace_id"] == "trace-http"
    assert captured == {
        "query": "missing query",
        "scope": "wiki",
        "top_k": 3,
        "max_evidence": 2,
        "include_trace": False,
    }
