from __future__ import annotations

import json
import pathlib
import sys

from fastapi.testclient import TestClient

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_cron_apply  # type: ignore[import-not-found]
import tm_review_tools  # type: ignore[import-not-found]
import tm_review_ui  # type: ignore[import-not-found]

HOST = {"Host": "127.0.0.1:9777"}


def _client(tmp_path: pathlib.Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(tm_review_ui, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(tm_review_ui, "ensure_write_ready", lambda *_args, **_kwargs: None)
    return TestClient(tm_review_ui.app)


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


def test_host_header_rejects_non_localhost(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/healthz", headers={"Host": "example.com"})

    assert response.status_code == 403


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


def test_non_local_dashboard_requires_token_for_browser_bootstrap(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/health", headers={"Host": "tigermemory-wsl:1998"})

    assert response.status_code == 401


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
    assert "TigerMemory 每日审批" in response.text
    assert "今日要决策" in response.text
    assert "/static/assets/tailwindcss.min.js" in response.text
    assert "/static/assets/lucide.min.js" in response.text
    assert "/static/i18n.js" in response.text
    assert 'onclick="window.tmI18n' in response.text
    assert "@keyframes fadeIn" in response.text
    assert "https://cdn.tailwindcss.com" not in response.text
    assert "digest-data" in response.text


def test_i18n_assets_are_public(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    json_response = client.get("/static/i18n.json", headers=HOST)
    js_response = client.get("/static/i18n.js", headers=HOST)

    assert json_response.status_code == 200
    assert json_response.json()["zh"]["nav.daily"] == "每日审批"
    assert json_response.json()["en"]["nav.daily"] == "Daily Review"
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
    assert "日常审阅队列" in data["digest"]["inbox_rows"][0]["codex_recommended_reason"]
    assert data["digest"]["inbox_rows"][0]["wiki_target"]["partition"] == "systems"
    assert data["digest"]["inbox_rows"][0]["wiki_target"]["path"].startswith("wiki/systems/")
    assert data["digest"]["inbox_rows"][0]["wiki_target"]["similar"][0]["path"] == "wiki/systems/review-ui-approval.md"
    assert data["digest"]["proposals"][0]["id"] == "proposal-2026-05-21-001"


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


def test_digest_entry_falls_back_to_latest_existing_report(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-22")
    _write_digest(tmp_path, "2026-05-21")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest", headers=HOST, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/digest/2026-05-21"


def test_digest_entry_prefers_today_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_ui, "today", lambda: "2026-05-22")
    _write_digest(tmp_path, "2026-05-21")
    _write_digest(tmp_path, "2026-05-22")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest", headers=HOST, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/digest/2026-05-22"


def test_digest_entry_returns_404_when_no_reports_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest", headers=HOST, follow_redirects=False)

    assert response.status_code == 404
    assert "no daily digest reports found" in response.text


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
    assert "tigermemory-memory-ops-v6" in response.text
    assert "request.mode === 'navigate'" in response.text
    assert "url.pathname.startsWith('/api/')" in response.text
    assert "url.pathname.startsWith('/digest')" in response.text
    assert response.headers["Cache-Control"].startswith("no-store")


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
    assert "health-data" in response.text
    assert "系统健康" in response.text
    assert "记忆运维台" in response.text
    assert "可视化脑海" in response.text
    assert "#f7f2e6" in response.text
    assert "#c8a560" in response.text
    assert "/static/tiger/tigerlogo.png" in response.text
    assert "dashboard-motif" in response.text
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

    responses = {
        "daily": client.get("/digest/2026-05-21", headers=HOST),
        "health": client.get("/health", headers=HOST),
        "quality": client.get("/quality", headers=HOST),
        "agent-tools": client.get("/agent-tools", headers=HOST),
        "settings": client.get("/settings", headers=HOST),
    }

    def shared_header(html: str) -> str:
        start = html.index("<header")
        end = html.index("</script>", html.index("</header>")) + len("</script>")
        return html[start:end]

    headers = {page: shared_header(response.text) for page, response in responses.items()}
    assert len(set(headers.values())) == 1
    for page, response in responses.items():
        assert response.status_code == 200
        assert f'data-page="{page}"' in response.text
        assert 'id="lang-toggle"' in response.text
        assert 'id="last-refresh"' in response.text
        assert 'id="sha-pill"' in response.text
        assert "tm-page-ready" in response.text
        header_html = headers[page]
        assert "setTimeout" not in header_html
        assert "/static/assets/tailwindcss.min.js" in response.text, f"{page} missing local tailwind"
        assert "/static/assets/lucide.min.js" in response.text, f"{page} missing local lucide"
        assert "https://cdn.tailwindcss.com" not in response.text, f"{page} still references cdn.tailwindcss"
        assert "https://unpkg.com" not in response.text, f"{page} still references unpkg"


def test_dashboard_transition_css_is_shared():
    css = (tm_review_ui.STATIC_DIR / "_components" / "style.css").read_text(encoding="utf-8")

    assert "body.tm-page-ready main" in css
    assert "tmContentIn" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "body.tm-page-leaving main" not in css
    assert "filter: blur" not in css


def test_quality_and_settings_no_longer_use_raw_json_page(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "dashboard_memory_quality", lambda date=None: {"ok": True, "date": date})
    monkeypatch.setattr(tm_review_ui, "get_user_preferences", lambda: {"ok": True, "preferences": {}})
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    quality = client.get("/quality", headers=HOST)
    settings = client.get("/settings", headers=HOST)

    assert "quality-data" in quality.text
    assert "settings-data" in settings.text
    assert "记忆系统质量" in quality.text
    assert "沟通深度档位" in settings.text
    assert "记忆运维台" in quality.text
    assert "记忆运维台" in settings.text
    assert "思考路径耗时" in quality.text
    assert "本地数据库" in settings.text
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

    def fake_promote(fact, partition, slug):
        promoted.append((fact["source_id"], partition, slug))
        return {"ok": True, "wiki_path": f"wiki/{partition}/{slug}.md", "commit_sha": f"wiki-{len(promoted)}"}

    monkeypatch.setattr(tm_review_tools, "execute_promote", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "archive123")
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
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_promote_wiki_uses_default_targets_without_prompt_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path, "2026-05-01-1200-codex-operations.md")
    promoted: list[tuple[str, str, str]] = []

    def fake_promote(fact, partition, slug):
        promoted.append((fact["source_id"], partition, slug))
        return {"ok": True, "wiki_path": f"wiki/{partition}/{slug}.md", "commit_sha": "wiki-1"}

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
    assert "批量进入短期记忆" in response.text
    assert "Codex 推荐操作" in response.text
    assert "写入 Wiki 推荐" in response.text
    assert "wiki-modal" in response.text
    assert "短期记忆库：适合近期偏好" in response.text
    assert "写入长期事实记忆" in response.text
    assert "目标 wiki 分区" not in response.text
    assert "英文 slug 前缀" not in response.text
    assert "data-row-status" in response.text
    assert "展开原文预览（约 200 字）" in response.text
    assert "AI 修改建议" in response.text


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
