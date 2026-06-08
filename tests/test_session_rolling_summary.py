from __future__ import annotations

import importlib.util
import json
import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "session-rolling-summary.py"


def load_session_rolling_summary():
    spec = importlib.util.spec_from_file_location("session_rolling_summary", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


srs = load_session_rolling_summary()


def make_raw_card(memory_id: str, session_id: str, blocker: str) -> dict[str, str]:
    return {
        "id": memory_id,
        "created_at": "2026-06-08T09:00:00Z",
        "memory": (
            "---\n"
            "memory_type: session-handoff\n"
            f"session_id: {session_id}\n"
            "ide: codex\n"
            "agent: codex\n"
            "persona_primary: executor\n"
            "confidence: high\n"
            "source: agent\n"
            "---\n"
            "\n"
            "## Task\n"
            "Keep the session open.\n"
            "\n"
            "## Blockers\n"
            f"- {blocker}\n"
            "\n"
            "## Evidence Refs\n"
            "- files: tools/session-rolling-summary.py\n"
        ),
    }


def test_build_machine_readable_summary_includes_promotion_candidates():
    raw_cards = [
        make_raw_card("m1", "codex-20260608-0900", "blocked by shared cache invalidation"),
        make_raw_card("m2", "codex-20260608-0905", "blocked by shared cache invalidation"),
        make_raw_card("m3", "codex-20260608-0910", "blocked by shared cache invalidation"),
        make_raw_card("m4", "codex-20260608-0915", "single-run follow-up"),
    ]
    cards = [srs.parse_card(card) for card in raw_cards]
    cards = [card for card in cards if card is not None]
    patterns = srs.detect_patterns(cards)

    summary = srs.build_machine_readable_summary(cards, patterns, days=7)

    assert summary["schema_version"] == srs.SUMMARY_SCHEMA_VERSION
    assert summary["window_days"] == 7
    assert summary["promotion_candidates"]

    candidate = summary["promotion_candidates"][0]
    assert candidate["decision"] == "propose_wiki_page"
    assert candidate["evidence_refs"] == ["memory:m1", "memory:m2", "memory:m3"]
    assert candidate["target_partition"] == "operations"
    assert candidate["needs_human_review"] is True
    assert candidate["count"] == 3
    assert candidate["blocker"] == "- blocked by shared cache invalidation"


def test_parse_card_accepts_content_shape():
    raw_card = make_raw_card("m1", "codex-20260608-0900", "blocked by shared cache invalidation")
    content_card = {
        "id": raw_card["id"],
        "created_at": raw_card["created_at"],
        "content": raw_card["memory"],
    }

    parsed = srs.parse_card(content_card)

    assert parsed is not None
    assert parsed["session_id"] == "codex-20260608-0900"


def test_main_json_emits_promotion_candidates_without_auto_promote(monkeypatch, capsys):
    raw_cards = [
        make_raw_card("m1", "codex-20260608-0900", "blocked by shared cache invalidation"),
        make_raw_card("m2", "codex-20260608-0905", "blocked by shared cache invalidation"),
        make_raw_card("m3", "codex-20260608-0910", "blocked by shared cache invalidation"),
    ]
    monkeypatch.setattr(srs, "search_handoff_cards", lambda days=7, size=50: raw_cards)

    def fail_promote(_patterns):
        raise AssertionError("JSON mode must not auto-promote wiki pages")

    monkeypatch.setattr(srs, "promote_recurring_blockers", fail_promote)

    exit_code = srs.main(["--days", "7", "--json", "--promote"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""

    summary = json.loads(captured.out)
    assert summary["schema_version"] == srs.SUMMARY_SCHEMA_VERSION
    assert summary["promotion_candidates"][0]["decision"] == "propose_wiki_page"
    assert summary["promotion_candidates"][0]["evidence_refs"] == [
        "memory:m1",
        "memory:m2",
        "memory:m3",
    ]
    assert summary["promotion_candidates"][0]["needs_human_review"] is True
