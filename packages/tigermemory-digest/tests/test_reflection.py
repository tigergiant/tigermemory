from __future__ import annotations

import datetime as dt
import json

from tigermemory_digest import reflection


def test_frontmatter_returns_mapping_and_body_after_yaml_fence():
    fm, body = reflection._frontmatter("---\nowner: codex\nstatus: active\n---\n正文\n")

    assert fm == {"owner": "codex", "status": "active"}
    assert body == "正文\n"


def test_parse_dt_converts_unix_timestamp_to_china_date():
    parsed = reflection._parse_dt("1767225600")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.strftime("%Y-%m-%d") == "2026-01-01"


def test_preview_collapses_whitespace_and_respects_limit():
    assert reflection._preview("alpha\n\n beta\tgamma", limit=12) == "alpha beta g"


def test_discard_review_candidates_keeps_high_signal_events_only():
    events = [
        {"event_id": "high", "score": 80, "is_transient": True, "text_excerpt": "important"},
        {"event_id": "normal", "score": 10, "is_transient": True, "text_excerpt": "ignore"},
    ]

    candidates = reflection.discard_review_candidates(events)

    assert len(candidates) == 1
    assert candidates[0]["event_id"] == "high"
    assert candidates[0]["reason"] == "high_score_discard"


def _write_discard_event(root, date: str, row: dict) -> None:
    path = root / date / "discard" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def test_discard_events_for_dates_merges_primary_and_wsl_roots(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    wsl = tmp_path / "wsl"
    _write_discard_event(primary, "2026-06-04", {"event_id": "d-root", "score": 10})
    _write_discard_event(wsl, "2026-06-04", {"event_id": "wsl-root", "score": 80})
    monkeypatch.setattr(reflection, "WSL_DISCARD_ROOT", wsl)

    rows = reflection.discard_events_for_dates(["2026-06-04"], audit_root=primary)

    assert [row["event_id"] for row in rows] == ["d-root", "wsl-root"]


def test_discard_events_for_dates_deduplicates_cross_root_events(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    wsl = tmp_path / "wsl"
    event = {"event_id": "same", "text_sha256_12": "abc123", "score": 90}
    _write_discard_event(primary, "2026-06-04", event)
    _write_discard_event(wsl, "2026-06-04", event)
    monkeypatch.setattr(reflection, "WSL_DISCARD_ROOT", wsl)

    rows = reflection.discard_events_for_dates(["2026-06-04"], audit_root=primary)

    assert len(rows) == 1
    assert rows[0]["event_id"] == "same"


def test_inbox_action_groups_split_archive_promote_and_keep_rows():
    rows = [
        reflection.InboxAuditRow("archive.md", "2026-01-01", 20, "codex", "systems", "A", "p", "s", "s", "archive", "old", "archive", "old", True),
        reflection.InboxAuditRow("promote.md", "2026-01-02", 1, "codex", "systems", "B", "p", "s", "s", "promote_to_mem0", "useful", "promote_to_mem0", "useful"),
        reflection.InboxAuditRow("keep.md", "2026-01-03", 1, "codex", "systems", "C", "p", "s", "s", "keep_in_inbox", "wait", "keep_in_inbox", "wait"),
    ]

    archive, promote, keep = reflection._inbox_action_groups(rows)

    assert [row.path for row in archive] == ["archive.md"]
    assert [row.path for row in promote] == ["promote.md"]
    assert [row.path for row in keep] == ["keep.md"]


def test_build_cron_intake_reads_compact_cards(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-health").mkdir()
    (operations / "daily-memory-digest-2026-06-09.md").write_text(
        "\n".join([
            "---",
            "mem0_count: 1",
            "inbox_count: 2",
            "discard_count: 3",
            "proposal_count: 1",
            "applied_count: 0",
            "stale_archive_count: 1",
            "promote_candidate_count: 0",
            "mem0_audit_candidate_count: 0",
            "self_evolution_count: 0",
            "---",
            "",
            "## ⚡ 今日要决策",
            "",
            "- 🔵 Proposed Changes：1 条",
            "",
            "## 🧩 今日沉淀卡",
            "",
            "- 结论：今天需要处理 proposal。",
            "- 建议行动：优先裁决 Proposed Changes。",
        ]),
        encoding="utf-8",
    )
    (operations / "daily-health" / "2026-06-09.md").write_text(
        "# Daily Health\n\n## 摘要\n\n- tm-http 正常。\n",
        encoding="utf-8",
    )
    (operations / "weekly-memory-review-2026-24.md").write_text(
        "# Weekly\n\n## 摘要\n\n- 本周稳定。\n\n## 漂移信号\n\n- none\n\n## 下周关注重点\n\n- 继续观察。\n",
        encoding="utf-8",
    )
    codex_home = tmp_path / ".codex"
    (codex_home / "reports").mkdir(parents=True)
    (codex_home / "reports" / "daily-ai-agent-radar-2026-06-09.md").write_text(
        "# Radar\n\n## 记忆友好收尾摘要\n\n今天 AI 雷达发现一个高信号工具，建议加入观察。\n\n## 建议动作\n\n- 加入观察。\n",
        encoding="utf-8",
    )

    result = reflection.build_cron_intake(
        date="2026-06-09",
        operations_dir=operations,
        codex_home=codex_home,
    )

    assert result["status"] == "ok"
    assert "4/4 个产物可读取" in result["summary"]
    assert "裁决 1 个 memory digest proposal" in result["action_items"]
    assert "处理 1 个 14 天 inbox archive 候选" in result["action_items"]
    rendered = reflection.render_cron_intake(result)
    assert "今天需要处理 proposal" in rendered
    assert "tm-http 正常" in rendered
    assert "AI 雷达" in rendered
    assert "高信号工具" in rendered
    assert "## 来源" in rendered


def test_write_cron_intake_card_persists_wiki_page(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection, "today_local", lambda: "2026-06-09")
    operations = tmp_path / "wiki" / "operations"
    result = {
        "status": "warn",
        "date": "2026-06-09",
        "window": "system-health",
        "summary": "2026-06-09 system-health cron 承接摘要。",
        "reports": [
            {
                "kind": "daily_health",
                "status": "warn",
                "path": "wiki/operations/daily-health/2026-06-09.md",
                "issues": ["daily-health color is red"],
            }
        ],
        "warnings": ["daily_health: daily-health color is red"],
        "action_items": ["处理 daily-health red：查看阻塞项和 known debt"],
    }

    path = reflection.write_cron_intake_card(result, operations_dir=operations)
    text = path.read_text(encoding="utf-8")

    assert path == operations / "cron-intake" / "2026-06-09-system-health.md"
    assert "owner: codex" in text
    assert 'title: "Cron 承接卡 2026-06-09 system-health"' in text
    assert 'intake_status: "warn"' in text
    assert "# Cron 承接卡 2026-06-09 system-health" in text
    assert "处理 daily-health red" in text
    assert "daily-health color is red" in text
    assert "## 来源" in text


def test_write_cron_intake_card_preserves_processing_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(reflection, "today_local", lambda: "2026-06-09")
    operations = tmp_path / "wiki" / "operations"
    card = operations / "cron-intake" / "2026-06-09-system-health.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "---",
            "",
            "# Cron 承接卡 2026-06-09 system-health",
            "",
            "## 建议动作",
            "",
            "- 已处理：恢复 WSL `tm-mcp.service`。",
            "- 已处理：修复 Runtime Config Manager。",
            "",
            "## 承接处理记录",
            "",
            "- 已处理：恢复 WSL `tm-mcp.service`。",
            "- 复测结果：`9766/healthz` 返回 `{\"ok\":true}`。",
            "",
            "## 来源",
            "",
            "- `wiki/operations/daily-health/2026-06-09.md`",
            "- `wiki/operations/daily-health-known-debt.md`",
            "- `packages/tigermemory-config/src/tigermemory_config/manager.py`",
            "",
        ]),
        encoding="utf-8",
    )
    result = {
        "status": "warn",
        "date": "2026-06-09",
        "window": "system-health",
        "summary": "2026-06-09 system-health cron 承接摘要。",
        "reports": [
            {
                "kind": "daily_health",
                "status": "warn",
                "path": "wiki/operations/daily-health/2026-06-09.md",
                "issues": ["daily-health color is red"],
                "summary": ["原始日报仍是 red。"],
            }
        ],
        "warnings": ["daily_health: daily-health color is red"],
        "action_items": ["处理 daily-health red：查看阻塞项和 known debt"],
    }

    path = reflection.write_cron_intake_card(result, operations_dir=operations)
    text = path.read_text(encoding="utf-8")

    assert path == card
    assert "## 承接处理记录" in text
    assert "已处理：恢复 WSL `tm-mcp.service`" in text
    assert "已处理：修复 Runtime Config Manager" in text
    assert "处理 daily-health red：查看阻塞项和 known debt" not in reflection._section_body(text, "建议动作")
    assert text.index("## 承接处理记录") < text.index("## 来源")
    assert text.count("## 承接处理记录") == 1
    sources = reflection._section_body(text, "来源")
    assert "`wiki/operations/daily-health/2026-06-09.md`" in sources
    assert "`wiki/operations/daily-health-known-debt.md`" in sources
    assert "`packages/tigermemory-config/src/tigermemory_config/manager.py`" in sources


