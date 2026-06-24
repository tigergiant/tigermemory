from __future__ import annotations

import json
import pathlib
import sys
import builtins
import datetime as dt
import subprocess
from html.parser import HTMLParser

from fastapi.testclient import TestClient

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_cron_apply  # type: ignore[import-not-found]
import tm_dev_supervisor_review  # type: ignore[import-not-found]
import tm_route  # type: ignore[import-not-found]
import tm_route_events  # type: ignore[import-not-found]
import tm_review_tools  # type: ignore[import-not-found]
import tm_review_ui  # type: ignore[import-not-found]

HOST = {"Host": "127.0.0.1:9777"}


def _client(tmp_path: pathlib.Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(tm_review_ui, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(tm_review_ui, "ensure_write_ready", lambda *_args, **_kwargs: None)
    return TestClient(tm_review_ui.app)


def test_runtime_events_api_returns_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path / "runtime-events"))
    tm_review_ui.tm_runtime_events.record_event(
        event_type="quality_cache_warm",
        service="tm-dashboard",
        component="quality_cache",
        ok=True,
        extra={"cached": True},
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/runtime/events?days=1&limit=5", headers=HOST)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"]["event_count"] == 1
    assert payload["summary"]["type_counts"]["quality_cache_warm"] == 1
    assert payload["events"][0]["service"] == "tm-dashboard"


def _write_digest(root: pathlib.Path, date: str = "2026-05-21") -> pathlib.Path:
    path = root / "wiki" / "operations" / f"daily-memory-digest-{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: active",
            f"updated: {date}",
            "title: \"Memory Digest\"",
            "last_run_at: 2026-05-21T23:55:00+08:00",
            "mem0_count: 2",
            "inbox_count: 1",
            "discard_count: 3",
            "proposal_count: 1",
            "applied_count: 0",
            "stale_archive_count: 1",
            "promote_candidate_count: 0",
            "wiki_proposal_inbox_count: 1",
            "---",
            "",
            "# Memory Digest 2026-05-21",
            "",
            "## ⚡ 今日要决策",
            "",
            "- 🔴 14 天兜底 archive 候选：1 条",
            "",
            "## 摘要",
            "",
            "fixture summary",
            "",
            "## 🧩 今日沉淀卡",
            "",
            "- 结论：fixture digest 可用于审批测试。",
            "- 建议行动：优先裁决 Proposed Changes。",
            "",
            "## 📝 inbox 决策区",
            "",
            "### 🔴 建议 archive",
            "",
            "- `inbox/2026-05-01-1200-codex-systems.md` **高亮：14 天兜底 archive**",
            "  - 入库时间：2026-05-01，已停留 20 天",
            "  - 中文标题：审批界面归档判断",
            "  - 中文预览：审批界面需要能快速判断这条开发收尾记录是否应归档。这里放入较长的中文预览，用于折叠区展示真正摘要。",
            "  - 原文预览：commit pushed pytest passed",
            "  - Codex 推荐操作：归档",
            "  - Codex 推荐理由：已超过 14 天且没有 apply 记录，建议隐藏出日常审阅队列。",
            "  - cron 建议动作：archive",
            "  - 建议理由：14-day fallback",
            "  - 虎哥裁决：[ ] apply  [ ] reject",
            "",
            "## 🧾 Inbox Wiki Proposal 台账",
            "",
            "- 待追踪 inbox/wiki_proposal：1 条，聚合为 1 个目标页。",
            "",
            "## 🧠 Proposed Changes",
            "",
            "#### proposal-2026-05-21-001",
            "",
            "**类型**：prompt-tuning",
            "",
            "**触发证据**：fixture",
            "",
            "```diff",
            "diff --git a/tools/tm_route.py b/tools/tm_route.py",
            "+x",
            "```",
            "",
            "**影响范围**：tools/tm_route.py",
            "",
            "**Spec Capsule**：complete",
            "",
            "- 问题：路由 prompt 需要更明确的验收证据。",
            "- 证据：fixture evidence",
            "- 约束：只能通过 ask-confirm 应用。",
            "- 方案：补充 prompt 文字和回归测试。",
            "- 验收：route 测试通过。",
            "- 回滚：revert cron-apply commit。",
            "- 是否需要虎哥确认：需要。",
            "",
            "## 📈 自评指标",
            "",
            "- 当日自评：76",
            "",
            "## 📚 附录",
            "",
            "raw appendix",
            "",
            "## 来源",
            "",
            "- fixture",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def _write_inbox(root: pathlib.Path, name: str = "2026-05-01-1200-codex-systems.md") -> pathlib.Path:
    path = root / "inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "owner: codex",
                "status: draft",
                "updated: 2026-05-21",
                "title_cn: 测试归档标题",
                "preview_cn: 这是一条用于验证归档摘要页的中文摘要，不应把完整 inbox 原文提交进 Git。",
                "summary_cn: 测试归档标题",
                "routed_by: tigermemory",
                "---",
                "",
                "# Inbox Fixture",
                "",
                "full raw inbox body should only be cached under .tmp.",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_investment_wiki_proposal(
    root: pathlib.Path,
    name: str = "2026-06-09-1200-codex-investment.md",
) -> pathlib.Path:
    path = root / "inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-06-09",
            "topic: investment",
            "title_cn: 贵州茅台正式报告",
            "preview_cn: 贵州茅台 600519.SH DeerFlow 正式报告 PDF，原始路径 C:\\Users\\Giant\\Documents\\New project\\reports\\maotai-2026-06.pdf",
            "summary_cn: 贵州茅台正式报告",
            "routed_by: tigermemory",
            "knowledge_target: wiki_proposal",
            "proposal_kind: wiki",
            "wiki_partition: investment",
            "wiki_slug_hint: research/600519",
            "wiki_action: update",
            "route_score: 88",
            "l2_review_score: 84",
            "target_confidence: 90",
            "---",
            "",
            "# 贵州茅台正式报告",
            "",
            "长期研究结论与风险提示，不含交易执行数据。",
        ]),
        encoding="utf-8",
    )
    return path


def _write_p310_report(root: pathlib.Path, folder: str, matrix: str, *, evidence: int, leak: int) -> pathlib.Path:
    path = root / ".tmp" / folder / f"{matrix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "case_count": 30,
                "passed": 20,
                "expected_path_case_count": 26,
                "answer_evidence_hit": evidence,
                "evidence_gate_hit": evidence + 2,
                "map_hit_but_evidence_miss": leak,
                "map_leak_reason_category_counts": {"relevance": leak},
                "answer_evidence_hit_by_bucket": {"workflow_fact": evidence},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_retrieval_release_status_reads_latest_p310_holdout(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("TM_HYBRID_MAP_ARM", raising=False)
    monkeypatch.delenv("TM_EMBED_SUMMARY_WEIGHT", raising=False)
    monkeypatch.delenv("TM_ANSWER_WIKI_MAP_BRIDGE", raising=False)
    monkeypatch.delenv("TM_ANSWER_WIKI_MAP", raising=False)
    _write_p310_report(tmp_path, "p310-funnel-old", "production", evidence=17, leak=8)
    _write_p310_report(tmp_path, "p310-funnel-new", "map_arm", evidence=21, leak=4)

    payload = tm_review_ui._dashboard_retrieval_release_status()

    assert payload["decision"] == "default_candidate"
    assert payload["default_enabled"] is False
    assert payload["deltas"]["answer_evidence_hit"] == 4
    assert payload["deltas"]["map_hit_but_evidence_miss"] == -4
    assert payload["latest"]["map_arm"]["answer_evidence_hit"] == 21


def test_retrieval_release_status_labels_live_service_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TM_HYBRID_MAP_ARM", "1")
    monkeypatch.setenv("TM_EMBED_SUMMARY_WEIGHT", "0")
    monkeypatch.setenv("TM_ANSWER_WIKI_MAP_BRIDGE", "0")
    monkeypatch.setenv("TM_ANSWER_WIKI_MAP", "0")
    _write_p310_report(tmp_path, "p310-funnel-old", "production", evidence=17, leak=8)
    _write_p310_report(tmp_path, "p310-funnel-new", "map_arm", evidence=21, leak=4)

    payload = tm_review_ui._dashboard_retrieval_release_status()

    assert payload["decision"] == "service_default_enabled"
    assert payload["default_enabled"] is True
    assert "已默认开启" in payload["summary"]


def test_retrieval_release_status_reports_missing_map_arm_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)

    payload = tm_review_ui._dashboard_retrieval_release_status()

    assert payload["decision"] == "no_recent_map_arm_evidence"
    assert payload["warnings"] == []


def test_retrieval_release_status_reports_missing_production_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_p310_report(tmp_path, "p310-funnel-new", "map_arm", evidence=21, leak=4)

    payload = tm_review_ui._dashboard_retrieval_release_status()

    assert payload["decision"] == "needs_production_baseline"
    assert payload["latest"]["production"] is None
    assert payload["latest"]["map_arm"]["answer_evidence_hit"] == 21


def test_retrieval_release_status_reports_corrupt_p310_json(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_p310_report(tmp_path, "p310-funnel-old", "production", evidence=17, leak=8)
    bad = tmp_path / ".tmp" / "p310-funnel-new" / "map_arm.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json", encoding="utf-8")

    payload = tm_review_ui._dashboard_retrieval_release_status()

    assert payload["decision"] == "artifact_error"
    assert payload["latest"]["map_arm"] is None
    assert payload["warnings"]
    assert "map_arm holdout" in payload["warnings"][0]


def test_retrieval_release_status_reports_runtime_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TM_HYBRID_MAP_ARM", "1")
    monkeypatch.setenv("TM_EMBED_SUMMARY_WEIGHT", "0")
    monkeypatch.setenv("TM_ANSWER_WIKI_MAP_BRIDGE", "0")
    monkeypatch.setenv("TM_ANSWER_WIKI_MAP", "0")

    payload = tm_review_ui._dashboard_retrieval_release_status()

    assert payload["default_enabled"] is True
    assert payload["flags"]["hybrid_map_arm_enabled"] is True
    assert payload["flags"]["planner_wiki_map_enabled"] is False


def test_host_header_rejects_non_localhost(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/healthz", headers={"Host": "example.com"})

    assert response.status_code == 403


def test_cli_registered_dashboard_port_allows_local_browser_cookie(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "ALLOWED_HOSTS", set(tm_review_ui.DEFAULT_ALLOWED_HOSTS))
    monkeypatch.setattr(tm_review_ui, "LOCAL_HOSTS", {"127.0.0.1", "localhost"})
    monkeypatch.setattr(tm_review_ui, "COOKIE_BOOTSTRAP_HOSTS", {"127.0.0.1", "localhost"})
    tm_review_ui.register_dashboard_bind_host("127.0.0.1", 9789)
    client = _client(tmp_path, monkeypatch)

    response = client.get("/health", headers={"Host": "127.0.0.1:9789"})
    healthz = client.get("/healthz", headers={"Host": "127.0.0.1:9789"})

    assert response.status_code == 200
    assert "tm_review_session" in response.headers["set-cookie"]
    assert healthz.json()["port"] == 9789


def test_session_token_cookie_flow(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/", headers=HOST, follow_redirects=False)

    assert response.status_code == 302
    assert "tm_review_session" in response.headers["set-cookie"]


def test_api_digest_requires_cookie_after_session_exists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)
    fresh = TestClient(tm_review_ui.app)

    response = fresh.get("/api/digest/2026-05-21", headers=HOST)

    assert response.status_code == 401


def test_direct_digest_sets_cookie_for_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)

    response = client.get("/digest/2026-05-21", headers=HOST)

    assert response.status_code == 200
    assert "tm_review_session" in response.headers["set-cookie"]


def test_tailscale_dashboard_bootstraps_browser_cookie(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/health", headers={"Host": "tigermemory-wsl:1998"})

    assert response.status_code == 200
    assert "tm_review_session" in response.headers["set-cookie"]


def test_unknown_non_local_dashboard_requires_token_for_browser_bootstrap(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/health", headers={"Host": "192.0.2.10:1998"})

    assert response.status_code == 403


def test_non_local_query_token_sets_session_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_DASHBOARD_TOKEN", "secret-token")
    client = _client(tmp_path, monkeypatch)

    response = client.get("/health?token=secret-token", headers={"Host": "tigermemory-wsl:1998"}, follow_redirects=False)

    assert response.status_code == 302
    assert "tm_review_session" in response.headers["set-cookie"]
    assert "token=" not in response.headers["location"]


def test_bearer_token_allows_api_access_without_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_DASHBOARD_TOKEN", "secret-token")
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        "/api/digest/2026-05-21",
        headers={"Host": "tigermemory-wsl:1998", "Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_digest_with_cookie_returns_html_and_embedded_json(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest/2026-05-21", headers=HOST)

    assert response.status_code == 200
    assert "TigerMemory 今日待确认" in response.text
    assert "今日要决策" in response.text
    assert "/static/assets/tailwindcss.min.js" in response.text
    assert "/static/assets/lucide.min.js" in response.text
    assert "/static/i18n.js" in response.text
    assert 'onclick="window.tmI18n' in response.text
    assert "@keyframes fadeIn" in response.text
    assert "https://cdn.tailwindcss.com" not in response.text
    assert "digest-data" in response.text
    assert "cron-intake-data" in response.text
    assert "cron-intake-section" in response.text
    assert "cron 承接卡" in response.text
    assert "wiki-proposal-ledger-section" in response.text
    assert "Wiki 提案台账" in response.text


def test_i18n_assets_are_public(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    json_response = client.get("/static/i18n.json", headers=HOST)
    js_response = client.get("/static/i18n.js", headers=HOST)

    assert json_response.status_code == 200
    assert json_response.json()["zh"]["nav.daily"] == "今日待确认"
    assert json_response.json()["zh"]["term.auto_refresh_45s"] == "自动刷新 45s"
    assert json_response.json()["en"]["nav.daily"] == "Daily Review"
    assert json_response.json()["en"]["term.auto_refresh_45s"] == "Auto-refresh 45s"
    assert js_response.status_code == 200
    assert "window.tmI18n" in js_response.text


def test_api_digest_parses_expected_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    live_inbox = tmp_path / "inbox" / "2026-05-01-1200-codex-systems.md"
    live_inbox.parent.mkdir(parents=True, exist_ok=True)
    live_inbox.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-05-01",
            "title_cn: 审批界面归档判断",
            "preview_cn: 审批界面需要能快速判断这条开发收尾记录是否应归档。这里放入较长的中文预览，用于折叠区展示真正摘要。",
            "summary_cn: 审批界面归档判断",
            "routed_by: tigermemory",
            "---",
            "",
            "commit pushed pytest passed",
        ]),
        encoding="utf-8",
    )
    similar = tmp_path / "wiki" / "systems" / "review-ui-approval.md"
    similar.parent.mkdir(parents=True, exist_ok=True)
    similar.write_text("审批界面需要快速判断 inbox 是否归档，并提供中文预览。", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/digest/2026-05-21", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["digest"]["counts"]["mem0"] == 2
    assert data["digest"]["counts"]["inbox"] == 1
    assert data["digest"]["counts"]["report_inbox"] == 1
    assert data["digest"]["inbox_rows"][0]["stale_archive"] is True
    assert data["digest"]["inbox_rows"][0]["title_cn"] == "审批界面归档判断"
    assert "快速判断" in data["digest"]["inbox_rows"][0]["preview_cn"]
    assert data["digest"]["inbox_rows"][0]["raw_summary"] == "commit pushed pytest passed"
    assert data["digest"]["inbox_rows"][0]["codex_recommended_action"] == "归档"
    assert "建议先归档" in data["digest"]["inbox_rows"][0]["codex_recommended_reason"]
    assert data["digest"]["inbox_rows"][0]["wiki_target"]["partition"] == "systems"
    assert data["digest"]["inbox_rows"][0]["wiki_target"]["path"].startswith("wiki/systems/")
    assert data["digest"]["inbox_rows"][0]["wiki_target"]["similar"][0]["path"] == "wiki/systems/review-ui-approval.md"
    assert data["digest"]["proposals"][0]["id"] == "proposal-2026-05-21-001"
    assert data["digest"]["proposals"][0]["spec_capsule"]["status"] == "complete"
    assert data["digest"]["proposals"][0]["spec_capsule"]["items"]["problem"].startswith("路由 prompt")


def test_api_cron_intake_returns_compact_persisted_report_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    _write_digest(tmp_path, "2026-05-21")
    health_dir = tmp_path / "wiki" / "operations" / "daily-health"
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "2026-05-21.md").write_text(
        "# Daily Health\n\n## 摘要\n\n- 服务正常。\n",
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "operations" / "weekly-memory-review-2026-21.md").write_text(
        "# Weekly\n\n## 摘要\n\n- 周报正常。\n\n## 漂移信号\n\n- none\n\n## 下周关注重点\n\n- 继续观察。\n",
        encoding="utf-8",
    )
    radar_dir = tmp_path / ".codex" / "reports"
    radar_dir.mkdir(parents=True)
    (radar_dir / "daily-ai-agent-radar-2026-05-21.md").write_text(
        "# Radar\n\n**记忆友好收尾摘要**\n\n2026-05-21 AI 雷达发现一个高信号 agent runtime 更新，建议加入观察。\n",
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/cron/intake/2026-05-21", headers=HOST)

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    intake = data["intake"]
    assert intake["status"] == "ok"
    assert "4/4 个产物可读取" in intake["summary"]
    assert any("memory_digest" == report["kind"] for report in intake["reports"])
    radar = next(report for report in intake["reports"] if report["kind"] == "ai_agent_radar")
    assert "高信号 agent runtime" in "\n".join(radar["friendly_closeout"])
    assert "no-store" in response.headers["cache-control"]


def test_api_cron_intake_rejects_invalid_date(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/cron/intake/not-a-date", headers=HOST)

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_daily_page_static_assets_wire_cron_intake_card():
    review_html = (tm_review_ui.STATIC_DIR / "review.html").read_text(encoding="utf-8")
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    style_css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert 'id="cron-intake-section"' in review_html
    assert 'id="cron-intake-data"' in review_html
    assert 'id="cron-intake-section" data-no-i18n' in review_html
    assert 'id="wiki-proposal-ledger-section"' in review_html
    assert "renderCronIntake" in pages_js
    assert "renderWikiProposalLedger" in pages_js
    assert "data-wiki-ledger-action=\"approve-all\"" in pages_js
    assert "批量写入 Wiki" in pages_js
    assert "写入 Wiki" in pages_js
    assert "wikiProposalScoreText(row)" in pages_js
    assert "查看技术详情" in pages_js
    assert "review_label" in pages_js
    assert "sample_items" in pages_js
    assert "runWikiProposalApproval" in pages_js
    assert "wikiTargetFromLedgerRow" in pages_js
    assert "openWikiProposalBatchModal" in pages_js
    assert "proposalSpecCapsuleHtml" in pages_js
    assert "Spec Capsule" in pages_js
    assert "investment-archive" in pages_js
    assert "enqueueWikiProposalInvestmentArchive" in pages_js
    assert "investment-wiki" in pages_js
    assert "写入投研 Wiki" in pages_js
    assert "移入投资提案归档" in pages_js
    assert "投资提案归档" in pages_js
    assert "生成可检索摘要，不写正式投研页" in pages_js
    assert "shadow-[inset_4px_0_0_#c8a560]" in pages_js
    assert "investment_archive" in pages_js
    assert "投资分类" in pages_js
    assert "建议 Wiki" in pages_js
    assert "completeCard" in pages_js
    assert "refreshDigestThenCompleteCards" in pages_js
    assert "markCompletedIfPathGoneAfterError" in pages_js
    assert "90000" in pages_js
    assert "markCompletedIfPathGone" in pages_js
    assert "digestHasInboxPath" in pages_js
    assert "digestRenderSignature" in pages_js
    assert "digestSignature(digest)" in pages_js
    assert "skipIfUnchanged" in pages_js
    assert "fetchDigestInFlight" in pages_js
    assert "tm-refresh-quiet" in pages_js
    assert "body.tm-refresh-quiet #inbox-list article" in style_css
    assert "animation: statusPulse" not in style_css
    assert "animation: tmGlowMove" not in style_css
    assert "tmQueuePulseLite" in style_css
    assert "/api/cron/intake/" in pages_js
    assert "cron-intake-summary" in pages_js
    assert "font-mono" in pages_js


def test_live_inbox_rows_forwards_route_fields_for_frontend_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)

    class FakeRecord:
        def __init__(self) -> None:
            self.path = str(tmp_path / "inbox" / "2026-06-06-1200-codex-systems.md")
            self.stale_archive = False
            self.age_days = 3
            self.title_cn = "路由字段透传"
            self.preview_cn = "用于验收 route_* 字段透传"
            self.summary_cn = "路由字段透传"
            self.summary = "for route field assertions"
            self.action = "promote_to_mem0"
            self.reason = "tmp route reason"
            self.codex_recommended_action = "写入 Mem0"
            self.codex_recommended_reason = "基线检查"
            self.topic = "systems"
            self.route_target = "mem0"
            self.route_label = "写入 Mem0"
            self.route_confidence = 93
            self.route_reason = "近期记忆更稳妥"
            self.route_flags = ("from_worker", "high_priority")
            self.route_hard_rule = True

    monkeypatch.setattr(tm_review_ui, "_load_kept_paths", lambda _date: set())
    monkeypatch.setattr(tm_review_ui.tm_memory_reflection, "audit_inbox", lambda *_, **__: [FakeRecord()])
    monkeypatch.setattr(
        tm_review_ui,
        "_wiki_target_suggestions",
        lambda *_args, **_kwargs: {"partition": "systems", "slug": "x", "path": "wiki/systems/x.md"},
    )

    visible, hidden = tm_review_ui._live_inbox_rows("2026-06-06")

    assert hidden == []
    assert len(visible) == 1
    row = visible[0]
    assert row["route_target"] == "mem0"
    assert row["route_label"] == "写入 Mem0"
    assert row["route_confidence"] == 93
    assert row["route_reason"] == "近期记忆更稳妥"
    assert row["route_flags"] == ["from_worker", "high_priority"]
    assert row["route_hard_rule"] is True


def test_live_inbox_rows_hides_auto_generated_investment_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)

    class FakeRecord:
        def __init__(self) -> None:
            self.path = str(tmp_path / "inbox" / "2026-06-08-1200-codex-investment.md")
            self.stale_archive = False
            self.age_days = 0
            self.title_cn = "投研流水提案"
            self.preview_cn = "TradingAgents 自动生成 decision-log 后产生的提案。"
            self.summary_cn = "投研流水提案"
            self.summary = "Generated by tools/tm_adapter.py --batch --portfolio"
            self.action = "keep_in_inbox"
            self.reason = "auto generated"
            self.codex_recommended_action = "自动投研流水"
            self.codex_recommended_reason = "默认隐藏"
            self.topic = "investment"
            self.route_target = "wiki"
            self.route_label = "自动投研流水"
            self.route_confidence = 96
            self.route_reason = "自动生成投研流水"
            self.route_flags = ("auto-generated-investment-log",)
            self.route_hard_rule = True

    monkeypatch.setattr(tm_review_ui, "_load_kept_paths", lambda _date: set())
    monkeypatch.setattr(tm_review_ui.tm_memory_reflection, "audit_inbox", lambda *_, **__: [FakeRecord()])
    monkeypatch.setattr(
        tm_review_ui,
        "_wiki_target_suggestions",
        lambda *_args, **_kwargs: {"partition": "investment", "slug": "x", "path": "wiki/investment/x.md"},
    )

    visible, hidden = tm_review_ui._live_inbox_rows("2026-06-08")

    assert visible == []
    assert len(hidden) == 1
    assert hidden[0]["hidden_reason"] == "auto-generated-investment-log"
    assert hidden[0]["route_flags"] == ["auto-generated-investment-log"]


