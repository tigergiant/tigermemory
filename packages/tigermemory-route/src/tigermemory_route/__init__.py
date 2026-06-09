"""tigermemory_route — memory routing decision engine (pure functions, no MCP).

Routes agent-submitted memories to:
  - mem0  (high quality, non-sensitive, non-transient)
  - inbox (medium quality, sensitive, person-topic, or needs human review)
  - discard (low quality or transient)

Depends on tigermemory_core._call_deepseek_json for LLM evaluation.

Inputs: 单条 memory dict (agent / topic / text 三段), 可选 metadata; tigermemory_core.AGENTS / TOPICS 枚举做合法性校验.
Outputs: dataclass `RouteDecision` (target ∈ {mem0, inbox, discard}, score, reason, confidence), 纯函数, 不写任何文件, 不打 Mem0/git.
Depends-on (must-have): tigermemory_core._call_deepseek_json (DeepSeek LLM 评分调用) + 本模块常量 DAILY_HEALTH_INDEX_MARKERS / RADAR_*_MARKERS 等 marker 列表; 不依赖文件系统.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import tigermemory_core as tm_core

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

KNOWLEDGE_TARGETS = {"mem0", "wiki_proposal", "human_review", "discard"}
WIKI_ACTIONS = {"create", "update"}

EVIDENCE_SOURCE_MARKERS = (
    "http://",
    "https://",
    "wiki/",
    "sources/",
    "tools/",
    "tests/",
    "scripts/",
    "D:\\",
    "C:\\",
    "/home/",
    "commit",
    "git_sha",
    "log",
    "report",
    "报告",
    "日志",
    "来源",
    "路径",
)

EVIDENCE_VALIDATION_MARKERS = (
    "pytest",
    "passed",
    "验证",
    "测试",
    "health",
    "返回码",
    "status",
    "截图",
    "confirmed",
    "已确认",
    "已验证",
    "待确认",
)

INVESTMENT_MARKERS = (
    "investment",
    "投研",
    "投资",
    "交易",
    "持仓",
    "账户",
    "qmt",
    "miniqmt",
    "b_qmt",
    "decision-log",
    "股票",
    "组合",
)

INVESTMENT_RUN_MARKERS = (
    "run_id",
    "order_id",
    "订单",
    "委托",
    "成交",
    "账户",
    "流水",
    "回测",
    "验证",
)

DATE_MARKER_RE = re.compile(r"\b20\d{2}-\d{2}(?:-\d{2})?\b")


ROUTE_PROMPT = """你是 tigermemory 的知识策展路由员。你的任务不是“能不能存”，而是把 agent 调用 write_memory
提交的一段内容放到最合适的一等知识目标：短期事件记忆、Wiki 提案、人工复核或丢弃。

核心原则：
- 不要懒惰地把普通中等置信内容扔给 human_review。只有真实不确定、冲突、敏感、权限边界、来源权威不足或会影响关键决策时才人工复核。
- 稳定规则、架构决策、runbook、长期边界说明、长篇投研/系统研究结论，应进入 wiki_proposal，而不是塞进 Mem0。
- Mem0 只存短、原子、近期但可复用的事实：偏好、交接卡事实、最近修复结论、短观察、具体失败/验证结果。
- discard 用于瞬态流水、空话、重复、低信息量、低分内容。
- 系统重试错误由宿主程序在调用失败时设置；正常内容分类不要输出 retry_error。

【评分维度】
1. 信噪比（0-50）：内容里实际信息量 vs 空话/套话/复读的比例
2. 具体性（0-50）：是否含数字、日期、人名、金额、URL、代码、文件路径、因果链等可验证钩子
3. 耐久性（0-100）：明天/下周/下个 agent 是否仍会复用
4. 规范性（0-100）：是否像规则、协议、架构边界、runbook、研究结论，可成为 canonical knowledge
5. 证据性（0-100）：是否给出文件、命令、commit、来源、验证结果或清晰因果
6. 作用域（0-100）：影响单次会话、单模块、跨 agent 工作流，还是长期系统/业务知识
7. 风险/复核必要性（0-100）：是否涉及隐私、投资/生产/权限、第三方判断、冲突事实或需人工授权

