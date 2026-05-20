from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_route  # type: ignore[import-not-found]


def test_route_memory_passes_requested_topic_and_taxonomy_context(monkeypatch):
    captured = {}

    def fake_call(prompt, content, **kwargs):
        captured["prompt"] = prompt
        captured["content"] = content
        captured["kwargs"] = kwargs
        return True, {
            "score": 85,
            "topic_inferred": "brand",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": [],
            "reasons": "brand guideline",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "IPFB 公众号 SVG 编辑器规范",
        "brand",
        "chatgpt",
        source_hint="openai-facade",
    )

    assert decision.route == "mem0"
    assert decision.topic_inferred == "brand"
    assert "requested_topic: brand" in captured["content"]
    assert "agent: chatgpt" in captured["content"]
    assert "source_hint: openai-facade" in captured["content"]
    assert "IPFB 公众号 SVG 编辑器规范" in captured["content"]
    assert "requested_topic 是调用方选择的业务分区" in captured["prompt"]
    assert "IPFB、品牌、文案、企划、商品、波段、公众号、微信图文、营销活动属于 brand" in captured["prompt"]
    assert "production 只用于 Doodiu ERP、生产、供应链、订单、采购、库存、工厂/生产系统" in captured["prompt"]
    assert "Memory Answer、MCP、HTTP、CLI、trace、eval、daily-health 工具" in captured["prompt"]


def test_curated_workflow_radar_summary_is_not_discarded_as_transient(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 86,
            "topic_inferred": "systems",
            "is_transient": True,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": ["contains today"],
            "reasons": "daily radar summary with local workflow guidance",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "今日结论：2026-05-20 这轮雷达的高信号不在新框架，而在 "
        "memory/writeback 契约、MCP 启动握手、agent runtime 浏览器边界和评测方法，"
        "都贴合 tigermemory / OpenClaw / Hermes / DeerFlow 本地工作流。",
        "systems",
        "codex",
    )

    assert decision.route == "mem0"
    assert decision.is_transient is False
    assert "curated workflow radar summary" in decision.reasons


def test_curated_workflow_radar_summary_needing_review_routes_to_inbox(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 62,
            "topic_inferred": "operations",
            "is_transient": True,
            "is_sensitive": False,
            "needs_human_review": True,
            "issues": ["classification unclear"],
            "reasons": "useful radar summary but needs review",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "今日结论：GitHub 日榜里和 tigermemory MCP、TradingAgents、评测方法相关的方向"
        "值得后续观察，但是否进入开发计划还需要人工确认。",
        "operations",
        "codex",
    )

    assert decision.route == "inbox"
    assert decision.is_transient is False


def test_raw_github_daily_list_can_still_be_discarded_as_transient(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 82,
            "topic_inferred": "systems",
            "is_transient": True,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": ["raw daily list"],
            "reasons": "today-only GitHub list without reusable local guidance",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "今天看了 GitHub 日榜，几个仓库比较火，明天再看看。",
        "systems",
        "codex",
    )

    assert decision.route == "discard"
