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
    # 2026-05-21: markers removed; closeout judgment is now LLM-only via prompt.
    assert "开发收尾记录例外" in captured["prompt"]
    assert "判定条件（必须同时满足三项）" in captured["prompt"]
    assert "含具体版本签名" in captured["prompt"]
    assert "含具体文件路径" in captured["prompt"]
    assert "含具体验证证据" in captured["prompt"]
    assert "知识策展路由员" in captured["prompt"]
    assert "knowledge_target" in captured["prompt"]
    assert "wiki_proposal" in captured["prompt"]
    assert "evidence_hints" in captured["prompt"]
    assert "长期有价值的信息不要仅因证据不完整就偷懒转 human_review 或 discard" in captured["prompt"]
    assert "human_review only" not in captured["prompt"]


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


def test_closeout_with_full_evidence_routes_to_mem0(monkeypatch):
    """After 2026-05-21 marker removal, closeout judgment is LLM-only via prompt.

    With three pieces of evidence (commit + files + validation) the prompt
    instructs the LLM to set is_transient=false. Score 70+ then routes to mem0
    via the standard high-score branch.
    """
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 88,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": [],
            "reasons": "implementation closeout with commit and validation",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "本轮开发已经完成并推到远端 master。实现范围：Retention dry-run audit 和 "
        "tm_agent_doctor。关键文件：tools/tm_retention_audit.py、tools/tm_agent_doctor.py、"
        "tests/test_tm_retention_audit.py。验证已跑：py -m pytest tests/test_tm_retention_audit.py "
        "tests/test_tm_agent_doctor.py -q，结果 6 passed, 2 skipped。Commit：b91d10d。"
        "Push：已推到 origin/master。",
        "systems",
        "codex",
    )

    assert decision.route == "mem0"
    assert decision.is_transient is False
    assert "high score (88)" in decision.reasons


def test_closeout_with_blocker_routes_to_inbox(monkeypatch):
    """Closeout with unresolved blocker → LLM should set needs_human_review=true,
    which routes to inbox via the medium-score branch even with is_transient=false.
    """
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 58,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": True,
            "issues": ["worktree blocker needs owner review"],
            "reasons": "closeout includes unresolved blocker",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "本轮开发完成。关键文件：tools/tm_route.py、tests/test_tm_route.py。"
        "验证已跑：pytest tests/test_tm_route.py -q，结果 passed。"
        "Commit：abc1234，Push：已推到 origin/master，但 WSL worktree 有 blocker。",
        "systems",
        "codex",
    )

    assert decision.route == "inbox"
    assert decision.is_transient is False
    assert "medium score (58) or needs review" in decision.reasons


def test_vague_progress_without_evidence_is_discarded_as_transient(monkeypatch):
    """Vague progress with no commit/files/validation → LLM should set
    is_transient=true → routes to discard. Prompt explicitly tells LLM that
    "本轮开发"/"Goal 完成" alone are not enough.
    """
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 80,
            "topic_inferred": "systems",
            "is_transient": True,
            "is_sensitive": False,
            "needs_human_review": False,
            "issues": ["progress only"],
            "reasons": "no commit, files, or validation",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "今天继续开发 agent doctor，感觉差不多完成了，后面再看。",
        "systems",
        "codex",
    )

    assert decision.route == "discard"
    assert decision.is_transient is True


def test_wiki_proposal_target_routes_to_inbox_with_metadata(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 86,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "knowledge_target": "wiki_proposal",
            "target_confidence": 91,
            "wiki_partition": "systems",
            "wiki_slug_hint": "unified-knowledge-routing",
            "wiki_action": "create",
            "review_reason": "",
            "score_breakdown": {
                "signal": 90,
                "specificity": 80,
                "durability": 95,
                "canonicality": 92,
                "evidence": 78,
                "scope": 88,
                "risk_review": 20,
            },
            "issues": [],
            "reasons": "stable routing contract",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "write_memory 统一路由契约成为长期系统规则，应沉淀为 Wiki 提案。",
        "systems",
        "codex",
    )

    assert decision.route == "inbox"
    assert decision.knowledge_target == "wiki_proposal"
    assert decision.target_confidence == 91
    assert decision.wiki_partition == "systems"
    assert decision.wiki_slug_hint == "unified-knowledge-routing"
    assert decision.wiki_action == "create"
    assert decision.score_breakdown["canonicality"] == 92