def test_build_cron_intake_surfaces_missing_ai_radar_artifact(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-memory-digest-2026-06-09.md").write_text(
        "---\nproposal_count: 0\nstale_archive_count: 0\n---\n\n## 🧩 今日沉淀卡\n\n- 结论：无新沉淀。\n",
        encoding="utf-8",
    )

    result = reflection.build_cron_intake(
        date="2026-06-09",
        operations_dir=operations,
        codex_home=tmp_path / ".codex",
    )

    assert result["status"] == "partial"
    assert any("AI radar report is not persisted" in warning for warning in result["warnings"])
    assert any("AI 雷达落本地短报告" in action for action in result["action_items"])


def test_memory_digest_intake_warns_when_report_is_before_evening_window(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-memory-digest-2026-06-09.md").write_text(
        "---\n"
        "last_run_at: 2026-06-09T07:44:56+08:00\n"
        "proposal_count: 0\n"
        "stale_archive_count: 0\n"
        "---\n\n"
        "## 🧩 今日沉淀卡\n\n"
        "- 结论：日报可读，但生成时间过早。\n",
        encoding="utf-8",
    )

    result = reflection.build_cron_intake(
        date="2026-06-09",
        window="memory-digest",
        operations_dir=operations,
        codex_home=tmp_path / ".codex",
    )

    assert result["status"] == "warn"
    assert any("memory digest may be stale" in warning for warning in result["warnings"])
    assert any("未按 23:40 触发" in action for action in result["action_items"])


