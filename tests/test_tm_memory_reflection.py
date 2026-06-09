from __future__ import annotations

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_reflection  # type: ignore[import-not-found]


def _write_inbox(path: pathlib.Path, body: str, summary_cn: str | None = None) -> None:
    summary_line = f"summary_cn: {summary_cn}" if summary_cn else None
    text = "\n".join([
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-01",
        "topic: systems",
        *(line for line in [summary_line] if line),
        "---",
        "",
        body,
        "",
    ])
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _write_raw_inbox(path: pathlib.Path, body: str, fm_lines: list[str] | None = None) -> None:
    lines = [
        "---",
        "owner: codex",
        "status: active",
        "updated: 2026-05-01",
        "topic: systems",
        *(fm_lines or []),
        "---",
        "",
        body,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _strip_details(text: str) -> list[str]:
    visible: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip() == "<details>":
            inside = True
            continue
        if line.strip() == "</details>":
            inside = False
            continue
        if not inside:
            visible.append(line)
    return visible


def test_daily_digest_decision_block_and_frontmatter_counts(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", "stale archive me")
    pdir = tmp_path / "cron-proposals" / "2026-05-15" / "proposal-2026-05-15-001"
    pdir.mkdir(parents=True)
    (pdir / "proposal.json").write_text(
        json.dumps({"type": "wiki-doc", "trigger": "fixture", "impact": "wiki/operations/x.md"}),
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "stale_archive_count: 1" in report
    assert "promote_candidate_count: 0" in report
    assert "mem0_audit_candidate_count: 0" in report
    assert "wiki_proposal_inbox_count: 0" in report
    frontmatter_end = report.splitlines().index("---", 1)
    first_30 = "\n".join(report.splitlines()[frontmatter_end + 1 : frontmatter_end + 31])
    assert "## ⚡ 今日要决策" in first_30
    assert "🔴 14 天兜底 archive 候选：1 条" in first_30
    assert "🟡 promote_to_mem0 / promote_to_wiki 候选：0 条" in first_30
    assert "🔵 Proposed Changes：1 条" in first_30
    assert "🧾 Inbox Wiki Proposal 台账：0 条 / 0 组" in first_30
    assert "🟢 Mem0 重复 / 误判候选：0 条" in first_30
    assert "⚪ discard 误判候选：0 条" in first_30
    assert "## 🧩 今日沉淀卡" in report
    assert "有 1 个 Proposed Change 需要裁决" in report
    assert "有 1 条 inbox 达到 14 天兜底" in report
    assert "建议行动：优先裁决 Proposed Changes。" in report


def test_daily_digest_renders_mem0_audit_candidates(tmp_path):
    audit_dir = tmp_path / "mem0-audit" / "2026-05-15"
    audit_dir.mkdir(parents=True)
    (audit_dir / "dedup_candidates.json").write_text(
        json.dumps([
            {
                "candidate_id": "mem-old",
                "canonical_id": "mem-new",
                "agent": "cascade",
                "topic": "systems",
                "created_at": "2026-05-15T08:00:00+08:00",
                "signature_distance": 3,
                "preview": "重复的 Cascade closeout 摘要",
                "reason": "signature_cluster_distance=3; canonical=mem-new",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=tmp_path / "inbox",
        audit_root=tmp_path / "discard-root",
        mem0_audit_root=tmp_path / "mem0-audit",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "mem0_audit_candidate_count: 1" in report
    assert "## 🟢 Mem0 重复 / 误判候选" in report
    assert "### 🟢 重复候选 (dedup)" in report
    assert "`mem-old` :: agent=cascade topic=systems dist=3" in report
    assert "canonical: `mem-new`" in report
    assert "虎哥裁决：[ ] confirm  [ ] reject" in report


def test_daily_digest_groups_inbox_actions_and_wraps_keep_rows(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(
        inbox / "2026-05-01-1200-codex-systems.md",
        "old",
        summary_cn="这是一条测试用的中文摘要。",
    )
    _write_inbox(inbox / "2026-05-14-1200-codex-systems.md", "new")

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "### 🔴 建议 archive" in report
    assert "### 🟡 建议 promote" in report
    assert "### ⚪ 仅观察 keep_in_inbox" in report
    assert "中文标题：这是一条测试用的中文摘要。" in report
    assert "中文预览：这是一条测试用的中文摘要。" in report
    assert "原文预览：old" in report
    assert "Codex 推荐操作：归档" in report
    assert "Codex 推荐理由：已停留 14 天且没有 apply 记录" in report
    assert "<summary>展开 1 条 keep_in_inbox</summary>" in report
    assert "### 💤 自动折叠：旧交接 / 自动流水" in report
    assert "<summary>展开 0 条低优先级历史项</summary>" in report
    assert "2026-05-01-1200-codex-systems.md` **高亮：14 天兜底 archive**" in report


def test_daily_digest_folds_legacy_session_handoff_out_of_keep_queue(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_raw_inbox(
        inbox / "2026-05-14-1200-codex-systems.md",
        "\n".join([
            "# Routed memory 85",
            "---",
            "memory_type: session-handoff",
            "session_id: codex-20260514-1200",
            "---",
            "## Task",
            "完成系统 cron 承接收尾，包含 commit 与测试证据。",
        ]),
        fm_lines=["summary_cn: session-handoff"],
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "low_priority_inbox_count: 1" in report
    assert "<summary>展开 0 条 keep_in_inbox</summary>" in report
    assert "<summary>展开 1 条低优先级历史项</summary>" in report
    assert "Codex 推荐操作：旧交接卡" in report
    assert "路由标记：legacy_session_handoff" in report


def test_daily_digest_groups_inbox_wiki_proposals_into_ledger(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_raw_inbox(
        inbox / "2026-05-14-1200-codex-systems.md",
        "# Wiki proposal 88\n目标是更新 cron-intake 窗口规则。",
        fm_lines=[
            "summary_cn: cron-intake 窗口规则提案",
            "knowledge_target: wiki_proposal",
            "proposal_kind: wiki",
            "wiki_partition: systems",
            "wiki_slug_hint: cron-intake-window-rules",
        ],
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "wiki_proposal_inbox_count: 1" in report
    assert "🧾 Inbox Wiki Proposal 台账：1 条 / 1 组" in report
    assert "## 🧾 Inbox Wiki Proposal 台账" in report
    assert "<summary>展开 1 个 wiki proposal 目标页</summary>" in report
    assert "`wiki/systems/cron-intake-window-rules.md`" in report
    assert "2026-05-14-1200-codex-systems.md" in report
    assert "<summary>展开 0 条 keep_in_inbox</summary>" in report


def test_cron_intake_surfaces_inbox_wiki_proposal_action(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-memory-digest-2026-05-15.md").write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: active",
            "updated: 2026-05-15",
            "mem0_count: 0",
            "inbox_count: 1",
            "discard_count: 0",
            "proposal_count: 0",
            "applied_count: 0",
            "stale_archive_count: 0",
            "promote_candidate_count: 0",
            "wiki_proposal_inbox_count: 3",
            "mem0_audit_candidate_count: 0",
            "self_evolution_count: 0",
            "---",
            "",
            "# Memory Digest 2026-05-15",
            "",
            "## ⚡ 今日要决策",
            "",
            "- 🧾 Inbox Wiki Proposal 台账：3 条 / 2 组 → 见下方 §Inbox Wiki Proposal 台账",
            "",
            "## 🧩 今日沉淀卡",
            "",
            "- 结论：有 inbox wiki proposal 需要归并。",
        ]),
        encoding="utf-8",
    )

    result = tm_memory_reflection.build_cron_intake(
        date="2026-05-15",
        window="memory-digest",
        operations_dir=operations,
    )

    assert result["reports"][0]["counts"]["wiki_proposal_inbox_count"] == 3
    assert "归并或转交 3 个 inbox/wiki_proposal 候选" in result["action_items"]


def test_legacy_inbox_extracts_existing_chinese_line(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", "# Routed memory 80\n这条历史 inbox 已经自带中文说明。")

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "中文标题：这条历史 inbox 已经自带中文说明。" in report


def test_codex_route_recommendation_investment_longform_prefers_wiki(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(
        inbox / "2026-05-15-1200-codex-investment.md",
        "新疆天业研究纪要：新能源企业研究，标的代码 600338，证券代码 600338。该研究为投研长文，给出长期结论与风险提示。",
        summary_cn="新疆天业研究纪要：新能源企业研究",
    )

    rows = tm_memory_reflection.audit_inbox(date="2026-05-15", inbox_dir=inbox, proposal_root=tmp_path / "cron-proposals")
    row = rows[0]

    assert row.route_target == "wiki"
    assert row.route_label == "写入 Wiki"
    assert row.route_confidence >= 80
    assert "investment_longform" in row.route_reason
    assert row.route_hard_rule is True
    assert row.codex_recommended_action == "写入 Wiki"


def test_codex_route_recommendation_auto_generated_decision_log_is_hidden_class(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_raw_inbox(
        inbox / "2026-05-15-1200-codex-investment.md",
        "# Wiki proposal\nGenerated by `tools/tm_adapter.py --batch --portfolio` for wiki/investment/decision-log/portfolio-fast-scan-2026-05-15.md",
        fm_lines=[
            "topic: investment",
            "summary_cn: TradingAgents 自动投研流水",
            "knowledge_target: wiki_proposal",
            "proposal_kind: wiki",
            "wiki_partition: investment",
            "wiki_slug_hint: decision-log-portfolio-fast-scan",
        ],
    )

    rows = tm_memory_reflection.audit_inbox(date="2026-05-15", inbox_dir=inbox, proposal_root=tmp_path / "cron-proposals")
    row = rows[0]

    assert row.route_target == "wiki"
    assert row.route_label == "自动投研流水"
    assert "auto-generated-investment-log" in row.route_flags
    assert row.route_hard_rule is True


def test_investment_wiki_proposal_ledger_adds_triage_metadata():
    row = tm_memory_reflection.InboxAuditRow(
        path="inbox/2026-06-09-1200-codex-investment.md",
        created_date="2026-06-09",
        age_days=0,
        agent="codex",
        topic="investment",
        title_cn="贵州茅台正式报告",
        preview_cn=(
            r"贵州茅台 600519.SH DeerFlow 正式报告 PDF，原始路径 "
            r"C:\Users\Giant\Documents\New project\reports\maotai-2026-06.pdf"
        ),
        summary_cn="贵州茅台正式报告",
        summary="长期研究结论与风险提示，不含交易执行数据。",
        action="keep_in_inbox",
        reason="wiki proposal ledger",
        codex_recommended_action="写入 Wiki",
        codex_recommended_reason="长期研究候选",
        route_target="wiki",
        route_label="写入 Wiki",
        route_confidence=90,
        knowledge_target="wiki_proposal",
        proposal_kind="wiki",
        wiki_partition="investment",
        wiki_slug_hint="research/600519",
        route_score=88,
        l2_review_score=84,
        target_confidence=90,
        wiki_action="update",
    )

    ledger = tm_memory_reflection.inbox_wiki_proposal_ledger([row])

    assert len(ledger) == 1
    item = ledger[0]
    assert item["status"] == "investment-wiki"
    assert item["review_label"] == "可写入投研 Wiki"
    triage = item["investment_triage"]
    assert triage["investment_doc_type"] == "report"
    assert triage["investment_target_path"] == "wiki/investment/research/600519.SH.md"
    assert triage["investment_review_level"] == "wiki_candidate"
    assert triage["preserve_original"] is True
    assert triage["copy_only"] is True
    assert triage["symbol"] == "600519.SH"
    assert triage["original_paths"] == [r"C:\Users\Giant\Documents\New project\reports\maotai-2026-06.pdf"]


def test_investment_wiki_proposal_triage_keeps_qmt_workflow_out_of_trade_log():
    row = tm_memory_reflection.InboxAuditRow(
        path="inbox/2026-06-09-1200-codex-investment.md",
        created_date="2026-06-09",
        age_days=0,
        agent="codex",
        topic="investment",
        title_cn="MiniQMT 集成状态",
        preview_cn="miniqmt 数据源和投研工作流接入状态，仅描述系统能力边界。",
        summary_cn="MiniQMT 集成状态",
        summary="投研系统规则更新。",
        action="keep_in_inbox",
        reason="wiki proposal ledger",
        codex_recommended_action="写入 Wiki",
        codex_recommended_reason="长期规则候选",
        knowledge_target="wiki_proposal",
        proposal_kind="wiki",
        wiki_partition="investment",
        wiki_slug_hint="miniqmt-integration-status",
        route_score=86,
        l2_review_score=82,
        target_confidence=90,
        wiki_action="update",
    )

    item = tm_memory_reflection.inbox_wiki_proposal_ledger([row])[0]
    triage = item["investment_triage"]

    assert item["status"] == "investment-wiki"
    assert triage["investment_doc_type"] == "workflow"
    assert triage["investment_target_path"] == "wiki/investment/miniqmt-integration-status.md"
    assert triage["investment_review_level"] == "wiki_candidate"


def test_codex_route_recommendation_alerts_to_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_inbox(
        inbox / "2026-05-15-1200-codex-systems.md",
        "QMT 告警：connect failed，前置条件未满足，已暂时跳过该告警未恢复，未发通知。",
        summary_cn="QMT 告警",
    )

    rows = tm_memory_reflection.audit_inbox(date="2026-05-15", inbox_dir=inbox, proposal_root=tmp_path / "cron-proposals")
    row = rows[0]

    assert row.route_target == "inbox"
    assert row.route_label == "转人工 inbox"
    assert row.route_confidence >= 88
    assert row.route_hard_rule is True
    assert row.route_reason.startswith("涉及故障/告警")


def test_codex_route_recommendation_benign_handoff_skip_phrases_stays_mem0(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_raw_inbox(
        inbox / "2026-05-15-1200-codex-systems.md",
        "\n".join([
            "# Routed memory 65",
            "## Task",
            "完成 session-handoff：跳过无关文件，未创建临时文件，commit 与测试通过。",
            "## Evidence Refs",
            "pytest passed",
        ]),
        fm_lines=["title: session-handoff", "summary_cn: session-handoff"],
    )

    rows = tm_memory_reflection.audit_inbox(date="2026-05-15", inbox_dir=inbox, proposal_root=tmp_path / "cron-proposals")
    row = rows[0]

    assert row.route_target == "mem0"
    assert row.route_label == "旧交接卡"
    assert row.route_hard_rule is True
    assert "legacy_session_handoff" in row.route_flags
    assert "needs_manual_inbox" not in row.route_flags


def test_codex_route_recommendation_low_score_capture_prefers_discard(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_raw_inbox(
        inbox / "2026-05-15-1200-codex-systems.md",
        "## Routed memory 0\nopenclaw-turn-capture-low-score raw capture turn capture.",
        fm_lines=["summary_cn: Routed memory 0/low-score capture"],
    )

    rows = tm_memory_reflection.audit_inbox(date="2026-05-15", inbox_dir=inbox, proposal_root=tmp_path / "cron-proposals")
    row = rows[0]

    assert row.route_target == "discard"
    assert row.route_label == "归档"
    assert "low-quality-capture" in row.route_flags
    assert row.route_hard_rule is True
    assert "低分/raw" in row.codex_recommended_reason


def test_codex_route_recommendation_extracts_session_handoff_task_title(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_raw_inbox(
        inbox / "2026-05-15-1200-codex-systems.md",
        "\n".join([
            "# Routed memory 65",
            "## Task",
            "请处理 session-handoff，包含会话收尾与 commit 记录。",
            "## 详情",
            "原始内容略。"
        ]),
        fm_lines=["title: session-handoff", "summary_cn: session-handoff"],
    )

    rows = tm_memory_reflection.audit_inbox(date="2026-05-15", inbox_dir=inbox, proposal_root=tmp_path / "cron-proposals")
    row = rows[0]

    assert row.title_cn.startswith("请处理 session-handoff")
    assert row.route_target == "mem0"
    assert row.codex_recommended_action == "旧交接卡"
    assert "legacy_session_handoff" in row.route_flags


def test_inbox_audit_replaces_generic_title_frontmatter(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "2026-05-01-1200-codex-systems.md").write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-05-01",
            "title_cn: 标题",
            "preview_cn: 标题 中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入",
            "summary_cn: 标题",
            "routed_by: tigermemory",
            "---",
            "",
            "# Routed memory 35",
            "",
            "# 标题",
            "中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入",
            "",
            "# 摘要",
            "该文档是中转 API 配置说明，主打 Claude Opus 4.5 保真满血版。",
            "",
        ]),
        encoding="utf-8",
    )

    rows = tm_memory_reflection.audit_inbox(
        date="2026-05-15",
        inbox_dir=inbox,
        proposal_root=tmp_path / "cron-proposals",
    )

    assert rows[0].title_cn.startswith("中转API配置说明")
    assert rows[0].title_cn != "标题"
    assert rows[0].preview_cn.startswith("该文档是中转 API 配置说明")


def test_enrich_inbox_rewrites_bad_title_frontmatter_without_llm(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "2026-05-01-1200-codex-systems.md"
    target.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-05-01",
            "title_cn: 标题",
            "preview_cn: 标题",
            "summary_cn: 标题",
            "routed_by: tigermemory",
            "---",
            "",
            "# 标题",
            "中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入",
            "",
            "# 摘要",
            "该文档是中转 API 配置说明，主打 Claude Opus 4.5 保真满血版。",
            "",
        ]),
        encoding="utf-8",
    )

    rows = tm_memory_reflection.repair_inbox_review_metadata(inbox_dir=inbox, limit=5, use_llm=False)
    updated = target.read_text(encoding="utf-8")

    assert rows[0]["path"].endswith("2026-05-01-1200-codex-systems.md")
    assert rows[0]["source"] == "body_chinese_lines"
    assert "title_cn: 中转API配置说明：Claude Opus 4.5 保真满血版" in updated
    assert "title_cn: 标题" not in updated


def test_enrich_inbox_uses_deepseek_when_deterministic_metadata_is_missing(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "2026-05-01-1200-codex-systems.md"
    target.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-05-01",
            "title_cn: 未提供中文摘要：请写入 agent 在正文首行补一句中文概括。",
            "preview_cn: 未提供中文摘要：请写入 agent 在正文首行补一句中文概括。",
            "routed_by: tigermemory",
            "---",
            "",
            "English-only closeout with pytest passed and pushed commit abc123.",
            "",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        tm_memory_reflection.tm_core,
        "_call_deepseek_json",
        lambda *_args, **_kwargs: (True, {
            "title_cn": "开发收尾记录补全",
            "preview_cn": "这条 inbox 是英文开发收尾记录，包含测试通过、提交推送和后续复盘所需信息，需要补齐中文标题与摘要供每日审批快速判断。",
        }),
    )

    rows = tm_memory_reflection.repair_inbox_review_metadata(inbox_dir=inbox, limit=5, use_llm=True)
    updated = target.read_text(encoding="utf-8")

    assert rows[0]["source"] == "deepseek"
    assert "title_cn: 开发收尾记录补全" in updated
    assert "preview_cn: 这条 inbox 是英文开发收尾记录" in updated


def test_legacy_inbox_without_chinese_uses_raw_preview_instead_of_placeholder(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    body = "Routed closeout pushed commit and passed pytest with enough English details for review."
    _write_inbox(inbox / "2026-05-01-1200-codex-systems.md", body)

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "未提供中文摘要" not in report
    assert "中文标题：Routed closeout pushed commit" in report


def test_preview_is_capped_at_one_hundred_sixty_characters():
    text = "a" * 200
    preview = tm_memory_reflection._preview(text)

    assert len(preview) == 160
    assert len(preview) >= 100


def test_daily_digest_raw_lists_are_in_appendix_details(tmp_path):
    discard_file = tmp_path / "discard-root" / "2026-05-15" / "discard" / "events.jsonl"
    discard_file.parent.mkdir(parents=True)
    discard_file.write_text(
        json.dumps({
            "event_id": "abc123",
            "score": 15,
            "agent": "codex",
            "requested_topic": "systems",
            "topic_inferred": "systems",
            "is_transient": True,
            "is_sensitive": False,
            "reasons": "low value",
            "text_excerpt": "discard raw text",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[{
            "id": "m1",
            "created_at": "2026-05-15T08:00:00+08:00",
            "content": "mem0 raw text",
            "metadata": {"topic": "systems", "source": "codex"},
        }],
        inbox_dir=tmp_path / "inbox",
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "## 📚 附录" in report
    assert "<summary>Mem0 当日正式写入（1 条，最多显示 40 条）</summary>" in report
    assert "<summary>discard quarantine（1 条，最多显示 40 条）</summary>" in report
    visible = "\n".join(_strip_details(report))
    assert "mem0 raw text" not in visible
    assert "discard raw text" not in visible


def test_daily_digest_visible_lines_stay_compact(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for idx in range(40):
        _write_inbox(inbox / f"2026-05-14-12{idx:02d}-codex-systems.md", f"keep {idx}")

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[
            {
                "id": f"m{idx}",
                "created_at": "2026-05-15T08:00:00+08:00",
                "content": f"memory {idx}",
                "metadata": {"topic": "systems", "source": "codex"},
            }
            for idx in range(40)
        ],
        inbox_dir=inbox,
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    outside_details = [line for line in _strip_details(report) if line.strip()]
    assert len(outside_details) <= 100
    assert not re.search(r"memory \d+", "\n".join(outside_details))


def test_daily_digest_embeds_self_evolution_summary_without_raw_events(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tm_memory_reflection,
        "_collect_self_evolution_summary_for_date",
        lambda *_args, **_kwargs: {
            "date": "2026-05-15",
            "event_count": 2,
            "counts": {"hook_blocked": 1, "lesson_searched": 1},
            "outcome_pending": 2,
            "samples": [
                {
                    "event_type": "hook_blocked",
                    "agent": "codex",
                    "session_id": "s1",
                    "rule_id": "owner",
                    "outcome": None,
                    "evidence_ref": ".tmp/guard-rejects.jsonl:1",
                    "summary": "blocked owner rule",
                }
            ],
            "inbox_route": "AGENTS.md section 9.3 topic=selfevolution",
        },
    )

    report = tm_memory_reflection.render_daily_report(
        date="2026-05-15",
        now_iso="2026-05-15T23:55:00+08:00",
        mem0_items=[],
        inbox_dir=tmp_path / "inbox",
        audit_root=tmp_path / "discard-root",
        proposal_root=tmp_path / "cron-proposals",
    )

    assert "self_evolution_count: 2" in report
    assert "Self-Evolution 候选 2 条" in report
    assert "## 🧭 Self-Evolution 候选" in report
    assert "只读证据事件：2 条" in report
    assert "AGENTS.md §9.3 的 selfevolution inbox" in report
    assert "```json" not in report
