from __future__ import annotations

import datetime
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_ops  # type: ignore[import-not-found]
import tm_route  # type: ignore[import-not-found]
import tm_review  # type: ignore[import-not-found]


def _decision(route: str = "mem0", **kwargs) -> tm_route.RouteDecision:
    data = {
        "route": route,
        "score": 95,
        "topic_inferred": "systems",
        "issues": [],
        "reasons": "test route",
        "is_transient": False,
        "is_sensitive": False,
        "needs_human_review": False,
    }
    data.update(kwargs)
    return tm_route.RouteDecision(**data)


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
    assert "handoff_verified" not in result


def test_write_memory_with_review_local_profile_persists_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: _decision())
    monkeypatch.setattr(
        tm_memory_ops.tm_core,
        "tigermemory_profile",
        lambda: tm_memory_ops.tm_core.TIGERMEMORY_PROFILE_LOCAL,
    )
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "local.sqlite"))
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "---\nmemory_type: session-handoff\nagent: codex\n---\nP4.1b local write review path",
    )

    assert result["route"] == "mem0"
    assert result["handoff_verified"] is True
    assert result["handoff_verification"]["source"] == "unknown"
    assert result["handoff_verification"]["failure_reason"] is None
    assert result["verified"]["direct_readback_ok"] is True
    assert result["verified"]["search_by_id_self_hit"] is True
    assert result["verified"]["digest_inclusion_reason"].startswith("n/a: local backend")
    assert result["id"]
    raw = tm_memory_ops.tm_core.mem0_search("P4.1b review", size=3)
    payload = json.loads(raw)
    assert payload["count"] == 1
    assert payload["results"][0]["metadata"]["source"] == "codex"
    assert payload["results"][0]["metadata"]["topic"] == "systems"


def test_fetch_mem0_items_by_date_range_local_profile_reads_sqlite_without_http(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tm_memory_ops.tm_core,
        "tigermemory_profile",
        lambda: tm_memory_ops.tm_core.TIGERMEMORY_PROFILE_LOCAL,
    )
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "local.sqlite"))
    monkeypatch.setattr(
        tm_memory_ops.tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not be used")),
    )

    tm_memory_ops.tm_core.mem0_write(
        "codex",
        "systems",
        "local profile audit read path should be visible to cron",
        metadata_extra={"source": "codex", "topic": "systems"},
    )

    now = datetime.datetime.now(tm_memory_ops.tm_core.TZ_CN)
    rows = tm_memory_ops.fetch_mem0_items_by_date_range(
        now - datetime.timedelta(days=1),
        now + datetime.timedelta(days=1),
        max_items=20,
    )

    assert len(rows) == 1
    assert rows[0]["content"] == "local profile audit read path should be visible to cron"
    assert rows[0]["metadata"]["source"] == "codex"
    assert rows[0]["backend_origin"] == tm_memory_ops.tm_core.TIGERMEMORY_PROFILE_LOCAL


def test_session_handoff_skips_route_llm_and_targets_verified_mem0(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fail_route(*_args, **_kwargs):
        raise AssertionError("session handoff must not call tm_route.route_memory")

    def fake_mem0_write(agent, topic, text, metadata_extra=None, **kwargs):
        captured.update({
            "agent": agent,
            "topic": topic,
            "text": text,
            "metadata_extra": metadata_extra,
            "kwargs": kwargs,
        })
        return json.dumps({"id": mem_id})

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", fail_route)
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", fake_mem0_write)
    monkeypatch.setattr(tm_memory_ops.tm_core, "verify_memory_id", lambda _id: {"direct_readback_ok": True})
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    text = (
        "---\n"
        "memory_type: session-handoff\n"
        "session_id: codex-20260609-0830\n"
        "ide: codex\n"
        "agent: codex\n"
        "source: agent\n"
        "---\n\n"
        "## Task\nFix cron intake handoff routing noise.\n"
    )
    result = tm_memory_ops.write_memory_with_review("codex", "systems", text)

    assert result["route"] == "mem0"
    assert result["id"] == mem_id
    assert result["knowledge_target"] == "mem0"
    assert result["route_mode"] == "session_handoff_fast_path"
    assert result["handoff_deepseek_called"] is False
    assert result["handoff_sensitive_guard"] is False
    assert result["handoff_verified"] is True
    assert result["handoff_verification"]["source"] == "agent"
    assert captured["topic"] == "systems"
    assert captured["metadata_extra"]["knowledge_target"] == "mem0"
    assert captured["metadata_extra"]["route_mode"] == "session_handoff_fast_path"
    assert captured["metadata_extra"]["handoff_deepseek_called"] is False


def test_session_handoff_sensitive_guard_routes_to_inbox_without_llm(monkeypatch):
    captured = {}

    def fail_route(*_args, **_kwargs):
        raise AssertionError("sensitive session handoff must not call tm_route.route_memory")

    def fake_write_and_commit_inbox(agent, topic, title, text, frontmatter_extra=None):
        captured.update({
            "agent": agent,
            "topic": topic,
            "title": title,
            "text": text,
            "frontmatter_extra": frontmatter_extra,
        })
        return "inbox/handoff-sensitive.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", fail_route)
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    text = (
        "---\n"
        "memory_type: session-handoff\n"
        "session_id: codex-20260609-0831\n"
        "source: agent\n"
        "---\n\n"
        "## Evidence Refs\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
    )
    result = tm_memory_ops.write_memory_with_review("codex", "systems", text)

    assert result["route"] == "inbox"
    assert result["outcome"] == "human_review"
    assert result["knowledge_target"] == "human_review"
    assert result["handoff_verified"] is False
    assert result["handoff_sensitive_guard"] is True
    assert result["handoff_sensitive_hit_types"] == ["bearer_token"]
    assert result["handoff_deepseek_called"] is False
    assert captured["frontmatter_extra"]["knowledge_target"] == "human_review"
    assert captured["frontmatter_extra"]["route_mode"] == "session_handoff_fast_path"
    assert captured["frontmatter_extra"]["handoff_sensitive_hit_types"] == ["bearer_token"]


