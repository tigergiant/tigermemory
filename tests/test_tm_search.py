from __future__ import annotations

import datetime
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_search  # type: ignore[import-not-found]


def test_format_search_hit_omits_breakdown_by_default():
    hit = tm_search.format_search_hit(
        "wiki",
        "wiki/systems/example.md",
        "Example",
        "snippet",
        1.0,
    )

    assert hit == {
        "source": "wiki",
        "path": "wiki/systems/example.md",
        "title": "Example",
        "snippet": "snippet",
        "score": 1.0,
    }


def test_format_search_hit_includes_optional_breakdown():
    hit = tm_search.format_search_hit(
        "wiki",
        "wiki/systems/example.md",
        "Example",
        "snippet",
        1.0,
        score_breakdown={"lexical_score": 12.0},
    )

    assert hit["score_breakdown"] == {"lexical_score": 12.0}


def test_search_lessons_group_includes_score_breakdown(monkeypatch, tmp_path):
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    (lessons / "2026-01-01-hook-safety.md").write_text(
        """---
title: Hook Safety
aliases: ["commit hook"]
---

Body mentions preflight.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_search.tm_lessons, "LESSONS_DIR", lessons)
    monkeypatch.setattr(tm_search.tm_core, "REPO_ROOT", tmp_path)

    hits = tm_search._search_lessons_group("hook preflight", 1)

    assert hits[0]["source"] == "lessons"
    assert hits[0]["score_breakdown"]["title_hits"] == 1
    assert hits[0]["score_breakdown"]["alias_hits"] == 1
    assert hits[0]["score_breakdown"]["body_hits"] == 1


def test_search_mem0_group_reports_native_score_and_rank_fallback(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "items": [
            {
                "id": "a",
                "content": "native",
                "score": 0.73,
                "created_at": now.isoformat(),
                "metadata": {"topic": "systems", "source": "codex", "route_decision": "mem0"},
            },
            {
                "id": "b",
                "content": "fallback",
                "metadata": {"topic": "operations", "source": "codex"},
            },
        ]
    }
    monkeypatch.setattr(tm_search.tm_core, "mem0_search", lambda *_args, **_kwargs: json.dumps(payload))

    hits, warning = tm_search._search_mem0_group("query", 2)

    assert warning is None
    assert hits[0]["score_breakdown"]["native_score"] == 0.73
    assert hits[0]["score_breakdown"]["rank_fallback"] is False
    assert hits[0]["score_breakdown"]["route_decision"] == "mem0"
    assert hits[1]["score_breakdown"]["native_score"] is None
    assert hits[1]["score_breakdown"]["rank_fallback"] is True


def test_search_tigermemory_propagates_wiki_breakdown(monkeypatch):
    monkeypatch.setattr(tm_search.tm_core, "primary_search_scope", lambda _q: "wiki")
    monkeypatch.setattr(
        tm_search.tm_core,
        "search_wiki_hybrid",
        lambda *_args, **_kwargs: [{
            "path": "wiki/systems/example.md",
            "title": "Example",
            "snippet": "body",
            "score": 0.1,
            "score_breakdown": {"rrf_score": 0.1},
        }],
    )

    result = tm_search.search_tigermemory("example", scope="wiki", dogfood_log=None)

    assert result["primary_results"][0]["score_breakdown"] == {"rrf_score": 0.1}


def test_search_tigermemory_wiki_scope_calls_search_wiki_hybrid_with_explain(monkeypatch):
    called_args = []
    def fake_search_wiki_hybrid(*args, **kwargs):
        called_args.append((args, kwargs))
        return [{
            "path": "wiki/systems/example.md",
            "title": "Example",
            "snippet": "body",
            "score": 0.1,
            "score_breakdown": {"degraded": True},
        }]

    monkeypatch.setattr(tm_search.tm_core, "primary_search_scope", lambda _q: "wiki")
    monkeypatch.setattr(tm_search.tm_core, "search_wiki_hybrid", fake_search_wiki_hybrid)

    result = tm_search.search_tigermemory("query text", scope="wiki", dogfood_log=None)

    assert len(called_args) == 1
    args, kwargs = called_args[0]
    assert args[0] == "query text"
    assert kwargs.get("explain") is True
    assert kwargs.get("include_inbox") is False
    assert result["primary_results"][0]["score_breakdown"]["degraded"] is True
