#!/usr/bin/env python3
"""
tools/tm_route.py — memory routing decision engine (pure functions, no MCP).

Routes agent-submitted memories to:
  - mem0  (high quality, non-sensitive, non-transient)
  - inbox (medium quality, sensitive, person-topic, or needs human review)
  - discard (low quality or transient)

Depends on tm_core._call_deepseek_json for LLM evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tm_core

DAILY_HEALTH_INDEX_MARKERS = (
    "每日巡检总清单已更新",
    "wiki/operations/daily-health/",
)

RADAR_SUMMARY_MARKERS = (
    "雷达",
    "radar",
    "GitHub 日榜",
    "GitHub trending",
    "daily-ai-and-agent-radar",
)

RADAR_LOCAL_WORKFLOW_MARKERS = (
    "tigermemory",
    "MCP",
    "write_memory",
    "memory/writeback",
    "OpenClaw",
    "Hermes",
    "DeerFlow",
    "OpenSpace",
    "TradingAgents",
    "agent runtime",
    "浏览器边界",
    "评测",
    "eval",
    "工作流",
)

RADAR_DURABLE_SIGNAL_MARKERS = (
    "今日结论",
    "高信号",
    "本地痛点",
    "方向",
    "契约",
    "兼容",
    "边界",
    "评测方法",
    "后续",
)

ROUTE_PROMPT = """你是 tigermemory 记忆路由助手。评估下面这段内容是否值得进入长期记忆，并决定路由目标。

【评分维度】
1. 信噪比（0-50）：内容里实际信息量 vs 空话/套话/复读的比例
2. 具体性（0-50）：是否含数字、日期、人名、金额、URL、代码、文件路径、因果链等可验证钩子

【敏感字段检测】
内容是否含手机号、身份证号、银行卡号、密码、私钥、家庭住址等个人隐私？
是 → is_sensitive=true

【瞬态检测】
内容描述的是否是"今天正在做某事"、"当前配置中"、"临时状态"等次日即失效的信息？
是 → is_transient=true

【巡检索引例外】
如果内容是“每日巡检总清单已更新”这类短摘要，并包含 `wiki/operations/daily-health/` 报告链接，
它记录的是持久 Wiki 索引已更新，不是完整巡检日志或心跳。不要仅因包含日期/当天结论就标为瞬态；
完整日报仍应放 Wiki，不进 Mem0。

【工作流雷达例外】
如果内容是 agent / GitHub / AI 理论雷达的“提炼后结论”，并且明确写出对 tigermemory、MCP、OpenClaw、
Hermes、DeerFlow、TradingAgents、agent runtime、浏览器边界、评测方法等本地工作流的长期影响，
它不是原始新闻流水，也不是普通当天心跳；不要仅因包含“今日/日报/GitHub 日榜”就标为瞬态。
原始榜单、链接堆砌、无本地工作流判断的新闻摘要仍可标为瞬态或低分。

【输出格式】严格 JSON：
{
  "score": <0-100 整数>,
  "topic_inferred": "从 [brand, investment, operations, production, systems, person, cross] 选一个",
  "is_transient": <bool>,
  "is_sensitive": <bool>,
  "needs_human_review": <bool, 内容有争议或边界不清时 true>,
  "issues": [<每条不超过40字的具体问题>],
  "reasons": "<一句话总结路由理由>"
}