def test_light_write_skips_route_memory_and_adds_metadata(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fail_route(*_args, **_kwargs):
        raise AssertionError("light=True must not call tm_route.route_memory")

    def fake_mem0_write(agent, topic, text, metadata_extra=None, **kwargs):
        captured.update({
            "agent": agent,
            "topic": topic,
            "text": text,
            "metadata_extra": metadata_extra,
            "kwargs": kwargs,
        })
        return json.dumps({"id": mem_id})

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", fail_route)
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", fake_mem0_write)
    monkeypatch.setattr(tm_memory_ops.tm_core, "verify_memory_id", lambda _id: {"direct_readback_ok": True})
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "daily-health pointer", light=True)

    assert result["route"] == "mem0"
    assert result["score"] == 50
    assert result["light_bypass"] is True
    assert result["light_deepseek_called"] is False
    assert captured["metadata_extra"]["light_bypass"] is True
    assert captured["metadata_extra"]["route_mode"] == "light_bypass"
    assert captured["metadata_extra"]["light_sensitive_guard"] is False


def test_light_sensitive_phone_id_and_bank_card_route_to_inbox(monkeypatch):
    captured = {}

    def fail_route(*_args, **_kwargs):
        raise AssertionError("light=True must not call tm_route.route_memory")

    def fake_write_and_commit_inbox(agent, topic, title, text, frontmatter_extra=None):
        captured.update({
            "agent": agent,
            "topic": topic,
            "title": title,
            "text": text,
            "frontmatter_extra": frontmatter_extra,
        })
        return "inbox/x.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", fail_route)
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "phone 13800138000 id 11010519491231002X card number 4111 1111 1111 1111",
        light=True,
    )

    assert result["route"] == "inbox"
    assert result["score"] == 0
    assert result["light_sensitive_guard"] is True
    assert set(result["light_sensitive_hit_types"]) == {"phone", "cn_id", "bank_card"}
    assert captured["frontmatter_extra"]["route_score"] == 0
    assert captured["frontmatter_extra"]["light_bypass"] is True
    assert captured["frontmatter_extra"]["light_deepseek_called"] is False
    assert captured["frontmatter_extra"]["light_sensitive_hit_types"] == ["phone", "cn_id", "bank_card"]


def test_light_sensitive_credentials_route_to_inbox(monkeypatch):
    captured = {}

    def fail_route(*_args, **_kwargs):
        raise AssertionError("light=True must not call tm_route.route_memory")

    def fake_write_and_commit_inbox(agent, topic, title, text, frontmatter_extra=None):
        captured.update({
            "frontmatter_extra": frontmatter_extra,
        })
        return "inbox/x.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", fail_route)
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        light=True,
    )

    assert result["route"] == "inbox"
    assert result["score"] == 0
    assert result["light_sensitive_hit_types"] == ["bearer_token"]
    assert captured["frontmatter_extra"]["light_deepseek_called"] is False


def test_light_sensitive_credential_patterns_match():
    password_hits = tm_memory_ops._light_sensitive_hits("password=abcdefghijklmnop")
    private_key_hits = tm_memory_ops._light_sensitive_hits(
        "-----BEGIN PRIVATE KEY-----\nabcdefghi\n-----END PRIVATE KEY-----"
    )

    assert any(hit["kind"] == "credential" for hit in password_hits)
    assert any(hit["kind"] == "private_key" for hit in private_key_hits)