def test_build_cron_intake_accepts_bold_ai_radar_sections(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-memory-digest-2026-06-09.md").write_text(
        "---\nproposal_count: 0\nstale_archive_count: 0\n---\n\n## 🧩 今日沉淀卡\n\n- 结论：无新沉淀。\n",
        encoding="utf-8",
    )
    (operations / "daily-health").mkdir(parents=True)
    (operations / "daily-health" / "2026-06-09.md").write_text(
        "# Daily Health\n\n## 摘要\n\n- tm-http 正常。\n",
        encoding="utf-8",
    )
    (operations / "weekly-memory-review-2026-24.md").write_text(
        "# Weekly\n\n## 摘要\n\n- 本周稳定。\n",
        encoding="utf-8",
    )
    (operations / "family-investment-daily-health-2026-06-09.md").write_text(
        "# Investment\n\n这份报告属于投资专线，不应进入 tigermemory 系统 cron 承接短卡。\n",
        encoding="utf-8",
    )
    codex_home = tmp_path / ".codex"
    (codex_home / "reports").mkdir(parents=True)
    (codex_home / "reports" / "daily-ai-agent-radar-2026-06-09.md").write_text(
        "# Radar\n\n**记忆友好收尾摘要**\n"
        "2026-06-09 AI 雷达发现 headroom 与 LongMINT 高信号，适合补强 tigermemory 评测。\n\n"
        "**建议动作**\n\n- 立即评估 headroom。\n- 加入观察 LongMINT。\n- 立即评估 TradingAgents-AShare。\n",
        encoding="utf-8",
    )

    result = reflection.build_cron_intake(
        date="2026-06-09",
        operations_dir=operations,
        codex_home=codex_home,
    )

    radar = next(report for report in result["reports"] if report["kind"] == "ai_agent_radar")
    assert radar["status"] == "ok"
    assert "headroom" in "\n".join(radar["friendly_closeout"])
    assert "立即评估 headroom" in "\n".join(radar["actions"])
    assert any("AI 雷达建议：立即评估 headroom" in item for item in result["action_items"])
    assert any("AI 雷达建议：加入观察 LongMINT" in item for item in result["action_items"])
    assert any("投资专线转交：立即评估 TradingAgents-AShare" in item for item in result["action_items"])
    assert not any("AI 雷达建议：立即评估 TradingAgents-AShare" in item for item in result["action_items"])
    assert not any(item == "无立即动作，继续观察" for item in result["action_items"])
    assert not any("missing 记忆友好收尾摘要" in warning for warning in result["warnings"])
    assert all("investment" not in report["kind"] for report in result["reports"])


