from __future__ import annotations

import pytest

import tigermemory_core as tm_core


def test_propose_wiki_admin_page_normalizes_deepseek_json(monkeypatch) -> None:
    def fake_call(system_prompt, user_msg, **kwargs):
        assert "Wiki Admin" in system_prompt
        assert "target_partition: systems" in user_msg
        assert kwargs["model"] == tm_core.DEFAULT_DEEPSEEK_ADMIN_MODEL
        return True, {
            "should_write": True,
            "title": "Starter Admin",
            "slug": "Starter_Admin",
            "summary": "A short durable summary.",
            "body_markdown": "## 已验证现状\n\nThe user provided a stable project note.",
            "rationale": "Stable enough for wiki.",
            "confidence": "88.7",
            "aliases": ["starter admin"],
            "tags": ["wiki-admin", "public-answer"],
            "key_facts": ["Admin proposals carry searchable metadata."],
            "evidence_refs": ["unit-test-source"],
        }

    monkeypatch.setattr(tm_core, "_call_deepseek_json", fake_call)

    result = tm_core.propose_wiki_admin_page(
        "This source material is long enough and stable enough for a wiki draft.",
        partition="systems",
        title="Fallback",
        source="unit-test-source",
    )

    assert result["schema"] == "tigermemory-admin-proposal-v1"
    assert result["should_write"] is True
    assert result["target_path"] == "wiki/systems/starter-admin.md"
    assert result["confidence"] == 88
    assert result["tags"] == ["wiki-admin", "public-answer"]
    assert result["key_facts"] == ["Admin proposals carry searchable metadata."]
    assert result["user_review_required"] is True
    assert 'summary: "A short durable summary."' in result["wiki_markdown"]
    assert "tags:" in result["wiki_markdown"]
    assert "key_facts:" in result["wiki_markdown"]
    assert "## 关键事实" in result["wiki_markdown"]
    assert "## 摘要" in result["wiki_markdown"]
    assert "## 来源" in result["wiki_markdown"]
    assert "unit-test-source" in result["wiki_markdown"]


def test_propose_wiki_admin_page_uses_admin_model_env(monkeypatch, tmp_path) -> None:
    def fake_call(_system_prompt, _user_msg, **kwargs):
        assert kwargs["model"] == "custom-admin-model"
        return True, {
            "should_write": True,
            "title": "Admin Env",
            "slug": "admin-env",
            "summary": "A short durable summary.",
            "body_markdown": "## 已验证现状\n\nThe user provided a stable project note.",
            "rationale": "Stable enough for wiki.",
            "confidence": 80,
        }

    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_ADMIN_MODEL=custom-admin-model\n", encoding="utf-8")
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(env_path))
    monkeypatch.setattr(tm_core, "_call_deepseek_json", fake_call)

    result = tm_core.propose_wiki_admin_page(
        "This source material is long enough and stable enough for a wiki draft.",
        partition="systems",
        title="Fallback",
    )

    assert result["target_path"] == "wiki/systems/admin-env.md"


def test_propose_wiki_admin_page_keeps_rejected_proposal_reviewable(monkeypatch) -> None:
    monkeypatch.setattr(
        tm_core,
        "_call_deepseek_json",
        lambda *_args, **_kwargs: (True, {
            "should_write": False,
            "rationale": "Contains private material.",
            "confidence": 0,
            "evidence_refs": ["user-provided text"],
        }),
    )

    result = tm_core.propose_wiki_admin_page(
        "This source material is long enough but should not be written.",
        partition="systems",
        title="Private Draft",
    )

    assert result["should_write"] is False
    assert result["user_review_required"] is True
    assert "target_path" not in result


def test_propose_wiki_admin_page_blocks_person_partition() -> None:
    with pytest.raises(ValueError, match="not supported"):
        tm_core.propose_wiki_admin_page(
            "This source material is long enough for a proposal.",
            partition="person",
            title="Private Person",
        )


def test_propose_wiki_admin_page_blocks_obvious_secret_before_llm(monkeypatch) -> None:
    called = False

    def fake_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return True, {}

    monkeypatch.setattr(tm_core, "_call_deepseek_json", fake_call)

    with pytest.raises(ValueError, match="remove secrets"):
        tm_core.propose_wiki_admin_page(
            "The setup note says API_KEY=sk-this-should-not-go-to-an-online-model.",
            partition="systems",
            title="Secret Note",
        )

    assert called is False