def test_bank_card_detector_accepts_luhn_candidate_lengths():
    # 16-digit Luhn-valid Visa test number with bank keyword context
    assert tm_memory_ops._bank_card_hits("银行卡号 4111 1111 1111 1111") == [
        {"kind": "bank_card", "pattern": "BANK_CARD_CONTEXT_RE"}
    ]
    # 16-digit Luhn-invalid number should not match
    assert tm_memory_ops._bank_card_hits("银行卡号 1234 5678 9012 3456") == []
    # 13-digit number should no longer match (16-19 range)
    assert tm_memory_ops._bank_card_hits("银行卡号 4222 2222 2222 2") == []


def test_light_sensitive_bank_card_false_positive_examples_do_not_match():
    examples = [
        "银行卡候选 13800138000",
        "银行卡候选 11010519491231002X",
        "银行卡候选 2026-05-20 13:45:00",
        "银行卡候选 fd65b298-05bd-493c-83ce-e37d84447362",
        "银行卡尾号 3815 / 维度 1024",
    ]

    for text in examples:
        assert tm_memory_ops._bank_card_hits(text) == []


def test_force_inbox_and_light_are_mutually_exclusive():
    with pytest.raises(ValueError):
        tm_memory_ops.write_memory_with_review("codex", "systems", "body", force_inbox=True, light=True)


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


def test_write_memory_wiki_proposal_target_writes_marked_inbox(monkeypatch):
    captured = {}
    decision = _decision(
        route="inbox",
        knowledge_target="wiki_proposal",
        target_confidence=92,
        wiki_partition="systems",
        wiki_slug_hint="Unified Knowledge Routing",
        wiki_action="update",
        evidence_hints=["evidence_hint: add source path before final Wiki compile"],
    )

    def fake_write_and_commit_inbox(agent, topic, title, body, frontmatter_extra=None):
        captured.update({
            "agent": agent,
            "topic": topic,
            "title": title,
            "body": body,
            "frontmatter_extra": frontmatter_extra,
        })
        return "inbox/wiki-proposal.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(tm_review, "review_draft", lambda _body: {
        "score": 83,
        "issues": [],
        "suggestions": ["add sources"],
        "ready_for_compile": True,
        "review_skipped": False,
    })
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "stable routing rule")

    assert result["route"] == "inbox"
    assert result["outcome"] == "wiki_proposal"
    assert result["path"] == "inbox/wiki-proposal.md"
    assert result["knowledge_target"] == "wiki_proposal"
    assert result["proposal_kind"] == "wiki"
    assert result["wiki_partition"] == "systems"
    assert result["wiki_slug_hint"] == "unified-knowledge-routing"
    assert result["wiki_action"] == "update"
    assert result["evidence_hints"] == ["evidence_hint: add source path before final Wiki compile"]
    assert result["warnings"] == ["evidence_hint: add source path before final Wiki compile"]
    assert result["review"]["score"] == 83
    assert captured["topic"] == "systems"
    assert captured["title"] == "Wiki proposal 95"
    assert "Wiki proposal: wiki/systems/unified-knowledge-routing.md" in captured["body"]
    assert "## Evidence hints" in captured["body"]
    assert "evidence_hint: add source path before final Wiki compile" in captured["body"]
    assert captured["frontmatter_extra"]["proposal_kind"] == "wiki"
    assert captured["frontmatter_extra"]["knowledge_target"] == "wiki_proposal"
    assert captured["frontmatter_extra"]["evidence_hints"] == [
        "evidence_hint: add source path before final Wiki compile"
    ]
    assert captured["frontmatter_extra"]["l2_review_score"] == 83


