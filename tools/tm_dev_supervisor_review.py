from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import uuid
from zoneinfo import ZoneInfo


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TZ_CN = ZoneInfo("Asia/Shanghai")

OFFICIAL_CHANNEL = "claude-official-review"
API_TEST_CHANNEL = "claude-api-test"
OFFICIAL_LAUNCHER = pathlib.Path(
    r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\start-official-claude.ps1"
)
API_TEST_EXE = pathlib.Path(r"C:\Users\Giant\AppData\Local\Microsoft\WinGet\Links\claude.exe")
API_TEST_EXPECTED_VERSION = "2.1.110"
OFFICIAL_CONFIG_DIR = pathlib.Path(r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\config")
OFFICIAL_PLUGIN_DIR = pathlib.Path(r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\plugins")
OFFICIAL_TMP_DIR = pathlib.Path(r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\tmp")

DEFAULT_WORKSPACE = "TigerMemory"
DEFAULT_ROLE = "tiger-development-reviewer"
DEFAULT_STAGE = "p0"
WORKSPACES = {
    "TigerMemory": REPO_ROOT,
    "NewProject": pathlib.Path(r"C:\Users\Giant\Documents\New project"),
    "ClaudeHub": pathlib.Path(r"C:\Users\Giant\Documents\ClaudeHub"),
    "ClaudeScratch": pathlib.Path(r"C:\Users\Giant\Documents\ClaudeScratch"),
}
SUPERVISOR_STATE_DIR = REPO_ROOT / ".supervisor"
SESSION_FILE = SUPERVISOR_STATE_DIR / "claude-sessions.json"
LEGACY_SESSION_FILE = REPO_ROOT / ".tmp" / "dev-supervisor" / "claude-sessions.json"
RUN_LOG_ROOT = SUPERVISOR_STATE_DIR / "run-logs"
LIMIT_STATE_FILE = SUPERVISOR_STATE_DIR / "claude-limits.json"
ARCHIVE_ROOT = REPO_ROOT / "sources" / "internal-analysis" / "development-reviews"
LEDGER_PATH = REPO_ROOT / "wiki" / "operations" / "development-supervisor-ledger.md"

PROVIDER_ENV_KEYS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_REASONING_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BETAS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "ENABLE_TOOL_SEARCH",
)

SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s`\"']+"),
    re.compile(r"(?i)(auth[_-]?token\s*[:=]\s*)[^\s`\"']+"),
    re.compile(r"sk-[A-Za-z0-9]{12,}"),
)


def _now() -> _dt.datetime:
    return _dt.datetime.now(TZ_CN)