def test_live_inbox_rows_hides_legacy_session_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)

    class FakeRecord:
        def __init__(self) -> None:
            self.path = str(tmp_path / "inbox" / "2026-06-09-1200-codex-systems.md")
            self.stale_archive = False
            self.age_days = 0
            self.title_cn = "旧交接卡"
            self.preview_cn = "Session Handoff Card 已由 Mem0 fast path 接管。"
            self.summary_cn = "旧交接卡"
            self.summary = "memory_type: session-handoff"
            self.action = "keep_in_inbox"
            self.reason = "legacy handoff"
            self.codex_recommended_action = "旧交接卡"
            self.codex_recommended_reason = "默认隐藏"
            self.topic = "systems"
            self.route_target = "mem0"
            self.route_label = "旧交接卡"
            self.route_confidence = 94
            self.route_reason = "历史交接卡"
            self.route_flags = ("legacy_session_handoff",)
            self.route_hard_rule = True

    monkeypatch.setattr(tm_review_ui, "_load_kept_paths", lambda _date: set())
    monkeypatch.setattr(tm_review_ui.tm_memory_reflection, "audit_inbox", lambda *_, **__: [FakeRecord()])
    monkeypatch.setattr(
        tm_review_ui,
        "_wiki_target_suggestions",
        lambda *_args, **_kwargs: {"partition": "systems", "slug": "x", "path": "wiki/systems/x.md"},
    )

    visible, hidden = tm_review_ui._live_inbox_rows("2026-06-09")

    assert visible == []
    assert len(hidden) == 1
    assert hidden[0]["hidden_reason"] == "legacy_session_handoff"
    assert hidden[0]["route_flags"] == ["legacy_session_handoff"]


def test_api_digest_surfaces_wiki_proposal_ledger_and_hides_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)

    class FakeRecord:
        def __init__(self, topic: str, partition: str, slug: str) -> None:
            self.path = str(tmp_path / "inbox" / f"2026-06-09-1200-codex-{topic}.md")
            self.created_date = "2026-06-09"
            self.stale_archive = False
            self.age_days = 0
            self.agent = "codex"
            self.topic = topic
            self.title_cn = "Wiki 提案"
            self.preview_cn = "这条记录已经被路由为 wiki proposal，应进入台账而不是普通待确认列表。"
            self.summary_cn = "Wiki 提案"
            self.summary = "wiki proposal fixture"
            self.action = "keep_in_inbox"
            self.reason = "wiki proposal ledger"
            self.codex_recommended_action = "写入 Wiki"
            self.codex_recommended_reason = "已具备长期沉淀价值"
            self.route_target = "wiki"
            self.route_label = "写入 Wiki"
            self.route_confidence = 90
            self.route_reason = "长期事实候选"
            self.route_flags = ()
            self.route_hard_rule = False
            self.already_applied = False
            self.knowledge_target = "wiki_proposal"
            self.proposal_kind = "wiki"
            self.wiki_partition = partition
            self.wiki_slug_hint = slug
            self.route_score = 88
            self.l2_review_score = 85
            self.target_confidence = 90
            self.wiki_action = "update"

    records = [
        FakeRecord("systems", "systems", "cron-result-intake-learning-plan"),
        FakeRecord("investment", "investment", "decision-log/example"),
    ]
    monkeypatch.setattr(tm_review_ui.tm_memory_reflection, "audit_inbox", lambda *_, **__: records)
    monkeypatch.setattr(tm_review_ui, "_load_kept_paths", lambda _date: set())
    monkeypatch.setattr(
        tm_review_ui,
        "_wiki_target_suggestions",
        lambda *_args, **_kwargs: {"partition": "systems", "slug": "x", "path": "wiki/systems/x.md"},
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/digest/2026-05-21", headers=HOST)

    digest = response.json()["digest"]
    assert digest["inbox_rows"] == []
    assert {row["hidden_reason"] for row in digest["hidden_inbox_rows"]} == {"wiki_proposal_ledger"}
    assert digest["counts"]["wiki_proposal_inbox"] == 2
    assert digest["counts"]["wiki_proposal_groups"] == 2
    assert len(digest["wiki_proposal_ledger"]) == 2
    statuses = {row["target"]: row["status"] for row in digest["wiki_proposal_ledger"]}
    assert statuses["wiki/systems/cron-result-intake-learning-plan.md"] == "pending"
    assert statuses["wiki/investment/decision-log/example.md"] == "investment-thread"
    system_row = next(row for row in digest["wiki_proposal_ledger"] if row["status"] == "pending")
    assert system_row["target_partition"] == "systems"
    assert system_row["target_slug"] == "cron-result-intake-learning-plan"
    assert system_row["review_label"] == "高可信"
    assert system_row["route_score_min"] == 88
    assert system_row["l2_review_score_min"] == 85
    assert system_row["sample_items"][0]["title"] == "Wiki 提案"
    investment_row = next(row for row in digest["wiki_proposal_ledger"] if row["status"] == "investment-thread")
    assert investment_row["investment_triage"]["investment_doc_type"] == "decision"
    assert investment_row["investment_triage"]["investment_review_level"] == "proposal"
    assert investment_row["investment_triage"]["preserve_original"] is True
    assert investment_row["investment_triage"]["copy_only"] is True


def test_api_digest_returns_self_evolution_summary_not_raw_events(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    monkeypatch.setattr(
        tm_review_ui,
        "_build_self_evolution_payload",
        lambda *_args, **_kwargs: {
            "source": "live",
            "date": "2026-05-21",
            "event_count": 2,
            "counts": {"hook_blocked": 2},
            "outcome_pending": 2,
            "samples": [
                {
                    "event_type": "hook_blocked",
                    "agent": "codex",
                    "session_id": "s1",
                    "rule_id": "owner",
                    "evidence_ref": ".tmp/guard-rejects.jsonl:1",
                    "summary": "blocked owner rule",
                }
            ],
            "inbox_route": "AGENTS.md section 9.3 topic=selfevolution",
        },
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/digest/2026-05-21", headers=HOST)

    digest = response.json()["digest"]
    assert digest["self_evolution"]["event_count"] == 2
    assert digest["self_evolution"]["counts"] == {"hook_blocked": 2}
    assert "events" not in digest["self_evolution"]
    assert len(digest["self_evolution"]["samples"]) == 1


def test_api_digest_uses_live_inbox_not_stale_report_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/digest/2026-05-21", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["digest"]["inbox_rows"] == []
    assert data["digest"]["counts"]["inbox"] == 0
    assert data["digest"]["counts"]["stale_archive"] == 0
    assert data["digest"]["counts"]["report_inbox"] == 1
    assert data["digest"]["report_inbox_rows"][0]["path"] == "inbox/2026-05-01-1200-codex-systems.md"


def test_daily_route_redirects_to_digest_entry(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/daily", headers=HOST, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/digest"


def test_digest_entry_uses_today_even_when_only_old_report_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-22")
    _write_digest(tmp_path, "2026-05-21")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest", headers=HOST, follow_redirects=False)

    assert response.status_code == 200
    assert '"date": "2026-05-22"' in response.text
    assert '"live_fallback": true' in response.text
    assert "TigerMemory 今日待确认" in response.text


def test_digest_entry_prefers_today_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-22")
    _write_digest(tmp_path, "2026-05-21")
    _write_digest(tmp_path, "2026-05-22")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest", headers=HOST, follow_redirects=False)

    assert response.status_code == 200
    assert '"date": "2026-05-22"' in response.text


def test_digest_entry_returns_live_inbox_when_no_reports_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-22")
    inbox = tmp_path / "inbox" / "2026-05-22-1200-codex-systems.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: draft",
            "updated: 2026-05-22",
            "title_cn: 没有日报时也显示待审",
            "preview_cn: 每日审批页面不应该依赖日报文件，日报缺失时也要直接读取当前 inbox 文件。",
            "summary_cn: 没有日报时也显示待审",
            "routed_by: tigermemory",
            "---",
            "",
            "live inbox fallback",
        ]),
        encoding="utf-8",
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest", headers=HOST, follow_redirects=False)

    assert response.status_code == 200
    assert "没有日报时也显示待审" in response.text
    assert '"live_fallback": true' in response.text


def test_api_digest_uses_deepseek_for_low_quality_live_inbox_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui.tm_memory_reflection, "INBOX_REVIEW_CACHE", tmp_path / ".tmp" / "inbox-review-cache.json")
    inbox = tmp_path / "inbox" / "2026-05-22-2246-cascade-production.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        "\n".join([
            "---",
            "owner: cascade",
            "status: draft",
            "updated: 2026-05-22",
            "title_cn: 标题",
            "preview_cn: 标题 中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入 元数据",
            "routed_by: tigermemory",
            "---",
            "",
            "# Routed memory 35",
            "2026-05-22 Windsurf Cascade post-response closeout summary. Topic: production",
            "Sanitized Cascade response: Rules used for this response.",
            "中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入教程，包含充值、令牌、模型分组和配置步骤。",
        ]),
        encoding="utf-8",
    )

    def fake_deepseek(_system, _user, **_kwargs):
        return True, {
            "title_cn": "中转 API 配置教程待审",
            "preview_cn": "这条收件箱记录整理了 Claude Opus 4.5 中转 API 的接入教程，包括充值、令牌创建、模型分组和 Claude Code 客户端配置。内容偏长期教程，审批时可考虑写入 Wiki。",
        }

    monkeypatch.setattr(tm_review_ui.tm_memory_reflection.tm_core, "_call_deepseek_json", fake_deepseek)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/digest/2026-05-22", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    row = data["digest"]["inbox_rows"][0]
    assert row["title_cn"] == "中转 API 配置教程待审"
    assert "Claude Opus 4.5 中转 API" in row["preview_cn"]
    assert row["cn_summary"] == "中转 API 配置教程待审"
    assert row["raw_summary"].startswith("# Routed memory 35")
    assert (tmp_path / ".tmp" / "inbox-review-cache.json").exists()