def test_wiki_proposal_missing_investment_evidence_returns_hints(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 84,
            "topic_inferred": "investment",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "knowledge_target": "wiki_proposal",
            "target_confidence": 80,
            "wiki_partition": "investment",
            "wiki_slug_hint": "b-qmt-node-rule",
            "wiki_action": "create",
            "review_reason": "",
            "evidence_hints": ["evidence_hint: add broker screenshot"],
            "score_breakdown": {
                "signal": 88,
                "specificity": 62,
                "durability": 90,
                "canonicality": 84,
                "evidence": 35,
                "scope": 82,
                "risk_review": 45,
            },
            "issues": ["missing source"],
            "reasons": "durable investment system rule with weak evidence",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "B_qmt 自动交易节点的价格类型配置应写入投研 Wiki。",
        "investment",
        "codex",
    )

    assert decision.route == "inbox"
    assert decision.knowledge_target == "wiki_proposal"
    assert decision.evidence_hints is not None
    assert "evidence_hint: add broker screenshot" in decision.evidence_hints
    assert any("source path" in hint for hint in decision.evidence_hints)
    assert any("validation result" in hint for hint in decision.evidence_hints)
    assert any("decision date" in hint for hint in decision.evidence_hints)


def test_daily_health_explicit_wiki_target_stays_inbox_not_mem0(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 86,
            "topic_inferred": "operations",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "knowledge_target": "wiki_proposal",
            "target_confidence": 91,
            "wiki_partition": "operations",
            "wiki_slug_hint": "daily-health-routing",
            "wiki_action": "update",
            "issues": [],
            "reasons": "daily health routing should update wiki summary",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "每日巡检总清单已更新；详见 wiki/operations/daily-health/2026-06-07.md",
        "operations",
        "codex",
    )

    assert decision.route == "inbox"
    assert decision.knowledge_target == "wiki_proposal"
    assert decision.wiki_partition == "operations"


def test_explicit_mem0_target_can_accept_medium_score_without_lazy_inbox(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 62,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "knowledge_target": "mem0",
            "target_confidence": 76,
            "issues": [],
            "reasons": "atomic reusable handoff fact",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory("2026-06-07 short reusable handoff fact.", "systems", "codex")

    assert decision.route == "mem0"
    assert decision.knowledge_target == "mem0"
    assert "target mem0" in decision.reasons


def test_sensitive_content_forces_human_review_target(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 95,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": True,
            "needs_human_review": False,
            "knowledge_target": "mem0",
            "target_confidence": 80,
            "issues": ["contains phone"],
            "reasons": "specific but sensitive",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory("联系 13800138000", "systems", "codex")

    assert decision.route == "inbox"
    assert decision.knowledge_target == "human_review"


def test_model_emitted_retry_error_is_ignored_as_content_target(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 88,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "knowledge_target": "retry_error",
            "target_confidence": 99,
            "issues": [],
            "reasons": "model incorrectly tried to classify normal content as retry",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory(
        "2026-06-07 routed memory contract keeps retry_error host-only.",
        "systems",
        "codex",
    )

    assert decision.route == "mem0"
    assert decision.knowledge_target is None
    assert "high score (88)" in decision.reasons


def test_person_wiki_partition_forces_human_review_target(monkeypatch):
    def fake_call(prompt, content, **kwargs):
        return True, {
            "score": 91,
            "topic_inferred": "systems",
            "is_transient": False,
            "is_sensitive": False,
            "needs_human_review": False,
            "knowledge_target": "wiki_proposal",
            "target_confidence": 90,
            "wiki_partition": "person",
            "wiki_slug_hint": "person-preference",
            "wiki_action": "create",
            "issues": [],
            "reasons": "person-like stable fact",
        }

    monkeypatch.setattr(tm_route.tm_core, "_call_deepseek_json", fake_call)

    decision = tm_route.route_memory("虎哥的个人偏好应由人工确认。", "systems", "codex")

    assert decision.route == "inbox"
    assert decision.knowledge_target == "human_review"
    assert decision.wiki_partition == "person"
    assert "person partition requires human review" in decision.reasons
