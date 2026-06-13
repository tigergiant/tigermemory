from __future__ import annotations

import json
import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_dev_supervisor_review as supervisor


def test_official_env_clears_custom_provider_keys():
    env = supervisor.official_env(
        {
            "ANTHROPIC_AUTH_TOKEN": "secret",
            "ANTHROPIC_API_KEY": "secret",
            "ANTHROPIC_BASE_URL": "https://custom.example",
            "PATH": "keep",
        }
    )

    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["PATH"] == "keep"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7891"
    assert env["CLAUDE_CONFIG_DIR"].endswith("ClaudeCodeOfficial\\config")


def test_api_test_env_uses_legacy_proxy_without_forcing_official_config():
    env = supervisor.api_test_env({"PATH": "keep", "ANTHROPIC_AUTH_TOKEN": "legacy-secret"})

    assert env["PATH"] == "keep"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "legacy-secret"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert "CLAUDE_CONFIG_DIR" not in env


def test_session_id_is_stable_per_channel_workspace_role_stage(monkeypatch, tmp_path):
    session_path = tmp_path / "claude-sessions.json"
    monkeypatch.setattr(supervisor, "SESSION_FILE", session_path)

    one = supervisor.ensure_session_id("claude-official-review", "TigerMemory", "tiger-development-reviewer", "p313")
    two = supervisor.ensure_session_id("claude-official-review", "TigerMemory", "tiger-development-reviewer", "p313")
    other = supervisor.ensure_session_id("claude-official-review", "TigerMemory", "tiger-development-reviewer", "p314")

    assert one == two
    assert one != other
    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert "claude-official-review|TigerMemory|tiger-development-reviewer|p313" in data["sessions"]


def test_run_official_check_rejects_uncleared_provider_env(monkeypatch, tmp_path):
    launcher = tmp_path / "start-official-claude.ps1"
    launcher.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", launcher)

    payload = {
        "ClaudeExe": str(tmp_path / "claude.exe"),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "set",
        "AnthropicBaseUrl": None,
    }

    def fake_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess(_args[0], 0, stdout=json.dumps(payload), stderr="")

    try:
        supervisor.run_official_check("TigerMemory", runner=fake_runner)
    except RuntimeError as exc:
        assert "did not clear custom Anthropic" in str(exc)
    else:
        raise AssertionError("expected provider env guard to fail")


def test_run_api_test_check_uses_whitelisted_workspace_and_version(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    workspace = tmp_path / "TigerMemory"
    workspace.mkdir()
    monkeypatch.setattr(supervisor, "API_TEST_EXE", claude_exe)
    monkeypatch.setattr(supervisor, "WORKSPACES", {"TigerMemory": workspace})

    def fake_runner(cmd, **kwargs):
        assert cmd == [str(claude_exe), "--version"]
        assert kwargs["cwd"] == str(workspace)
        assert kwargs["env"]["HTTP_PROXY"] == "http://127.0.0.1:7890"
        return subprocess.CompletedProcess(cmd, 0, stdout="2.1.110 (Claude Code)\n", stderr="")

    payload = supervisor.run_api_test_check("TigerMemory", runner=fake_runner)

    assert payload["Channel"] == "claude-api-test"
    assert payload["ProviderSecretRead"] is False
    assert payload["Workdir"] == str(workspace)


def test_archive_redacts_secret_like_text(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "development-reviews")
    raw_session_id = "00000000-0000-0000-0000-000000000000"

    out_path = supervisor.archive_review(
        channel="claude-official-review",
        workspace="TigerMemory",
        role="tiger-development-reviewer",
        stage="p0",
        session_ref_value=supervisor.session_ref(raw_session_id),
        prompt_hash="abc123",
        prompt="please review api_key=secret-token and Bearer verysecretbearertoken",
        output="ok sk-abcdefghijklmnop",
    )

    text = out_path.read_text(encoding="utf-8")
    assert raw_session_id not in text
    assert f"session_ref: {supervisor.session_ref(raw_session_id)}" in text
    assert "secret-token" not in text
    assert "verysecretbearertoken" not in text
    assert "sk-abcdefghijklmnop" not in text
    assert "[REDACTED]" in text