def test_pwa_manifest_is_public_and_uses_memory_ops(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/manifest.webmanifest", headers=HOST)

    assert response.status_code == 200
    assert '"name": "TigerMemory"' in response.text
    assert "/digest" in response.text
    assert "/static/tiger/tigermemory_tiger_logo_192.png" in response.text
    assert "/static/tiger/tigermemory_tiger_logo_512.png" in response.text


def test_service_worker_does_not_cache_dynamic_review_pages(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/service-worker.js", headers=HOST)

    assert response.status_code == 200
    assert "tigermemory-memory-ops-v83" in response.text
    assert "'/digest'" in response.text
    assert "request.mode === 'navigate'" in response.text
    assert "url.pathname.startsWith('/api/')" in response.text
    assert "url.pathname.startsWith('/digest')" in response.text
    assert "client.navigate(client.url)" in response.text
    assert response.headers["Cache-Control"].startswith("no-store")


def test_favicon_served_from_local_asset_without_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/favicon.ico", headers=HOST)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")


def test_sw_reset_page_clears_browser_cache(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/sw-reset", headers=HOST)

    assert response.status_code == 200
    assert "getRegistrations" in response.text
    assert "caches.keys" in response.text
    assert "location.replace('/digest')" in response.text
    assert response.headers["Cache-Control"].startswith("no-store")


def test_digest_html_and_api_are_no_store(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    page = client.get("/digest/2026-05-21", headers=HOST)
    api = client.get("/api/digest/2026-05-21", headers=HOST)

    assert page.headers["Cache-Control"].startswith("no-store")
    assert api.headers["Cache-Control"].startswith("no-store")


def test_dashboard_shell_pages_are_no_store(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    for path in ["/start", "/review", "/ledger", "/health", "/quality", "/canvas", "/settings"]:
        response = client.get(path, headers=HOST)
        assert response.status_code == 200
        assert response.headers["Cache-Control"].startswith("no-store")


def test_review_route_returns_page_shell_not_bare_json(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/review", headers=HOST)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<body" in response.text
    assert "detail" not in response.text[:120]


def test_ledger_route_is_dashboard_module_and_delegates_api(tmp_path, monkeypatch):
    ledger = tm_review_ui.tm_tigerledger_review
    assert ledger is not None
    db_path = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(ledger.core, "DB_PATH", db_path)
    monkeypatch.setattr(ledger.core, "DATA_DIR", tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    page = client.get("/ledger", headers=HOST)

    assert page.status_code == 200
    assert 'body data-page="ledger"' in page.text
    assert 'data-target-page="ledger"' in page.text
    assert "/static/dashboard-common.js" in page.text
    assert "/api/ledger/review" in page.text
    assert "记账审批" in page.text

    result = ledger.core.expense_write(
        action="record",
        kind="expense",
        amount=28.5,
        category="餐饮",
        occurred_at="2026-06-20T12:00:00+08:00",
        merchant="测试餐厅",
        note="午饭",
        payment_method="支付宝",
        tags=["alipay"],
        source_agent="expense-import-test",
        source_text="fixture",
    )
    assert result["ok"] is True
    row_id = int(result["id"])

    summary = client.get("/api/ledger/review/summary?month=2026-06", headers=HOST)
    entries = client.get("/api/ledger/review/entries?month=2026-06&status=pending", headers=HOST)
    approved = client.post(f"/api/ledger/review/entries/{row_id}/approve", headers=HOST)

    assert summary.status_code == 200
    assert summary.json()["status_counts"]["pending"] == 1
    assert entries.status_code == 200
    assert entries.json()["rows"][0]["id"] == row_id
    assert approved.status_code == 200
    assert approved.json()["entry"]["review_status"] == "approved"


def test_start_route_returns_beginner_shell(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/start", headers=HOST)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["Cache-Control"].startswith("no-store")
    assert 'id="root" data-tm-react-start' in response.text
    assert 'id="tm-start-data"' in response.text
    assert "__TM_START_JSON__" not in response.text
    assert "tm ask --offline" in response.text
    assert "tm agent status" in response.text
    assert "/static/react/start/assets/" in response.text
    assert "window.tmPages.start.init" not in response.text
    assert "/static/assets/tailwindcss.min.js" not in response.text


def test_start_agent_connect_status_api(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    import tigermemory_dashboard.server as dashboard_server

    monkeypatch.setattr(dashboard_server, "REPO_ROOT", tmp_path)
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    localappdata = tmp_path / "localappdata"
    programfiles = tmp_path / "programfiles"
    programfiles_x86 = tmp_path / "programfiles_x86"
    (home / ".codex").mkdir(parents=True)
    (home / ".vscode" / "extensions" / "github.copilot-1.0.0").mkdir(parents=True)
    (home / ".vscode" / "extensions" / "continue.continue-1.0.0").mkdir(parents=True)
    (appdata / "Trae").mkdir(parents=True)
    (appdata / "JetBrains" / "Toolbox").mkdir(parents=True)
    (appdata / "CodeGeeX").mkdir(parents=True)
    (localappdata / "Programs" / "Microsoft VS Code").mkdir(parents=True)
    (localappdata / "Programs" / "Microsoft VS Code" / "Code.exe").write_text("", encoding="utf-8")
    localappdata.mkdir(exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    monkeypatch.setenv("ProgramFiles", str(programfiles))
    monkeypatch.setenv("ProgramFiles(x86)", str(programfiles_x86))
    monkeypatch.setenv("PATH", "")
    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    (tmp_path / "tigermemory_cli.py").write_text("# cli\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/start/agent-connect/status", headers=HOST)
    data = response.json()

    assert response.status_code == 200
    assert data["action"] == "status"
    assert any(row["target"] == "codex" for row in data["targets"])
    installed = {row["id"]: row for row in data["installed_agents"]}
    expected_ids = {
        "codex",
        "claude-code",
        "gemini",
        "antigravity",
        "windsurf",
        "cursor",
        "opencode",
        "resonmix",
        "trae",
        "zcode",
        "vscode",
        "visual-studio",
        "jetbrains-toolbox",
        "intellij-idea",
        "pycharm",
        "android-studio",
        "eclipse",
        "notepadpp",
        "zed",
        "qoder",
        "kiro",
        "aider",
        "qwen-code",
        "github-copilot",
        "continue-dev",
        "cline",
        "roo-code",
        "tongyi-lingma",
        "baidu-comate",
        "tencent-codebuddy",
        "codegeex",
        "huawei-codearts",
    }
    assert expected_ids <= set(installed)
    assert installed["codex"]["installed"] is True
    assert installed["codex"]["support"] == "supported"
    assert installed["trae"]["installed"] is True
    assert installed["trae"]["support"] == "planned"
    assert installed["vscode"]["installed"] is True
    assert installed["jetbrains-toolbox"]["installed"] is True
    assert installed["github-copilot"]["installed"] is True
    assert installed["continue-dev"]["installed"] is True
    assert installed["codegeex"]["installed"] is True
    assert data["software_scan"]["installed_count"] == 7


def test_start_agent_connect_apply_api_writes_project_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    import tigermemory_dashboard.server as dashboard_server

    monkeypatch.setattr(dashboard_server, "REPO_ROOT", tmp_path)
    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    (tmp_path / "tigermemory_cli.py").write_text("# cli\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Existing\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/start/agent-connect/apply",
        headers=HOST,
        json={"targets": ["codex"], "dry_run": False},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert "tigermemory-agent-connect:start target=codex" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_start_page_i18n_keys_are_complete():
    html = (tm_review_ui.STATIC_DIR / "start.html").read_text(encoding="utf-8")
    data = json.loads((tm_review_ui.STATIC_DIR / "i18n.json").read_text(encoding="utf-8"))
    keys = {
        token.split('"', 1)[0]
        for token in html.split('data-i18n="')[1:]
        if token.split('"', 1)[0]
    }

    assert keys
    assert sorted(keys - set(data["zh"])) == []
    assert sorted(keys - set(data["en"])) == []


class _OnboardingVisualI18nAudit(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, bool]] = []
        self.unbound_cjk: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        attr = dict(attrs)
        parent = self.stack[-1] if self.stack else {"target_slide": False, "visual": False, "i18n": False}
        class_names = set(str(attr.get("class", "")).split())
        slide = str(attr.get("data-onboarding-slide", ""))
        target_slide = parent["target_slide"] or slide in {"3", "4", "5", "6"}
        visual = parent["visual"] or ("onboarding-visual" in class_names and target_slide)
        i18n = parent["i18n"] or bool(attr.get("data-i18n"))
        if tag not in self.VOID_TAGS:
            self.stack.append({"target_slide": target_slide, "visual": visual, "i18n": i18n})

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not self.stack:
            return
        current = self.stack[-1]
        text = data.strip()
        if current["target_slide"] and current["visual"] and text and any("\u3400" <= char <= "\u9fff" for char in text) and not current["i18n"]:
            self.unbound_cjk.append(text)


def test_start_late_slide_visual_text_is_i18n_bound():
    html = (tm_review_ui.STATIC_DIR / "start.html").read_text(encoding="utf-8")
    audit = _OnboardingVisualI18nAudit()
    audit.feed(html)

    assert audit.unbound_cjk == []


def test_start_dynamic_onboarding_i18n_keys_are_complete():
    data = json.loads((tm_review_ui.STATIC_DIR / "i18n.json").read_text(encoding="utf-8"))
    js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    required = {
        "start.agent.template_badge",
        "start.llm.preview.connected",
        "start.llm.preview.not_connected",
        "start.agent.preview.title",
        "start.agent.preview.ready",
        "start.agent.preview.actionable",
        "start.agent.software.summary",
        "start.agent.software.status.supported",
        "start.agent.software.status.planned",
        "start.agent.software.status.missing",
        "start.agent.software.note.supported",
        "start.agent.software.note.planned",
        "start.agent.software.note.missing",
        "start.agent.software.detected_empty",
        "start.agent.software.missing_toggle",
        "start.agent.software.empty",
        "start.llm.testing_button",
        "start.llm.testing_status",
        "start.llm.test_passed_status",
        "start.finish.ready_title.complete",
        "start.finish.ready_title.partial",
        "start.finish.check.local.title",
        "start.finish.check.local.desc",
        "start.finish.check.llm.title",
        "start.finish.check.llm.ok",
        "start.finish.check.llm.todo",
        "start.finish.check.agent.title",
        "start.finish.check.agent.counts",
        "start.finish.check.agent.todo",
        "start.finish.check.style.title",
        "start.finish.check.style.desc",
        "start.finish.check.ok",
        "start.finish.check.todo",
        *{f"start.step.name.{idx}" for idx in range(7)},
    }
    for depth in "abcd":
        for field in ("name", "chip", "answer", "note"):
            required.add(f"start.depth.preview.{depth}.{field}")

    assert all(key in js or key.startswith("start.step.name.") for key in required)
    assert sorted(required - set(data["zh"])) == []
    assert sorted(required - set(data["en"])) == []


def test_start_page_supports_direct_step_preview_and_motion_fallback():
    html = (tm_review_ui.STATIC_DIR / "start.html").read_text(encoding="utf-8")
    js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")

    assert "initialStepFromUrl" in js
    assert "tm-i18n-ready" in js
    assert "searchParams.set('step'" in js
    assert "dataset.stepCurrent" in js
    assert "dataset.startStep" in js
    assert "tmPanelRise" in html
    assert "tmElementRise" in html
    assert "tmSoftSweep" in html
    assert "prefers-reduced-motion: reduce" in html
    assert ".onboarding-slide > *" in html


def test_i18n_missing_keys_keep_html_fallback_text():
    i18n_js = (tm_review_ui.STATIC_DIR / "i18n.js").read_text(encoding="utf-8")

    assert "el.textContent = key" not in i18n_js
    assert 'el.removeAttribute("data-i18n-missing")' in i18n_js


def test_dashboard_git_helpers_do_not_climb_to_parent_repo(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", snapshot)

    dirty = tm_review_ui._worktree_dirty_state()

    assert tm_review_ui.git_sha() == "unknown"
    assert tm_review_ui._recent_agent_commits() == []
    assert tm_review_ui._get_opposite_sha(False) is None
    assert dirty["dirty"] is False
    assert dirty["error"] is None
    assert dirty["git_present"] is False


def test_dashboard_git_status_timeout_degrades_to_warning(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "DASHBOARD_GIT_STATUS_TIMEOUT", 0.01)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout=kwargs.get("timeout", 0.01), output="", stderr="")

    monkeypatch.setattr(tm_review_ui.subprocess, "run", fake_run)

    dirty = tm_review_ui._worktree_dirty_state()

    assert dirty["dirty"] is None
    assert dirty["status_count"] == 0
    assert dirty["sample"] == []
    assert dirty["git_present"] is True
    assert "timed out" in dirty["error"]


def test_dashboard_worktree_check_discloses_runtime_source(tmp_path, monkeypatch):
    class Proc:
        returncode = 0

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(args, timeout=0.5):
        if args[:3] == ["git", "branch", "--show-current"]:
            return Proc("master\n")
        if args[:3] == ["git", "rev-parse", "--short"]:
            return Proc("abcdef1\n")
        if args[:4] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
            return Proc("origin/master\n")
        if args[:3] == ["git", "rev-list", "--left-right"]:
            return Proc("0 0\n")
        return Proc("")

    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        tm_review_ui,
        "_worktree_dirty_state",
        lambda: {"dirty": False, "status_count": 0, "sample": [], "error": None},
    )
    monkeypatch.setattr(tm_review_ui, "_is_wsl_runtime", lambda: False)
    monkeypatch.setattr(tm_review_ui.tm_core, "tigermemory_profile", lambda: "local")
    monkeypatch.setattr(tm_review_ui, "_run", fake_run)

    check = tm_review_ui._dashboard_worktree_check()

    assert check["repo_root"] == str(tmp_path)
    assert check["runtime_profile"] == "local"
    assert check["runtime_side"] == "Windows"
    assert check["status"] == "ok"


def test_ensure_write_ready_fast_forwards_when_only_behind(monkeypatch):
    class Proc:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    calls: list[tuple[str, ...]] = []

    def fake_run_checked(args, timeout=0.5):
        cmd = tuple(args)
        calls.append(cmd)
        if cmd == ("git", "status", "--short"):
            return Proc("")
        if cmd == ("git", "fetch", "origin", "master"):
            return Proc("")
        if cmd == ("git", "rev-parse", "HEAD"):
            return Proc("local\n")
        if cmd == ("git", "rev-parse", "origin/master"):
            return Proc("remote\n")
        if cmd == ("git", "rev-list", "--left-right", "--count", "HEAD...origin/master"):
            return Proc("0\t1\n")
        if cmd == ("git", "pull", "--ff-only", "origin", "master"):
            return Proc("updated\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(tm_review_ui, "_run_checked", fake_run_checked)

    tm_review_ui.ensure_write_ready()

    assert ("git", "pull", "--ff-only", "origin", "master") in calls


def test_ensure_write_ready_blocks_unpushed_local_commits(monkeypatch):
    class Proc:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def fake_run_checked(args, timeout=0.5):
        cmd = tuple(args)
        if cmd == ("git", "status", "--short"):
            return Proc("")
        if cmd == ("git", "fetch", "origin", "master"):
            return Proc("")
        if cmd == ("git", "rev-parse", "HEAD"):
            return Proc("local\n")
        if cmd == ("git", "rev-parse", "origin/master"):
            return Proc("remote\n")
        if cmd == ("git", "rev-list", "--left-right", "--count", "HEAD...origin/master"):
            return Proc("1\t0\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(tm_review_ui, "_run_checked", fake_run_checked)

    try:
        tm_review_ui.ensure_write_ready()
    except RuntimeError as exc:
        assert "local commit(s) not pushed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_ensure_write_ready_blocks_diverged_history(monkeypatch):
    class Proc:
        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def fake_run_checked(args, timeout=0.5):
        cmd = tuple(args)
        if cmd == ("git", "status", "--short"):
            return Proc("")
        if cmd == ("git", "fetch", "origin", "master"):
            return Proc("")
        if cmd == ("git", "rev-parse", "HEAD"):
            return Proc("local\n")
        if cmd == ("git", "rev-parse", "origin/master"):
            return Proc("remote\n")
        if cmd == ("git", "rev-list", "--left-right", "--count", "HEAD...origin/master"):
            return Proc("1\t1\n")
        raise AssertionError(cmd)

    monkeypatch.setattr(tm_review_ui, "_run_checked", fake_run_checked)

    try:
        tm_review_ui.ensure_write_ready()
    except RuntimeError as exc:
        assert "diverged" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_health_worktree_renderer_discloses_source_path():
    pages_js = (REPO_ROOT / "tools" / "static" / "dashboard-pages.js").read_text(encoding="utf-8")

    assert "worktree-root" in pages_js
    assert "check.repo_root" in pages_js
    assert "check.runtime_side" in pages_js


def test_agent_status_degrades_when_connect_helper_missing(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tm_agent_connect":
            raise ImportError("missing helper")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/status", headers=HOST)
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["cursor"]["connected"] is False
    assert data["claude"]["connected"] is False
    assert "helper" in data["warning"]


def test_digest_page_embeds_live_data_without_empty_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    page = client.get("/digest/2026-05-21", headers=HOST)
    api = client.get("/api/digest/2026-05-21", headers=HOST)

    assert '"loading": true' not in page.text
    assert "正在加载每日审批数据" not in page.text
    assert '"mem0": 2' in page.text
    assert "window.tmPages.daily.init" in page.text
    assert api.json()["digest"]["counts"]["mem0"] == 2
    assert api.json()["digest"].get("loading") is not True


def test_dashboard_data_pages_return_fast_shells(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "dashboard_health_summary", lambda: (_ for _ in ()).throw(RuntimeError("slow health should be api-only")))
    monkeypatch.setattr(tm_review_ui, "dashboard_memory_quality", lambda date=None: (_ for _ in ()).throw(RuntimeError("slow quality should be api-only")))
    monkeypatch.setattr(tm_review_ui, "get_user_preferences", lambda: (_ for _ in ()).throw(RuntimeError("slow prefs should be api-only")))
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    health = client.get("/health", headers=HOST)
    quality = client.get("/quality", headers=HOST)
    settings = client.get("/settings", headers=HOST)
    start = client.get("/start", headers=HOST)

    assert start.status_code == 200
    assert 'data-tm-react-start' in start.text
    assert health.status_code == 200
    assert '"loading": true' in health.text
    assert 'data-tm-react-health' in health.text
    assert "/static/dashboard-pages.js" not in health.text
    assert "/static/react/health/assets/" in health.text
    assert quality.status_code == 200
    assert '"loading": true' in quality.text
    assert 'data-tm-react-quality' in quality.text
    assert "/static/dashboard-pages.js" not in quality.text
    assert "/static/react/quality/assets/" in quality.text
    assert settings.status_code == 200
    assert '"loading": true' in settings.text
    assert 'data-tm-react-settings' in settings.text
    assert "/static/dashboard-pages.js" not in settings.text
    assert "/static/react/settings/assets/" in settings.text


def test_self_evolution_page_returns_fast_loading_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        tm_review_ui,
        "self_evolution_data",
        lambda date=None: (_ for _ in ()).throw(RuntimeError("slow self-evolution should be api-only")),
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    page = client.get("/self-evolution", headers=HOST)

    assert page.status_code == 200
    assert '"loading": true' in page.text
    assert "window.tmPages.selfEvolution.init" in page.text
    assert "self-evolution-data" in page.text


def test_self_evolution_data_uses_dedicated_cache_ttl(monkeypatch):
    seen = {}

    def fake_cache_get(key, ttl):
        seen["key"] = key
        seen["ttl"] = ttl
        return {"ok": True, "date": "2026-05-21", "cached": True}, True

    monkeypatch.setattr(tm_review_ui, "_run_cache_get", fake_cache_get)

    data = tm_review_ui.self_evolution_data("2026-05-21")

    assert data["cached"] is True
    assert seen["key"] == "api:self-evolution:2026-05-21"
    assert seen["ttl"] == tm_review_ui.SELF_EVOLUTION_CACHE_TTL
    assert tm_review_ui.SELF_EVOLUTION_CACHE_TTL > tm_review_ui.API_CACHE_TTL


def test_self_evolution_shell_reuses_cached_payload(monkeypatch):
    cached = {"ok": True, "date": "2026-05-21", "cached": True, "loading": False}
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-21")
    monkeypatch.setattr(tm_review_ui, "_run_cache_get", lambda key, ttl: (cached, True))

    shell = tm_review_ui._self_evolution_shell()

    assert shell == cached


def test_react_dashboard_theme_exposes_tailwind_tm_tokens():
    css_path = (
        REPO_ROOT
        / "packages"
        / "tigermemory-dashboard-ui"
        / "src"
        / "styles.css"
    )
    css = css_path.read_text(encoding="utf-8")

    assert "@theme" in css
    for token in [
        "--color-tm-bg",
        "--color-tm-card",
        "--color-tm-card-alt",
        "--color-tm-overlay",
        "--color-tm-border",
        "--color-tm-border-divider",
        "--color-tm-primary",
        "--color-tm-secondary",
        "--color-tm-tertiary",
        "--color-tm-accent",
        "--color-tm-ok-bg",
        "--color-tm-warn-bg",
        "--color-tm-fail-bg",
    ]:
        assert token in css


def test_react_dashboard_pages_use_shared_shell_components():
    ui_src = REPO_ROOT / "packages" / "tigermemory-dashboard-ui" / "src"
    shell = ui_src / "components" / "DashboardShell.tsx"
    start = (ui_src / "main.tsx").read_text(encoding="utf-8")
    digest = (ui_src / "digest" / "main.tsx").read_text(encoding="utf-8")
    health = (ui_src / "health" / "main.tsx").read_text(encoding="utf-8")
    quality = (ui_src / "quality" / "main.tsx").read_text(encoding="utf-8")
    settings = (ui_src / "settings" / "main.tsx").read_text(encoding="utf-8")

    assert shell.exists()
    shell_text = shell.read_text(encoding="utf-8")
    for exported in ["DashboardShell", "DashboardHeader", "DashboardCard", "dashboardNavItems"]:
        assert f"export function {exported}" in shell_text or f"export const {exported}" in shell_text

    assert "./components/DashboardShell" in start
    assert "../components/DashboardShell" in digest
    assert "../components/DashboardShell" in health
    assert "../components/DashboardShell" in quality
    assert "../components/DashboardShell" in settings
    assert "const nav =" not in start
    assert "const NAV =" not in digest
    assert "const NAV =" not in health
    assert "const NAV =" not in quality
    assert "const NAV =" not in settings


def test_dashboard_modularization_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "dashboard_health_summary", lambda: {"ok": True})
    monkeypatch.setattr(tm_review_ui, "dashboard_memory_quality", lambda date=None: {"ok": True})
    monkeypatch.setattr(tm_review_ui, "get_user_preferences", lambda: {"ok": True})
    monkeypatch.setattr(tm_review_ui, "git_sha", lambda: "abc123")

    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    # dashboard shell pages — /digest、/health、/quality、/settings 已迁移为 React island，不再引用旧 dashboard-common.js。
    legacy_pages = ["/agent-tools"]
    react_pages = ["/digest/2026-05-21", "/health", "/quality", "/settings"]
    pages = react_pages + legacy_pages
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)

    for route in pages:
        res = client.get(route, headers=HOST)
        assert res.status_code == 200

    # 1. dashboard-common.js 仍被未迁移页引用
    for route in legacy_pages:
        res = client.get(route, headers=HOST)
        assert "/static/dashboard-common.js" in res.text

    # 2. dashboard-pages.js 被 agent-tools 引用；
    # /start、/digest、/health、/quality 与 /settings 均已迁移为 React island，不再依赖旧 dashboard-pages.js 控制器。
    digest = client.get("/digest/2026-05-21", headers=HOST)
    start = client.get("/start", headers=HOST)
    health = client.get("/health", headers=HOST)
    quality = client.get("/quality", headers=HOST)
    settings = client.get("/settings", headers=HOST)
    agent_tools = client.get("/agent-tools", headers=HOST)
    assert "/static/dashboard-pages.js" not in digest.text
    assert "/static/react/digest/assets/" in digest.text
    assert "/static/dashboard-pages.js" not in start.text
    assert "/static/react/start/assets/" in start.text
    assert "/static/dashboard-pages.js" not in health.text
    assert "/static/react/health/assets/" in health.text
    assert "/static/dashboard-pages.js" not in quality.text
    assert "/static/react/quality/assets/" in quality.text
    assert 'data-tm-react-quality' in quality.text
    assert "/static/dashboard-pages.js" not in settings.text
    assert "/static/react/settings/assets/" in settings.text
    assert 'data-tm-react-settings' in settings.text
    assert "/static/dashboard-pages.js" in agent_tools.text

    # 3. service-worker.js 缓存新增 JS
    sw_res = client.get("/service-worker.js", headers=HOST)
    assert sw_res.status_code == 200
    assert "/static/dashboard-common.js" in sw_res.text
    assert "/static/dashboard-pages.js" in sw_res.text

    # 4. health/quality/settings/agent-tools 页面不再直接出现 inline 定义；
    # /start 与 /digest 页面均由 React bundle 接管。
    assert "data-tm-react-start" in start.text
    assert "function copyCommand" not in start.text
    assert "data-tm-react-digest" in digest.text
    assert "window.tmPages.daily.init" not in digest.text
    assert "function renderInbox" not in digest.text
    assert "function openWikiModal" not in digest.text
    assert "async function fetchDigest" not in digest.text
    assert "setInterval(fetchHealth" not in health.text
    assert "setInterval(fetchQuality" not in quality.text
    assert "window.tmPages.quality.init" not in quality.text
    assert "window.tmPages.settings.init" not in settings.text
    assert "function renderDepth" not in settings.text
    assert "function renderChips" not in settings.text
    assert "async function fetchSettings" not in settings.text
    assert "window.tmPages.agentTools.init" in agent_tools.text
    assert "async function checkAgentStatus" not in agent_tools.text
    assert "async function runDoctor" not in agent_tools.text
    assert "async function runEval" not in agent_tools.text

    # 5. dashboard-pages.js 仍保留 legacy start fallback 与其他模块控制器，
    # 但正式 /start 页面优先使用 React 构建产物。
    js_content = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    assert "clearInterval" in js_content
    assert "window.tmPages.start" in js_content
    assert "window.tmPages.settings" in js_content
    assert "window.tmPages.daily" in js_content
    assert "window.tmPages.agentTools" in js_content
    assert "AbortController" in js_content
    assert "this.abortController.abort()" in js_content

    # 6. dashboard-common.js 中存在 window.tmDashboardRouter 及其初始化和导航拦截逻辑
    common_js = (tm_review_ui.STATIC_DIR / "dashboard-common.js").read_text(encoding="utf-8")
    assert "window.tmDashboardRouter" in common_js
    assert "path === '/start'" in common_js
    assert "FULL_DOCUMENT_ROUTES = new Set(['/start'" in common_js
    assert "syncDocumentTitle(doc)" in common_js
    assert "currentTitle.removeAttribute(attr.name)" in common_js
    assert "nextTitle.attributes" in common_js
    assert "isFullDocumentRoute(url)" in common_js
    assert "isFullDocumentPath(parsed.pathname)" in common_js
    assert "window.location.reload()" in common_js
    assert "tmDashboardRouter = {" in common_js
    assert "navigateTo(" in common_js
    assert "window.history.pushState" in common_js
    assert "cache: {" in common_js
    assert "clearCache(" in common_js
    assert "updateRefreshIndicator(" in common_js
    assert "PREFETCH_ROUTES = []" in common_js
    assert "if (!PREFETCH_ROUTES.length) return false;" in common_js
    assert "scheduleIdlePrefetch(" in common_js
    assert "prefetchDashboardRoutes(" in common_js
    assert "PREFETCH_TIMEOUT_MS = 20000" in common_js
    assert "X-TigerMemory-Prefetch" in common_js

    server_content = pathlib.Path(tm_review_ui.__file__).read_text(encoding="utf-8")
    assert "DASHBOARD_PAGE_CACHE_TTL" in server_content
    assert "CRON_INTAKE_CACHE_TTL" in server_content
    assert "cached_cron_intake_data(" in server_content
    assert "page:digest:" in server_content
    assert "page:canvas:" in server_content
    assert "fetchBackground(" in common_js
    assert "tm-lang-change" in common_js
    assert "urlObj.pathname + urlObj.search" in common_js
    assert "currentUrlObj.pathname + currentUrlObj.search" in common_js

    # 7. i18n.json 中存在刚刚缓存和正在刷新的翻译字典键值对
    i18n_json = (tm_review_ui.STATIC_DIR / "i18n.json").read_text(encoding="utf-8")
    assert "header.cached" in i18n_json
    assert "header.refreshing" in i18n_json





def test_digest_react_source_keeps_daily_review_actions():
    source = (REPO_ROOT / "packages" / "tigermemory-dashboard-ui" / "src" / "digest" / "main.tsx").read_text(encoding="utf-8")

    assert "const copy = {" in source
    assert "window.navigator.language" in source
    assert "WikiTargetModal" in source
    assert "wiki_target" in source
    assert "partition" in source
    assert "slug" in source
    assert "openWikiProposalBatch" in source
    assert "runWikiLedgerAction" in source
    assert "investment_archive" in source
    assert "markCompletedIfPathGoneAfterError" in source
    assert "fetch(`/api/digest/${date}`" in source
    assert "按每条提案自己的推荐目标写入" in source


def test_review_write_ready_allows_unstaged_foreign_dirty():
    status = "\n".join(
        [
            " M wiki/investment/decision-log/600887.SH-2026-05.md",
            "?? wiki/investment/decision-log/portfolio-fast-scan-2026-05-22.md",
        ]
    )

    assert tm_review_ui._blocking_dirty_paths(status) == []


def test_review_write_ready_blocks_staged_or_meta_dirty():
    status = "\n".join(
        [
            "M  tools/tm_review_ui.py",
            " M AGENTS.md",
            "UU wiki/operations/inbox-archive/2026-05-22.md",
        ]
    )

    blocked = tm_review_ui._blocking_dirty_paths(status)

    assert "M  tools/tm_review_ui.py" in blocked
    assert " M AGENTS.md" in blocked
    assert "UU wiki/operations/inbox-archive/2026-05-22.md" in blocked


def test_health_summary_endpoint_uses_agent_doctor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tm_review_ui.tm_agent_doctor,
        "run_agent_doctor",
        lambda **_kwargs: {"status": "ok", "checks": [], "summary": {"ok_count": 0}},
    )
    monkeypatch.setattr(tm_review_ui, "git_sha", lambda: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/health/summary", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["dashboard"]["port"] == tm_review_ui.PORT
    assert data["dashboard"]["git_sha"] == "abc123"
    assert [service["name"] for service in data["services"]] == ["Dashboard", "tm-http", "tm-mcp", "Mem0", "OpenClaw"]


def test_dashboard_health_summary_marks_advanced_services_optional_in_local_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", "local")
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "_worktree_dirty_state", lambda: {"dirty": False, "status_count": 0, "sample": [], "error": None})
    monkeypatch.setattr(tm_review_ui, "_probe_url", lambda *_args, **_kwargs: {"ok": False, "latency_ms": 1, "error": "connection refused"})
    monkeypatch.setattr(
        tm_review_ui,
        "_dashboard_fast_agent_doctor",
        lambda: {
            "status": "ok",
            "checks": [
                {"name": "tm_http", "status": "warn", "latency_ms": 1},
                {"name": "mem0_api", "status": "warn", "latency_ms": 1},
            ],
        },
    )

    data = tm_review_ui.dashboard_health_summary()
    statuses = {service["name"]: service["status"] for service in data["services"]}

    assert data["dashboard"]["runtime_profile"] == "local"
    assert statuses["tm-http"] == "optional"
    assert statuses["tm-mcp"] == "optional"
    assert statuses["Mem0"] == "optional"
    assert statuses["OpenClaw"] == "optional"
    assert not any("处于告警状态" in warning for warning in data["warnings"])


def test_health_page_uses_real_template_not_json_page(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tm_review_ui,
        "dashboard_health_summary",
        lambda: {
            "ok": True,
            "generated_at": "2026-05-21T20:30:15+08:00",
            "dashboard": {"version": "0.2.0", "git_sha": "abc123", "port": 1998},
            "services": [
                {"name": "Dashboard", "icon": "layout-dashboard", "port": ":1998", "status": "ok", "status_label": "正常", "detail": "v0.2.0"},
                {"name": "tm-http", "icon": "server", "port": ":8790", "status": "ok", "status_label": "正常", "latency_ms": 514},
                {"name": "tm-mcp", "icon": "network", "port": ":9766", "status": "ok", "status_label": "正常"},
                {"name": "Mem0", "icon": "database", "port": ":8765", "status": "ok", "status_label": "正常"},
                {"name": "OpenClaw", "icon": "message-square", "port": "socket", "status": "warn", "status_label": "待接入"},
            ],
            "agent_doctor": {"checks": [], "summary": {"ok_count": 5, "warn_count": 0, "fail_count": 0}},
            "recent_commits": ["abc123 [codex] update: fixture"],
            "daily_digest": {"date": "2026-05-21", "path": "wiki/operations/daily-memory-digest-2026-05-21.md", "exists": True},
        },
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/health", headers=HOST)

    assert response.status_code == 200
    assert "tm-health-data" in response.text
    assert 'data-tm-react-health' in response.text
    assert "/static/react/health/assets/" in response.text
    assert "<pre" not in response.text
    assert "bg-zinc-950" not in response.text
    assert "bg-black" not in response.text


def test_dashboard_pages_share_identical_header(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    monkeypatch.setattr(
        tm_review_ui,
        "dashboard_health_summary",
        lambda: {
            "ok": True,
            "generated_at": "2026-05-21T20:30:15+08:00",
            "dashboard": {"version": "0.2.0", "git_sha": "abc123", "port": 1998},
            "services": [],
            "agent_doctor": {"checks": [], "summary": {"ok_count": 0}},
            "recent_commits": [],
            "daily_digest": {"date": "2026-05-21", "path": "wiki/operations/daily-memory-digest-2026-05-21.md", "exists": True},
        },
    )
    monkeypatch.setattr(tm_review_ui, "dashboard_memory_quality", lambda date=None: {"ok": True, "date": date or "2026-05-21"})
    monkeypatch.setattr(tm_review_ui, "get_user_preferences", lambda: {"ok": True, "preferences": {}})
    monkeypatch.setattr(tm_review_ui, "git_sha", lambda: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    react_responses = {
        "digest": client.get("/digest/2026-05-21", headers=HOST),
        "health": client.get("/health", headers=HOST),
        "quality": client.get("/quality", headers=HOST),
        "settings": client.get("/settings", headers=HOST),
        "start": client.get("/start", headers=HOST),
    }
    legacy_responses = {
        "agent-tools": client.get("/agent-tools", headers=HOST),
    }

    for page, response in react_responses.items():
        assert response.status_code == 200
        assert "/static/dashboard-pages.js" not in response.text
        assert "/static/react/" in response.text
        assert "data-tm-react-" in response.text
    for page, response in legacy_responses.items():
        assert response.status_code == 200
        assert "/static/dashboard-pages.js" in response.text
        assert "/static/assets/tailwindcss.min.js" in response.text, f"{page} missing local tailwind"
        assert "/static/assets/lucide.min.js" in response.text, f"{page} missing local lucide"
        assert "https://cdn.tailwindcss.com" not in response.text, f"{page} still references cdn.tailwindcss"
        assert "https://unpkg.com" not in response.text, f"{page} still references unpkg"


def test_dashboard_transition_css_is_shared():
    css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert "body.tm-page-ready main" in css
    assert "tmContentIn" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "body.tm-page-leaving main" in css
    assert "filter: blur" not in css.replace("backdrop-filter", "")


def test_quality_and_settings_no_longer_use_raw_json_page(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "dashboard_memory_quality", lambda date=None: {"ok": True, "date": date})
    monkeypatch.setattr(tm_review_ui, "get_user_preferences", lambda: {"ok": True, "preferences": {}})
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    quality = client.get("/quality", headers=HOST)
    settings = client.get("/settings", headers=HOST)

    assert "tm-quality-data" in quality.text
    assert 'data-tm-react-quality' in quality.text
    assert "/static/react/quality/assets/" in quality.text
    assert "tm-settings-data" in settings.text
    assert 'data-tm-react-settings' in settings.text
    assert "/static/react/settings/assets/" in settings.text
    assert "/static/dashboard-pages.js" not in quality.text
    assert "/static/dashboard-pages.js" not in settings.text
    assert "沟通规则执行度" not in quality.text
    combined = quality.text + settings.text
    assert "阶段 2 占位" not in combined
    assert "bg-zinc-950" not in combined
    assert "bg-black" not in combined


def test_quality_memory_endpoint_reports_trace_latency(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    monkeypatch.setattr(tm_review_ui.tm_answer_trace, "load_trace_rows", lambda **_kwargs: ([{"status": "ok"}], []))
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "summarize_rows",
        lambda rows, invalid, latest=10: {"row_count": len(rows), "duration_ms": {"p50": 10, "p95": 20}},
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/quality/memory?date=2026-05-21", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["trace_latency_supported"] is True
    assert data["trace_summary"]["duration_ms"]["p95"] == 20


def test_quality_memory_endpoint_accepts_range_param(tmp_path, monkeypatch):
    calls: list[tuple[str | None, str | None]] = []

    def fake_quality(date=None, range_key=None):
        calls.append((date, range_key))
        return {"ok": True, "date": date, "range": {"key": range_key}}

    monkeypatch.setattr(tm_review_ui, "dashboard_memory_quality", fake_quality)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/quality/memory?date=2026-06-10&range=30d", headers=HOST)

    assert response.status_code == 200
    assert response.json()["range"]["key"] == "30d"
    assert calls == [("2026-06-10", "30d")]


def test_development_supervisor_status_api_is_read_only(tmp_path, monkeypatch):
    ledger = tmp_path / "wiki" / "operations" / "development-supervisor-ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("# Ledger\n\n- 2026-06-18 status=success channel=claude-official-review\n", encoding="utf-8")
    archive = tmp_path / "sources" / "internal-analysis" / "development-reviews"
    today_dir = archive / tm_review_ui.today()
    today_dir.mkdir(parents=True)
    (today_dir / "review.md").write_text("---\nchannel: claude-official-review\n---\nVerdict: pass\n", encoding="utf-8")
    launcher = tmp_path / "official.ps1"
    launcher.write_text("# ok\n", encoding="utf-8")
    api_exe = tmp_path / "claude.exe"
    api_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(tm_dev_supervisor_review, "ARCHIVE_ROOT", archive)
    monkeypatch.setattr(tm_dev_supervisor_review, "LEDGER_PATH", ledger)
    monkeypatch.setattr(tm_dev_supervisor_review, "OFFICIAL_LAUNCHER", launcher)
    monkeypatch.setattr(tm_dev_supervisor_review, "API_TEST_EXE", api_exe)
    monkeypatch.setattr(tm_dev_supervisor_review, "SUPERVISOR_STATE_DIR", tmp_path / ".supervisor")

    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)
    response = client.get("/api/development-supervisor/status", headers=HOST)

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["channels"]["formal_default"] == "claude-official-review"
    assert data["runtime"]["windows_launcher_check"] in {
        "checked_in_current_process",
        "dashboard_runs_outside_windows; launcher paths are checked by the Windows wrapper",
    }
    assert data["exists"]["ledger"] is True
    assert data["archive_count"] == 1
    assert data["latest_archives"][0].endswith("review.md")
    assert any("tm_stage_accept.py" in step for step in data["next_steps"])


def test_settings_preferences_round_trip_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "PREFS_DB", tmp_path / "prefs.sqlite")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    update = client.post(
        "/api/settings/preferences",
        headers=HOST,
        json={"preferences": {"communication_depth": "C"}, "propose_wiki": False},
    )
    readback = client.get("/api/settings/preferences", headers=HOST)

    assert update.json()["ok"] is True
    assert readback.json()["preferences"]["communication_depth"] == "C"


def test_inbox_archive_moves_file_and_returns_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{inbox.name}", "action": "archive"})

    data = response.json()
    assert data["ok"] is True
    assert data["commit_sha"] == "abc123"
    assert not inbox.exists()
    assert data["archived_to"] == "wiki/operations/inbox-archive/2026-05-01.md"
    assert data["source_cache_to"] == f".tmp/inbox-archive-sources/2026-05-01/{inbox.name}"
    archive_page = tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-01.md"
    assert archive_page.exists()
    page_text = archive_page.read_text(encoding="utf-8")
    assert "测试归档标题" in page_text
    assert "这是一条用于验证归档摘要页的中文摘要" in page_text
    assert f"<!-- inbox-archive-entry: inbox/{inbox.name} -->" in page_text
    assert (tmp_path / ".tmp" / "inbox-archive-sources" / "2026-05-01" / inbox.name).exists()


def test_investment_archive_writes_investment_archive_page_and_removes_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_investment_wiki_proposal(tmp_path)
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{inbox.name}"], "action": "investment_archive"},
    )

    data = response.json()
    assert data["ok"] is True
    assert data["commit_sha"] == "abc123"
    assert not inbox.exists()
    archive_page = tmp_path / "wiki" / "investment" / "proposal-archive" / "2026-06-09.md"
    assert archive_page.exists()
    page_text = archive_page.read_text(encoding="utf-8")
    assert "贵州茅台正式报告" in page_text
    assert "投资分类：PDF/长报告" in page_text
    assert "建议 Wiki：wiki/investment/research/600519.SH.md" in page_text
    assert "保留原件；只复制/追加" in page_text
    assert f"<!-- investment-proposal-archive-entry: inbox/{inbox.name} -->" in page_text
    assert len(commits) == 1
    assert set(commits[0]) == {
        f"inbox/{inbox.name}",
        "wiki/investment/proposal-archive/2026-06-09.md",
        "wiki/operations/inbox-archive/2026-06-09.md",
    }


def test_inbox_archive_body_fallback_extracts_clean_summary_section(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = tmp_path / "inbox" / "2026-05-06-0046-claude-code-systems.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        "\n".join(
            [
                "---",
                "owner: claude-code",
                "status: draft",
                "updated: 2026-05-06",
                "routed_by: tigermemory",
                "---",
                "",
                "# OpenClaw 版本更新 2026.4.15 -> 2026.5.4",
                "",
                "## 摘要",
                "",
                "OpenClaw Gateway 从 2026.4.15 更新至最新稳定版 2026.5.4，服务已正常重启并验证通过。",
                "",
                "## 已验证现状",
                "",
                "- **更新前版本**: 2026.4.15",
            ]
        ),
        encoding="utf-8",
    )

    result = tm_review_tools.archive_inbox_file_to_summary(str(inbox))

    assert result["ok"] is True
    assert result["summary_source"] == "body_summary_section"
    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-06.md").read_text(encoding="utf-8")
    assert "### OpenClaw 版本更新 2026.4.15 -> 2026.5.4" in page_text
    assert "**摘要**：OpenClaw Gateway 从 2026.4.15 更新至最新稳定版 2026.5.4，服务已正常重启并验证通过。" in page_text
    assert "# OpenClaw 版本更新 2026.4.15 -> 2026.5.4 ## 摘要" not in page_text


def test_inbox_archive_uses_deepseek_for_low_quality_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = tmp_path / "inbox" / "2026-05-07-1530-codex-systems.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        "\n".join(
            [
                "---",
                "owner: codex",
                "status: draft",
                "updated: 2026-05-07",
                "routed_by: tigermemory",
                "---",
                "",
                "# Routed memory 20",
                "",
                '{"id": 1, "type": "expense_tracker_record", "amount": 35, "category": "餐饮", "desc": "午饭测试", "date": "2026-05-07"}',
            ]
        ),
        encoding="utf-8",
    )

    def fake_deepseek(_system, _user, **_kwargs):
        return True, {
            "title": "午饭测试记账记录",
            "summary": "2026-05-07 写入了一条餐饮类午饭测试记账记录，金额 35 元。该条目属于开发或数据链路测试信息，归档后保留标题和摘要即可。",
        }

    monkeypatch.setattr(tm_review_tools.tm_core, "_call_deepseek_json", fake_deepseek)

    result = tm_review_tools.archive_inbox_file_to_summary(str(inbox))

    assert result["ok"] is True
    assert result["summary_source"] == "deepseek"
    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-07.md").read_text(encoding="utf-8")
    assert "### 午饭测试记账记录" in page_text
    assert "**摘要**：2026-05-07 写入了一条餐饮类午饭测试记账记录" in page_text


def test_inbox_archive_uses_deepseek_for_placeholder_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = tmp_path / "inbox" / "2026-05-21-1832-codex-systems.md"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(
        "\n".join(
            [
                "---",
                "owner: codex",
                "status: draft",
                "updated: 2026-05-21",
                "title_cn: 未提供中文摘要：请写入 agent 在正文首行补一句中文概括。",
                "preview_cn: 未提供中文摘要：请写入 agent 在正文首行补一句中文概括。",
                "routed_by: tigermemory",
                "---",
                "",
                "# Dashboard PWA 验收",
                "",
                "Dashboard PWA 已完成本地首屏安装验证，healthz 返回 git_sha，下一步等待手机端 Tailscale 验证。",
            ]
        ),
        encoding="utf-8",
    )

    def fake_deepseek(_system, _user, **_kwargs):
        return True, {
            "title": "Dashboard PWA 本地验收完成",
            "summary": "2026-05-21 记录 Dashboard PWA 的本地验收结果：首屏安装可用，healthz 能返回当前 git_sha，后续还需要手机端通过 Tailscale 做访问验证。",
        }

    monkeypatch.setattr(tm_review_tools.tm_core, "_call_deepseek_json", fake_deepseek)

    result = tm_review_tools.archive_inbox_file_to_summary(str(inbox))

    assert result["ok"] is True
    assert result["summary_source"] == "deepseek"
    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-21.md").read_text(encoding="utf-8")
    assert "未提供中文摘要" not in page_text
    assert "### Dashboard PWA 本地验收完成" in page_text


def test_inbox_archive_commit_paths_exclude_raw_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path)
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{inbox.name}", "action": "archive"})

    assert response.json()["ok"] is True
    assert commits == [[f"inbox/{inbox.name}", "wiki/operations/inbox-archive/2026-05-01.md"]]
    assert all(".tmp/inbox-archive-sources" not in path for path in commits[0])


def test_committable_paths_skip_missing_untracked_source(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    (tmp_path / ".git").mkdir()
    archive_page = tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-01.md"
    archive_page.parent.mkdir(parents=True)
    archive_page.write_text("archive", encoding="utf-8")

    def fake_run(cmd, **_kwargs):
        return type("Proc", (), {"returncode": 1})()

    monkeypatch.setattr(tm_review_ui, "_run", fake_run)

    assert tm_review_ui._committable_paths([
        "inbox/2026-05-01-1200-codex-systems.md",
        "wiki/operations/inbox-archive/2026-05-01.md",
    ]) == ["wiki/operations/inbox-archive/2026-05-01.md"]


def test_locked_write_action_clears_api_cache(tmp_path, monkeypatch):
    event_root = tmp_path / "runtime-events"
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(event_root))
    with tm_review_ui._API_CACHE_LOCK:
        tm_review_ui._API_CACHE["api:digest:test"] = {"payload": {"stale": True}}

    result = tm_review_ui._locked_write_action(lambda: {"ok": True})

    assert result == {"ok": True}
    with tm_review_ui._API_CACHE_LOCK:
        assert tm_review_ui._API_CACHE == {}
    events = tm_review_ui.tm_runtime_events.load_events(
        dates=[tm_review_ui.tm_runtime_events._date_key()],
        event_root=event_root,
    )
    assert events[-1]["event_type"] == "dashboard_write_action"
    assert events[-1]["service"] == "tm-dashboard"


def test_inbox_archive_upserts_duplicate_source_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    assert client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{first.name}", "action": "archive"}).json()["ok"] is True
    second = _write_inbox(tmp_path)
    assert client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{second.name}", "action": "archive"}).json()["ok"] is True

    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-01.md").read_text(encoding="utf-8")
    assert page_text.count(f"<!-- inbox-archive-entry: inbox/{first.name} -->") == 1


def test_inbox_promote_mem0_uses_review_tool_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path)
    calls: dict[str, object] = {}

    def fake_promote(fact, topic=None):
        calls["fact"] = fact
        calls["topic"] = topic
        return {"ok": True, "memory_id": "mem-1"}

    monkeypatch.setattr(tm_review_tools, "execute_promote_mem0", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{inbox.name}", "action": "promote_mem0"})

    data = response.json()
    assert data["ok"] is True
    assert calls["fact"]["topic"] == "systems"
    assert calls["topic"] is None
    assert not inbox.exists()
    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-01.md").read_text(encoding="utf-8")
    assert "- 实际动作：promote_mem0" in page_text


def test_inbox_promote_wiki_uses_selected_target_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path)
    calls: dict[str, object] = {}
    commits: list[list[str]] = []

    def fake_promote(fact, partition, slug, commit=True):
        calls["fact"] = fact
        calls["partition"] = partition
        calls["slug"] = slug
        calls["commit"] = commit
        return {"ok": True, "changed_paths": [f"wiki/{partition}/{slug}.md"]}

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_tools, "execute_promote", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/action",
        headers=HOST,
        json={
            "path": f"inbox/{inbox.name}",
            "action": "promote_wiki",
            "partition": "operations",
            "slug": "selected-review-target",
        },
    )

    data = response.json()
    assert data["ok"] is True
    assert data["commit_sha"] == "abc123"
    assert calls["partition"] == "operations"
    assert calls["slug"] == "selected-review-target"
    assert calls["commit"] is False
    assert not inbox.exists()
    assert commits == [[
        "wiki/operations/selected-review-target.md",
        f"inbox/{inbox.name}",
        "wiki/operations/inbox-archive/2026-05-01.md",
    ]]
    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-01.md").read_text(encoding="utf-8")
    assert "- 实际动作：promote_wiki" in page_text


def test_inbox_action_invalid_path_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": "../x.md", "action": "archive"})

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_keep_action_hides_row_from_digest_decision_area(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    _write_inbox(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/action",
        headers=HOST,
        json={
            "path": "inbox/2026-05-01-1200-codex-systems.md",
            "action": "keep",
            "date": "2026-05-21",
        },
    )

    data = response.json()
    assert data["ok"] is True
    assert data["hidden"] is True
    marker = tmp_path / ".tmp" / "review-ui-decisions" / "2026-05-21" / "kept.json"
    assert marker.exists()

    digest = client.get("/api/digest/2026-05-21", headers=HOST).json()["digest"]
    assert digest["inbox_rows"] == []
    assert digest["hidden_inbox_rows"][0]["path"] == "inbox/2026-05-01-1200-codex-systems.md"
    assert digest["counts"]["review_hidden"] == 1


def test_batch_inbox_archive_selected_commits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{first.name}", f"inbox/{second.name}"], "action": "archive"},
    )

    data = response.json()
    assert data["ok"] is True
    assert data["success_count"] == 2
    assert data["failure_count"] == 0
    assert data["commit_sha"] == "abc123"
    assert len(commits) == 1
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_archive_same_day_appends_to_one_summary_page(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-01-1210-codex-systems.md")
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{first.name}", f"inbox/{second.name}"], "action": "archive"},
    )

    assert response.json()["ok"] is True
    page_text = (tmp_path / "wiki" / "operations" / "inbox-archive" / "2026-05-01.md").read_text(encoding="utf-8")
    assert f"<!-- inbox-archive-entry: inbox/{first.name} -->" in page_text
    assert f"<!-- inbox-archive-entry: inbox/{second.name} -->" in page_text


def test_batch_inbox_promote_mem0_archives_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    promoted: list[str] = []

    def fake_promote(fact, topic=None):
        promoted.append(fact["source_id"])
        return {"ok": True, "memory_id": f"mem-{len(promoted)}", "topic": topic or fact["topic"]}

    monkeypatch.setattr(tm_review_tools, "execute_promote_mem0", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{first.name}", f"inbox/{second.name}"], "action": "promote_mem0"},
    )

    data = response.json()
    assert data["ok"] is True
    assert data["success_count"] == 2
    assert promoted == [f"inbox/{first.name}", f"inbox/{second.name}"]
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_promote_wiki_generates_slugs_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    promoted: list[tuple[str, str, str]] = []

    def fake_promote(fact, partition, slug, **_kwargs):
        promoted.append((fact["source_id"], partition, slug))
        return {"ok": True, "wiki_path": f"wiki/{partition}/{slug}.md", "changed_paths": [f"wiki/{partition}/{slug}.md"]}

    monkeypatch.setattr(tm_review_tools, "execute_promote", fake_promote)
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "archive123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={
            "paths": [f"inbox/{first.name}", f"inbox/{second.name}"],
            "action": "promote_wiki",
            "partition": "systems",
            "slug_prefix": "daily-review-note",
        },
    )

    data = response.json()
    assert data["ok"] is True
    assert data["success_count"] == 2
    assert promoted[0][1] == "systems"
    assert promoted[0][2].startswith("daily-review-note-1-")
    assert promoted[1][2].startswith("daily-review-note-2-")
    assert len(commits) == 1
    assert "wiki/systems/daily-review-note-1-codex-systems.md" in commits[0]
    assert "wiki/systems/daily-review-note-2-codex-systems.md" in commits[0]
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_promote_wiki_uses_default_targets_without_prompt_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path, "2026-05-01-1200-codex-operations.md")
    promoted: list[tuple[str, str, str]] = []

    def fake_promote(fact, partition, slug, **_kwargs):
        promoted.append((fact["source_id"], partition, slug))
        return {"ok": True, "wiki_path": f"wiki/{partition}/{slug}.md", "changed_paths": [f"wiki/{partition}/{slug}.md"]}

    monkeypatch.setattr(tm_review_tools, "execute_promote", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "archive123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{inbox.name}"], "action": "promote_wiki"},
    )

    data = response.json()
    assert data["ok"] is True
    assert promoted[0][1] == "operations"
    assert promoted[0][2]
    assert not inbox.exists()