def test_build_cron_intake_filters_reports_by_window(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-memory-digest-2026-06-08.md").write_text(
        "---\nproposal_count: 0\nstale_archive_count: 0\n---\n\n## 🧩 今日沉淀卡\n\n- 结论：日报可读。\n",
        encoding="utf-8",
    )
    (operations / "daily-health").mkdir(parents=True)
    (operations / "daily-health" / "2026-06-09.md").write_text(
        "# Daily Health\n\n## 摘要\n\n- 体检可读。\n",
        encoding="utf-8",
    )
    (operations / "weekly-memory-review-2026-24.md").write_text(
        "# Weekly\n\n## 摘要\n\n- 周报可读。\n",
        encoding="utf-8",
    )
    codex_home = tmp_path / ".codex"
    (codex_home / "reports").mkdir(parents=True)
    (codex_home / "reports" / "daily-ai-agent-radar-2026-06-09.md").write_text(
        "# Radar\n\n**记忆友好收尾摘要**\n\n2026-06-09 AI 雷达有高信号。\n",
        encoding="utf-8",
    )

    digest = reflection.build_cron_intake(
        date="2026-06-08",
        window="memory-digest",
        operations_dir=operations,
        codex_home=codex_home,
    )
    health = reflection.build_cron_intake(
        date="2026-06-09",
        window="system-health",
        operations_dir=operations,
        codex_home=codex_home,
    )
    monday_health = reflection.build_cron_intake(
        date="2026-06-08",
        window="system-health",
        operations_dir=operations,
        codex_home=codex_home,
    )
    radar = reflection.build_cron_intake(
        date="2026-06-09",
        window="ai-radar",
        operations_dir=operations,
        codex_home=codex_home,
    )

    assert [row["kind"] for row in digest["reports"]] == ["memory_digest"]
    assert [row["kind"] for row in health["reports"]] == ["daily_health"]
    assert [row["kind"] for row in monday_health["reports"]] == ["daily_health", "weekly_review"]
    assert [row["kind"] for row in radar["reports"]] == ["ai_agent_radar"]
    assert digest["status"] == health["status"] == radar["status"] == "ok"
    assert monday_health["status"] == "partial"


def test_build_cron_intake_surfaces_missing_memory_digest_action(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)

    result = reflection.build_cron_intake(
        date="2026-06-08",
        window="memory-digest",
        operations_dir=operations,
    )

    assert result["status"] == "partial"
    assert any("tigermemory-memory-route-reflection" in item for item in result["action_items"])
    assert not any(item == "无立即动作，继续观察" for item in result["action_items"])