【敏感字段检测】
内容是否含手机号、身份证号、银行卡号、密码、私钥、家庭住址等个人隐私？
是 → is_sensitive=true，knowledge_target 必须是 human_review

【瞬态检测】
内容描述的是否是"今天正在做某事"、"当前配置中"、"临时状态"等次日即失效的信息？
是 → is_transient=true，通常 knowledge_target=discard，除非它是下面列出的持久索引/收尾例外

【巡检索引例外】
如果内容是“每日巡检总清单已更新”这类短摘要，并包含 `wiki/operations/daily-health/` 报告链接，
它记录的是持久 Wiki 索引已更新，不是完整巡检日志或心跳。不要仅因包含日期/当天结论就标为瞬态；
完整日报仍应放 Wiki，不进 Mem0。

【工作流雷达例外】
如果内容是 agent / GitHub / AI 理论雷达的“提炼后结论”，并且明确写出对 tigermemory、MCP、OpenClaw、
Hermes、DeerFlow、TradingAgents、agent runtime、浏览器边界、评测方法等本地工作流的长期影响，
它不是原始新闻流水，也不是普通当天心跳；不要仅因包含“今日/日报/GitHub 日榜”就标为瞬态。
原始榜单、链接堆砌、无本地工作流判断的新闻摘要仍可标为瞬态或低分。

【开发收尾记录例外】
开发收尾记录是后续 agent 复盘和接手所需的持久系统事实，不应被视为瞬态。
判定条件（必须同时满足三项）：
1. 含具体版本签名：commit hash、push、origin/master、远端 master、推到 等任一
2. 含具体文件路径：tools/、tests/、wiki/、scripts/ 子路径，或具体 .py/.md 文件名
3. 含具体验证证据：pytest passed/skipped、py_compile 通过、lint-page OK、命令实际输出 等
三项齐全 → is_transient=false，按 score 路由。
只含“本轮开发”“Goal 完成”“差不多了”但没有 commit、没有文件路径、没有验证结果的泛泛进度播报 → is_transient=true，照常按瞬态丢弃。

【目标选择】
- knowledge_target="mem0"：短、原子、近期但可复用；不需要成为 canonical 文档；通常 score>=70。
- knowledge_target="wiki_proposal"：稳定规则、架构/接口决策、runbook、边界说明、长期研究/投研结论、需要多人共享的系统知识。只提交提案，不直写 Wiki。
- knowledge_target="human_review"：敏感/person、事实冲突、权限/所有权不清、低证据但高影响、投资/生产高风险、第三方权威待核验。不要把普通 medium confidence 当成人工复核。
- knowledge_target="discard"：低信噪、瞬态、重复、无具体证据、只表达“正在做/准备做/可能”。

【证据链提示】
- 证据链很重要，但不是唯一门槛。长期有价值的信息不要仅因证据不完整就偷懒转 human_review 或 discard。
- 当内容适合 wiki_proposal 但缺来源、日期、验证结果、run_id/order_id、文件路径等证据时，保持正确 knowledge_target，并在 evidence_hints 里列出要补的证据。
- evidence_hints 是给写入 agent 的即时提醒，不是人工审核理由；每条不超过 80 字，最多 4 条。

【Wiki 提案字段】
当 knowledge_target="wiki_proposal" 时：
- wiki_partition 从 [brand, investment, operations, production, systems, person, self-evolution] 选一个；不能确定时优先用 topic_inferred 对应分区。
- wiki_slug_hint 给短横线小写英文 slug 建议；不确定可空。
- wiki_action 从 ["create", "update"] 选一个；新主题 create，修订既有规则 update。