def test_write_memory_wiki_proposal_merges_same_day_target(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_memory_ops.tm_core, "REPO_ROOT", tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    existing = inbox / "2026-06-08-1200-codex-systems.md"
    existing.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-06-08",
            "routed_by: tigermemory",
            "proposal_kind: wiki",
            "wiki_partition: systems",
            "wiki_slug_hint: unified-knowledge-routing",
            "---",
            "",
            "# Wiki proposal 90",
            "",
            "existing body",
        ]),
        encoding="utf-8",
    )
    decision = _decision(
        route="inbox",
        knowledge_target="wiki_proposal",
        target_confidence=92,
        wiki_partition="systems",
        wiki_slug_hint="Unified Knowledge Routing",
        wiki_action="update",
    )
    commits = []

    def fake_now(fmt: str = "%Y-%m-%d") -> str:
        return {
            "%Y-%m-%d": "2026-06-08",
            "%Y-%m-%d %H:%M": "2026-06-08 23:20",
            "%Y%m%d-%H%M": "20260608-2320",
        }.get(fmt, "2026-06-08")

    monkeypatch.setattr(tm_memory_ops.tm_core, "now", fake_now)
    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(tm_review, "review_draft", lambda _body: {
        "score": 83,
        "issues": [],
        "suggestions": [],
        "ready_for_compile": True,
        "review_skipped": False,
    })
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_commit_push", lambda files, msg: commits.append((files, msg)) or "def456")
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "new proposal body")

    assert result["route"] == "inbox"
    assert result["outcome"] == "wiki_proposal"
    assert result["path"] == "inbox/2026-06-08-1200-codex-systems.md"
    assert result["deduped"] is True
    assert commits == [(["inbox/2026-06-08-1200-codex-systems.md"], "[codex] update: Wiki proposal 95")]
    merged = existing.read_text(encoding="utf-8")
    assert "existing body" in merged
    assert "## Merged routed write 2026-06-08 23:20" in merged
    assert "new proposal body" in merged


def test_write_memory_wiki_proposal_skips_review_when_budget_is_exhausted(monkeypatch):
    captured = {}
    decision = _decision(
        route="inbox",
        knowledge_target="wiki_proposal",
        target_confidence=92,
        wiki_partition="systems",
        wiki_slug_hint="budget-aware-routing",
        wiki_action="create",
    )

    def fail_review(*_args, **_kwargs):
        raise AssertionError("budget-exhausted wiki proposal must skip L2 review")

    def fake_write_and_commit_inbox(agent, topic, title, body, frontmatter_extra=None):
        captured.update({
            "body": body,
            "frontmatter_extra": frontmatter_extra,
        })
        return "inbox/wiki-budget.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(tm_review, "review_draft", fail_review)
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "stable routing rule",
        total_budget_s=0,
    )

    assert result["route"] == "inbox"
    assert result["outcome"] == "wiki_proposal"
    assert result["review"]["review_skipped"] is True
    assert "budget left too little time" in result["review"]["reason"]
    assert captured["frontmatter_extra"]["l2_review_skipped"] is True
    assert "budget left too little time" in captured["body"]


def test_write_memory_human_review_target_returns_inbox_with_outcome(monkeypatch):
    captured = {}
    decision = _decision(
        route="inbox",
        knowledge_target="human_review",
        target_confidence=71,
        review_reason="authority conflict",
        needs_human_review=True,
    )

    def fake_write_and_commit_inbox(agent, topic, title, text, frontmatter_extra=None):
        captured["topic"] = topic
        captured["frontmatter_extra"] = frontmatter_extra
        return "inbox/review.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "conflicting authority claim")

    assert result["route"] == "inbox"
    assert result["outcome"] == "human_review"
    assert result["knowledge_target"] == "human_review"
    assert result["review_reason"] == "authority conflict"
    assert captured["frontmatter_extra"]["knowledge_target"] == "human_review"
    assert captured["frontmatter_extra"]["needs_human_review"] is True


def test_mem0_target_failure_returns_inbox_with_retry_error_outcome(monkeypatch):
    captured = {}
    decision = _decision(route="mem0", knowledge_target="mem0", target_confidence=88)

    def fake_write_and_commit_inbox(agent, topic, title, text, frontmatter_extra=None):
        captured["frontmatter_extra"] = frontmatter_extra
        return "inbox/retry.md", "abc123"

    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(tm_memory_ops.tm_core, "mem0_write", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(tm_memory_ops.tm_core, "write_and_commit_inbox", fake_write_and_commit_inbox)
    monkeypatch.setattr(tm_memory_ops.tm_core, "git_remote_blob_url", lambda rel: f"https://example/{rel}")
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "---\n"
        "memory_type: session-handoff\n"
        "session_id: codex-20260608-0900\n"
        "repo: D:\\tigermemory\n"
        "ide: codex\n"
        "agent: codex\n"
        "source: agent\n"
        "---\n"
        "atomic fact",
    )

    assert result["route"] == "inbox"
    assert result["outcome"] == "retry_error"
    assert result["handoff_verified"] is False
    assert result["handoff_verification"]["source"] == "agent"
    assert "mem0 write failed" in result["handoff_verification"]["failure_reason"]
    assert result["path"] == "inbox/retry.md"
    assert result["knowledge_target"] == "retry_error"
    assert "mem0 write failed" in result["reasons"]
    assert captured["frontmatter_extra"]["knowledge_target"] == "retry_error"
    assert captured["frontmatter_extra"]["review_reason"] == "mem0 write failed after router chose mem0"


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
