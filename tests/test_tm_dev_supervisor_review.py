from __future__ import annotations

import json
import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_dev_supervisor_review as supervisor


def isolate_limit_state(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "LIMIT_STATE_FILE", tmp_path / "limits.json")


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


def test_parse_and_record_session_limit_reset(monkeypatch, tmp_path):
    limit_path = tmp_path / "limits.json"
    monkeypatch.setattr(supervisor, "LIMIT_STATE_FILE", limit_path)
    now = supervisor._dt.datetime(2026, 6, 14, 13, 5, tzinfo=supervisor.TZ_CN)

    reset_at = supervisor.record_session_limit(
        "claude-official-review",
        "You've hit your session limit · resets 3:30pm (Asia/Shanghai)",
        now=now,
    )

    assert reset_at == supervisor._dt.datetime(2026, 6, 14, 15, 30, tzinfo=supervisor.TZ_CN)
    data = json.loads(limit_path.read_text(encoding="utf-8"))
    assert data["channels"]["claude-official-review"]["reset_at"].startswith("2026-06-14T15:30:00")


def test_active_limit_cooldown_does_not_infer_from_archives(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "LIMIT_STATE_FILE", tmp_path / "limits.json")
    archive_root = tmp_path / "reviews"
    day_dir = archive_root / "2026-06-14"
    day_dir.mkdir(parents=True)
    (day_dir / "failed.md").write_text(
        "channel: claude-official-review\nfailure_kind: session_limit\n"
        "You've hit your session limit · resets 3:30pm (Asia/Shanghai)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", archive_root)
    now = supervisor._dt.datetime(2026, 6, 14, 13, 5, tzinfo=supervisor.TZ_CN)

    reset_at = supervisor.active_limit_cooldown("claude-official-review", now=now)

    assert reset_at is None
    assert not (tmp_path / "limits.json").exists()


def test_parse_usage_status_from_claude_code_json():
    now = supervisor._dt.datetime(2026, 6, 14, 16, 20, tzinfo=supervisor.TZ_CN)
    output = json.dumps(
        {
            "type": "result",
            "result": (
                "You are currently using your subscription to power your Claude Code usage\n\n"
                "Current session: 13% used · resets Jun 14, 9:10pm (Asia/Shanghai)\n"
                "Current week (all models): 23% used · resets Jun 14, 10pm (Asia/Shanghai)"
            ),
        }
    )

    usage = supervisor.parse_usage_status(output, now=now)

    assert usage["windows"]["five_hour"]["used_percentage"] == 13
    assert usage["windows"]["five_hour"]["resets_at"].startswith("2026-06-14T21:10:00")
    assert usage["windows"]["seven_day"]["used_percentage"] == 23


def test_parse_usage_status_tolerates_garbled_separator():
    now = supervisor._dt.datetime(2026, 6, 14, 16, 20, tzinfo=supervisor.TZ_CN)
    output = json.dumps(
        {
            "result": (
                "Current session: 13% used бд resets Jun 14, 9:10pm (Asia/Shanghai)\n"
                "Current week (all models): 23% used ??? resets Jun 14, 10pm (Asia/Shanghai)"
            )
        }
    )

    usage = supervisor.parse_usage_status(output, now=now)

    assert usage["windows"]["five_hour"]["used_percentage"] == 13
    assert usage["windows"]["seven_day"]["used_percentage"] == 23


def test_active_usage_limit_only_blocks_exhausted_window():
    now = supervisor._dt.datetime(2026, 6, 14, 16, 20, tzinfo=supervisor.TZ_CN)
    available = {
        "windows": {
            "five_hour": {
                "used_percentage": 13,
                "resets_at": "2026-06-14T21:10:00+08:00",
            }
        }
    }
    exhausted = {
        "windows": {
            "five_hour": {
                "used_percentage": 100,
                "resets_at": "2026-06-14T21:10:00+08:00",
            }
        }
    }

    assert supervisor.active_usage_limit(available, now=now) is None
    assert supervisor.active_usage_limit(exhausted, now=now) == supervisor._dt.datetime(
        2026, 6, 14, 21, 10, tzinfo=supervisor.TZ_CN
    )


