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
import uuid
from zoneinfo import ZoneInfo


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TZ_CN = ZoneInfo("Asia/Shanghai")

OFFICIAL_CHANNEL = "claude-official-review"
OFFICIAL_LAUNCHER = pathlib.Path(
    r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\start-official-claude.ps1"
)
OFFICIAL_CONFIG_DIR = pathlib.Path(r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\config")
OFFICIAL_PLUGIN_DIR = pathlib.Path(r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\plugins")
OFFICIAL_TMP_DIR = pathlib.Path(r"C:\Users\Giant\AppData\Local\ClaudeCodeOfficial\tmp")

DEFAULT_WORKSPACE = "TigerMemory"
DEFAULT_ROLE = "tiger-development-reviewer"
DEFAULT_STAGE = "p0"
SESSION_FILE = REPO_ROOT / ".tmp" / "dev-supervisor" / "claude-sessions.json"
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


def _load_sessions(path: pathlib.Path | None = None) -> dict:
    path = SESSION_FILE if path is None else path
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


def update_session_record(
    channel: str,
    workspace: str,
    role: str,
    stage: str,
    *,
    prompt_hash: str,
    output_path: pathlib.Path,
) -> None:
    data = _load_sessions()
    record = data.setdefault("sessions", {})[_session_key(channel, workspace, role, stage)]
    record["last_prompt_hash"] = prompt_hash
    record["last_output_path"] = str(output_path)
    record["updated_at"] = _now().isoformat()
    _save_sessions(data)


def run_official_check(workspace: str, *, runner=subprocess.run) -> dict:
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


def archive_review(
    *,
    channel: str,
    workspace: str,
    role: str,
    stage: str,
    session_ref_value: str,
    prompt_hash: str,
    prompt: str,
    output: str,
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
    output_path: pathlib.Path,
) -> None:
    if not LEDGER_PATH.exists():
        return
    rel = output_path.relative_to(REPO_ROOT).as_posix()
    line = (
        f"- {_now().strftime('%Y-%m-%d %H:%M')} | channel={channel} | workspace={workspace} | "
        f"role={role} | stage={stage} | session_ref={session_ref_value} | "
        f"prompt_hash={prompt_hash} | archive={rel}\n"
    )
    with LEDGER_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line)


def run_review(args: argparse.Namespace, *, runner=subprocess.run) -> pathlib.Path:
    if args.channel != "official_review":
        raise SystemExit("Only official_review is automated in P0; api_test is manual/future only")
    prompt = _read_prompt(args)
    prompt_hash = _sha12(prompt)
    check = run_official_check(args.workspace, runner=runner)
    claude_exe = pathlib.Path(check["ClaudeExe"])
    if not claude_exe.exists():
        raise RuntimeError(f"official Claude exe missing after check: {claude_exe}")
    session_id = ensure_session_id(OFFICIAL_CHANNEL, args.workspace, args.role, args.stage)
    session_ref_value = session_ref(session_id)
    cmd = [
        str(claude_exe),
        "-p",
        "--agent",
        args.role,
        "--permission-mode",
        "plan",
        "--session-id",
        session_id,
        "--name",
        f"dev-supervisor-{args.stage}",
        "--output-format",
        "text",
        prompt,
    ]
    completed = runner(
        cmd,
        cwd=str(REPO_ROOT),
        env=official_env(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=args.timeout,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        output = (output + "\n\nSTDERR:\n" + (completed.stderr or "").strip()).strip()
        raise RuntimeError(f"official review failed before archive: {output[:1000]}")
    out_path = archive_review(
        channel=OFFICIAL_CHANNEL,
        workspace=args.workspace,
        role=args.role,
        stage=args.stage,
        session_ref_value=session_ref_value,
        prompt_hash=prompt_hash,
        prompt=prompt,
        output=output,
    )
    update_session_record(
        OFFICIAL_CHANNEL,
        args.workspace,
        args.role,
        args.stage,
        prompt_hash=prompt_hash,
        output_path=out_path,
    )
    append_ledger(
        channel=OFFICIAL_CHANNEL,
        workspace=args.workspace,
        role=args.role,
        stage=args.stage,
        session_ref_value=session_ref_value,
        prompt_hash=prompt_hash,
        output_path=out_path,
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TigerMemory development supervisor Claude review wrapper")
    parser.add_argument("prompt", nargs="?", help="Prompt to send to official Claude review")
    parser.add_argument("--prompt-file", help="UTF-8 prompt file")
    parser.add_argument("--channel", choices=["official_review", "api_test"], default="official_review")
    parser.add_argument("--workspace", choices=["TigerMemory"], default=DEFAULT_WORKSPACE)
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--stage", default=DEFAULT_STAGE)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--check-only", action="store_true", help="Only run official channel CheckOnly")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.channel != "official_review":
        print("api_test is not automated in P0; use manual review or add a token-safe wrapper first", file=sys.stderr)
        return 2
    if args.check_only:
        payload = run_official_check(args.workspace)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    out_path = run_review(args)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
