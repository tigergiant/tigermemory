#!/usr/bin/env python3
"""tools/tm_route_test.py — pytest / unittest suite for tm_route."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import tm_route


def _make_ds_result(**kwargs):
    defaults = {
        "score": 85,
        "topic_inferred": "systems",
        "is_transient": False,
        "is_sensitive": False,
        "needs_human_review": False,
        "issues": [],
        "reasons": "good",
    }
    defaults.update(kwargs)
    return True, defaults


class TestRouteMemory(unittest.TestCase):
    def test_empty_text_discard(self):
        d = tm_route.route_memory("", "systems", "kimi")
        assert d.route == "discard"
        assert d.score == 0
        assert d.issues == ["text is empty"]

    def test_high_score_mem0(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(score=85)):
            d = tm_route.route_memory("Tigermemory v2 deployed.", "systems", "kimi")
            assert d.route == "mem0"
            assert d.score == 85
            assert d.topic_inferred == "systems"

    def test_medium_score_inbox(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(score=50)):
            d = tm_route.route_memory("Some okay content.", "systems", "kimi")
            assert d.route == "inbox"
            assert d.score == 50

    def test_low_score_discard(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(score=20)):
            d = tm_route.route_memory("bad bad bad.", "systems", "kimi")
            assert d.route == "discard"
            assert d.score == 20

    def test_transient_discard(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(is_transient=True, score=85)):
            d = tm_route.route_memory("Tiger is configuring X today.", "systems", "kimi")
            assert d.route == "discard"
            assert d.is_transient is True
            assert "transient content" in d.reasons

    def test_daily_health_index_summary_mem0_even_if_marked_transient(self):
        text = (
            "2026-05-15 每日巡检总清单已更新：结论黄，"
            "详情见 wiki/operations/daily-health/2026-05-15.md"
        )
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(is_transient=True, score=85)):
            d = tm_route.route_memory(text, "operations", "codex")
            assert d.route == "mem0"
            assert d.is_transient is False
            assert "daily health index summary" in d.reasons

    def test_daily_health_index_summary_still_respects_sensitive_routing(self):
        text = (
            "2026-05-15 每日巡检总清单已更新：结论黄，"
            "详情见 wiki/operations/daily-health/2026-05-15.md"
        )
        with patch(
            "tm_core._call_deepseek_json",
            return_value=_make_ds_result(is_transient=True, is_sensitive=True, score=85),
        ):
            d = tm_route.route_memory(text, "operations", "codex")
            assert d.route == "inbox"
            assert d.is_sensitive is True

    def test_daily_health_index_summary_medium_score_inbox(self):
        text = (
            "2026-05-15 每日巡检总清单已更新：结论黄，"
            "详情见 wiki/operations/daily-health/2026-05-15.md"
        )
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(is_transient=True, score=50)):
            d = tm_route.route_memory(text, "operations", "codex")
            assert d.route == "inbox"
            assert d.is_transient is False

    def test_sensitive_inbox(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(is_sensitive=True, score=85)):
            d = tm_route.route_memory("Phone 13800138000.", "systems", "kimi")
            assert d.route == "inbox"
            assert d.is_sensitive is True
            assert "sensitive content" in d.reasons

    def test_person_topic_inbox(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(score=85)):
            d = tm_route.route_memory("Tiger likes coffee.", "person", "kimi")
            assert d.route == "inbox"
            assert "person partition requires human review" in d.reasons

    def test_needs_review_inbox(self):
        with patch("tm_core._call_deepseek_json", return_value=_make_ds_result(score=85, needs_human_review=True)):
            d = tm_route.route_memory("Ambiguous content.", "systems", "kimi")
            assert d.route == "inbox"
            assert d.needs_human_review is True
            assert "needs review" in d.reasons

    def test_deepseek_fail_warn_inbox_unreviewed(self):
        with patch("tm_core._call_deepseek_json", return_value=(False, "HTTP 401")):
            d = tm_route.route_memory("Anything.", "systems", "kimi")
            assert d.route == "inbox"
            assert d.unreviewed is True
            assert d.reasons == "api_unreachable"
            assert "DeepSeek routing failed" in d.issues[0]

    def test_as_metadata(self):
        d = tm_route.RouteDecision(
            route="mem0", score=75, topic_inferred="brand",
            issues=[], reasons="ok", is_transient=False,
            is_sensitive=False, needs_human_review=False,
        )
        assert d.as_metadata() == {
            "route_decision": "mem0",
            "route_score": 75,
            "route_topic_inferred": "brand",
        }

    def test_as_metadata_unreviewed(self):
        d = tm_route.RouteDecision(
            route="inbox", score=0, topic_inferred="systems",
            issues=[], reasons="fail", is_transient=False,
            is_sensitive=False, needs_human_review=False,
            unreviewed=True,
        )
        assert d.as_metadata() == {
            "route_decision": "inbox",
            "route_score": 0,
            "route_topic_inferred": "systems",
            "unreviewed": True,
        }

    def test_malformed_score_neutral(self):
        with patch("tm_core._call_deepseek_json", return_value=(True, {"score": "bad"})):
            d = tm_route.route_memory("Something.", "systems", "kimi")
            # score defaults to 50, which is inbox
            assert d.route == "inbox"
            assert d.score == 50

    def test_malformed_topic_falls_back(self):
        with patch("tm_core._call_deepseek_json", return_value=(True, {"score": 85, "topic_inferred": "nonexistent"})):
            d = tm_route.route_memory("Something.", "systems", "kimi")
            assert d.topic_inferred == "systems"
            assert d.route == "mem0"


if __name__ == "__main__":
    unittest.main()