def test_run_review_respects_session_limit_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "LIMIT_STATE_FILE", tmp_path / "limits.json")
    launcher = tmp_path / "start-official-claude.ps1"
    launcher.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", launcher)
    claude_exe = tmp_path / "claude.exe"
    reset_at = supervisor._dt.datetime.now(supervisor.TZ_CN) + supervisor._dt.timedelta(hours=1)
    supervisor._save_limit_state(
        {
            "version": 1,
            "channels": {
                "claude-official-review": {
                    "reset_at": reset_at.isoformat(),
                    "updated_at": supervisor._dt.datetime.now(supervisor.TZ_CN).isoformat(),
                }
            },
        }
    )

    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }
    review_called = False
    future_reset_day = (supervisor._dt.datetime.now(supervisor.TZ_CN) + supervisor._dt.timedelta(days=1)).strftime("%b %d")

    def fake_runner(cmd, **_kwargs):
        nonlocal review_called
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if "/usage" in cmd:
            usage = json.dumps(
                {
                    "result": (
                        f"Current session: 100% used · resets {future_reset_day}, 9:10pm (Asia/Shanghai)\n"
                        f"Current week (all models): 23% used · resets {future_reset_day}, 10pm (Asia/Shanghai)"
                    )
                }
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=usage, stderr="")
        review_called = True
        return subprocess.CompletedProcess(cmd, 0, stdout="review ok", stderr="")

    args = supervisor.build_parser().parse_args(["--stage", "pcooldown", "review this"])
    try:
        supervisor.run_review(args, runner=fake_runner)
    except RuntimeError as exc:
        assert "confirmed by Claude CLI /usage" in str(exc)
    else:
        raise AssertionError("expected cooldown RuntimeError")
    assert review_called is False


