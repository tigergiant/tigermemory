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


def make_canvas_raw_card(memory_id: str, session_id: str, canvas_patch: str) -> dict[str, str]:
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
            "Patch the project canvas state for the current run.\n"
            "\n"
            "## Decisions\n"
            "Keep the update machine-readable and side-effect free.\n"
            "\n"
            "## Evidence Refs\n"
            f"- canvas_patch: {canvas_patch}\n"
            "- files: wiki/operations/project-canvas.md\n"
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


def test_build_machine_readable_summary_includes_canvas_update_candidates():
    raw_cards = [
        make_canvas_raw_card(
            "m9",
            "codex-20260608-1015",
            "P2_RollingSummary updated with canvas_patch evidence",
        ),
    ]
    cards = [srs.parse_card(card) for card in raw_cards]
    cards = [card for card in cards if card is not None]
    patterns = srs.detect_patterns(cards)

    summary = srs.build_machine_readable_summary(cards, patterns, days=7)

    assert summary["canvas_update_candidates"]

    candidate = summary["canvas_update_candidates"][0]
    assert candidate["decision"] == "propose_canvas_update"
    assert candidate["candidate_type"] == "project_canvas_update"
    assert candidate["target_path"] == "wiki/operations/project-canvas.md"
    assert candidate["target_module"] == "P2_RollingSummary"
    assert candidate["summary"] == "Patch the project canvas state for the current run."
    assert candidate["reason"] == "canvas_patch evidence: P2_RollingSummary updated with canvas_patch evidence"
    assert candidate["evidence_refs"] == [
        "memory:m9",
        "canvas_patch: P2_RollingSummary updated with canvas_patch evidence",
        "files: wiki/operations/project-canvas.md",
    ]
    assert candidate["needs_human_review"] is True
    assert candidate["confidence"] == "high"


def test_build_machine_readable_summary_includes_recurring_blocker_canvas_candidates():
    raw_cards = [
        make_raw_card("m1", "codex-20260608-0900", "blocked by shared cache invalidation"),
        make_raw_card("m2", "codex-20260608-0905", "blocked by shared cache invalidation"),
        make_raw_card("m3", "codex-20260608-0910", "blocked by shared cache invalidation"),
        make_canvas_raw_card(
            "m9",
            "codex-20260608-1015",
            "P2_RollingSummary updated with canvas_patch evidence",
        ),
    ]
    cards = [srs.parse_card(card) for card in raw_cards]
    cards = [card for card in cards if card is not None]
    patterns = srs.detect_patterns(cards)

    summary = srs.build_machine_readable_summary(cards, patterns, days=7)

    assert summary["promotion_candidates"]
    assert summary["promotion_candidates"][0]["decision"] == "propose_wiki_page"
    assert summary["promotion_candidates"][0]["evidence_refs"] == [
        "memory:m1",
        "memory:m2",
        "memory:m3",
    ]
    assert summary["promotion_candidates"][0]["needs_human_review"] is True
    assert summary["promotion_candidates"][0]["count"] == 3
    assert summary["promotion_candidates"][0]["blocker"] == "- blocked by shared cache invalidation"

    assert len(summary["canvas_update_candidates"]) == 2
    recurring_candidate = summary["canvas_update_candidates"][1]
    assert recurring_candidate["decision"] == "propose_canvas_update"
    assert recurring_candidate["candidate_type"] == "recurring_blocker"
    assert recurring_candidate["target_path"] == "wiki/operations/project-canvas.md"
    assert recurring_candidate["target_module"] == "当前阻塞"
    assert recurring_candidate["summary"] == "- blocked by shared cache invalidation"
    assert recurring_candidate["reason"] == (
        "recurring blocker evidence: - blocked by shared cache invalidation "
        "appeared 3 times across 3 cards; human review required"
    )
    assert recurring_candidate["evidence_refs"] == ["memory:m1", "memory:m2", "memory:m3"]
    assert recurring_candidate["needs_human_review"] is True
    assert recurring_candidate["confidence"] == "medium"


def test_canvas_patch_none_produces_no_candidates():
    raw_card = make_canvas_raw_card("m10", "codex-20260608-1020", "none")

    cards = [srs.parse_card(raw_card)]
    cards = [card for card in cards if card is not None]
    patterns = srs.detect_patterns(cards)

    summary = srs.build_machine_readable_summary(cards, patterns, days=7)

    assert summary["canvas_update_candidates"] == []


def test_project_canvas_patch_na_produces_no_candidates():
    raw_card = {
        "id": "m11",
        "created_at": "2026-06-08T09:00:00Z",
        "memory": (
            "---\n"
            "memory_type: session-handoff\n"
            "session_id: codex-20260608-1025\n"
            "ide: codex\n"
            "agent: codex\n"
            "persona_primary: executor\n"
            "confidence: high\n"
            "source: agent\n"
            "---\n"
            "\n"
            "## Task\n"
            "Patch the project canvas state for the current run.\n"
            "\n"
            "## Evidence Refs\n"
            "- project_canvas_patch: n/a\n"
            "- files: wiki/operations/project-canvas.md\n"
        ),
    }

    cards = [srs.parse_card(raw_card)]
    cards = [card for card in cards if card is not None]
    patterns = srs.detect_patterns(cards)

    summary = srs.build_machine_readable_summary(cards, patterns, days=7)

    assert summary["canvas_update_candidates"] == []


def test_bulleted_none_blockers_do_not_create_candidates():
    raw_cards = [
        make_raw_card("m-none-1", "codex-20260608-1030", "none"),
        make_raw_card("m-none-2", "codex-20260608-1035", "none"),
        make_raw_card("m-none-3", "codex-20260608-1040", "none"),
    ]
    cards = [srs.parse_card(card) for card in raw_cards]
    cards = [card for card in cards if card is not None]
    patterns = srs.detect_patterns(cards)

    summary = srs.build_machine_readable_summary(cards, patterns, days=7)

    assert summary["recurring_blockers"] == {}
    assert summary["promotion_candidates"] == []
    assert summary["canvas_update_candidates"] == []


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
    assert summary["canvas_update_candidates"] == [
        {
            "decision": "propose_canvas_update",
            "candidate_type": "recurring_blocker",
            "target_path": "wiki/operations/project-canvas.md",
            "target_module": "当前阻塞",
            "summary": "- blocked by shared cache invalidation",
            "reason": (
                "recurring blocker evidence: - blocked by shared cache invalidation "
                "appeared 3 times across 3 cards; human review required"
            ),
            "evidence_refs": ["memory:m1", "memory:m2", "memory:m3"],
            "needs_human_review": True,
            "confidence": "medium",
        }
    ]