【输出格式】严格 JSON：
{
  "score": <0-100 整数>,
  "topic_inferred": "从 [brand, investment, operations, production, systems, person, cross] 选一个",
  "is_transient": <bool>,
  "is_sensitive": <bool>,
  "needs_human_review": <bool, 内容有争议或边界不清时 true>,
  "knowledge_target": "mem0 | wiki_proposal | human_review | discard",
  "target_confidence": <0-100 整数>,
  "wiki_partition": "<wiki_proposal 时填写，否则可为空>",
  "wiki_slug_hint": "<wiki_proposal 时填写，否则可为空>",
  "wiki_action": "create | update | null",
  "review_reason": "<human_review 时说明必须人工复核的具体原因；否则可为空>",
  "evidence_hints": [<缺证据时填写；每条不超过80字；没有则空数组>],
  "score_breakdown": {
    "signal": <0-100>,
    "specificity": <0-100>,
    "durability": <0-100>,
    "canonicality": <0-100>,
    "evidence": <0-100>,
    "scope": <0-100>,
    "risk_review": <0-100>
  },
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
    knowledge_target: str | None = None
    target_confidence: int | None = None
    wiki_partition: str | None = None
    wiki_slug_hint: str | None = None
    wiki_action: str | None = None
    review_reason: str | None = None
    evidence_hints: list[str] | None = None
    score_breakdown: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        """Return metadata dict for mem0_write or inbox frontmatter."""
        meta: dict[str, Any] = {
            "route_decision": self.route,
            "route_score": self.score,
            "route_topic_inferred": self.topic_inferred,
        }
        if self.unreviewed:
            meta["unreviewed"] = True
        if self.knowledge_target:
            meta["knowledge_target"] = self.knowledge_target
        if self.target_confidence is not None:
            meta["target_confidence"] = self.target_confidence
        if self.wiki_partition:
            meta["wiki_partition"] = self.wiki_partition
        if self.wiki_slug_hint:
            meta["wiki_slug_hint"] = self.wiki_slug_hint
        if self.wiki_action:
            meta["wiki_action"] = self.wiki_action
        if self.review_reason:
            meta["review_reason"] = self.review_reason
        if self.needs_human_review:
            meta["needs_human_review"] = True
        if self.evidence_hints:
            meta["evidence_hints"] = self.evidence_hints
        if self.score_breakdown:
            meta["score_breakdown"] = self.score_breakdown
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


def _bounded_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(100, max(0, value))
    if isinstance(value, float):
        return min(100, max(0, int(round(value))))
    return default


def _clean_knowledge_target(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    target = value.strip()
    # retry_error is an infrastructure outcome set by host code only.
    if target in KNOWLEDGE_TARGETS:
        return target
    return None


def _clean_wiki_partition(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    partition = value.strip()
    if partition == "selfevolution":
        partition = "self-evolution"
    if partition in tm_core.PARTITION_OWNERS:
        return partition
    return None


def _clean_wiki_action(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    action = value.strip()
    if action in WIKI_ACTIONS:
        return action
    return None


def _clean_optional_text(value: Any, *, max_len: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:max_len]


def _clean_score_breakdown(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "signal",
        "specificity",
        "durability",
        "canonicality",
        "evidence",
        "scope",
        "risk_review",
    }
    cleaned: dict[str, Any] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or key not in allowed:
            continue
        score = _bounded_int(raw)
        if score is not None:
            cleaned[key] = score
    return cleaned or None


def _clean_evidence_hints(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            continue
        hint = " ".join(raw.strip().split())
        if not hint or hint in cleaned:
            continue
        cleaned.append(hint[:120])
        if len(cleaned) >= 4:
            break
    return cleaned


def _append_hint(hints: list[str], hint: str) -> None:
    if hint not in hints and len(hints) < 4:
        hints.append(hint)


def _is_investment_like(text: str, requested_topic: str, wiki_partition: str | None) -> bool:
    return (
        requested_topic == "investment"
        or wiki_partition == "investment"
        or _has_any_marker(text, INVESTMENT_MARKERS)
    )


def _build_evidence_hints(
    text: str,
    *,
    requested_topic: str,
    knowledge_target: str | None,
    wiki_partition: str | None,
    score_breakdown: dict[str, Any] | None,
    model_hints: list[str],
) -> list[str] | None:
    """Return non-blocking evidence prompts for write callers.

    Evidence hygiene should improve future writes without downgrading durable
    information into human review just because a source reference is missing.
    """
    hints = list(model_hints)
    if knowledge_target != "wiki_proposal":
        return hints or None

    evidence_score = None
    if isinstance(score_breakdown, dict):
        evidence_score = score_breakdown.get("evidence")
    has_source = _has_any_marker(text, EVIDENCE_SOURCE_MARKERS)
    has_validation = _has_any_marker(text, EVIDENCE_VALIDATION_MARKERS)

    if not has_source:
        _append_hint(
            hints,
            "evidence_hint: add source path, URL, log, report, or commit reference before final Wiki compile",
        )
    if not has_validation or (isinstance(evidence_score, int) and evidence_score < 70):
        _append_hint(
            hints,
            "evidence_hint: add validation result or mark unverified claims as pending",
        )
    if _is_investment_like(text, requested_topic, wiki_partition):
        if not DATE_MARKER_RE.search(text):
            _append_hint(
                hints,
                "evidence_hint: add investment decision date, run date, or target month",
            )
        if not _has_any_marker(text, INVESTMENT_RUN_MARKERS):
            _append_hint(
                hints,
                "evidence_hint: add symbol, account/order/run id, or research artifact reference when applicable",
            )
    return hints or None


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
    Route a memory text to the legacy physical route plus an optional knowledge target.

    The legacy route remains mem0/inbox/discard for caller compatibility.
    knowledge_target carries the richer target: mem0, wiki_proposal,
    human_review, discard, or host-only retry_error on infrastructure failure.

    Routing rules (first match wins):
      1. sensitive/person/person-wiki -> inbox + human_review
      2. daily/radar durable exceptions preserve non-transient storage
      3. is_transient == True         -> discard
      4. score < 30                   -> discard
      5. explicit knowledge_target    -> matching physical route
      6. 30 <= score < 70 or needs_human_review -> inbox
      7. score >= 70                  -> mem0
      8. DeepSeek unavailable         -> inbox + retry_error + unreviewed=true
    """
    if not text or not text.strip():
        return RouteDecision(
            route="discard", score=0, topic_inferred=topic,
            issues=["text is empty"], reasons="empty text",
            is_transient=False, is_sensitive=False, needs_human_review=False,
            knowledge_target="discard",
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
            knowledge_target="retry_error",
            review_reason="routing LLM unavailable",
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
    knowledge_target = _clean_knowledge_target(parsed.get("knowledge_target"))
    target_confidence = _bounded_int(parsed.get("target_confidence"))
    wiki_partition = _clean_wiki_partition(parsed.get("wiki_partition"))
    wiki_slug_hint = _clean_optional_text(parsed.get("wiki_slug_hint"), max_len=120)
    wiki_action = _clean_wiki_action(parsed.get("wiki_action"))
    review_reason = _clean_optional_text(parsed.get("review_reason"), max_len=240)
    score_breakdown = _clean_score_breakdown(parsed.get("score_breakdown"))
    evidence_hints = _build_evidence_hints(
        text,
        requested_topic=topic,
        knowledge_target=knowledge_target,
        wiki_partition=wiki_partition,
        score_breakdown=score_breakdown,
        model_hints=_clean_evidence_hints(parsed.get("evidence_hints")),
    )
    has_explicit_knowledge_target = knowledge_target is not None
    if needs_human_review and knowledge_target == "mem0":
        knowledge_target = "human_review"
        if not review_reason:
            review_reason = "needs_human_review true"

    issues = parsed.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    issues = [str(x) for x in issues][:5]

    reasons = str(parsed.get("reasons", "")).strip()
    if not reasons:
        reasons = "no reason given"

    extra_fields: dict[str, Any] = {
        "knowledge_target": knowledge_target,
        "target_confidence": target_confidence,
        "wiki_partition": wiki_partition,
        "wiki_slug_hint": wiki_slug_hint,
        "wiki_action": wiki_action,
        "review_reason": review_reason,
        "evidence_hints": evidence_hints,
        "score_breakdown": score_breakdown,
    }

    # Apply routing rules (ordered)
    if is_sensitive:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"sensitive content: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=True,
            **{**extra_fields, "knowledge_target": "human_review"},
        )

    if topic == "person" or topic_inferred == "person" or wiki_partition == "person":
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"person partition requires human review: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=True,
            **{**extra_fields, "knowledge_target": "human_review"},
        )

    if not has_explicit_knowledge_target and is_daily_health_index_summary and score >= 70 and not needs_human_review:
        return RouteDecision(
            route="mem0", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"daily health index summary: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **extra_fields,
        )

    if not has_explicit_knowledge_target and is_daily_health_index_summary and score >= 30 and not needs_human_review:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"daily health index summary needs review: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **extra_fields,
        )

    if not has_explicit_knowledge_target and is_curated_workflow_radar_summary and score >= 70 and not needs_human_review:
        return RouteDecision(
            route="mem0", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"curated workflow radar summary: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **extra_fields,
        )

    if not has_explicit_knowledge_target and is_curated_workflow_radar_summary and score >= 30:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues,
            reasons=f"curated workflow radar summary needs review: {reasons}",
            is_transient=False, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **extra_fields,
        )

    if is_transient:
        return RouteDecision(
            route="discard", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"transient content: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **{**extra_fields, "knowledge_target": "discard" if has_explicit_knowledge_target else knowledge_target},
        )

    if score < 30:
        return RouteDecision(
            route="discard", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"low score ({score}): {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **{**extra_fields, "knowledge_target": "discard" if has_explicit_knowledge_target else knowledge_target},
        )

    if has_explicit_knowledge_target:
        if knowledge_target in {"discard"}:
            return RouteDecision(
                route="discard", score=score, topic_inferred=topic_inferred,
                issues=issues, reasons=f"target discard: {reasons}",
                is_transient=is_transient, is_sensitive=is_sensitive,
                needs_human_review=needs_human_review,
                **extra_fields,
            )
        if knowledge_target == "human_review":
            return RouteDecision(
                route="inbox", score=score, topic_inferred=topic_inferred,
                issues=issues, reasons=f"target human_review: {reasons}",
                is_transient=is_transient, is_sensitive=is_sensitive,
                needs_human_review=True,
                **extra_fields,
            )
        if knowledge_target == "wiki_proposal":
            return RouteDecision(
                route="inbox", score=score, topic_inferred=topic_inferred,
                issues=issues, reasons=f"target wiki_proposal: {reasons}",
                is_transient=is_transient, is_sensitive=is_sensitive,
                needs_human_review=needs_human_review,
                **extra_fields,
            )
        if knowledge_target == "mem0":
            return RouteDecision(
                route="mem0", score=score, topic_inferred=topic_inferred,
                issues=issues, reasons=f"target mem0: {reasons}",
                is_transient=is_transient, is_sensitive=is_sensitive,
                needs_human_review=needs_human_review,
                **extra_fields,
            )

    if score < 70 or needs_human_review:
        return RouteDecision(
            route="inbox", score=score, topic_inferred=topic_inferred,
            issues=issues, reasons=f"medium score ({score}) or needs review: {reasons}",
            is_transient=is_transient, is_sensitive=is_sensitive,
            needs_human_review=needs_human_review,
            **extra_fields,
        )

    return RouteDecision(
        route="mem0", score=score, topic_inferred=topic_inferred,
        issues=issues, reasons=f"high score ({score}): {reasons}",
        is_transient=is_transient, is_sensitive=is_sensitive,
        needs_human_review=needs_human_review,
        **extra_fields,
    )