def test_batch_inbox_promote_wiki_uses_proposal_frontmatter_target(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path, "2026-05-01-1200-codex-operations.md")
    text = inbox.read_text(encoding="utf-8")
    inbox.write_text(
        text.replace(
            "routed_by: tigermemory",
            "routed_by: tigermemory\nknowledge_target: wiki_proposal\nwiki_partition: systems\nwiki_slug_hint: proposed-target",
        ),
        encoding="utf-8",
    )
    promoted: list[tuple[str, str, str, bool]] = []
    commits: list[list[str]] = []

    def fake_promote(fact, partition, slug, **kwargs):
        promoted.append((fact["source_id"], partition, slug, kwargs.get("commit")))
        return {"ok": True, "wiki_path": f"wiki/{partition}/{slug}.md", "changed_paths": [f"wiki/{partition}/{slug}.md"]}

    def fake_commit(paths, _message):
        commits.append(paths)
        return "archive123"

    monkeypatch.setattr(tm_review_tools, "execute_promote", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{inbox.name}"], "action": "promote_wiki"},
    )

    data = response.json()
    assert data["ok"] is True
    assert promoted == [(f"inbox/{inbox.name}", "systems", "proposed-target", False)]
    assert len(commits) == 1
    assert "wiki/systems/proposed-target.md" in commits[0]
    assert "wiki/operations/inbox-archive/2026-05-01.md" in commits[0]


