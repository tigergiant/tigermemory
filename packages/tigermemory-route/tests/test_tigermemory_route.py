"""Package-internal smoke tests for tigermemory_route.

These tests focus on package boundaries and pure-function behavior
(routing rules, dataclass methods, marker detection). End-to-end LLM
integration scenarios are covered by tests/test_tm_route.py through the
legacy shim entry point.
"""
from __future__ import annotations

import pathlib
import sys

_PKG_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

import tigermemory_route as route


def test_routedecision_as_metadata_default():
    decision = route.RouteDecision(
        route="mem0", score=85, topic_inferred="systems",
        issues=[], reasons="ok",
        is_transient=False, is_sensitive=False, needs_human_review=False,
    )
    meta = decision.as_metadata()
    assert meta == {
        "route_decision": "mem0",
        "route_score": 85,
        "route_topic_inferred": "systems",
    }


def test_routedecision_as_metadata_unreviewed_flag():
    decision = route.RouteDecision(
        route="inbox", score=0, topic_inferred="systems",
        issues=["api_unreachable"], reasons="api_unreachable",
        is_transient=False, is_sensitive=False, needs_human_review=False,
        unreviewed=True,
    )
    meta = decision.as_metadata()
    assert meta["unreviewed"] is True


def test_empty_text_is_discarded_without_llm_call():
    """Empty text short-circuits before any tigermemory_core call."""
    decision = route.route_memory("", "systems", "cascade")
    assert decision.route == "discard"
    assert decision.score == 0
    assert decision.reasons == "empty text"


def test_whitespace_only_text_is_discarded_without_llm_call():
    decision = route.route_memory("   \n\t  \n", "systems", "cascade")
    assert decision.route == "discard"


def test_daily_health_index_marker_detection_topic_gated():
    text = (
        "每日巡检总清单已更新；详见 wiki/operations/daily-health/2026-05-25.md"
    )
    assert route._is_daily_health_index_summary(text, "operations") is True
    assert route._is_daily_health_index_summary(text, "systems") is True
    # Non-operations / non-systems topic must reject even with markers.
    assert route._is_daily_health_index_summary(text, "brand") is False


def test_daily_health_index_marker_requires_both_markers():
    only_one_marker = "每日巡检总清单已更新，但没有链接"
    assert route._is_daily_health_index_summary(only_one_marker, "operations") is False


def test_curated_workflow_radar_summary_requires_three_marker_sets():
    text = (
        "今日结论：今天的雷达里 tigermemory 和 MCP 评测方法值得跟踪。"
        # Has summary (雷达) + local workflow (tigermemory, MCP) + durable (今日结论, 评测方法)
    )
    assert route._is_curated_workflow_radar_summary(text, "systems") is True


def test_curated_workflow_radar_summary_missing_one_marker_set():
    # Has summary + local workflow but no durable signal marker
    text = "今天看了 tigermemory MCP 的雷达内容"
    assert route._is_curated_workflow_radar_summary(text, "systems") is False


def test_curated_workflow_radar_topic_gating():
    text = (
        "今日结论：今天的雷达里 tigermemory 和 MCP 评测方法值得跟踪。"
    )
    assert route._is_curated_workflow_radar_summary(text, "brand") is False


def test_routing_rule_sensitive_overrides_score(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 95,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": True,
            "needs_human_review": False,
            "issues": ["contains phone number"],
            "reasons": "phone number detected",
        }
    monkeypatch.setattr(route.tm_core, "_call_deepseek_json", fake_call)
    decision = route.route_memory(
        "联系 13800000000 王经理", "systems", "cascade",
    )
    assert decision.route == "inbox"
    assert decision.is_sensitive is True


def test_routing_rule_person_topic_routes_to_inbox(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 95,
            "topic_inferred": "person",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": [],
            "reasons": "high quality person fact",
        }
    monkeypatch.setattr(route.tm_core, "_call_deepseek_json", fake_call)
    decision = route.route_memory(
        "虎哥偏好用 PowerShell 7", "person", "cascade",
    )
    assert decision.route == "inbox"


def test_routing_rule_high_score_routes_to_mem0(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 88,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": [],
            "reasons": "concrete commit + files + validation",
        }
    monkeypatch.setattr(route.tm_core, "_call_deepseek_json", fake_call)
    decision = route.route_memory(
        "Commit 52c7d74 修了 tests/test_tm_lessons.py 的 dedup 漏洞，pytest 819/819 pass。",
        "systems",
        "cascade",
    )
    assert decision.route == "mem0"
    assert "high score (88)" in decision.reasons


def test_routing_rule_low_score_routes_to_discard(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 15,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": ["empty content"],
            "reasons": "noise",
        }
    monkeypatch.setattr(route.tm_core, "_call_deepseek_json", fake_call)
    decision = route.route_memory("继续。", "systems", "cascade")
    assert decision.route == "discard"


def test_routing_rule_deepseek_unreachable_routes_to_inbox_unreviewed(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return False, "connection refused"
    monkeypatch.setattr(route.tm_core, "_call_deepseek_json", fake_call)
    decision = route.route_memory(
        "Some text here", "systems", "cascade",
    )
    assert decision.route == "inbox"
    assert decision.unreviewed is True
    assert "DeepSeek routing failed" in decision.issues[0]


def test_routing_rule_malformed_score_defaults_to_neutral(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": "high",  # invalid type
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": [],
            "reasons": "test",
        }
    monkeypatch.setattr(route.tm_core, "_call_deepseek_json", fake_call)
    decision = route.route_memory(
        "Some text", "systems", "cascade",
    )
    # Score defaults to 50 (neutral) → medium-score branch → inbox.
    assert decision.score == 50
    assert decision.route == "inbox"
