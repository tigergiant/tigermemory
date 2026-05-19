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


def test_write_memory_can_preserve_requested_topic_for_storage(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fake_mem0_write(agent, topic, text, metadata_extra=None, **kwargs):
        captured["agent"] = agent
        captured["topic"] = topic
        captured["text"] = text
        captured["metadata_extra"] = metadata_extra
        captured["kwargs"] = kwargs
        return json.dumps({"id": mem_id})

    monkeypatch.setattr(
        tm_memory_ops.tm_route,
        "route_memory",
        lambda *_args, **_kwargs: tm_route.RouteDecision(
            route="mem0",
            score=85,
            topic_inferred="operations",
            issues=[],
            reasons="technical brand workflow",
            is_transient=False,
            is_sensitive=False,
            needs_human_review=False,
        ),
    )
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", fake_mem0_write)
    monkeypatch.setattr(tm_memory_ops.tm_core, "verify_memory_id", lambda _id: {"direct_readback_ok": True})
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "chatgpt",
        "brand",
        "body",
        preserve_requested_topic=True,
    )

    assert result["route"] == "mem0"
    assert result["topic"] == "brand"
    assert result["topic_inferred"] == "operations"
    assert result["warnings"] == [
        "topic mismatch: requested_topic=brand, topic_inferred=operations, stored_topic=brand"
    ]
    assert captured["topic"] == "brand"
    assert captured["metadata_extra"]["route_requested_topic"] == "brand"
    assert captured["metadata_extra"]["route_topic_inferred"] == "operations"
    assert captured["metadata_extra"]["stored_topic"] == "brand"


def test_write_memory_preserves_requested_topic_by_default(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fake_mem0_write(agent, topic, text, metadata_extra=None, **kwargs):
        captured["topic"] = topic
        captured["metadata_extra"] = metadata_extra
        return json.dumps({"id": mem_id})

    monkeypatch.setattr(
        tm_memory_ops.tm_route,
        "route_memory",
        lambda *_args, **_kwargs: tm_route.RouteDecision(
            route="mem0",
            score=90,
            topic_inferred="production",
            issues=[],
            reasons="misread production-ready wording",
            is_transient=False,
            is_sensitive=False,
            needs_human_review=False,
        ),
    )
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", fake_mem0_write)
    monkeypatch.setattr(tm_memory_ops.tm_core, "verify_memory_id", lambda _id: {"direct_readback_ok": True})
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "2026-05-19 Memory Answer production-ready closeout.",
    )

    assert result["route"] == "mem0"
    assert result["topic"] == "systems"
    assert result["topic_inferred"] == "production"
    assert captured["topic"] == "systems"
    assert captured["metadata_extra"]["route_requested_topic"] == "systems"
    assert captured["metadata_extra"]["route_topic_inferred"] == "production"
    assert captured["metadata_extra"]["stored_topic"] == "systems"
    assert result["warnings"] == [
        "topic mismatch: requested_topic=systems, topic_inferred=production, stored_topic=systems"
    ]


def test_write_memory_can_opt_out_of_requested_topic_preservation(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fake_mem0_write(agent, topic, text, metadata_extra=None, **kwargs):
        captured["topic"] = topic
        captured["metadata_extra"] = metadata_extra
        return json.dumps({"id": mem_id})

    monkeypatch.setattr(
        tm_memory_ops.tm_route,
        "route_memory",
        lambda *_args, **_kwargs: tm_route.RouteDecision(
            route="mem0",
            score=90,
            topic_inferred="operations",
            issues=[],
            reasons="operator note",
            is_transient=False,
            is_sensitive=False,
            needs_human_review=False,
        ),
    )
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", fake_mem0_write)
    monkeypatch.setattr(tm_memory_ops.tm_core, "verify_memory_id", lambda _id: {"direct_readback_ok": True})
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "body",
        preserve_requested_topic=False,
    )

    assert result["topic"] == "operations"
    assert result["warnings"] == []
    assert captured["topic"] == "operations"
    assert captured["metadata_extra"]["stored_topic"] == "operations"


def test_write_memory_does_not_preserve_requested_topic_for_sensitive_content(monkeypatch):
    captured = {}

    def fake_write_and_commit_inbox(agent, topic, title, text, frontmatter_extra=None):
        captured["topic"] = topic
        captured["frontmatter_extra"] = frontmatter_extra
        return "inbox/x.md", "abc123"

    monkeypatch.setattr(
        tm_memory_ops.tm_route,
        "route_memory",
        lambda *_args, **_kwargs: tm_route.RouteDecision(
            route="inbox",
            score=85,
            topic_inferred="person",
            issues=[],
            reasons="sensitive personal content",
            is_transient=False,
            is_sensitive=True,
            needs_human_review=False,
        ),
    )
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "chatgpt",
        "brand",
        "body",
        preserve_requested_topic=True,
    )

    assert result["route"] == "inbox"
    assert result["topic"] == "person"
    assert result.get("warnings", []) == []
    assert captured["topic"] == "person"
    assert captured["frontmatter_extra"]["route_requested_topic"] == "brand"
    assert captured["frontmatter_extra"]["stored_topic"] == "person"


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


def test_schedule_embed_refresh_debounces_by_scope(monkeypatch):
    created = []

    class FakeTimer:
        def __init__(self, interval, function, args=()):
            self.interval = interval
            self.function = function
            self.args = args
            self.daemon = False
            self.cancelled = False
            self.started = False
            created.append(self)

        def cancel(self):
            self.cancelled = True

        def start(self):
            self.started = True

    monkeypatch.setattr(tm_memory_ops.threading, "Timer", FakeTimer)
    monkeypatch.setattr(tm_memory_ops, "EMBED_REFRESH_DEBOUNCE_SECONDS", 7)
    tm_memory_ops._embed_timers.clear()

    first = tm_memory_ops.schedule_embed_refresh(scope="wiki", reason="first", paths=["wiki/a.md"])
    second = tm_memory_ops.schedule_embed_refresh(scope="wiki", reason="second", paths=["wiki/b.md"])

    assert first["embed_refresh_scheduled"] is True
    assert first["embed_refresh_scope"] == "wiki"
    assert second["embed_refresh_debounce_seconds"] == 7
    assert len(created) == 2
    assert created[0].cancelled is True
    assert created[1].started is True
    assert created[1].args == ("wiki", "second", ["wiki/b.md"])


def test_refresh_embed_index_uses_refresh_subprocess(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(tm_memory_ops.subprocess, "run", fake_run)

    tm_memory_ops._refresh_embed_index("wiki", "test", ["wiki/a.md"])

    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][2:] == ["refresh", "--scope", "wiki"]
    assert captured["kwargs"]["cwd"] == tm_memory_ops.tm_core.REPO_ROOT
    assert captured["kwargs"]["check"] is False