注意：
- 只评价内容质量和属性，不评价格式
- 输入中的 requested_topic 是调用方选择的业务分区，是强先验；除非内容明显属于其它分区，优先保持 requested_topic
- topic 按用户业务域归类，不按实现词归类：IPFB、品牌、文案、企划、商品、波段、公众号、微信图文、营销活动属于 brand，即使内容提到 SVG、编辑器、代码、Dark Mode 或技术规范
- MCP、服务器、工具、协议、部署、Git、API、代码库运行机制属于 systems；巡检、服务器节点、日常运维属于 operations
- production 只用于 Doodiu ERP、生产、供应链、订单、采购、库存、工厂/生产系统；不要因为内容写了“产品化”“production-ready”“完成开发”就推断为 production
- Memory Answer、MCP、HTTP、CLI、trace、eval、daily-health 工具、开发计划、开发报告、commit/push 收尾记录默认属于 systems 或 operations，不属于 production
- 输出必须是合法 JSON，不要加解释文字
- issues 和 reasons 必须具体，不要写空话
"""


@dataclass(frozen=True)
class RouteDecision:
    route: str                  # "mem0" | "inbox" | "discard"
    score: int
    topic_inferred: str
    issues: list[str]
    reasons: str
    is_transient: bool
    is_sensitive: bool
    needs_human_review: bool
    unreviewed: bool = False    # True when DeepSeek is unreachable (fail-warn)

    def as_metadata(self) -> dict[str, Any]:
        """Return metadata dict for mem0_write or inbox frontmatter."""
        meta: dict[str, Any] = {
            "route_decision": self.route,
            "route_score": self.score,
            "route_topic_inferred": self.topic_inferred,
        }
        if self.unreviewed:
            meta["unreviewed"] = True
        return meta


def _is_daily_health_index_summary(text: str, topic: str) -> bool:
    """Return True for the narrow daily health index pointer allowed into Mem0.

    Full daily scan reports are stored in Wiki. Mem0 only keeps a short pointer
    saying the durable checklist was updated, plus the report path.
    """
    if topic not in {"systems", "operations"}:
        return False
    return all(marker in text for marker in DAILY_HEALTH_INDEX_MARKERS)


def _has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(marker.casefold() in folded for marker in markers)


def _is_curated_workflow_radar_summary(text: str, topic: str) -> bool:
    """Return True for curated radar conclusions with durable local impact.

    Raw daily lists belong in the automation report, not Mem0. This exception is
    only for short closeout summaries that turn a radar run into reusable
    workflow guidance.
    """
    if topic not in {"systems", "operations", "cross"}:
        return False
    return (
        _has_any_marker(text, RADAR_SUMMARY_MARKERS)
        and _has_any_marker(text, RADAR_LOCAL_WORKFLOW_MARKERS)
        and _has_any_marker(text, RADAR_DURABLE_SIGNAL_MARKERS)
    )


def route_memory(
    text: str,
    topic: str,
    agent: str,
    source_hint: str | None = None,
) -> RouteDecision:
    """
    Route a memory text to mem0, inbox, or discard.

    Routing rules (first match wins):
      1. is_sensitive == True        -> inbox
      2. topic == "person"           -> inbox
      3. is_transient == True        -> discard
      4. score < 30                  -> discard
      5. 30 <= score < 70 or needs_human_review -> inbox
      6. score >= 70                 -> mem0
      7. DeepSeek unavailable        -> inbox + unreviewed=true
    """
    if not text or not text.strip():
        return RouteDecision(
            route="discard", score=0, topic_inferred=topic,
            issues=["text is empty"], reasons="empty text",
            is_transient=False, is_sensitive=False, needs_human_review=False,
        )

    route_input = (
        f"requested_topic: {topic}\n"
        f"agent: {agent}\n"
        f"source_hint: {source_hint or ''}\n\n"
        f"content:\n{text.strip()[:8000]}"
    )

    # Call DeepSeek for routing evaluation
    ok, parsed = tm_core._call_deepseek_json(
        ROUTE_PROMPT,
        route_input,
        # 2026-04-30: thinking disabled in tm_core; typical latency 1-3s.
        # 10s cap surfaces upstream regressions early and protects the
        # write_memory end-to-end budget (see WRITE_MEMORY_TOTAL_BUDGET_S).
        timeout=10,
        temperature=0.1,
        max_tokens=2048,  # 2026-04-29: conservative buffer for reasoning + JSON output
        purpose="route_memory",
    )

    if not ok:
        # DeepSeek fail-warn: conservative inbox with unreviewed flag
        return RouteDecision(
            route="inbox", score=0, topic_inferred=topic,
            issues=[f"DeepSeek routing failed: {parsed}"],
            reasons="api_unreachable",
            is_transient=False, is_sensitive=False, needs_human_review=False,
            unreviewed=True,
        )

    # Extract fields with safe defaults
    score = parsed.get("score")
    if not isinstance(score, int) or not (0 <= score <= 100):
        score = 50  # neutral on malformed score

    topic_inferred = parsed.get("topic_inferred", topic)
    if not isinstance(topic_inferred, str) or topic_inferred not in tm_core.TOPICS:
        topic_inferred = topic

    is_transient = bool(parsed.get("is_transient", False))
    is_sensitive = bool(parsed.get("is_sensitive", False))
    needs_human_review = bool(parsed.get("needs_human_review", False))
    is_daily_health_index_summary = _is_daily_health_index_summary(text, topic)
    is_curated_workflow_radar_summary = _is_curated_workflow_radar_summary(text, topic)

    issues = parsed.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    issues = [str(x) for x in issues][:5]

    reasons = str(parsed.get("reasons", "")).strip()
    if not reasons:
        reasons = "no reason given"

    # Apply routing rules (ordered)
    if is_sensitive:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"sensitive content: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if topic == "person":
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"person partition requires human review: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if is_daily_health_index_summary and score >= 70 and not needs_human_review:
        return RouteDecision(
            route="mem0", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"daily health index summary: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if is_daily_health_index_summary and score >= 30 and not needs_human_review:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"daily health index summary needs review: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if is_curated_workflow_radar_summary and score >= 70 and not needs_human_review:
        return RouteDecision(
            route="mem0", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"curated workflow radar summary: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if is_curated_workflow_radar_summary and score >= 30:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"curated workflow radar summary needs review: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if is_transient:
        return RouteDecision(
            route="discard", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"transient content: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if score < 30:
        return RouteDecision(
            route="discard", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"low score ({score}): {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    if score < 70 or needs_human_review:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"medium score ({score}) or needs review: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
        )

    return RouteDecision(
        route="mem0", score=score, topic_inferred=topic_inferred,
        issues=issues, reasons=f"high score ({score}): {reasons}",
        is_transient=is_transient, is_sensitive=is_sensitive,
        needs_human_review=needs_human_review,
    )