def _read_prompt(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.prompt:
        parts.append(args.prompt)
    if args.prompt_file:
        parts.append(pathlib.Path(args.prompt_file).read_text(encoding="utf-8"))
    if not parts:
        raise SystemExit("prompt or --prompt-file is required unless --check-only is used")
    return "\n\n".join(parts).strip()


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def session_ref(session_id: str) -> str:
    return _sha12(session_id)


def _redact(text: str) -> str:
    value = text
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", value)
    return value


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_session_limit_reset(output: str, *, now: _dt.datetime | None = None) -> _dt.datetime | None:
    match = re.search(r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", output, re.IGNORECASE)
    if not match:
        return None
    base = now or _now()
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    marker = (match.group(3) or "").lower()
    if marker == "pm" and hour < 12:
        hour += 12
    if marker == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    reset_at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset_at <= base:
        reset_at += _dt.timedelta(days=1)
    return reset_at


def _load_limit_state(path: pathlib.Path | None = None) -> dict:
    path = LIMIT_STATE_FILE if path is None else path
    if not path.exists():
        return {"version": 1, "channels": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_limit_state(data: dict, path: pathlib.Path | None = None) -> None:
    path = LIMIT_STATE_FILE if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_session_limit(channel: str, output: str, *, now: _dt.datetime | None = None) -> _dt.datetime | None:
    reset_at = _parse_session_limit_reset(output, now=now)
    if reset_at is None:
        return None
    data = _load_limit_state()
    channels = data.setdefault("channels", {})
    channels[channel] = {
        "reset_at": reset_at.isoformat(),
        "updated_at": (now or _now()).isoformat(),
        "message_sha256_12": _sha12(output),
    }
    _save_limit_state(data)
    return reset_at


def active_limit_cooldown(channel: str, *, now: _dt.datetime | None = None) -> _dt.datetime | None:
    current = now or _now()
    data = _load_limit_state()
    record = data.get("channels", {}).get(channel)
    if record:
        try:
            reset_at = _dt.datetime.fromisoformat(record["reset_at"])
        except (KeyError, ValueError):
            reset_at = None
        if reset_at is not None and reset_at > current:
            return reset_at
    return None


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_usage_reset(value: str, *, now: _dt.datetime | None = None) -> _dt.datetime | None:
    base = now or _now()
    text = value.strip()
    match = re.search(
        r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        text,
        re.IGNORECASE,
    )
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if month is None:
            return None
        day = int(match.group(2))
        hour = int(match.group(3))
        minute = int(match.group(4) or "0")
        marker = match.group(5).lower()
        if marker == "pm" and hour < 12:
            hour += 12
        if marker == "am" and hour == 12:
            hour = 0
        try:
            reset_at = base.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if reset_at < base - _dt.timedelta(days=180):
            reset_at = reset_at.replace(year=reset_at.year + 1)
        return reset_at
    return _parse_session_limit_reset(text, now=base)


def parse_usage_status(output: str, *, now: _dt.datetime | None = None) -> dict:
    """Parse Claude Code `/usage` output.

    Claude Code currently exposes subscription limits through the interactive
    `/usage` command and status-line JSON. The wrapper uses `/usage` as the
    cheap CLI probe before trusting a cached local cooldown.
    """
    raw_text = output.strip()
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        result = raw_text
    else:
        result = str(payload.get("result") or payload.get("message") or raw_text)

    windows: dict[str, dict] = {}
    patterns = {
        "five_hour": r"Current session:\s*([0-9]+(?:\.[0-9]+)?)%\s*used\b.*?\bresets\s+([^\n(]+)",
        "seven_day": r"Current week(?:\s*\(all models\))?:\s*([0-9]+(?:\.[0-9]+)?)%\s*used\b.*?\bresets\s+([^\n(]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, result, re.IGNORECASE)
        if not match:
            continue
        reset_at = _parse_usage_reset(match.group(2), now=now)
        windows[key] = {
            "used_percentage": float(match.group(1)),
            "resets_at": reset_at.isoformat() if reset_at else None,
        }
    return {"raw": result, "windows": windows}


def active_usage_limit(usage: dict, *, now: _dt.datetime | None = None) -> _dt.datetime | None:
    current = now or _now()
    resets: list[_dt.datetime] = []
    for window in usage.get("windows", {}).values():
        try:
            used = float(window.get("used_percentage", 0))
        except (TypeError, ValueError):
            continue
        reset_raw = window.get("resets_at")
        if not reset_raw:
            continue
        try:
            reset_at = _dt.datetime.fromisoformat(reset_raw)
        except ValueError:
            continue
        if used >= 99.5 and reset_at > current:
            resets.append(reset_at)
    return min(resets) if resets else None


def clear_limit_cooldown(channel: str) -> None:
    data = _load_limit_state()
    channels = data.get("channels", {})
    if channel in channels:
        channels.pop(channel, None)
        _save_limit_state(data)


def record_cli_usage_limit(channel: str, reset_at: _dt.datetime, *, now: _dt.datetime | None = None) -> None:
    current = now or _now()
    data = _load_limit_state()
    data.setdefault("channels", {})[channel] = {
        "reset_at": reset_at.isoformat(),
        "updated_at": current.isoformat(),
        "message_sha256_12": "from-cli-usage",
        "source": "claude_cli_usage",
    }
    _save_limit_state(data)


def _yaml_list(key: str, values: list[str]) -> str:
    if not values:
        return f"{key}: []"
    rows = [f"{key}:"]
    for value in values:
        safe = _redact(value).replace("'", "''")
        rows.append(f"  - '{safe}'")
    return "\n".join(rows)


def _run_log_path(stage: str, role: str, prompt_hash: str) -> pathlib.Path:
    date = _now().strftime("%Y-%m-%d")
    safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "-", stage).strip("-") or "stage"
    safe_role = re.sub(r"[^A-Za-z0-9_.-]+", "-", role).strip("-") or "role"
    return RUN_LOG_ROOT / date / f"{safe_stage}-{safe_role}-{prompt_hash}.log"


def _terminate_process(process: subprocess.Popen, *, grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace_seconds)


def run_streaming_command(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    stall_timeout: int,
    heartbeat_interval: int,
    log_path: pathlib.Path,
    popen_factory=subprocess.Popen,
) -> subprocess.CompletedProcess:
    """Run Claude while mirroring output into a local run log.

    A nonzero hard timeout is only a last-resort safety stop. By default,
    stall_timeout is disabled because Claude can legitimately think for a long
    time without emitting user-visible text.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    start = time.monotonic()
    last_activity = [start]
    next_heartbeat = start + heartbeat_interval if heartbeat_interval > 0 else None
    forced_returncode: int | None = None

    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        log.write(f"# Claude supervisor run log\nstarted_at: {_now().isoformat()}\n\n")
        lock = threading.Lock()

        def reader(stream, label: str, chunks: list[str]) -> None:
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    with lock:
                        chunks.append(line)
                        log.write(f"[{label}] {line}")
                        last_activity[0] = time.monotonic()
            finally:
                stream.close()

        process = popen_factory(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_thread = threading.Thread(target=reader, args=(process.stdout, "stdout", stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=reader, args=(process.stderr, "stderr", stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        while process.poll() is None:
            now = time.monotonic()
            elapsed = now - start
            idle = now - last_activity[0]
            if next_heartbeat is not None and now >= next_heartbeat:
                msg = (
                    f"Claude still running: elapsed={int(elapsed)}s, "
                    f"idle={int(idle)}s, log={log_path}"
                )
                print(msg, file=sys.stderr, flush=True)
                with lock:
                    log.write(f"[heartbeat] {msg}\n")
                next_heartbeat = now + heartbeat_interval
            if timeout > 0 and elapsed >= timeout:
                msg = f"Claude review hard timeout after {timeout} seconds; run log: {log_path}"
                with lock:
                    stderr_chunks.append(msg)
                    log.write(f"[hard_timeout] {msg}\n")
                _terminate_process(process)
                forced_returncode = 124
                break
            if stall_timeout > 0 and idle >= stall_timeout:
                msg = (
                    f"Claude review had no stream activity for {stall_timeout} seconds; "
                    f"run log: {log_path}"
                )
                with lock:
                    stderr_chunks.append(msg)
                    log.write(f"[stall_timeout] {msg}\n")
                _terminate_process(process)
                forced_returncode = 124
                break
            time.sleep(1)

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        returncode = forced_returncode if forced_returncode is not None else process.returncode
        log.write(f"\nfinished_at: {_now().isoformat()}\nreturncode: {returncode}\n")
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def resolve_add_dirs(values: list[str], *, workdir: pathlib.Path) -> list[str]:
    resolved: list[str] = []
    for raw in values:
        path = pathlib.Path(raw)
        if not path.is_absolute():
            path = workdir / path
        if path.is_file():
            path = path.parent
        if not path.exists():
            raise RuntimeError(f"add-dir does not exist: {path}")
        if not path.is_dir():
            raise RuntimeError(f"add-dir is not a directory: {path}")
        item = str(path)
        if item not in resolved:
            resolved.append(item)
    return resolved


def _load_sessions(path: pathlib.Path | None = None) -> dict:
    path = SESSION_FILE if path is None else path
    if path == SESSION_FILE and not path.exists() and LEGACY_SESSION_FILE.exists():
        return json.loads(LEGACY_SESSION_FILE.read_text(encoding="utf-8"))
    if not path.exists():
        return {"version": 1, "sessions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_sessions(data: dict, path: pathlib.Path | None = None) -> None:
    path = SESSION_FILE if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _session_key(channel: str, workspace: str, role: str, stage: str) -> str:
    return f"{channel}|{workspace}|{role}|{stage}"


def ensure_session_id(channel: str, workspace: str, role: str, stage: str) -> str:
    data = _load_sessions()
    sessions = data.setdefault("sessions", {})
    key = _session_key(channel, workspace, role, stage)
    record = sessions.get(key)
    if not record:
        record = {
            "channel": channel,
            "workspace": workspace,
            "role": role,
            "stage": stage,
            "session_id": str(uuid.uuid4()),
            "created_at": _now().isoformat(),
            "last_prompt_hash": None,
            "last_output_path": None,
        }
        sessions[key] = record
    record["updated_at"] = _now().isoformat()
    _save_sessions(data)
    return record["session_id"]


def new_ephemeral_session_id() -> str:
    return str(uuid.uuid4())


def update_session_record(
    channel: str,
    workspace: str,
    role: str,
    stage: str,
    *,
    prompt_hash: str,
    output_path: pathlib.Path,
    status: str = "success",
    failure_kind: str | None = None,
) -> None:
    data = _load_sessions()
    key = _session_key(channel, workspace, role, stage)
    record = data.setdefault("sessions", {}).get(key)
    if record is None:
        raise RuntimeError(f"session record not found for {key}; call ensure_session_id first")
    record["last_prompt_hash"] = prompt_hash
    record["last_output_path"] = str(output_path)
    record["last_status"] = status
    if failure_kind:
        record["last_failure_kind"] = failure_kind
    record["updated_at"] = _now().isoformat()
    _save_sessions(data)


def run_official_check(workspace: str, *, runner=subprocess.run) -> dict:
    if workspace != "TigerMemory":
        raise RuntimeError("official_review automation currently supports only TigerMemory workspace")
    if not OFFICIAL_LAUNCHER.exists():
        raise RuntimeError(f"official launcher not found: {OFFICIAL_LAUNCHER}")
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(OFFICIAL_LAUNCHER),
        "-Workspace",
        workspace,
        "-CheckOnly",
    ]
    completed = runner(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"official check failed: {completed.stderr.strip() or completed.stdout.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"official check returned non-json output: {completed.stdout[:500]}") from exc
    if payload.get("AnthropicAuthToken") != "unset" or payload.get("AnthropicBaseUrl"):
        raise RuntimeError("official check did not clear custom Anthropic provider environment")
    if payload.get("ProxyExitLocation") != "US":
        raise RuntimeError(f"official proxy exit is not US: {payload.get('ProxyExitLocation')}")
    return payload


def run_api_test_check(workspace: str, *, runner=subprocess.run) -> dict:
    workdir = WORKSPACES.get(workspace)
    if workdir is None:
        raise RuntimeError(f"unsupported api_test workspace: {workspace}")
    if not workdir.exists():
        raise RuntimeError(f"api_test workspace does not exist: {workdir}")
    if not API_TEST_EXE.exists():
        raise RuntimeError(f"api_test Claude exe not found: {API_TEST_EXE}")
    completed = runner(
        [str(API_TEST_EXE), "--version"],
        cwd=str(workdir),
        env=api_test_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"api_test version check failed: {(completed.stderr or completed.stdout).strip()}")
    version = (completed.stdout or "").strip()
    if API_TEST_EXPECTED_VERSION not in version:
        raise RuntimeError(f"api_test Claude version changed: {version}")
    return {
        "Workspace": workspace,
        "Workdir": str(workdir),
        "ClaudeExe": str(API_TEST_EXE),
        "ClaudeVersion": version,
        "Proxy": "http://127.0.0.1:7890",
        "Channel": API_TEST_CHANNEL,
        "ProviderSecretRead": False,
    }


def query_official_usage_status(
    *,
    claude_exe: pathlib.Path,
    workdir: pathlib.Path,
    env: dict[str, str],
    runner=subprocess.run,
    now: _dt.datetime | None = None,
) -> dict:
    cmd = [
        str(claude_exe),
        "-p",
        "/usage",
        "--no-session-persistence",
        "--permission-mode",
        "plan",
        "--model",
        "sonnet",
        "--effort",
        "low",
        "--output-format",
        "json",
    ]
    completed = runner(
        cmd,
        cwd=str(workdir),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=120,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        details = (completed.stderr or output).strip()
        raise RuntimeError(f"official usage query failed: {details[:500]}")
    usage = parse_usage_status(output, now=now)
    if not usage.get("windows"):
        raise RuntimeError("official usage query returned no rate-limit windows")
    return usage


def verify_cached_cooldown(
    channel: str,
    *,
    reset_at: _dt.datetime,
    claude_exe: pathlib.Path,
    workdir: pathlib.Path,
    env: dict[str, str],
    runner=subprocess.run,
    now: _dt.datetime | None = None,
) -> _dt.datetime | None:
    current = now or _now()
    try:
        usage = query_official_usage_status(
            claude_exe=claude_exe,
            workdir=workdir,
            env=env,
            runner=runner,
            now=current,
        )
    except RuntimeError as exc:
        clear_limit_cooldown(channel)
        print(
            f"warning: local cooldown until {reset_at.isoformat()} could not be verified by Claude CLI /usage; "
            f"cleared cached cooldown and will let the real Claude call decide. {exc}",
            file=sys.stderr,
        )
        return None

    usage_reset = active_usage_limit(usage, now=current)
    if usage_reset is None:
        clear_limit_cooldown(channel)
        print(
            f"warning: cleared stale local cooldown until {reset_at.isoformat()}; "
            "Claude CLI /usage says the subscription window is not exhausted.",
            file=sys.stderr,
        )
        return None

    record_cli_usage_limit(channel, usage_reset, now=current)
    return usage_reset


def official_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in PROVIDER_ENV_KEYS:
        env.pop(key, None)
    env.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:7891",
            "HTTPS_PROXY": "http://127.0.0.1:7891",
            "ALL_PROXY": "http://127.0.0.1:7891",
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "CLAUDE_CODE_PROXY_RESOLVES_HOSTS": "1",
            "CLAUDE_CONFIG_DIR": str(OFFICIAL_CONFIG_DIR),
            "CLAUDE_CODE_PLUGIN_CACHE_DIR": str(OFFICIAL_PLUGIN_DIR),
            "CLAUDE_CODE_TMPDIR": str(OFFICIAL_TMP_DIR),
            "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1",
            "CLAUDE_CODE_HIDE_CWD": "0",
        }
    )
    return env


def api_test_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "HTTPS_PROXY": "http://127.0.0.1:7890",
            "ALL_PROXY": "http://127.0.0.1:7890",
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1",
            "CLAUDE_CODE_HIDE_CWD": "0",
        }
    )
    return env


def archive_review(
    *,
    channel: str,
    workspace: str,
    role: str,
    stage: str,
    session_ref_value: str,
    prompt_hash: str,
    requested_model: str | None,
    requested_effort: str | None,
    session_mode: str,
    review_status: str,
    failure_kind: str | None,
    prompt: str,
    output: str,
    add_dirs: list[str] | None = None,
    run_log_path: pathlib.Path | None = None,
) -> pathlib.Path:
    date = _now().strftime("%Y-%m-%d")
    out_dir = ARCHIVE_ROOT / date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stage}-{role}-{prompt_hash}.md"
    body = f"""---
title: "Development supervisor Claude review {stage} {prompt_hash}"
owner: codex
status: draft
updated: {_now().strftime("%Y-%m-%d")}
source_url: https://local.tigermemory.dev/{channel}
fetched_by: codex-via-tm_dev_supervisor_review
fetched_at: {_now().isoformat()}
channel: {channel}
workspace: {workspace}
review_role: {role}
stage: {stage}
session_ref: {session_ref_value}
prompt_sha256_12: {prompt_hash}
requested_model: {requested_model or "default"}
requested_effort: {requested_effort or "default"}
session_mode: {session_mode}
review_status: {review_status}
failure_kind: {failure_kind or "none"}
run_log_path: {str(run_log_path) if run_log_path else "none"}
{_yaml_list("add_dirs", add_dirs or [])}
---

# Development supervisor Claude review {stage} {prompt_hash}

## Original Task

{_redact(prompt)}

## Claude Output

{_redact(output)}
"""
    out_path.write_text(body, encoding="utf-8")
    return out_path


def append_ledger(
    *,
    channel: str,
    workspace: str,
    role: str,
    stage: str,
    session_ref_value: str,
    prompt_hash: str,
    requested_model: str | None,
    requested_effort: str | None,
    session_mode: str,
    review_status: str,
    failure_kind: str | None,
    output_path: pathlib.Path,
) -> None:
    if not LEDGER_PATH.exists():
        return
    rel = output_path.relative_to(REPO_ROOT).as_posix()
    line = (
        f"- {_now().strftime('%Y-%m-%d %H:%M')} | channel={channel} | workspace={workspace} | "
        f"role={role} | stage={stage} | session_ref={session_ref_value} | "
        f"model={requested_model or 'default'} | effort={requested_effort or 'default'} | "
        f"session_mode={session_mode} | status={review_status} | failure={failure_kind or 'none'} | "
        f"prompt_hash={prompt_hash} | archive={rel}\n"
    )
    text = LEDGER_PATH.read_text(encoding="utf-8")
    marker = "## 审核调用记录"
    marker_pos = text.find(marker)
    if marker_pos == -1:
        LEDGER_PATH.write_text(text.rstrip() + "\n\n" + marker + "\n" + line, encoding="utf-8")
        return
    insert_start = text.find("\n", marker_pos)
    if insert_start == -1:
        LEDGER_PATH.write_text(text.rstrip() + "\n" + line, encoding="utf-8")
        return
    next_heading = text.find("\n## ", insert_start + 1)
    if next_heading == -1:
        new_text = text.rstrip() + "\n" + line
    else:
        prefix = text[:next_heading].rstrip()
        suffix = text[next_heading:].lstrip("\n")
        new_text = prefix + "\n" + line + "\n" + suffix
    LEDGER_PATH.write_text(new_text, encoding="utf-8")


def classify_failure(output: str) -> str:
    lowered = output.lower()
    if "already in use" in lowered and "session id" in lowered:
        return "session_busy"
    if "session limit" in lowered or "hit your session limit" in lowered:
        return "session_limit"
    if "socket connection was closed" in lowered or "connection was closed unexpectedly" in lowered:
        return "connection_closed"
    if "no stream activity" in lowered:
        return "stall_timeout"
    if "hard timeout" in lowered:
        return "hard_timeout"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return "cli_error"


def run_review(args: argparse.Namespace, *, runner=subprocess.run) -> pathlib.Path:
    prompt = _read_prompt(args)
    prompt_hash = _sha12(prompt)
    if args.channel == "official_review":
        channel_name = OFFICIAL_CHANNEL
        check = run_official_check(args.workspace, runner=runner)
        claude_exe = pathlib.Path(check["ClaudeExe"])
        run_env = official_env()
        if not args.ignore_limit_cooldown:
            reset_at = active_limit_cooldown(channel_name)
            if reset_at is not None:
                verified_reset = verify_cached_cooldown(
                    channel_name,
                    reset_at=reset_at,
                    claude_exe=claude_exe,
                    workdir=pathlib.Path(check["Workdir"]),
                    env=run_env,
                    runner=runner,
                )
                if verified_reset is not None:
                    raise RuntimeError(
                        f"{channel_name} session-limit cooldown was confirmed by Claude CLI /usage until "
                        f"{verified_reset.isoformat()}; retry after reset or pass --ignore-limit-cooldown to force a call."
                    )
    elif args.channel == "api_test":
        channel_name = API_TEST_CHANNEL
        check = run_api_test_check(args.workspace, runner=runner)
        claude_exe = pathlib.Path(check["ClaudeExe"])
        run_env = api_test_env()
    else:
        raise SystemExit(f"unsupported channel: {args.channel}")
    if not claude_exe.exists():
        raise RuntimeError(f"Claude exe missing after check: {claude_exe}")
    workdir = pathlib.Path(check["Workdir"])
    add_dirs = resolve_add_dirs(args.add_dir, workdir=workdir)
    if args.session_mode == "stage":
        session_id = ensure_session_id(channel_name, args.workspace, args.role, args.stage)
    else:
        session_id = new_ephemeral_session_id()
    session_ref_value = session_ref(session_id)
    cmd = [
        str(claude_exe),
        "-p",
        "--agent",
        args.role,
        "--permission-mode",
        "plan",
    ]
    for directory in add_dirs:
        cmd.extend(["--add-dir", directory])
    if args.model:
        cmd.extend(["--model", args.model])
    if args.effort:
        cmd.extend(["--effort", args.effort])
    cmd.extend(
        [
        "--session-id",
        session_id,
        "--name",
        f"dev-supervisor-{args.stage}",
        "--output-format",
        "text",
        "--",
        prompt,
        ]
    )
    run_log_path = _run_log_path(args.stage, args.role, prompt_hash)
    try:
        if runner is subprocess.run:
            completed = run_streaming_command(
                cmd,
                cwd=str(workdir),
                env=run_env,
                timeout=args.timeout,
                stall_timeout=args.stall_timeout,
                heartbeat_interval=args.heartbeat_interval,
                log_path=run_log_path,
            )
        else:
            completed = runner(
                cmd,
                cwd=str(workdir),
                env=run_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=args.timeout,
            )
    except subprocess.TimeoutExpired as exc:
        output = (
            f"Claude review timed out after {args.timeout} seconds.\n\n"
            f"STDOUT:\n{_coerce_output(exc.output).strip()}\n\n"
            f"STDERR:\n{_coerce_output(exc.stderr).strip()}"
        ).strip()
        out_path = archive_review(
            channel=channel_name,
            workspace=args.workspace,
            role=args.role,
            stage=args.stage,
            session_ref_value=session_ref_value,
            prompt_hash=prompt_hash,
            requested_model=args.model,
            requested_effort=args.effort,
            session_mode=args.session_mode,
            review_status="failed",
            failure_kind="timeout",
            prompt=prompt,
            output=output,
            add_dirs=add_dirs,
            run_log_path=run_log_path,
        )
        append_ledger(
            channel=channel_name,
            workspace=args.workspace,
            role=args.role,
            stage=args.stage,
            session_ref_value=session_ref_value,
            prompt_hash=prompt_hash,
            requested_model=args.model,
            requested_effort=args.effort,
            session_mode=args.session_mode,
            review_status="failed",
            failure_kind="timeout",
            output_path=out_path,
        )
        raise RuntimeError(f"official review timed out and was archived: {out_path}") from exc
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        output = (output + "\n\nSTDERR:\n" + (completed.stderr or "").strip()).strip()
        failure_kind = classify_failure(output)
        if failure_kind == "session_limit":
            record_session_limit(channel_name, output)
        out_path = archive_review(
            channel=channel_name,
            workspace=args.workspace,
            role=args.role,
            stage=args.stage,
            session_ref_value=session_ref_value,
            prompt_hash=prompt_hash,
            requested_model=args.model,
            requested_effort=args.effort,
            session_mode=args.session_mode,
            review_status="failed",
            failure_kind=failure_kind,
            prompt=prompt,
            output=output,
            add_dirs=add_dirs,
            run_log_path=run_log_path,
        )
        if args.session_mode == "stage":
            update_session_record(
                channel_name,
                args.workspace,
                args.role,
                args.stage,
                prompt_hash=prompt_hash,
                output_path=out_path,
                status="failed",
                failure_kind=failure_kind,
            )
        append_ledger(
            channel=channel_name,
            workspace=args.workspace,
            role=args.role,
            stage=args.stage,
            session_ref_value=session_ref_value,
            prompt_hash=prompt_hash,
            requested_model=args.model,
            requested_effort=args.effort,
            session_mode=args.session_mode,
            review_status="failed",
            failure_kind=failure_kind,
            output_path=out_path,
        )
        raise RuntimeError(f"review failed ({channel_name}), archived at {out_path}: {output[:500]}")
    out_path = archive_review(
        channel=channel_name,
        workspace=args.workspace,
        role=args.role,
        stage=args.stage,
        session_ref_value=session_ref_value,
        prompt_hash=prompt_hash,
        requested_model=args.model,
        requested_effort=args.effort,
        session_mode=args.session_mode,
        review_status="success",
        failure_kind=None,
        prompt=prompt,
        output=output,
        add_dirs=add_dirs,
        run_log_path=run_log_path,
    )
    if args.session_mode == "stage":
        update_session_record(
            channel_name,
            args.workspace,
            args.role,
            args.stage,
            prompt_hash=prompt_hash,
            output_path=out_path,
        )
    append_ledger(
        channel=channel_name,
        workspace=args.workspace,
        role=args.role,
        stage=args.stage,
        session_ref_value=session_ref_value,
        prompt_hash=prompt_hash,
        requested_model=args.model,
        requested_effort=args.effort,
        session_mode=args.session_mode,
        review_status="success",
        failure_kind=None,
        output_path=out_path,
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TigerMemory development supervisor Claude review wrapper")
    parser.add_argument("prompt", nargs="?", help="Prompt to send to official Claude review")
    parser.add_argument("--prompt-file", help="UTF-8 prompt file")
    parser.add_argument("--channel", choices=["official_review", "api_test"], default="official_review")
    parser.add_argument("--workspace", choices=sorted(WORKSPACES), default=DEFAULT_WORKSPACE)
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--stage", default=DEFAULT_STAGE)
    parser.add_argument("--model", help="Claude model alias or full model name for this call, e.g. sonnet, opus, claude-opus-4-8")
    parser.add_argument("--effort", help="Claude reasoning effort for this call, e.g. low, medium, high, xhigh, max")
    parser.add_argument("--add-dir", action="append", default=[], help="Extra directory Claude may read for this review; repeatable")
    parser.add_argument(
        "--session-mode",
        choices=["fresh", "stage"],
        default="fresh",
        help=(
            "fresh creates a new one-shot Claude session and carries context through review archives; "
            "stage reuses one session id per channel/workspace/role/stage and may hit Claude session locks"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Hard safety timeout in seconds; 0 disables it. Default is 3600 for slow formal reviews.",
    )
    parser.add_argument(
        "--stall-timeout",
        type=int,
        default=0,
        help="Optional no-stream-activity timeout in seconds; 0 disables it because Claude may think silently.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=60,
        help="Seconds between local heartbeat lines while Claude is still running; 0 disables heartbeat output.",
    )
    parser.add_argument(
        "--ignore-limit-cooldown",
        action="store_true",
        help="Force a Claude call even when a recent session-limit reset time says the channel is cooling down.",
    )
    parser.add_argument("--check-only", action="store_true", help="Only run official channel CheckOnly")
    parser.add_argument(
        "--usage-status",
        action="store_true",
        help="Query Claude Code /usage through the selected channel and print parsed rate-limit status.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.usage_status:
        if args.channel != "official_review":
            print("ERROR: --usage-status currently supports official_review only", file=sys.stderr)
            return 1
        try:
            payload = run_official_check(args.workspace)
            usage = query_official_usage_status(
                claude_exe=pathlib.Path(payload["ClaudeExe"]),
                workdir=pathlib.Path(payload["Workdir"]),
                env=official_env(),
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(usage, ensure_ascii=False, indent=2))
        return 0
    if args.check_only:
        if args.channel == "official_review":
            payload = run_official_check(args.workspace)
        else:
            payload = run_api_test_check(args.workspace)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    try:
        out_path = run_review(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
