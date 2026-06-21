from __future__ import annotations

import json
import pathlib
import os
import sys
import types

from fastapi.testclient import TestClient

from tigermemory_dashboard import server


HOST = {"Host": "127.0.0.1:9777"}


def test_api_update_status_is_read_only(tmp_path, monkeypatch):
    calls = []

    class FakeUpdate:
        @staticmethod
        def get_update_status(root, refresh_remote=False):
            calls.append((pathlib.Path(root), refresh_remote))
            return {
                "ok": True,
                "source_mode": "git_source",
                "app_root": str(root),
                "update_available": True,
                "safe_to_apply": True,
                "recommended_action": "Run tm update apply.",
            }

    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setattr(server, "tm_update", FakeUpdate)
    monkeypatch.setattr(server, "resolve_app_root", lambda: app_root)
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ensure_write_ready", lambda *_args, **_kwargs: None)
    client = TestClient(server.app)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/update/status?refresh=true", headers=HOST)

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_mode"] == "git_source"
    assert calls == [(app_root, False)]


def test_dashboard_static_surfaces_public_ask_llm_status() -> None:
    script = pathlib.Path(
        "packages/tigermemory-dashboard/src/tigermemory_dashboard/static/dashboard-pages.js"
    ).read_text(encoding="utf-8")

    assert "public_ask_llm" in script
    assert "在线问答" in script
    assert "tm llm status --json" in script
    assert "tm ask --offline" in script


def test_start_shell_uses_public_starter_commands() -> None:
    payload = server._start_shell()
    commands = [item["command"] for item in payload["commands"]]

    assert 'tm search --scope wiki --query "agent behavior rules"' in commands
    assert 'tm ask --offline --query "agent behavior rules" --scope wiki' in commands
    assert all("项目画布" not in command for command in commands)
    assert all("hello local memory" not in command for command in commands)


def test_start_static_uses_install_success_intro_and_public_commands() -> None:
    html = pathlib.Path(
        "packages/tigermemory-dashboard/src/tigermemory_dashboard/static/start.html"
    ).read_text(encoding="utf-8")

    assert "欢迎来到 TigerMemory" in html
    assert html.count("data-onboarding-slide") == 7
    assert "API Key 只会保存到本机 runtime 配置" in html
    assert "保存并接入 TigerMemory" in html
    assert "llm-config-status" in html
    assert "llm-provider" in html
    assert "OpenAI-compatible" in html
    assert "llm-admin-model" in html
    assert "普通版 / local" in html
    assert "高级版 / hybrid" in html
    assert "data-start-depth=\"A\"" in html
    assert "data-start-depth=\"D\"" in html
    assert "depth-preview-answer" in html
    assert "depth-preview-note" in html
    pages_js = pathlib.Path(
        "packages/tigermemory-dashboard/src/tigermemory_dashboard/static/dashboard-pages.js"
    ).read_text(encoding="utf-8")
    assert "depthPreviews" in pages_js
    assert "D 全套" in pages_js
    assert "验收清单" in pages_js
    assert "DEEPSEEK_API_KEY" in pages_js
    assert "TIGERMEMORY_LLM_PROVIDER" in pages_js
    assert "/api/start/llm-config" in pages_js
    assert 'tm search --scope wiki --query "agent behavior rules"' in html
    assert 'tm ask --offline --query "agent behavior rules" --scope wiki' in html
    assert "项目画布" not in html
    assert "hello local memory" not in html


def test_start_onboarding_i18n_covers_agent_connect_and_english() -> None:
    data = json.loads(
        pathlib.Path("packages/tigermemory-dashboard/src/tigermemory_dashboard/static/i18n.json").read_text(
            encoding="utf-8"
        )
    )
    zh = data["zh"]
    en = data["en"]

    required = [
        "start.welcome.title",
        "start.mode.title",
        "start.depth.title",
        "start.llm.title",
        "start.agent.title",
        "start.dashboard.onboarding_title",
        "start.finish.title",
        "start.agent.status.missing_block",
        "start.agent.target.root-agents.label",
        "start.agent.target.pre-tool-use-example.summary",
    ]
    for key in required:
        assert key in zh
        assert key in en
        assert zh[key] != en[key]

    assert en["start.agent.status.missing_block"] == "Built-in template ready"
    assert "built-in default templates" in en["start.agent.template_note"]
    assert "TigerMemory 已经内置" in zh["start.agent.copy"]


def test_start_shell_includes_preferences(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "PREFS_DB", tmp_path / "prefs.sqlite")

    payload = server._start_shell()

    assert payload["preferences"]["communication_depth"]
    assert "llm_status" in payload


