from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_ops  # type: ignore[import-not-found]
import tm_route  # type: ignore[import-not-found]


def _decision(route: str = "mem0") -> tm_route.RouteDecision:
    return tm_route.RouteDecision(
        route=route,
        score=95,
        topic_inferred="systems",
        issues=[],
        reasons="test route",
        is_transient=False,
        is_sensitive=False,
        needs_human_review=False,
    )


def test_write_memory_success_adds_verified_readback(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: _decision())
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", lambda *_args, **_kwargs: json.dumps({"id": mem_id}))
    monkeypatch.setattr(tm_memory_ops.tm_core, "verify_memory_id", lambda _id: {
        "direct_readback_ok": True,
        "status": "exists_active",
        "state": "active",
        "created_at_local": "2026-05-16T03:23:05+08:00",
        "text_len": 42,
        "text_sha256_12": "abc123",
        "search_by_id_self_hit": True,
        "digest_date": "2026-05-16",
        "digest_contains": False,
        "digest_inclusion_reason": "inside digest window but id not found in digest",
        "warnings": [],
    })
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "body")

    assert result["route"] == "mem0"
    assert result["id"] == mem_id
    assert result["verified"]["direct_readback_ok"] is True
    assert result["verified"]["search_by_id_self_hit"] is True


def test_write_memory_results_empty_falls_back_to_inbox(monkeypatch):
    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: _decision())
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", lambda *_args, **_kwargs: json.dumps({"results": []}))
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", lambda *_args, **_kwargs: ("inbox/x.md", "abc123"))
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "body")

    assert result["route"] == "inbox"
    assert result["path"] == "inbox/x.md"
    assert "results=[]" in result["reasons"]


def test_write_memory_budget_exhaustion_skips_mem0(monkeypatch):
    calls = {"mem0": 0}

    def fake_mem0_write(*_args, **_kwargs):
        calls["mem0"] += 1
        return "{}"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: _decision())
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", fake_mem0_write)
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", lambda *_args, **_kwargs: ("inbox/x.md", "abc123"))
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "body", total_budget_s=0)

    assert result["route"] == "inbox"
    assert calls["mem0"] == 0
    assert "budget exhausted" in result["reasons"]