def test_run_review_clears_stale_cooldown_when_usage_says_available(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "LIMIT_STATE_FILE", tmp_path / "limits.json")
    launcher = tmp_path / "start-official-claude.ps1"
    launcher.write_text("# noop\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "OFFICIAL_LAUNCHER", launcher)
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    reset_at = supervisor._dt.datetime.now(supervisor.TZ_CN) + supervisor._dt.timedelta(hours=1)
    supervisor._save_limit_state(
        {
            "version": 1,
            "channels": {
                "claude-official-review": {
                    "reset_at": reset_at.isoformat(),
                    "updated_at": supervisor._dt.datetime.now(supervisor.TZ_CN).isoformat(),
                }
            },
        }
    )
    payload = {
        "ClaudeExe": str(claude_exe),
        "Workdir": str(tmp_path),
        "ProxyExitLocation": "US",
        "AnthropicAuthToken": "unset",
        "AnthropicBaseUrl": None,
    }
    review_called = False

    def fake_runner(cmd, **_kwargs):
        nonlocal review_called
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if "/usage" in cmd:
            usage = json.dumps(
                {
                    "result": (
                        "Current session: 13% used · resets Jun 14, 9:10pm (Asia/Shanghai)\n"
                        "Current week (all models): 23% used · resets Jun 14, 10pm (Asia/Shanghai)"
                    )
                }
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=usage, stderr="")
        review_called = True
        return subprocess.CompletedProcess(cmd, 0, stdout="review ok", stderr="")

    args = supervisor.build_parser().parse_args(["--stage", "pcooldown-clear", "review this"])
    out_path = supervisor.run_review(args, runner=fake_runner)

    assert review_called is True
    assert out_path.exists()
    assert "claude-official-review" not in supervisor._load_limit_state()["channels"]


def test_append_ledger_inserts_under_review_section(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "# Ledger\n\n## 审核调用记录\n- old\n\n## 来源\n\n- source\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "LEDGER_PATH", ledger)
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    review = tmp_path / "sources" / "review.md"
    review.parent.mkdir()
    review.write_text("x", encoding="utf-8")

    supervisor.append_ledger(
        channel="claude-official-review",
        workspace="TigerMemory",
        role="tiger-development-reviewer",
        stage="p",
        session_ref_value="ref",
        prompt_hash="hash",
        requested_model="sonnet",
        requested_effort="high",
        session_mode="fresh",
        review_status="success",
        failure_kind=None,
        output_path=review,
    )

    text = ledger.read_text(encoding="utf-8")
    assert text.index("stage=p") < text.index("## 来源")
    assert text.rstrip().endswith("- source")


def test_auto_commit_review_artifacts_is_path_limited(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    (tmp_path / ".git").mkdir()
    archive = tmp_path / "sources" / "internal-analysis" / "development-reviews" / "2026-06-17" / "review.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("review", encoding="utf-8")
    ledger = tmp_path / "wiki" / "operations" / "development-supervisor-ledger.md"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("ledger", encoding="utf-8")
    rel_archive = "sources/internal-analysis/development-reviews/2026-06-17/review.md"
    rel_ledger = "wiki/operations/development-supervisor-ledger.md"
    calls = []
    diff_cached_calls = 0

    def fake_runner(cmd, **kwargs):
        nonlocal diff_cached_calls
        calls.append(cmd)
        assert kwargs["cwd"] == str(tmp_path)
        if cmd == ["git", "diff", "--cached", "--name-only"]:
            diff_cached_calls += 1
            output = "" if diff_cached_calls == 1 else f"{rel_archive}\n{rel_ledger}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr="")
        if cmd in (["git", "rev-parse", "HEAD"], ["git", "rev-parse", "origin/master"]):
            return subprocess.CompletedProcess(cmd, 0, stdout="abc\n", stderr="")
        if cmd == ["git", "rev-parse", "--short", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")
        if cmd in (
            ["git", "fetch", "origin", "master"],
            ["git", "add", "--", rel_archive, rel_ledger],
            [
                "git",
                "commit",
                "-m",
                "[codex] update: archive development supervisor review review-stage",
                "--",
                rel_archive,
                rel_ledger,
            ],
            ["git", "push", "origin", "master"],
        ):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected git command: {cmd}")

    sha = supervisor.auto_commit_review_artifacts([archive, ledger], stage="review stage", runner=fake_runner)

    assert sha == "abc123"
    assert ["git", "add", "--", rel_archive, rel_ledger] in calls
    commit_cmd = next(cmd for cmd in calls if cmd[:2] == ["git", "commit"])
    assert "." not in commit_cmd


def test_auto_commit_review_artifacts_refuses_existing_staged_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    (tmp_path / ".git").mkdir()
    archive = tmp_path / "sources" / "internal-analysis" / "development-reviews" / "2026-06-17" / "review.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("review", encoding="utf-8")

    def fake_runner(cmd, **kwargs):
        if cmd == ["git", "diff", "--cached", "--name-only"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="other.md\n", stderr="")
        raise AssertionError(f"unexpected git command after staged blocker: {cmd}")

    try:
        supervisor.auto_commit_review_artifacts([archive], stage="review", runner=fake_runner)
    except RuntimeError as exc:
        assert "staged changes already exist" in str(exc)
    else:
        raise AssertionError("expected staged changes to block supervisor auto-commit")


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


def test_api_test_review_uses_same_archive_and_ledger_spine(monkeypatch, tmp_path):
    claude_exe = tmp_path / "claude.exe"
    claude_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "API_TEST_EXE", claude_exe)
    monkeypatch.setattr(supervisor, "WORKSPACES", {"TigerMemory": tmp_path})
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(supervisor, "LEDGER_PATH", tmp_path / "ledger.md")
    monkeypatch.setattr(supervisor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "SESSION_FILE", tmp_path / "sessions.json")
    supervisor.LEDGER_PATH.write_text("# Ledger\n\n## 审核调用记录\n", encoding="utf-8")

    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd == [str(claude_exe), "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="2.1.110 (Claude Code)\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="api draft review ok", stderr="")

    args = supervisor.build_parser().parse_args(
        ["--channel", "api_test", "--stage", "papi", "review this"]
    )
    out_path = supervisor.run_review(args, runner=fake_runner)

    assert out_path.exists()
    assert out_path.is_relative_to(tmp_path / "reviews")
    text = out_path.read_text(encoding="utf-8")
    assert "channel: claude-api-test" in text
    assert "api draft review ok" in text
    ledger = supervisor.LEDGER_PATH.read_text(encoding="utf-8")
    assert "channel=claude-api-test" in ledger
    assert "stage=papi" in ledger
    assert "archive=reviews/" in ledger


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


def test_archive_review_does_not_overwrite_existing_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "ARCHIVE_ROOT", tmp_path / "development-reviews")

    first = supervisor.archive_review(
        channel="claude-official-review",
        workspace="TigerMemory",
        role="tiger-development-reviewer",
        stage="same-stage",
        session_ref_value="first",
        prompt_hash="abc123",
        requested_model="sonnet",
        requested_effort="medium",
        session_mode="fresh",
        review_status="failed",
        failure_kind="cli_error",
        prompt="review",
        output="failed",
    )
    second = supervisor.archive_review(
        channel="claude-official-review",
        workspace="TigerMemory",
        role="tiger-development-reviewer",
        stage="same-stage",
        session_ref_value="second",
        prompt_hash="abc123",
        requested_model="sonnet",
        requested_effort="medium",
        session_mode="fresh",
        review_status="success",
        failure_kind=None,
        prompt="review",
        output="ok",
    )

    assert first != second
    assert first.exists()
    assert second.exists()
    assert "review_status: failed" in first.read_text(encoding="utf-8")
    assert "review_status: success" in second.read_text(encoding="utf-8")


def test_run_review_passes_model_and_effort_without_changing_session_key(monkeypatch, tmp_path):
    isolate_limit_state(monkeypatch, tmp_path)
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
    isolate_limit_state(monkeypatch, tmp_path)
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


def test_run_review_separates_prompt_that_starts_with_dash(monkeypatch, tmp_path):
    isolate_limit_state(monkeypatch, tmp_path)
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

    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("---\ntitle: frontmatter\n---\nreview this", encoding="utf-8")
    args = supervisor.build_parser().parse_args(["--stage", "pdash", "--prompt-file", str(prompt_file)])
    supervisor.run_review(args, runner=fake_runner)

    claude_cmd = calls[-1]
    assert "--" in claude_cmd
    assert claude_cmd[claude_cmd.index("--") + 1].startswith("---\n")


def test_run_review_archives_session_busy_failure(monkeypatch, tmp_path):
    isolate_limit_state(monkeypatch, tmp_path)
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
    isolate_limit_state(monkeypatch, tmp_path)
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
    isolate_limit_state(monkeypatch, tmp_path)
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