def test_start_llm_config_writes_runtime_env_without_echoing_secret(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(env_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_ADMIN_MODEL", raising=False)
    keys = ["TIGERMEMORY_LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_ADMIN_MODEL"]

    try:
        result = server.save_start_llm_config(
            server.StartLlmConfigRequest(
                api_key="sk-test-secret",
                base_url="https://api.deepseek.com/v1/chat/completions",
                model="deepseek-v4-flash",
            )
        )

        assert result["ok"] is True
        assert result["llm_status"]["llm_configured"] is True
        assert result["llm_status"]["effective_provider"] == "deepseek"
        assert result["llm_status"]["providers"][0]["api_key"]["configured"] is True
        assert "sk-test-secret" not in str(result)
        assert "TIGERMEMORY_LLM_PROVIDER=deepseek" in env_path.read_text(encoding="utf-8")
        assert "DEEPSEEK_API_KEY=sk-test-secret" in env_path.read_text(encoding="utf-8")
        assert "DEEPSEEK_BASE_URL=https://api.deepseek.com/v1/chat/completions" in env_path.read_text(encoding="utf-8")
        assert "DEEPSEEK_MODEL=deepseek-v4-flash" in env_path.read_text(encoding="utf-8")
    finally:
        for key in keys:
            os.environ.pop(key, None)


def test_start_llm_config_supports_openai_compatible_without_duplicate_secret(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(env_path))
    keys = [
        "TIGERMEMORY_LLM_PROVIDER",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_ADMIN_MODEL",
        "OPENAI_API_KEY",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    try:
        result = server.save_start_llm_config(
            server.StartLlmConfigRequest(
                provider="openai_compatible",
                api_key="sk-openai-compatible-secret",
                base_url="https://api.example.com/v1/chat/completions",
                model="gpt-compatible-mini",
            )
        )

        text = env_path.read_text(encoding="utf-8")
        assert result["ok"] is True
        assert result["llm_status"]["effective_provider"] == "openai_compatible"
        assert result["llm_status"]["providers"][1]["configured"] is True
        assert "sk-openai-compatible-secret" not in str(result)
        assert "TIGERMEMORY_LLM_PROVIDER=openai_compatible" in text
        assert "DEEPSEEK_API_KEY=sk-openai-compatible-secret" in text
        assert "DEEPSEEK_BASE_URL=https://api.example.com/v1/chat/completions" in text
        assert "DEEPSEEK_MODEL=gpt-compatible-mini" in text
        assert "DEEPSEEK_ADMIN_MODEL=gpt-compatible-mini" in text
        assert "OPENAI_API_KEY=" not in text
    finally:
        for key in keys:
            os.environ.pop(key, None)


def test_start_llm_config_route_writes_runtime_env_without_echoing_secret(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(env_path))
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    keys = ["TIGERMEMORY_LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_ADMIN_MODEL"]
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    try:
        client = TestClient(server.app)
        client.get("/", headers=HOST, follow_redirects=False)

        response = client.post(
            "/api/start/llm-config",
            headers=HOST,
            json={
                "provider": "deepseek",
                "api_key": "sk-route-secret",
                "base_url": "https://api.deepseek.com/v1/chat/completions",
                "model": "deepseek-v4-flash",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["llm_status"]["llm_configured"] is True
        assert "sk-route-secret" not in str(payload)
        assert "DEEPSEEK_API_KEY=sk-route-secret" in env_path.read_text(encoding="utf-8")
    finally:
        for key in keys:
            os.environ.pop(key, None)


def test_start_llm_config_rejects_unsafe_remote_http(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(tmp_path / ".env"))
    monkeypatch.delenv("TM_ALLOW_UNSECURE_HTTP", raising=False)

    try:
        server.save_start_llm_config(
            server.StartLlmConfigRequest(
                api_key="sk-test-secret",
                base_url="http://example.com/v1/chat/completions",
                model="deepseek-v4-flash",
            )
        )
    except (RuntimeError, ValueError) as exc:
        assert "Bearer Guard" in str(exc) or "传输安全" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unsafe remote HTTP endpoint should be rejected")


def test_daily_review_missing_private_digest_uses_empty_public_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(server, "tm_memory_reflection", None)
    monkeypatch.setattr(server, "_worktree_dirty_state", lambda: {"dirty": False})
    monkeypatch.setattr(server, "_mem0_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server, "_run_cache_get", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(server, "_run_cache_set", lambda *_args, **_kwargs: None)

    payload = server.daily_review_data("2099-01-01")

    assert payload["fallback"] is True
    assert payload["inbox_rows"] == []
    assert payload["hidden_inbox_rows"] == []


def test_dashboard_main_no_open_prints_start_url_without_browser(monkeypatch, capsys) -> None:
    calls = []
    fake_uvicorn = types.SimpleNamespace(run=lambda app, host, port: calls.append((host, port)))

    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(server, "register_dashboard_bind_host", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "start_idle_watcher", lambda: True)
    monkeypatch.setattr(server, "start_quality_cache_warmer", lambda: True)
    monkeypatch.setattr(
        server.webbrowser,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("browser should not open")),
    )

    assert server.main(["--port", "2000", "--no-open"]) == 0

    out = capsys.readouterr().out
    assert "dashboard_url=http://127.0.0.1:2000/start" in out
    assert "browser=disabled" in out
    assert calls == [("127.0.0.1", 2000)]


def test_quality_cache_warmer_skips_when_private_reflection_module_missing(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(server, "_dashboard_background_enabled", lambda: True)
    monkeypatch.setattr(server, "tm_memory_reflection", None)

    assert server.start_quality_cache_warmer(interval_seconds=5) is False


def test_dashboard_fast_agent_doctor_includes_public_ask_llm(monkeypatch) -> None:
    fake_agent_doctor = types.SimpleNamespace(
        check_tm_http=lambda timeout=0.3: {"name": "tm_http", "status": "ok", "ok": True},
        check_mem0=lambda timeout=0.5: {"name": "mem0_api", "status": "ok", "ok": True},
        check_public_ask_llm=lambda: {
            "name": "public_ask_llm",
            "status": "ok",
            "ok": True,
            "llm_configured": True,
            "routine_model": "deepseek-v4-flash",
        },
    )
    monkeypatch.setattr(server, "tm_agent_doctor", fake_agent_doctor)
    monkeypatch.setattr(server, "_dashboard_worktree_check", lambda: {"name": "worktree", "status": "ok", "ok": True})

    report = server._dashboard_fast_agent_doctor()

    checks = {check["name"]: check for check in report["checks"]}
    assert report["status"] == "ok"
    assert checks["public_ask_llm"]["llm_configured"] is True