def test_batch_inbox_action_rejects_invalid_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": ["../x.md"], "action": "archive"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "inside repo" in data["error"]


def test_review_html_contains_batch_controls_and_status_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest/2026-05-21", headers=HOST)

    assert response.status_code == 200
    assert "批量归档" in response.text
    assert "存入即时记忆" in response.text
    assert "写入知识库推荐" in response.text
    assert "wiki-modal" in response.text
    assert "AI 修改建议" in response.text
    assert "即时记忆：适合近期偏好" in response.text  # 保留在 review.html 的 button title 属性中

    # 动态及去内联文案在模块化后的 JS 文件中进行断言
    js_content = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    assert "Codex 推荐操作" in js_content
    assert "进入即时记忆：适合近期偏好" in js_content  # JS 内的 actionHelps
    assert "写入长期事实记忆" in js_content
    assert "data-row-status" in js_content
    assert "展开详情" in js_content




def test_proposal_apply_calls_tm_cron_apply(tmp_path, monkeypatch):
    called: dict[str, str] = {}
    monkeypatch.setattr(tm_cron_apply, "load_report_proposals", lambda _date: {
        "proposal-2026-05-21-001": tm_cron_apply.Proposal("proposal-2026-05-21-001", "prompt-tuning", True)
    })

    def fake_apply(date: str, proposal: tm_cron_apply.Proposal):
        called["date"] = date
        called["id"] = proposal.proposal_id
        called["type"] = proposal.proposal_type
        return {"commit": "abc123"}

    monkeypatch.setattr(tm_cron_apply, "apply_one", fake_apply)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/proposal/apply",
        headers=HOST,
        json={"date": "2026-05-21", "proposal_id": "proposal-2026-05-21-001"},
    )

    assert response.json()["ok"] is True
    assert called == {"date": "2026-05-21", "id": "proposal-2026-05-21-001", "type": "prompt-tuning"}


def test_batch_archive_stale_commits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/batch/archive-stale", headers=HOST, json={"date": "2026-05-21"})

    data = response.json()
    assert data["ok"] is True
    assert data["commit_sha"] == "abc123"
    assert len(data["archived"]) == 2
    assert len(commits) == 1


