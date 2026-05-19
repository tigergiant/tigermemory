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