def test_build_cron_intake_surfaces_red_daily_health_action(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    (operations / "daily-health").mkdir(parents=True)
    (operations / "daily-health" / "2026-06-09.md").write_text(
        "# Daily Health\n\n## 摘要\n\n本轮巡检结论暂定为 red。\n\n## 中文总览\n\n- 健康色：`red`\n",
        encoding="utf-8",
    )

    result = reflection.build_cron_intake(
        date="2026-06-09",
        window="system-health",
        operations_dir=operations,
    )

    assert result["status"] == "warn"
    report = result["reports"][0]
    assert report["health_color"] == "red"
    assert any("daily-health red" in item for item in result["action_items"])
    assert not any(item == "无立即动作，继续观察" for item in result["action_items"])


def test_build_cron_intake_surfaces_missing_daily_health_action(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)

    result = reflection.build_cron_intake(
        date="2026-06-11",
        window="system-health",
        operations_dir=operations,
    )

    assert result["status"] == "partial"
    assert any("tigermemory-daily-health-scan" in item for item in result["action_items"])
    assert not any(item == "无立即动作，继续观察" for item in result["action_items"])


def test_default_intake_date_uses_yesterday_for_memory_digest(monkeypatch):
    monkeypatch.setattr(reflection, "_yesterday_local", lambda: "2026-06-08")
    monkeypatch.setattr(reflection, "today_local", lambda: "2026-06-09")

    assert reflection.default_intake_date("memory-digest") == "2026-06-08"
    assert reflection.default_intake_date("system-health") == "2026-06-09"
    assert reflection.default_intake_date("ai-radar") == "2026-06-09"


def test_build_compact_report_ai_radar_preserves_actions_and_ledger(tmp_path):
    codex_home = tmp_path / ".codex"
    reports = codex_home / "reports"
    reports.mkdir(parents=True)
    (reports / "daily-ai-agent-radar-2026-06-16.md").write_text(
        "\n".join([
            "## 今日结论",
            "- 已验证事实：agentcsp 值得评估。",
            "",
            "## AI 雷达学习台账候选",
            "- 项目: agentcsp | 分类: 立即评估 | 学习点: context policy | 证据: https://example.test/agentcsp",
            "",
            "## 建议动作",
            "1. 立即评估 `agentcsp` 的上下文边界。",
            "",
            "## 记忆友好收尾摘要",
            "2026-06-16 的重点是 context policy 和报告压缩。",
            "",
            "## 来源与验证",
            "- `wiki/systems/ai-radar-learning-ledger.md`",
        ]),
        encoding="utf-8",
    )

    result = reflection.build_compact_report(
        kind="ai-radar",
        date="2026-06-16",
        codex_home=codex_home,
    )

    assert result["status"] == "ok"
    assert any("agentcsp" in item for item in result["learning_items"])
    assert any("立即评估" in item for item in result["action_required"])
    assert "wiki/systems/ai-radar-learning-ledger.md" in result["source_paths"]


def test_build_compact_report_daily_health_surfaces_yellow_status(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    (operations / "daily-health").mkdir(parents=True)
    (operations / "daily-health" / "2026-06-17.md").write_text(
        "\n".join([
            "# Daily Health",
            "",
            "## 摘要",
            "- 主链路可用，但 prompt audit 有缺口。",
            "",
            "## 中文总览",
            "- 健康色：`yellow`",
            "",
            "## 问题",
            "- prompt-audit root-agent-rules missing。",
            "",
            "## 建议",
            "- 修复 root agent rules 标记。",
            "",
            '{"commit_sha":"0123456789abcdef0123456789abcdef01234567","push_result":"pushed"}',
        ]),
        encoding="utf-8",
    )

    result = reflection.build_compact_report(
        kind="daily-health",
        date="2026-06-17",
        operations_dir=operations,
    )

    assert result["status"] == "warn"
    assert result["health_color"] == "yellow"
    assert any("prompt-audit" in item for item in result["issues"])
    assert result["evidence"]["commit_sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert result["evidence"]["push_result"] == "pushed"


def test_build_compact_report_answer_trace_preserves_counts(tmp_path):
    trace = tmp_path / "answer-trace-summary.json"
    trace.write_text(
        json.dumps(
            {
                "row_count": 25,
                "selected_run_id": "daily-health-2026-06-17",
                "failure_count": 1,
                "invalid_row_count": 0,
                "duration_ms": {"p95": 14081.4},
                "status_counts": {"ok": 21, "conflict": 2, "not_found": 2},
                "llm_counts": {"ok": 22, "skipped": 3},
                "recommendation_quality": {
                    "recommendation_shown_count": 24,
                    "recommendation_used_as_evidence_count": 0,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = reflection.build_compact_report(kind="answer-trace", path=trace)

    assert result["status"] == "warn"
    assert result["summary"]["row_count"] == 25
    assert result["summary"]["duration_p95_ms"] == 14081.4
    assert result["summary"]["recommendation_shown_count"] == 24
    assert any("1 failure" in item for item in result["issues"])


def test_build_compact_report_memory_digest_promotes_count_issues_to_actions(tmp_path):
    operations = tmp_path / "wiki" / "operations"
    operations.mkdir(parents=True)
    (operations / "daily-memory-digest-2026-06-16.md").write_text(
        "\n".join([
            "---",
            "proposal_count: 0",
            "stale_archive_count: 2",
            "wiki_proposal_inbox_count: 4",
            "mem0_audit_warning_count: 1",
            "---",
            "",
            "## ⚡ 今日要决策",
            "- 14 天兜底 archive 候选：2 条",
            "",
            "## 🧠 Proposed Changes",
            "- none",
            "",
            "## 🧭 今日沉淀卡",
            "- 结论：需要处理 inbox 积压。",
        ]),
        encoding="utf-8",
    )

    result = reflection.build_compact_report(
        kind="memory-digest",
        date="2026-06-16",
        operations_dir=operations,
    )

    assert result["status"] == "warn"
    assert "- none" not in result["action_required"]
    assert any("stale inbox archive" in item for item in result["action_required"])
    assert any("wiki proposal inbox" in item for item in result["action_required"])
    assert any("mem0 audit warning" in item for item in result["action_required"])