def test_healthz_returns_version(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/healthz", headers=HOST)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "version" in response.json()


def test_proposal_failure_returns_ok_false(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_cron_apply, "load_report_proposals", lambda _date: {})

    def fail(_date, _proposal):
        raise tm_cron_apply.CronApplyError("bad patch")

    monkeypatch.setattr(tm_cron_apply, "apply_one", fail)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/proposal/apply",
        headers=HOST,
        json={"date": "2026-05-21", "proposal_id": "proposal-2026-05-21-001"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "bad patch" in response.json()["error"]


def test_main_rejects_non_local_bind():
    assert tm_review_ui.main(["--host", "example.com"]) == 2


def test_agent_tools_page_returns_correct_html(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/agent-tools", headers=HOST)

    assert response.status_code == 200
    assert "智能体" in response.text or "Agent" in response.text
    assert "/static/assets/tailwindcss.min.js" in response.text
    assert "/static/assets/lucide.min.js" in response.text
    assert "https://cdn.tailwindcss.com" not in response.text
    assert "https://unpkg.com" not in response.text


def test_api_agent_status_endpoint(tmp_path, monkeypatch):
    import tm_agent_connect

    class FakePath:
        def __init__(self, exists=True):
            self._exists = exists

        def exists(self):
            return self._exists

        def read_text(self, encoding="utf-8"):
            return '{"mcpServers": {"tigermemory": {}}}'

        def __str__(self):
            return "/fake/path"

    monkeypatch.setattr(
        tm_agent_connect,
        "detect_config_paths",
        lambda: {"cursor": FakePath(True), "claude_desktop": FakePath(False)},
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/status", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["cursor"]["exists"] is True
    assert data["cursor"]["connected"] is True
    assert data["claude"]["exists"] is False
    assert data["claude"]["connected"] is False


def test_api_agent_doctor_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tm_review_ui.tm_agent_doctor,
        "run_agent_doctor",
        lambda **_kwargs: {"status": "ok", "checks": [{"name": "worktree", "ok": True, "status": "ok"}], "recommended_action": "Keep clean"},
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/doctor", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["report"]["status"] == "ok"
    assert data["report"]["checks"][0]["name"] == "worktree"


def test_api_agent_eval_endpoint(tmp_path, monkeypatch):
    import tm_eval_runner
    monkeypatch.setattr(
        tm_eval_runner,
        "load_or_create_eval_suite",
        lambda _name: [{"id": "case-1", "description": "Test wiki", "query": "test"}],
    )
    monkeypatch.setattr(
        tm_eval_runner,
        "run_wiki_eval",
        lambda _case: (1, 15.5),
    )
    monkeypatch.setattr(
        tm_eval_runner,
        "run_mem0_eval",
        lambda _case: (True, 50.0),
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/eval?skip_mem0=false", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["total_cases"] == 1
    assert data["wiki"]["recall_1"] == 1.0
    assert data["wiki"]["avg_latency_ms"] == 15.5
    assert data["mem0"]["active"] is True
    assert data["mem0"]["accuracy"] == 1.0
    assert data["mem0"]["avg_latency_ms"] == 50.0


def test_api_agent_eval_accepts_wiki_degraded_tuple(tmp_path, monkeypatch):
    import tm_eval_runner
    monkeypatch.setattr(
        tm_eval_runner,
        "load_or_create_eval_suite",
        lambda _name: [{"id": "case-1", "description": "Test wiki", "query": "test"}],
    )
    monkeypatch.setattr(
        tm_eval_runner,
        "run_wiki_eval",
        lambda _case: (1, 15.5, False),
    )
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/eval?skip_mem0=true", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["results"][0]["wiki_rank"] == 1
    assert data["results"][0]["wiki_degraded"] is False
    assert data["mem0"]["active"] is False


def test_api_agent_eval_import_error_returns_json(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tm_eval_runner":
            raise ImportError("No module named 'tigermemory_eval'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/eval", headers=HOST)

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "评测模块不可用" in data["error"]
    assert "tigermemory_eval" in data["hint"]


def test_route_events_record_final_outcome_without_raw_text(tmp_path):
    import datetime as dt
    decision = tm_route.RouteDecision(
        route="inbox",
        score=88,
        topic_inferred="investment",
        issues=[],
        reasons="stable knowledge should become a wiki proposal",
        is_transient=False,
        is_sensitive=False,
        needs_human_review=False,
        knowledge_target="wiki_proposal",
        target_confidence=90,
        wiki_partition="investment",
        wiki_slug_hint="example",
        wiki_action="create",
    )
    root = tmp_path / "events"
    text = "2026-06-10 这是一条不应复制原文的投研记忆。"

    event = tm_route_events.record_route_event(
        agent="codex",
        requested_topic="investment",
        storage_topic="investment",
        text=text,
        decision=decision,
        result={"route": "inbox", "outcome": "wiki_proposal", "path": "inbox/example.md"},
        outcome="wiki_proposal",
        event_root=root,
        now=dt.datetime(2026, 6, 10, 12, 0, tzinfo=tm_review_ui.tm_core.TZ_CN),
    )
    rows = tm_route_events.load_route_events(dates=["2026-06-10"], event_root=root)
    summary = tm_route_events.summarize_route_events(rows, dates=["2026-06-10"], event_root=root)
    raw = (root / "2026-06-10" / "events.jsonl").read_text(encoding="utf-8")

    assert event["flow_target"] == "wiki"
    assert summary["flow_counts"] == {"mem0": 0, "wiki": 1, "inbox": 0, "discard": 0}
    assert rows[0]["text_sha256_12"]
    assert text not in raw
    assert "text_sha256_12" in raw


def test_dashboard_memory_quality_falls_back_to_live_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"count": 8, "items": [], "results": [], "latency_ms": 12})
    monkeypatch.setattr(tm_review_ui, "_quality_live_mem0_count", lambda *_args, **_kwargs: (2, "Mem0 cached"))
    monkeypatch.setattr(tm_review_ui.tm_memory_reflection, "discard_events_for_dates", lambda *_args, **_kwargs: [{"event_id": "d1"}])
    monkeypatch.setattr(tm_review_ui.tm_answer_trace, "load_trace_rows", lambda **_kwargs: ([], []))
    monkeypatch.setattr(tm_review_ui.tm_answer_trace, "summarize_rows", lambda *_args, **_kwargs: {"duration_ms": {}, "status_counts": {}, "latest": []})

    data = tm_review_ui.dashboard_memory_quality("2026-05-27")

    assert data["digest_available"] is False
    assert data["fallback_mode"] is True
    assert data["counts"]["mem0"] == 2
    assert data["counts"]["inbox"] == 2
    assert data["counts"]["wiki"] is None
    assert data["counts"]["wiki_count_source"] == "live_not_connected"
    assert data["counts"]["discard"] == 1
    assert "digest not found" in data["digest_error"]
    assert data["counts"]["inbox_pending"] == 2
    assert data["counts"]["inbox_today"] == 0
    flow = data["route_flow"]
    assert "input_total" in flow and "sources" in flow and "outputs" in flow
    outputs = {slot["key"]: slot for slot in flow["outputs"]}
    assert {"mem0", "wiki", "inbox", "discard", "issue"} <= set(outputs)
    assert outputs["mem0"]["value"] == 2
    assert outputs["wiki"]["value"] is None
    assert outputs["inbox"]["value"] == data["counts"]["inbox_pending"]
    assert "当前待确认队列" in outputs["inbox"]["basis"]
    assert outputs["discard"]["value"] == 1


def test_dashboard_memory_quality_cached_live_keeps_unknown_wiki(monkeypatch):
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-06-10")
    monkeypatch.setattr(tm_review_ui, "_worktree_dirty_state", lambda: {"dirty": False, "status_count": 0, "sample": [], "error": None})

    def fake_route_history(counts, dates, warnings=None):
        counts["route_event_counts"] = {"mem0": 0, "wiki": 0, "inbox": 0, "discard": 0}
        counts["route_event_total"] = 0
        counts["route_event_dates"] = []
        counts["route_event_missing_dates"] = list(dates)
        return {
            "event_count": 0,
            "flow_counts": counts["route_event_counts"],
            "dates_with_events": [],
            "missing_event_dates": list(dates),
        }

    monkeypatch.setattr(tm_review_ui, "_attach_quality_route_history", fake_route_history)
    monkeypatch.setattr(
        tm_review_ui,
        "_run_cache_get",
        lambda *_args, **_kwargs: ({
            "ok": True,
            "date": "2026-06-10",
            "digest_available": False,
            "counts": {
                "mem0": 3,
                "wiki": None,
                "inbox": 2,
                "inbox_pending": 2,
                "inbox_today": 0,
                "discard": 0,
                "wiki_count_source": "live_not_connected",
            },
            "trace_summary": {"status_counts": {}, "latest": []},
            "warnings": [],
            "errors": [],
        }, True),
    )

    data = tm_review_ui.dashboard_memory_quality("2026-06-10")

    assert data["cached"] is True
    assert data["counts"]["wiki"] is None
    output_map = {slot["key"]: slot for slot in data["route_flow"]["outputs"]}
    assert output_map["wiki"]["value"] is None
    assert output_map["wiki"]["status"] == "warn"


def test_dashboard_memory_quality_digest_backfill_uses_frontmatter_and_live_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path, "2026-05-27")
    _write_inbox(tmp_path, "2026-05-27-1200-codex-systems.md")
    _write_inbox(tmp_path, "2026-05-26-1200-codex-systems.md")
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "load_trace_rows",
        lambda **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "summarize_rows",
        lambda *_args, **_kwargs: {"duration_ms": {}, "status_counts": {"not_found": 1, "error": 2}, "latest": []},
    )

    data = tm_review_ui.dashboard_memory_quality("2026-05-27")

    assert data["digest_available"] is True
    assert data["fallback_mode"] is False
    assert data["counts"]["mem0"] == 2
    assert data["counts"]["inbox"] == 2
    assert data["counts"]["inbox_pending"] == 2
    assert data["counts"]["inbox_today"] == 1
    assert data["counts"]["wiki"] == 1
    assert data["counts"]["wiki_count_source"] == "wiki_proposal_inbox"
    flow = data["route_flow"]
    output_map = {slot["key"]: slot for slot in flow["outputs"]}
    assert {"mem0", "wiki", "inbox", "discard", "issue"} <= set(output_map)
    assert output_map["inbox"]["value"] == 1
    assert output_map["issue"]["value"] == 2


def test_quality_trace_summary_loads_and_passes_feedback_aggregate(monkeypatch):
    feedback_summary = {
        "schema_version": "memory-answer-feedback-summary-v1",
        "event_count": 1,
        "trace_count": 1,
        "invalid_row_count": 0,
        "action_counts": {"clicked": 1},
        "surface_counts": {"review_ui": 1},
        "score_bucket_counts": {"high": 1},
        "use_hint_counts": {"read_next": 1},
        "reason_category_counts": {"policy": 1},
    }
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "load_trace_rows",
        lambda **_kwargs: ([{"trace_id": "trace-1", "status": "ok", "trace": {}}], []),
    )
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "load_feedback_events",
        lambda **_kwargs: ([{"trace_id": "trace-1", "surface": "review_ui", "action": "clicked"}], []),
    )
    monkeypatch.setattr(tm_review_ui.tm_answer_trace, "summarize_feedback_events", lambda events, invalid: feedback_summary)

    def fake_summarize_rows(rows, invalid, **kwargs):
        captured["feedback_summary"] = kwargs.get("feedback_summary")
        return {
            "duration_ms": {},
            "status_counts": {},
            "latest": [],
            "recommendation_quality": {"feedback_summary": kwargs.get("feedback_summary") or {}},
        }

    monkeypatch.setattr(tm_review_ui.tm_answer_trace, "summarize_rows", fake_summarize_rows)

    report = tm_review_ui._load_quality_trace_summary(since_hours=24 * 7, dates=["2026-06-10"])

    assert captured["feedback_summary"] == feedback_summary
    assert report["recommendation_quality"]["feedback_summary"]["action_counts"] == {"clicked": 1}


def test_dashboard_memory_quality_range_aggregates_available_digest_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-06-10")
    _write_digest(tmp_path, "2026-06-09")
    second_digest = _write_digest(tmp_path, "2026-06-10")
    second_digest.write_text(
        second_digest.read_text(encoding="utf-8").replace(
            "inbox/2026-05-01-1200-codex-systems.md",
            "inbox/2026-06-10-1200-codex-systems.md",
        ),
        encoding="utf-8",
    )
    _write_inbox(tmp_path, "2026-06-09-1200-codex-systems.md")
    _write_inbox(tmp_path, "2026-06-10-1200-codex-systems.md")
    monkeypatch.setattr(
        tm_review_ui,
        "parse_digest",
        lambda _date: (_ for _ in ()).throw(AssertionError("range quality should use lightweight digest snapshots")),
    )
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"count": 8, "items": [], "results": [], "latency_ms": 12})
    trace_calls: list[int] = []
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "load_trace_rows",
        lambda **kwargs: trace_calls.append(kwargs["since_hours"]) or ([{"trace_id": "t1", "status": "error"}], []),
    )
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "summarize_rows",
        lambda *_args, **_kwargs: {"duration_ms": {}, "status_counts": {"error": 1}, "latest": []},
    )

    data = tm_review_ui.dashboard_memory_quality("2026-06-10", "7d")

    assert data["range"]["key"] == "7d"
    assert data["range"]["label"] == "近 7 天"
    assert data["available_dates"] == ["2026-06-09", "2026-06-10"]
    assert "2026-06-04" in data["missing_dates"]
    assert data["counts"]["mem0"] == 4
    assert data["counts"]["discard"] == 6
    assert data["counts"]["wiki"] == 2
    assert data["counts"]["wiki_count_source"] == "wiki_proposal_inbox"
    assert data["counts"]["inbox_pending"] == 2
    assert data["counts"]["inbox_today"] == 2
    assert data["counts"]["review_entered"] == 2
    assert data["route_flow"]["period_label"] == "近 7 天"
    assert data["route_flow"]["trace_period_label"] == "近 7 天"
    assert trace_calls == [24 * 7]


def test_dashboard_memory_quality_range_keeps_digest_mem0_when_live_mem0_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-06-10")
    _write_digest(tmp_path, "2026-06-09")
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"error": "timed out", "latency_ms": 1000})
    monkeypatch.setattr(
        tm_review_ui.tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timed out")),
    )
    monkeypatch.setattr(
        tm_review_ui,
        "_live_digest_fallback",
        lambda *_args, **_kwargs: {
            "counts": {
                "inbox": 0,
                "inbox_pending": 0,
                "inbox_today": 0,
                "review_hidden": 0,
                "mem0": 0,
                "discard": 0,
                "wiki": 0,
            },
            "inbox_rows": [],
            "hidden_inbox_rows": [],
            "report_inbox_rows": [],
        },
    )
    monkeypatch.setattr(tm_review_ui.tm_answer_trace, "load_trace_rows", lambda **_kwargs: ([], []))
    monkeypatch.setattr(
        tm_review_ui.tm_answer_trace,
        "summarize_rows",
        lambda *_args, **_kwargs: {"duration_ms": {}, "status_counts": {}, "latest": []},
    )

    data = tm_review_ui.dashboard_memory_quality("2026-06-10", "7d")

    assert data["counts"]["mem0"] == 2
    assert "今日实时增量未计入" in data["counts"]["mem0_basis"]


def test_dashboard_memory_quality_range_cached_note_marks_partial_route_ledger(monkeypatch):
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-06-11")
    monkeypatch.setattr(tm_review_ui, "_worktree_dirty_state", lambda: {"dirty": False, "status_count": 0, "sample": [], "error": None})

    cached_payload = {
        "ok": True,
        "date": "2026-06-11",
        "counts": {
            "route_event_total": 32,
            "route_event_counts": {"mem0": 30, "wiki": 0, "inbox": 1, "discard": 1},
            "route_event_dates": ["2026-06-10", "2026-06-11"],
            "route_event_missing_dates": ["2026-06-05", "2026-06-06"],
        },
        "route_flow": {
            "history": {"note": "old note"},
            "outputs": [],
        },
        "warnings": [],
        "errors": [],
    }
    monkeypatch.setattr(
        tm_review_ui,
        "_attach_quality_route_history",
        lambda counts, _dates, _warnings=None: {
            "event_count": counts["route_event_total"],
            "flow_counts": counts["route_event_counts"],
            "dates_with_events": counts["route_event_dates"],
            "missing_event_dates": counts["route_event_missing_dates"],
        },
    )
    monkeypatch.setattr(tm_review_ui, "_run_cache_get", lambda *_args, **_kwargs: (cached_payload, True))

    data = tm_review_ui.dashboard_memory_quality("2026-06-11", "7d")

    assert data["cached"] is True
    assert data["route_flow"]["history"]["note"] == "近 7 天主图只展示已记录路由流水 32 条；缺少 2 天流水，历史日报和待审积压只作参考。"


def test_quality_live_mem0_count_uses_server_date_filter(monkeypatch):
    tm_review_ui._mem0_dashboard_reset_for_tests()
    captured: dict[str, object] = {}

    def fake_mem0_request(url: str, *, timeout: float):
        captured["url"] = url
        captured["timeout"] = timeout
        return json.dumps({"total": 24, "items": [{"id": "m1"}]})

    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_base", lambda: "http://mem0.local")
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_user_id", lambda: "tiger")
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_request", fake_mem0_request)

    count, basis = tm_review_ui._quality_live_mem0_count("2026-06-10", {})

    assert count == 24
    assert "服务端日期过滤" in basis
    assert "from_date=" in str(captured["url"])
    assert "to_date=" in str(captured["url"])
    assert "sort_column=created_at" in str(captured["url"])
    assert captured["timeout"] == 2.0


def test_mem0_payload_serves_stale_cache_after_timeout(monkeypatch):
    tm_review_ui._mem0_dashboard_reset_for_tests()
    calls = {"count": 0}

    def fake_mem0_request(_url: str, *, timeout: float):
        calls["count"] += 1
        if calls["count"] == 1:
            return json.dumps({"total": 9, "results": [{"id": "m1"}]})
        raise RuntimeError("Mem0 timeout: synthetic")

    monkeypatch.setattr(tm_review_ui, "MEM0_DASHBOARD_CACHE_TTL", 0.0)
    monkeypatch.setattr(tm_review_ui, "MEM0_DASHBOARD_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_base", lambda: "http://mem0.local")
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_user_id", lambda: "tiger")
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_request", fake_mem0_request)

    first = tm_review_ui._mem0_payload("session-handoff", size=1, timeout=0.1)
    second = tm_review_ui._mem0_payload("session-handoff", size=1, timeout=0.1)

    assert first["count"] == 9
    assert second["count"] == 9
    assert second["stale"] is True
    assert second["mem0_guard"]["status"] == "error-stale-cache"
    assert calls["count"] == 2


