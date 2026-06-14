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


def test_streaming_command_writes_stdout_stderr_to_run_log(tmp_path):
    log_path = tmp_path / "run.log"
    result = supervisor.run_streaming_command(
        [
            sys.executable,
            "-c",
            "import sys; print('hello stdout', flush=True); print('hello stderr', file=sys.stderr, flush=True)",
        ],
        cwd=str(tmp_path),
        env={},
        timeout=5,
        stall_timeout=0,
        heartbeat_interval=0,
        log_path=log_path,
    )

    assert result.returncode == 0
    assert "hello stdout" in result.stdout
    assert "hello stderr" in result.stderr
    text = log_path.read_text(encoding="utf-8")
    assert "[stdout] hello stdout" in text
    assert "[stderr] hello stderr" in text


def test_streaming_command_hard_timeout_keeps_partial_output(tmp_path):
    log_path = tmp_path / "timeout.log"
    result = supervisor.run_streaming_command(
        [
            sys.executable,
            "-c",
            "import time; print('partial stdout', flush=True); time.sleep(5)",
        ],
        cwd=str(tmp_path),
        env={},
        timeout=1,
        stall_timeout=0,
        heartbeat_interval=0,
        log_path=log_path,
    )

    assert result.returncode == 124
    assert "partial stdout" in result.stdout
    assert "hard timeout" in result.stderr
    text = log_path.read_text(encoding="utf-8")
    assert "partial stdout" in text
    assert "[hard_timeout]" in text


def test_classify_streaming_timeout_failures():
    assert supervisor.classify_failure("Claude review hard timeout after 3600 seconds") == "hard_timeout"
    assert supervisor.classify_failure("Claude review had no stream activity for 600 seconds") == "stall_timeout"


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
        requested_model="sonnet",
        requested_effort="medium",
        session_mode="fresh",
        review_status="success",
        failure_kind=None,
        prompt="please review api_key=secret-token and Bearer verysecretbearertoken",
        output="ok sk-abcdefghijklmnop",
    )

    text = out_path.read_text(encoding="utf-8")
    assert raw_session_id not in text
    assert f"session_ref: {supervisor.session_ref(raw_session_id)}" in text
    assert "requested_model: sonnet" in text
    assert "requested_effort: medium" in text
    assert "session_mode: fresh" in text
    assert "review_status: success" in text
    assert "secret-token" not in text
    assert "verysecretbearertoken" not in text
    assert "sk-abcdefghijklmnop" not in text
    assert "[REDACTED]" in text


def test_run_review_passes_model_and_effort_without_changing_session_key(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", tmp_path / "start-official-claude.ps1")
    supervisor.OFFICIAL_LAUNCHER.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")

    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="review ok", stderr="")

    args = supervisor.build_parser().parse_args(
        [
            "--stage",
            "pmodel",
            "--model",
            "sonnet",
            "--effort",
            "medium",
            "--session-mode",
            "stage",
            "review this",
        ]
    )
    out_path = supervisor.run_review(args, runner=fake_runner)

    claude_cmd = calls[-1]
    assert "--model" in claude_cmd
    assert claude_cmd[claude_cmd.index("--model") + 1] == "sonnet"
    assert "--effort" in claude_cmd
    assert claude_cmd[claude_cmd.index("--effort") + 1] == "medium"
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "requested_model: sonnet" in text
    assert "requested_effort: medium" in text
    data = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert "claude-official-review|TigerMemory|tiger-development-reviewer|pmodel" in data["sessions"]


def test_run_review_defaults_to_fresh_session_without_persistent_registry(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", tmp_path / "start-official-claude.ps1")
    supervisor.OFFICIAL_LAUNCHER.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")

    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="review ok", stderr="")

    args = supervisor.build_parser().parse_args(["--stage", "pfresh", "--model", "sonnet", "review this"])
    out_path = supervisor.run_review(args, runner=fake_runner)

    claude_cmd = calls[-1]
    assert "--session-id" in claude_cmd
    assert not (tmp_path / "sessions.json").exists()
    assert "session_mode: fresh" in out_path.read_text(encoding="utf-8")


def test_run_review_archives_session_busy_failure(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", tmp_path / "start-official-claude.ps1")
    supervisor.OFFICIAL_LAUNCHER.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")

    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }

    def fake_runner(cmd, **kwargs):
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="Error: Session ID 00000000-0000-0000-0000-000000000000 is already in use.",
        )

    args = supervisor.build_parser().parse_args(["--stage", "pfail", "review this"])
    try:
        supervisor.run_review(args, runner=fake_runner)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected failed Claude call")

    archives = list((tmp_path / "reviews").rglob("*.md"))
    assert len(archives) == 1
    text = archives[0].read_text(encoding="utf-8")
    assert "review_status: failed" in text
    assert "failure_kind: session_busy" in text
    assert "status=failed" in supervisor.LEDGER_PATH.read_text(encoding="utf-8")


def test_run_review_passes_add_dir_and_archives_it(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    extra_dir = tmp_path / "external-config"
    extra_dir.mkdir()
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", tmp_path / "start-official-claude.ps1")
    supervisor.OFFICIAL_LAUNCHER.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")

    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="review ok", stderr="")

    args = supervisor.build_parser().parse_args(
        [
            "--stage",
            "padddir",
            "--add-dir",
            str(extra_dir),
            "review this",
        ]
    )
    out_path = supervisor.run_review(args, runner=fake_runner)

    claude_cmd = calls[-1]
    assert "--add-dir" in claude_cmd
    assert claude_cmd[claude_cmd.index("--add-dir") + 1] == str(extra_dir)
    text = out_path.read_text(encoding="utf-8")
    assert "add_dirs:" in text
    assert str(extra_dir) in text


def test_run_review_archives_timeout_failure(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", tmp_path / "start-official-claude.ps1")
    supervisor.OFFICIAL_LAUNCHER.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")

    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }

    def fake_runner(cmd, **kwargs):
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        raise subprocess.TimeoutExpired(cmd, timeout=3, output="partial stdout", stderr="partial stderr")

    args = supervisor.build_parser().parse_args(["--stage", "ptimeout", "--timeout", "3", "review this"])
    try:
        supervisor.run_review(args, runner=fake_runner)
    except RuntimeError as exc:
        assert "timed out and was archived" in str(exc)
    else:
        raise AssertionError("expected timeout RuntimeError")

    archives = list((tmp_path / "reviews").rglob("*.md"))
    assert len(archives) == 1
    text = archives[0].read_text(encoding="utf-8")
    assert "review_status: failed" in text
    assert "failure_kind: timeout" in text
    assert "partial stdout" in text
    assert "partial stderr" in text
    assert "failure=timeout" in supervisor.LEDGER_PATH.read_text(encoding="utf-8")