def test_mem0_payload_circuit_breaker_skips_repeated_live_calls(monkeypatch):
    tm_review_ui._mem0_dashboard_reset_for_tests()
    calls = {"count": 0}

    def fake_mem0_request(_url: str, *, timeout: float):
        calls["count"] += 1
        raise RuntimeError("Mem0 timeout: synthetic")

    monkeypatch.setattr(tm_review_ui, "MEM0_DASHBOARD_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(tm_review_ui, "MEM0_DASHBOARD_COOLDOWN", 60.0)
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_base", lambda: "http://mem0.local")
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_user_id", lambda: "tiger")
    monkeypatch.setattr(tm_review_ui.tm_core, "mem0_request", fake_mem0_request)

    first = tm_review_ui._mem0_payload("no-cache", size=1, timeout=0.1)
    second = tm_review_ui._mem0_payload("no-cache", size=1, timeout=0.1)

    assert first["count"] is None
    assert second["count"] is None
    assert second["mem0_guard"]["status"] == "circuit-open"
    assert calls["count"] == 1


def test_quality_route_flow_prefers_route_recommendation_distribution():
    inbox_rows = []
    for idx in range(6):
        inbox_rows.append({"path": f"inbox/2026-06-09-12{idx:02d}-codex-systems.md", "route_target": "mem0"})
    inbox_rows.append({"path": "inbox/2026-06-09-1300-codex-systems.md", "route_target": "wiki"})
    inbox_rows.extend([
        {"path": "inbox/2026-06-09-1310-codex-systems.md", "route_target": "inbox"},
        {"path": "inbox/2026-06-09-1320-codex-systems.md", "route_target": "inbox"},
        {"path": "inbox/2026-06-08-1320-codex-systems.md", "route_target": "wiki"},
    ])

    flow = tm_review_ui._build_quality_route_flow(
        counts={"mem0": 0, "wiki": 0, "inbox_today": 9, "discard": 0},
        report_date="2026-06-09",
        trace_summary={"status_counts": {"not_found": 12, "error": 1}},
        trace_rows=[{"trace_id": str(idx)} for idx in range(10)],
        inbox_rows=inbox_rows,
        source_mode="digest",
    )

    output_map = {slot["key"]: slot for slot in flow["outputs"]}
    assert flow["flow_source"] == "route_recommendation"
    assert flow["input_total"] == 9
    assert flow["route_recommendation_counts"] == {"mem0": 6, "wiki": 1, "inbox": 2, "discard": 0}
    assert output_map["mem0"]["value"] == 6
    assert output_map["wiki"]["value"] == 1
    assert output_map["inbox"]["value"] == 2
    assert output_map["discard"]["value"] == 0
    assert output_map["issue"]["value"] == 1
    assert flow["trace_count"] == 10


def test_quality_route_flow_filters_recommendations_by_date_range():
    inbox_rows = [
        {"path": "inbox/2026-06-08-1200-codex-systems.md", "route_target": "mem0"},
        {"path": "inbox/2026-06-09-1200-codex-systems.md", "route_target": "wiki"},
        {"path": "inbox/2026-06-07-1200-codex-systems.md", "route_target": "discard"},
    ]

    flow = tm_review_ui._build_quality_route_flow(
        counts={"mem0": 0, "wiki": 0, "inbox_today": 2, "discard": 0, "review_entered": 2},
        report_date="2026-06-09",
        trace_summary={"status_counts": {}},
        trace_rows=[],
        inbox_rows=inbox_rows,
        source_mode="range",
        date_filter={"2026-06-08", "2026-06-09"},
        period_label="近 7 天",
    )

    output_map = {slot["key"]: slot for slot in flow["outputs"]}
    assert flow["flow_source"] == "range"
    assert flow["input_total"] == 2
    assert flow["route_recommendation_counts"] == {"mem0": 1, "wiki": 1, "inbox": 0, "discard": 0}
    assert output_map["mem0"]["value"] == 0
    assert output_map["wiki"]["value"] == 0
    assert output_map["inbox"]["value"] == 2
    assert output_map["discard"]["value"] == 0


def test_quality_route_flow_prefers_logged_route_events_over_range_backfill():
    flow = tm_review_ui._build_quality_route_flow(
        counts={
            "mem0": None,
            "wiki": 2,
            "inbox_pending": 13,
            "review_entered": 77,
            "discard": 0,
            "route_event_counts": {"mem0": 30, "wiki": 0, "inbox": 1, "discard": 1},
            "route_event_dates": ["2026-06-10", "2026-06-11"],
            "route_event_missing_dates": ["2026-06-05", "2026-06-06", "2026-06-07"],
        },
        report_date="2026-06-11",
        trace_summary={"status_counts": {"not_found": 13}},
        trace_rows=[],
        inbox_rows=[],
        source_mode="range",
        date_filter={"2026-06-05", "2026-06-06", "2026-06-07", "2026-06-10", "2026-06-11"},
        period_label="近 7 天",
    )

    output_map = {slot["key"]: slot for slot in flow["outputs"]}
    assert flow["flow_source"] == "route_events"
    assert flow["input_total"] == 32
    assert output_map["mem0"]["value"] == 30
    assert output_map["wiki"]["value"] == 0
    assert output_map["inbox"]["value"] == 1
    assert output_map["discard"]["value"] == 1
    assert "真实路由流水 route=inbox 1 条" in output_map["inbox"]["basis"]
    assert "主图只展示已记录路由流水 32 条" in flow["history"]["note"]


def test_quality_route_flow_prefers_logged_fallback_over_recommendation():
    flow = tm_review_ui._build_quality_route_flow(
        counts={
            "mem0": None,
            "wiki": None,
            "inbox_pending": 13,
            "discard": 0,
            "route_event_counts": {"mem0": 0, "wiki": 0, "inbox": 1, "discard": 0},
        },
        report_date="2026-06-11",
        trace_summary={"status_counts": {}},
        trace_rows=[],
        inbox_rows=[{"path": "inbox/2026-06-11-0009-codex-systems.md", "route_target": "mem0"}],
        source_mode="live",
        period_label="今日",
    )

    output_map = {slot["key"]: slot for slot in flow["outputs"]}
    assert flow["flow_source"] == "route_events"
    assert flow["input_total"] == 1
    assert output_map["mem0"]["value"] == 0
    assert output_map["inbox"]["value"] == 1
    assert "真实路由流水 route=inbox 1 条" in output_map["inbox"]["basis"]


def test_quality_route_flow_preserves_cached_recommendation_distribution():
    flow = tm_review_ui._build_quality_route_flow(
        counts={
            "mem0": 0,
            "wiki": 0,
            "inbox_today": 9,
            "discard": 0,
            "route_recommendation_counts": {"mem0": 6, "wiki": 1, "inbox": 2, "discard": 0},
        },
        report_date="2026-06-09",
        trace_summary={"status_counts": {"not_found": 12, "error": 1}},
        trace_rows=[{"trace_id": str(idx)} for idx in range(10)],
        inbox_rows=[],
        source_mode="digest",
    )

    output_map = {slot["key"]: slot for slot in flow["outputs"]}
    assert flow["flow_source"] == "route_recommendation"
    assert flow["input_total"] == 9
    assert output_map["mem0"]["value"] == 6
    assert output_map["wiki"]["value"] == 1
    assert output_map["inbox"]["value"] == 2
    assert output_map["discard"]["value"] == 0
    assert output_map["issue"]["value"] == 1


def test_api_health_memory_overview_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    (tmp_path / "wiki" / "systems").mkdir(parents=True)
    (tmp_path / "wiki" / "systems" / "one.md").write_text("# one\n", encoding="utf-8")
    _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    monkeypatch.setattr(tm_review_ui, "_get_mem0_approximate_count", lambda: 7)
    monkeypatch.setattr(tm_review_ui, "_get_7day_digest_trend", lambda: [{"date": "2026-05-27", "mem0": 1, "inbox": 1, "discard": 0, "available": True}])
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/health/memory-overview", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["wiki_pages"] == 1
    assert data["inbox_pending"] == 1
    assert data["mem0_approximate"] == 7
    assert data["trend_7d"][0]["available"] is True


def test_dashboard_digest_trend_reads_frontmatter_only(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-27")
    monkeypatch.setattr(
        tm_review_ui,
        "parse_digest",
        lambda _date: (_ for _ in ()).throw(AssertionError("trend should not parse full digest")),
    )
    digest_dir = tmp_path / "wiki" / "operations"
    digest_dir.mkdir(parents=True)
    (digest_dir / "daily-memory-digest-2026-05-27.md").write_text(
        "---\nmem0_count: 11\ninbox_count: 3\ndiscard_count: 2\n---\n# digest\n",
        encoding="utf-8",
    )

    rows = tm_review_ui._get_7day_digest_trend()

    assert rows[-1]["available"] is True
    assert rows[-1]["mem0"] == 11
    assert rows[-1]["inbox"] == 3
    assert rows[-1]["discard"] == 2


def test_dashboard_health_summary_does_not_compute_memory_overview(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        tm_review_ui.tm_agent_doctor,
        "run_agent_doctor",
        lambda **_kwargs: {"status": "ok", "checks": []},
    )
    monkeypatch.setattr(
        tm_review_ui,
        "dashboard_memory_overview",
        lambda: (_ for _ in ()).throw(AssertionError("memory overview should stay on its own endpoint")),
    )

    data = tm_review_ui.dashboard_health_summary()

    assert data["ok"] is True
    assert "memory_overview" not in data


def test_mem0_approximate_count_accepts_total(monkeypatch):
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"total": 696, "items": []})

    assert tm_review_ui._get_mem0_approximate_count() == 696


def test_api_agent_recent_activity_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "_recent_agent_commits", lambda: [{"type": "commit", "agent": "codex", "title": "[codex] update: test"}])
    monkeypatch.setattr(tm_review_ui, "_recent_handoff_cards", lambda: [{"type": "handoff", "agent": "openclaw", "title": "handoff"}])
    monkeypatch.setattr(tm_review_ui, "_ce_plugin_last_write", lambda: {"type": "ce-plugin", "agent": "tigermemory-ce", "title": "ce write"})
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/agent/recent-activity", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert [item["type"] for item in data["items"][:3]] == ["ce-plugin", "commit", "handoff"]


def test_dashboard_recent_activity_sorts_by_created_at(monkeypatch):
    monkeypatch.setattr(
        tm_review_ui,
        "_recent_agent_commits",
        lambda: [
            {"type": "commit", "agent": "codex", "title": "old commit", "created_at": "2026-05-20T10:00:00+08:00"},
            {"type": "commit", "agent": "cascade", "title": "new commit", "created_at": "2026-05-27T10:00:00+08:00"},
        ],
    )
    monkeypatch.setattr(
        tm_review_ui,
        "_recent_handoff_cards",
        lambda: [
            {"type": "handoff", "agent": "openclaw", "title": "mid handoff", "created_at": "2026-05-25T10:00:00+08:00"},
        ],
    )
    monkeypatch.setattr(tm_review_ui, "_ce_plugin_last_write", lambda: None)

    result = tm_review_ui.dashboard_agent_recent_activity()

    assert result["ok"] is True
    assert [item["created_at"] for item in result["items"]] == [
        "2026-05-27T10:00:00+08:00",
        "2026-05-25T10:00:00+08:00",
        "2026-05-20T10:00:00+08:00",
    ]


def test_dashboard_recent_activity_ce_plugin_stays_at_top(monkeypatch):
    monkeypatch.setattr(
        tm_review_ui,
        "_recent_agent_commits",
        lambda: [{"type": "commit", "agent": "cascade", "title": "newer", "created_at": "2026-05-27T10:00:00+08:00"}],
    )
    monkeypatch.setattr(tm_review_ui, "_recent_handoff_cards", lambda: [])
    monkeypatch.setattr(
        tm_review_ui,
        "_ce_plugin_last_write",
        lambda: {"type": "ce-plugin", "agent": "tigermemory-ce", "title": "ce", "created_at": "2026-05-20T10:00:00+08:00"},
    )

    result = tm_review_ui.dashboard_agent_recent_activity()

    assert result["items"][0]["type"] == "ce-plugin"


def test_recent_handoff_cards_empty_task_section(monkeypatch):
    fake_payload = {"items": [{"content": "## Task\n\n\n", "created_at": "2026-05-27T10:00:00+08:00"}]}
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *a, **kw: fake_payload)
    monkeypatch.setattr(tm_review_ui, "_mem0_items", lambda payload: payload.get("items", []))

    cards = tm_review_ui._recent_handoff_cards()

    assert cards and cards[0]["title"] == "## Task\n\n\n"[:120]


def test_dashboard_p0_i18n_static_guards():
    i18n_js = (tm_review_ui.STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")

    assert "get: lookup" in i18n_js
    assert ".chip, [data-chip-key], [data-action]" in i18n_js
    assert "next.includes(target)" in i18n_js
    assert "data.hint || data.error" in pages_js
    assert "实时模式：当前直接读取 Mem0、收件箱、回答轨迹和 discard 审计" in pages_js
    assert "renderStatusBars" in pages_js


def test_dashboard_write_actions_do_not_block_event_loop():
    source = pathlib.Path(tm_review_ui.__file__).read_text(encoding="utf-8")

    assert "WRITE_ACTION_LOCK = threading.Lock()" in source
    assert "await run_in_threadpool(_render_digest_page, date)" in source
    assert "await run_in_threadpool(daily_review_data, date)" in source
    assert "await run_in_threadpool(_locked_write_action, execute_inbox_action, req)" in source
    assert "await run_in_threadpool(_locked_write_action, execute_batch_inbox_action, req)" in source


def test_locked_write_action_logs_elapsed(capsys):
    class Req:
        action = "promote_mem0"
        paths = ["inbox/a.md", "inbox/b.md"]

    result = tm_review_ui._locked_write_action(lambda _req: {"ok": True}, Req())

    assert result == {"ok": True}
    assert "write action done action=promote_mem0 count=2 ok=True" in capsys.readouterr().err


def test_quality_cache_warmer_runs_without_browser_request(monkeypatch):
    calls: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-27")
    monkeypatch.setattr(
        tm_review_ui,
        "dashboard_memory_quality",
        lambda date=None, range_key=None: calls.append((date, range_key)) or {"ok": True, "date": date, "cached": False},
    )

    result = tm_review_ui._warm_quality_cache_once()

    assert calls == [("2026-05-27", None), ("2026-05-27", "7d"), ("2026-05-27", "30d")]
    assert result["ok"] is True
    assert result["date"] == "2026-05-27"
    assert result["cached"] is False
    assert [item["range"] for item in result["ranges"]] == ["today", "7d", "30d"]


def test_dashboard_main_starts_quality_cache_warmer():
    source = pathlib.Path(tm_review_ui.__file__).read_text(encoding="utf-8")
    assert "TM_DASHBOARD_QUALITY_WARM_INTERVAL" in source
    assert "TM_DASHBOARD_BACKGROUND" in source
    assert "start_quality_cache_warmer()" in source


def test_dashboard_action_controls_and_toast_static_guards():
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    review_html = (tm_review_ui.STATIC_DIR / "review.html").read_text(encoding="utf-8")
    style_css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert "actionInFlight" in pages_js
    assert "enqueueWriteJob" in pages_js
    assert "enqueueInboxRowAction(row, card, action)" in pages_js
    assert "openActionConfirmModal(row, card, action)" in pages_js
    assert "state.kind === 'action-confirm'" in pages_js
    assert "modalTitle.removeAttribute('data-i18n')" in pages_js
    assert "modalConfirm.removeAttribute('data-i18n')" in pages_js
    assert "scheduleWriteQueue" in pages_js
    assert "batchableQueuedJobs" in pages_js
    assert "queueProgressWidth(job)" in pages_js
    assert "actionTimeoutMs(action, count = 1)" in pages_js
    assert "45000 + itemCount * 30000" in pages_js
    assert "this.actionTimeoutMs(action, paths.length)" in pages_js
    assert "this.closeWikiModal()" in pages_js
    assert "resetActionQueueDock()" in pages_js
    assert "dock.classList.remove('tm-action-queue--active', 'tm-action-queue--failed')" in pages_js
    assert "/api/inbox/batch-action" in pages_js
    assert "processWriteQueue" in pages_js
    assert "处理队列" in pages_js
    assert "daily.processing.handling" not in pages_js
    assert "bottom-6 left-1/2" in pages_js
    assert "bottom-6 left-1/2" in review_html
    assert 'id="action-queue"' in review_html
    assert 'id="wiki-modal-title"' in review_html
    assert "tm-action-queue:hover" in style_css
    reduced_motion = style_css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".tm-action-queue,\n" not in reduced_motion
    assert ".tm-action-queue {\n    animation: none !important;\n    transition: none !important;\n  }" in reduced_motion
    assert "tmBusySheen" in style_css
    assert "tmQueueSheen" in style_css
    assert "tmQueueBar" not in style_css


def test_review_pages_js_exposes_approval_target_fields():
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")

    assert "审批建议目标" in pages_js
    assert "routeRecommendationLabel(row)" in pages_js
    assert "routeTargetText(row)" in pages_js
    assert "routeFlagsText(row)" in pages_js
    assert "route_hard_rule" in pages_js
    assert "route_target" in pages_js
    assert "route_flags" in pages_js
    assert "审批建议：" in pages_js
    assert "置信度" in pages_js
    assert "未命中具体目标" in pages_js
    assert "hardRuleAllowsAction(row, action)" in pages_js
    assert "审批建议硬性约束不推荐执行此动作" in pages_js
    assert "hardRuleHint(row)" in pages_js
    assert "硬性建议：仅可" in pages_js
    assert "灰色按钮是不推荐路线" in pages_js
    assert "actionEmphasisClass(row, action)" in pages_js


def test_dashboard_memory_overview_mem0_offline_subline():
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    assert "即时记忆暂时无法连接" in pages_js
    assert "mem0Available" in pages_js


def test_quality_page_flow_panel_keeps_all_routes_visible():
    quality_html = (tm_review_ui.STATIC_DIR / "quality.html").read_text(encoding="utf-8")
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    style_css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")
    quality_react = (
        REPO_ROOT
        / "packages"
        / "tigermemory-dashboard-ui"
        / "src"
        / "quality"
        / "main.tsx"
    ).read_text(encoding="utf-8")

    assert 'id="route-section"' in quality_html
    assert 'id="status-section"' in quality_html
    assert 'id="failure-section"' in quality_html
    assert 'id="quality-alert"' in quality_html
    assert 'id="quality-empty-state"' in quality_html
    assert 'data-quality-range="today"' in quality_html
    assert 'data-quality-range="7d"' in quality_html
    assert 'data-quality-range="30d"' in quality_html
    assert "hasDuration" in pages_js
    assert "有回答记录后显示耗时" in pages_js
    assert "等待真实回答记录" in pages_js
    assert "还没有可用于质量判断的实时写入或回答记录" in pages_js
    assert "今日整理尚未生成" not in pages_js
    assert "每日整理" not in pages_js
    assert "实时模式：当前直接读取 Mem0、收件箱、回答轨迹和 discard 审计" in pages_js
    assert "new URLSearchParams({ range: this.rangeKey || 'today' })" in pages_js
    assert "统计 ${rangeSpan}" in pages_js
    assert "renderRangeControls(memory)" in pages_js
    assert "feedbackSummary.action_counts" in pages_js
    assert "!feedbackHasCounts && !Object.keys(sidecarStatus || {}).length" in pages_js
    assert "显式反馈" in pages_js
    assert "setQualityUpdating(nextRange, true)" in pages_js
    assert "正在更新${c.esc(range.label)}数据" in pages_js
    assert "当前数字仍是上一范围" in pages_js
    assert "renderQualityLoading(nextRange)" not in pages_js
    assert "历史补算" in pages_js
    assert "prefetchQualityRanges()" in pages_js
    assert "quality range prefetch failed" in pages_js
    assert ".tm-quality-updating" in style_css
    assert "abortRef.current?.abort()" in quality_react
    assert 'new URLSearchParams({ range: nextRange })' in quality_react
    assert "setUpdatingRange(nextRange)" in quality_react
    assert '"mem0"' in quality_react
    assert '"wiki"' in quality_react
    assert '"inbox"' in quality_react
    assert '"discard"' in quality_react
    assert "['即时记忆', sourceValues.daily" in pages_js
    assert "'缺日志'" in pages_js
    assert "flowPayload.flow_source === 'route_events'" in pages_js
    assert "真实退回人工" in pages_js
    assert "已忽略数" not in pages_js
    assert "statusSection.classList.add('hidden')" not in pages_js
    assert "P5 真实失败池暂无样本" in quality_react
    assert "renderFlowPanel(memory)" in pages_js
    assert "if (routeSection) routeSection.classList.remove('hidden')" in pages_js
    assert "routeSection.classList.add('hidden')" not in pages_js
    assert "const flowSummaryCards = [" in pages_js
    assert "const outputCards = model.outputs.map((slot) =>" in pages_js
    assert "const routeOutputs = flowOutputs.filter(slot => !['issue', 'anomaly'].includes(String(slot.key || '').toLowerCase()));" in pages_js
    assert "['回答失败', outputValues.issue, '未找到另看状态分布']" in pages_js
    assert "key: 'issue'" not in pages_js
    assert "const pct = knownValue && flowTotal > 0 ? Math.round((slot.value || 0) * 100 / flowTotal) : (knownValue ? 0 : null);" in pages_js
    assert "四条写入路线同时展示" in pages_js
    assert "回答异常 ${c.numberText(outputValues.issue)} 条来自最近 7 天回答轨迹" not in pages_js
    assert "五条路线同时展示" not in pages_js


def test_canvas_star_map_uses_stable_compact_layout():
    canvas_html = (tm_review_ui.STATIC_DIR / "canvas.html").read_text(encoding="utf-8")
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    style_css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert ".canvas-graph-toolbar" in style_css
    assert "grid-template-columns: minmax(0, 1fr) auto" in style_css
    assert ".canvas-graph-hint" in style_css
    assert "-webkit-line-clamp: 2" in style_css
    assert "Math.random" not in pages_js.split("window.tmPages.canvas = {")[1]
    assert "graphWorld: {width: 1680, height: 1080}" in pages_js
    assert "const minFitScale = rect.width < 560 ? 0.18 : 0.34" in pages_js
    assert "const spacing = 64" in pages_js
    assert "const iterations = 96" in pages_js


def test_canvas_payload_includes_canvas_update_candidates(monkeypatch):
    raw_card = {
        "id": "m-canvas-1",
        "created_at": "2026-06-09T09:00:00Z",
        "memory": (
            "---\n"
            "memory_type: session-handoff\n"
            "session_id: codex-20260609-0900\n"
            "ide: codex\n"
            "agent: codex\n"
            "confidence: high\n"
            "source: agent\n"
            "---\n"
            "\n"
            "## Task\n"
            "Patch the project canvas state for the current run.\n"
            "\n"
            "## Decisions\n"
            "Keep the canvas patch as a reviewed candidate.\n"
            "\n"
            "## Blockers\n"
            "none\n"
            "\n"
            "## Handoff\n"
            "Review before editing the verified project canvas.\n"
            "\n"
            "## Evidence Refs\n"
            "- canvas_patch: P2_RollingSummary updated with canvas_patch evidence\n"
            "- files: wiki/operations/project-canvas.md\n"
        ),
    }
    monkeypatch.setattr(tm_review_ui, "_worktree_dirty_state", lambda: {"dirty": False, "status_count": 0})
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"items": [raw_card], "count": 1})
    monkeypatch.setattr(tm_review_ui, "_local_inbox_handoff_items", lambda *_args, **_kwargs: [])
    tm_review_ui._API_CACHE.clear()
    assert hasattr(tm_review_ui._load_session_rolling_summary_module(), "build_canvas_update_candidates")

    payload = tm_review_ui._load_canvas_payload()

    assert payload["ok"] is True
    assert payload["candidate_count"] == 1, {
        "warnings": payload.get("candidate_warnings"),
        "candidates": payload.get("canvas_candidates"),
        "source": payload.get("candidate_source"),
    }
    assert payload["candidate_source"] == "mem0:session-handoff + inbox/wiki_proposal + session-rolling-summary.py"
    assert payload["candidate_warnings"] == []
    assert len(payload["active_modules"]) > payload["candidate_count"]

    candidate = payload["canvas_candidates"][0]
    assert candidate["decision"] == "propose_canvas_update"
    assert candidate["review_state"] == "建议纳入"
    assert candidate["target_module"] == "P2_RollingSummary"
    assert candidate["summary"] == "Patch the project canvas state for the current run."
    assert candidate["reason"] == "canvas_patch evidence: P2_RollingSummary updated with canvas_patch evidence"
    assert candidate["evidence_count"] == 3
    assert candidate["source"] == "agent"
    assert candidate["confidence"] == "high"


def test_canvas_candidates_include_routed_inbox_handoff_when_mem0_empty(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox" / "2026-06-09-0408-codex-operations.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        "\n".join([
            "---",
            "routed_by: tigermemory",
            "knowledge_target: wiki_proposal",
            "---",
            "",
            "# Wiki proposal 82",
            "",
            "## Proposed Wiki body",
            "",
            "---",
            "memory_type: session-handoff",
            "session_id: codex-20260609-merge-canvas-candidates",
            "ide: codex",
            "agent: codex",
            "confidence: high",
            "source: agent",
            "---",
            "",
            "## Task",
            "Merged the candidate shelf into the dashboard.",
            "",
            "## Decisions",
            "Keep canvas changes human reviewed.",
            "",
            "## Blockers",
            "none",
            "",
            "## Handoff",
            "Review the candidate before editing the formal canvas.",
            "",
            "## Evidence Refs",
            "- canvas_patch: DashboardProjectCanvasCandidateShelf ready for review",
            "- commit: 138f76ff",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"items": [], "count": 0})
    tm_review_ui._API_CACHE.clear()

    payload = tm_review_ui._load_canvas_candidates()

    assert payload["candidate_count"] == 1
    assert payload["candidate_source"] == "mem0:session-handoff + inbox/wiki_proposal + session-rolling-summary.py"
    assert payload["candidate_warnings"] == []
    candidate = payload["canvas_candidates"][0]
    assert candidate["target_module"] == "DashboardProjectCanvasCandidateShelf"
    assert candidate["source"] == "agent"
    assert candidate["evidence_refs"][0] == "memory:inbox:inbox/2026-06-09-0408-codex-operations.md"


def test_canvas_candidates_are_short_ttl_cached(monkeypatch):
    calls = {"count": 0}

    def fake_mem0_payload(*_args, **_kwargs):
        calls["count"] += 1
        return {"items": [], "count": 0}

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", fake_mem0_payload)
    monkeypatch.setattr(tm_review_ui, "_local_inbox_handoff_items", lambda *_args, **_kwargs: [])
    tm_review_ui._API_CACHE.clear()

    first = tm_review_ui._load_canvas_candidates()
    second = tm_review_ui._load_canvas_candidates()

    assert calls["count"] == 1
    assert first["candidate_cached"] is False
    assert second["candidate_cached"] is True
    assert first["canvas_candidates"] == []
    assert second["canvas_candidates"] == []


def test_canvas_payload_returns_candidate_warning_when_mem0_unavailable(tmp_path, monkeypatch):
    missing_canvas_path = tmp_path / "wiki" / "operations" / "project-canvas.md"
    monkeypatch.setattr(tm_review_ui, "CANVAS_SOURCE_PATH", missing_canvas_path)
    monkeypatch.setattr(tm_review_ui, "_worktree_dirty_state", lambda: {"dirty": False, "status_count": 0})
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: {"items": [], "error": "connection refused"})
    monkeypatch.setattr(tm_review_ui, "_local_inbox_handoff_items", lambda *_args, **_kwargs: [])
    tm_review_ui._API_CACHE.clear()

    payload = tm_review_ui._load_canvas_payload()

    assert payload["ok"] is False
    assert payload["canvas_candidates"] == []
    assert payload["candidate_count"] == 0
    assert payload["candidate_source"] == "mem0:session-handoff"
    assert payload["candidate_warnings"] == ["待纳入星图候选读取失败：connection refused"]


def test_canvas_candidates_degrade_when_mem0_config_missing(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(tm_review_ui, "_mem0_payload", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("missing runtime/openmemory/.env")))
    monkeypatch.setattr(tm_review_ui, "_local_inbox_handoff_items", lambda *_args, **_kwargs: [])
    tm_review_ui._API_CACHE.clear()

    payload = tm_review_ui._load_canvas_candidates()

    assert payload["canvas_candidates"] == []
    assert payload["candidate_count"] == 0
    assert payload["candidate_source"] == "mem0:session-handoff"
    assert payload["candidate_warnings"] == ["待纳入星图候选读取失败：missing runtime/openmemory/.env"]


def test_canvas_candidate_mem0_payload_can_use_env_fallback(monkeypatch):
    tm_review_ui._mem0_dashboard_reset_for_tests()
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"count": 1, "results": [{"id": "m1"}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("MEM0_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    monkeypatch.setattr(tm_review_ui.urllib.request, "urlopen", fake_urlopen)

    payload = tm_review_ui._canvas_candidates_mem0_payload_from_env(
        "memory_type: session-handoff",
        size=3,
        timeout=1.5,
    )

    assert payload is not None
    assert payload["count"] == 1
    assert len(payload["items"]) == 1
    assert "search_query=memory_type%3A+session-handoff" in seen["url"]
    assert seen["auth"] == "Bearer test-key"
    assert seen["timeout"] == 1.5


def test_canvas_candidate_shelf_is_separate_from_star_map():
    canvas_html = (tm_review_ui.STATIC_DIR / "canvas.html").read_text(encoding="utf-8")
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    canvas_controller = pages_js.split("window.tmPages.canvas = {", 1)[1]
    graph_builder = pages_js.split("buildGraphModel(stages)", 1)[1].split("drawGraph", 1)[0]

    assert "待纳入星图" in canvas_html
    assert 'id="canvas-candidates"' in canvas_html
    assert "canvas-candidate-count" in canvas_html
    assert "canvasCandidates" in canvas_controller
    assert "renderCandidates(data)" in canvas_controller
    assert "canvas_candidates" not in graph_builder
    assert "candidate_count" not in pages_js.split("canvas-module-count", 1)[1].split("renderCandidates", 1)[0]


def test_health_page_compacts_optional_advanced_services_for_local_mode():
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    style_css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert "renderServices(report)" in pages_js
    assert "runtime_profile) === 'local'" in pages_js
    assert "service.status !== 'optional'" in pages_js
    assert "高级连接未启用" in pages_js
    assert "基础模式不需要" in pages_js
    assert "基础模式可用" in pages_js
    assert "healthRenderSignature" in pages_js
    assert "memoryOverviewRenderSignature" in pages_js
    assert "fetchHealthInFlight" in pages_js
    assert "fetchMemoryOverviewInFlight" in pages_js
    assert "this.fetchHealth();" in pages_js
    assert "this.fetchMemoryOverview({quiet: true});" in pages_js
    assert "body[data-page=\"health\"] .status-dot" in style_css
    assert "body.tm-refresh-quiet #service-grid > *" in style_css


def test_dashboard_p2_static_sections():
    health_html = (tm_review_ui.STATIC_DIR / "health.html").read_text(encoding="utf-8")
    agent_html = (tm_review_ui.STATIC_DIR / "agent-tools.html").read_text(encoding="utf-8")
    pages_js = (tm_review_ui.STATIC_DIR / "dashboard-pages.js").read_text(encoding="utf-8")
    style_css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert 'id="memory-overview"' in health_html
    assert 'id="recent-activity-list"' in agent_html
    assert "renderMemoryOverview" in pages_js
    assert "fetchRecentActivity" in pages_js
    assert "fetchMemoryOverview" in pages_js
    assert "/api/health/memory-overview" in pages_js
    assert "animateMotion" in pages_js
    assert "tm-flow-dot-anim" in pages_js
    assert "tm-flow-path-stream" not in pages_js
    assert "bindFlowHover" in pages_js
    assert "scheduleDrawFlowLines" in pages_js
    assert "window.addEventListener('resize', () => { if (window.tmPages && window.tmPages.quality" not in pages_js
    assert "tmFlowDotPulse" in style_css
    assert "tmPathFlowAnim" in style_css
    assert "tmIssueBreathing 3s infinite ease-in-out" in style_css


def test_dashboard_smoke_script_execution(monkeypatch):
    import tm_dashboard_smoke
    import json

    class FakeResponse:
        def __init__(self, data, status=200):
            self.data = data
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def read(self):
            return self.data

        def decode(self, encoding="utf-8"):
            return self.data.decode(encoding)

    def mock_open(req, timeout=5):
        url = req.full_url if hasattr(req, "full_url") else req
        if "healthz" in url:
            return FakeResponse(json.dumps({"ok": True, "git_sha": "ea7f5b2", "version": "0.2.0"}).encode("utf-8"))
        elif "/api/health/summary" in url:
            payload = {
                "ok": True,
                "source": "dashboard-runtime",
                "source_path": "tools/tm_review_ui.py",
                "source_updated_at": "2026-05-30T00:00:00+08:00",
                "generated_at": "2026-05-30T00:00:00+08:00",
                "latency_ms": 1,
                "cache": {"hit": False, "ttl_seconds": 30, "source": "dashboard-runtime"},
                "stale": False,
                "repo_dirty": False,
                "services": [],
                "dashboard": {"version": "0.2.0", "git_sha": "ea7f5b2"},
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        elif "/api/quality/memory" in url:
            payload = {
                "ok": True,
                "source": "live-inbox",
                "source_path": "",
                "source_updated_at": "",
                "generated_at": "2026-05-30T00:00:00+08:00",
                "latency_ms": 1,
                "cache": {"hit": False, "ttl_seconds": 30},
                "stale": False,
                "fallback": True,
                "fallback_mode": True,
                "repo_dirty": False,
                "counts": {},
                "trace_summary": {},
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        elif "/api/digest/" in url:
            payload = {
                "ok": True,
                "digest": {
                    "source": "wiki/operations/daily-memory-digest-2026-05-30.md",
                    "source_path": "wiki/operations/daily-memory-digest-2026-05-30.md",
                    "source_updated_at": "2026-05-30T00:00:00+08:00",
                    "generated_at": "2026-05-30T00:00:00+08:00",
                    "latency_ms": 1,
                    "fallback": False,
                    "cached": False,
                    "stale": False,
                    "warnings": [],
                    "errors": [],
                    "cache": {"hit": False, "ttl_seconds": 30},
                    "counts": {},
                },
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        elif "/api/canvas" in url:
            payload = {
                "ok": True,
                "source": "project-canvas.md",
                "source_path": "wiki/operations/project-canvas.md",
                "source_updated_at": "2026-05-30T00:00:00+08:00",
                "generated_at": "2026-05-30T00:00:00+08:00",
                "latency_ms": 1,
                "cache": {"hit": False, "ttl_seconds": 25},
                "stale": False,
                "repo_dirty": False,
                "mermaid_src": "flowchart LR\nA-->B",
                "active_modules": [],
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        elif "/api/self-evolution/" in url:
            payload = {
                "ok": True,
                "date": "2026-05-30",
                "generated_at": "2026-05-30T00:00:00+08:00",
                "mode": "propose_only",
                "summary": {"event_count": 0, "counts": {}, "samples": []},
                "proposal_summary": {"total": 0, "eligible": 0, "min_repeats": 3, "min_confidence": 0.75},
                "proposal_run": {"run_id": "self-evolution-2026-05-30", "window": {}},
                "proposals": [],
                "baseline": {"status": "ok", "counts": {}, "rates": {}},
                "evidence_sources": {"events": [], "telemetry": [], "env": "TM_SELF_EVOLUTION_EVIDENCE_ROOTS"},
                "warnings": [],
                "errors": [],
                "latency_ms": 1,
                "cached": False,
                "stale": False,
                "source": "self-evolution evidence",
                "cache": {"hit": False, "ttl_seconds": 300},
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))
        elif "digest" in url:
            html = '<body data-page="daily"><header></header><a class="nav-tab" data-target-page="daily"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        elif "health" in url:
            html = '<body data-page="health"><header></header><a class="nav-tab" data-target-page="health"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        elif "quality" in url:
            html = '<body data-page="quality"><header></header><a class="nav-tab" data-target-page="quality"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        elif "agent-tools" in url:
            html = '<body data-page="agent-tools"><header></header><a class="nav-tab" data-target-page="agent-tools"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        elif "settings" in url:
            html = '<body data-page="settings"><header></header><a class="nav-tab" data-target-page="settings"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        elif "canvas" in url:
            html = '<body data-page="canvas"><header></header><a class="nav-tab" data-target-page="canvas"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        elif "self-evolution" in url:
            html = '<body data-page="self-evolution"><header></header><a class="nav-tab" data-target-page="self-evolution"></a><code id="sha-pill">ea7f5b2</code></body>'
            return FakeResponse(html.encode("utf-8"))
        return FakeResponse(b"")

    class FakeOpener:
        def open(self, req, timeout=5):
            return mock_open(req, timeout)

    monkeypatch.setattr(tm_dashboard_smoke.urllib.request, "build_opener", lambda *args, **kwargs: FakeOpener())

    exited = []
    monkeypatch.setattr(sys, "exit", lambda code: exited.append(code))

    tm_dashboard_smoke.main(["--base-url", "http://127.0.0.1:1998"])
    assert exited == [0]

    exited.clear()
    tm_dashboard_smoke.main(["--base-url", "http://127.0.0.1:1998", "--json"])
    assert exited == [0]
