"""Background job runner for TradingAgents single-stock deep dives.
Inputs: CLI arguments, local repository files, or data supplied by the caller.
Outputs: A deterministic stdout report, file rewrite, or helper return value documented by the command.
Depends-on (must-have): Python stdlib and local tigermemory helper modules; external services only when explicitly requested.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid
from typing import Any

import tigermemory_core as tm_core


TERMINAL_STATUSES = {"completed", "failed"}
JOB_ID_RE = re.compile(r"^ta-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
TICKER_RE = re.compile(r"^[0-9A-Z._-]{3,20}$")
TRADE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROFILE_RE = re.compile(r"^(deep|fast)$")
DECISION_LOG_PREFIX = "wiki/investment/decision-log/"
TZ_CN = datetime.timezone(datetime.timedelta(hours=8))


def jobs_root() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(
            "TRADINGAGENTS_JOB_ROOT",
            str(tm_core.REPO_ROOT / "runtime" / "tradingagents_jobs"),
        )
    )


def ta_root() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(
            "TRADINGAGENTS_ROOT",
            str(pathlib.Path.home() / "workspaces" / "TradingAgents"),
        )
    )


def python_bin() -> str:
    root = ta_root()
    return os.environ.get("TRADINGAGENTS_PYTHON", str(root / ".venv" / "bin" / "python"))


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def validate_ticker(ticker: str) -> str:
    value = ticker.strip().upper()
    if not TICKER_RE.fullmatch(value):
        raise ValueError(f"invalid ticker: {ticker!r}")
    return value


def validate_trade_date(trade_date: str) -> str:
    value = trade_date.strip()
    if not TRADE_DATE_RE.fullmatch(value):
        raise ValueError("trade_date must be YYYY-MM-DD")
    return value


def validate_job_id(job_id: str) -> str:
    value = job_id.strip()
    if not JOB_ID_RE.fullmatch(value):
        raise ValueError(f"invalid job_id: {job_id!r}")
    return value


def validate_profile(profile: str) -> str:
    value = (profile or "deep").strip().lower()
    if not PROFILE_RE.fullmatch(value):
        raise ValueError("profile must be 'deep' or 'fast'")
    return value


def job_dir(job_id: str) -> pathlib.Path:
    return jobs_root() / validate_job_id(job_id)


def status_path(job_id: str) -> pathlib.Path:
    return job_dir(job_id) / "status.json"


def result_path(job_id: str) -> pathlib.Path:
    return job_dir(job_id) / "result.json"


def stdout_path(job_id: str) -> pathlib.Path:
    return job_dir(job_id) / "stdout.txt"


def stderr_path(job_id: str) -> pathlib.Path:
    return job_dir(job_id) / "stderr.txt"


def _atomic_write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tail_text(path: pathlib.Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _pid_alive(pid: int | None) -> bool | None:
    if not pid or pid <= 0:
        return None
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    return _pid_alive_posix(pid)


def _pid_alive_posix(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


def _pid_alive_windows(pid: int) -> bool | None:
    """Check pid liveness on Windows via OpenProcess + GetExitCodeProcess.

    Avoids ``os.kill(pid, 0)``, which on Windows is implemented through
    ``TerminateProcess(pid, 0)`` per the Python docs. When pid happens to
    equal ``os.getpid()`` (e.g. test fixtures that mock Popen with the
    current process pid) that call tries to terminate the caller itself,
    leaving the Python process in an inconsistent state that hangs the
    next ``subprocess.run(capture_output=True)`` inside its reader thread.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    ERROR_INVALID_PARAMETER = 87

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            err = ctypes.get_last_error()
            if err == ERROR_ACCESS_DENIED:
                return True
            if err == ERROR_INVALID_PARAMETER:
                return False
            return None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _parse_last_json_line(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _portable_repo_root(raw_root: str) -> pathlib.Path:
    normalized = raw_root.replace("\\", "/")
    m = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", normalized)
    if sys.platform == "win32" and m:
        rest = m.group(2) or ""
        return pathlib.Path(f"{m.group(1).upper()}:/{rest}")
    m = re.match(r"^([A-Za-z]):(?:/(.*))?$", normalized)
    if sys.platform != "win32" and m:
        rest = m.group(2) or ""
        return pathlib.Path(f"/mnt/{m.group(1).lower()}/{rest}")
    return pathlib.Path(raw_root)


def _decision_log_ref(raw_path: str) -> tuple[pathlib.Path, str] | None:
    normalized = str(raw_path).replace("\\", "/")
    if normalized.startswith(DECISION_LOG_PREFIX):
        return tm_core.REPO_ROOT, normalized
    marker = f"/{DECISION_LOG_PREFIX}"
    idx = normalized.find(marker)
    if idx < 0:
        return None
    repo_root = _portable_repo_root(normalized[:idx])
    rel_path = normalized[idx + 1 :]
    if not rel_path.startswith(DECISION_LOG_PREFIX):
        return None
    return repo_root, rel_path


def _adapter_output_paths(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    report_paths = payload.get("report_paths")
    if isinstance(report_paths, dict):
        paths.extend(str(value) for value in report_paths.values() if value)
    for key in ("summary_path", "portfolio_summary_path", "monthly_log_path"):
        value = payload.get(key)
        if value:
            paths.append(str(value))
    return paths


def _run_git(repo_root: pathlib.Path, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _commit_decision_log_paths(repo_root: pathlib.Path, rel_paths: list[str], trade_date: str, agent: str) -> dict[str, Any]:
    rel_paths = sorted(set(rel_paths))
    if not rel_paths:
        return {"ok": True, "committed": False, "reason": "no_decision_log_paths"}
    top = _run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        return {"ok": False, "committed": False, "reason": "repo_root_not_git", "repo_root": str(repo_root)}

    staged = _run_git(repo_root, ["diff", "--cached", "--name-only"])
    if staged.returncode != 0:
        return {"ok": False, "committed": False, "reason": "staged_check_failed", "stderr": staged.stderr.strip()}
    if staged.stdout.strip():
        return {"ok": False, "committed": False, "reason": "staged_changes_present"}

    status = _run_git(repo_root, ["status", "--porcelain", "--", *rel_paths])
    if status.returncode != 0:
        return {"ok": False, "committed": False, "reason": "status_failed", "stderr": status.stderr.strip()}
    if not status.stdout.strip():
        return {"ok": True, "committed": False, "reason": "no_changes", "paths": rel_paths}

    add = _run_git(repo_root, ["add", "--", *rel_paths])
    if add.returncode != 0:
        return {"ok": False, "committed": False, "reason": "add_failed", "stderr": add.stderr.strip(), "paths": rel_paths}

    message = f"[{agent}] create: archive TradingAgents decision logs {trade_date}"
    commit = _run_git(repo_root, ["commit", "-m", message], timeout=120)
    if commit.returncode != 0:
        _run_git(repo_root, ["restore", "--staged", "--", *rel_paths])
        return {"ok": False, "committed": False, "reason": "commit_failed", "stderr": commit.stderr.strip(), "paths": rel_paths}

    result: dict[str, Any] = {
        "ok": True,
        "committed": True,
        "commit_output": commit.stdout.strip(),
        "paths": rel_paths,
    }
    if _env_enabled("TRADINGAGENTS_DECISION_LOG_AUTO_PUSH", default=True):
        branch = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch.returncode == 0 and branch.stdout.strip() and branch.stdout.strip() != "HEAD":
            push = _run_git(repo_root, ["push", "origin", branch.stdout.strip()], timeout=180)
            result["pushed"] = push.returncode == 0
            if push.returncode != 0:
                result["push_error"] = push.stderr.strip()
        else:
            result["pushed"] = False
            result["push_error"] = "detached_or_unknown_branch"
    else:
        result["pushed"] = False
        result["push_skipped"] = True
    return result


def archive_decision_log_outputs(payload: dict[str, Any]) -> dict[str, Any]:
    if not _env_enabled("TRADINGAGENTS_DECISION_LOG_AUTO_COMMIT", default=True):
        return {"ok": True, "enabled": False}
    grouped: dict[pathlib.Path, list[str]] = {}
    skipped: list[str] = []
    for raw_path in _adapter_output_paths(payload):
        ref = _decision_log_ref(raw_path)
        if not ref:
            skipped.append(raw_path)
            continue
        repo_root, rel_path = ref
        if not (repo_root / rel_path).exists():
            skipped.append(raw_path)
            continue
        grouped.setdefault(repo_root, []).append(rel_path)

    trade_date = str(payload.get("trade_date") or datetime.datetime.now(TZ_CN).date())
    agent = os.environ.get("TM_AGENT") or os.environ.get("TRADINGAGENTS_ARCHIVE_AGENT") or "codex"
    commits = [
        _commit_decision_log_paths(repo_root, rel_paths, trade_date, agent)
        for repo_root, rel_paths in sorted(grouped.items(), key=lambda item: str(item[0]))
    ]
    return {
        "ok": all(item.get("ok") for item in commits),
        "enabled": True,
        "commits": commits,
        "skipped": skipped,
    }


def _base_status(job_id: str, ticker: str, trade_date: str, status: str, profile: str = "deep") -> dict[str, Any]:
    return {
        "ok": status not in {"failed"},
        "job_id": job_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "profile": profile,
        "status": status,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }


def start_job(ticker: str, trade_date: str, profile: str = "deep") -> dict[str, Any]:
    ticker = validate_ticker(ticker)
    trade_date = validate_trade_date(trade_date)
    profile = validate_profile(profile)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    job_id = f"ta-{stamp}-{uuid.uuid4().hex[:8]}"
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=False)

    status = _base_status(job_id, ticker, trade_date, "starting", profile=profile)
    status.update(
        {
            "ta_root": str(ta_root()),
            "stdout_path": str(stdout_path(job_id)),
            "stderr_path": str(stderr_path(job_id)),
            "result_path": str(result_path(job_id)),
        }
    )
    _atomic_write_json(status_path(job_id), status)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tm_core.REPO_ROOT / "tools")
    command = [sys.executable, str(pathlib.Path(__file__).resolve()), "worker", job_id, ticker, trade_date, profile]
    proc = subprocess.Popen(
        command,
        cwd=str(tm_core.REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    status["status"] = "running"
    status["worker_pid"] = proc.pid
    status["updated_at"] = now_utc()
    _atomic_write_json(status_path(job_id), status)
    return {
        "ok": True,
        "job_id": job_id,
        "status": "running",
        "ticker": ticker,
        "trade_date": trade_date,
        "profile": profile,
        "poll_after_sec": 30,
        "status_path": str(status_path(job_id)),
    }


def run_worker(job_id: str, ticker: str, trade_date: str, profile: str = "deep") -> int:
    job_id = validate_job_id(job_id)
    ticker = validate_ticker(ticker)
    trade_date = validate_trade_date(trade_date)
    profile = validate_profile(profile)
    status = _base_status(job_id, ticker, trade_date, "running", profile=profile)
    status.update(
        {
            "worker_pid": os.getpid(),
            "started_at": now_utc(),
            "ta_root": str(ta_root()),
            "stdout_path": str(stdout_path(job_id)),
            "stderr_path": str(stderr_path(job_id)),
            "result_path": str(result_path(job_id)),
        }
    )
    _atomic_write_json(status_path(job_id), status)
    started = time.monotonic()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ta_root())
    try:
        result = subprocess.run(
            [python_bin(), "tools/tm_adapter.py", ticker, trade_date, "--profile", profile],
            cwd=str(ta_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(os.environ.get("TRADINGAGENTS_MCP_TIMEOUT_SEC", "1800")),
            env=env,
        )
        stdout_path(job_id).write_text(result.stdout or "", encoding="utf-8")
        stderr_path(job_id).write_text(result.stderr or "", encoding="utf-8")
        payload = _parse_last_json_line(result.stdout or "")
        elapsed = round(time.monotonic() - started, 3)
        if result.returncode == 0 and payload and payload.get("ok") is not False:
            archive_result = archive_decision_log_outputs(payload)
            payload["decision_log_archive"] = archive_result
            _atomic_write_json(result_path(job_id), payload)
            status.update(
                {
                    "ok": True,
                    "status": "completed",
                    "returncode": result.returncode,
                    "elapsed_sec": elapsed,
                    "finished_at": now_utc(),
                    "updated_at": now_utc(),
                    "rating": payload.get("rating"),
                    "processed_signal": payload.get("processed_signal"),
                    "profile": payload.get("profile") or profile,
                    "warnings": payload.get("warnings") or [],
                    "report_paths": payload.get("report_paths") or {},
                    "decision_log_archive": archive_result,
                    "cost_estimate_usd": payload.get("cost_estimate_usd"),
                }
            )
            _atomic_write_json(status_path(job_id), status)
            return 0

        status.update(
            {
                "ok": False,
                "status": "failed",
                "returncode": result.returncode,
                "elapsed_sec": elapsed,
                "finished_at": now_utc(),
                "updated_at": now_utc(),
                "error": "tm_adapter failed or emitted invalid JSON",
                "stdout_tail": _tail_text(stdout_path(job_id)),
                "stderr_tail": _tail_text(stderr_path(job_id)),
            }
        )
        if payload:
            status["adapter_payload"] = payload
        _atomic_write_json(status_path(job_id), status)
        return result.returncode or 1
    except Exception as exc:
        status.update(
            {
                "ok": False,
                "status": "failed",
                "elapsed_sec": round(time.monotonic() - started, 3),
                "finished_at": now_utc(),
                "updated_at": now_utc(),
                "error": str(exc),
                "error_type": type(exc).__name__,
                "stdout_tail": _tail_text(stdout_path(job_id)),
                "stderr_tail": _tail_text(stderr_path(job_id)),
            }
        )
        _atomic_write_json(status_path(job_id), status)
        return 1


def get_status(job_id: str) -> dict[str, Any]:
    job_id = validate_job_id(job_id)
    path = status_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"job not found: {job_id}")
    status = _read_json(path)
    if status.get("status") not in TERMINAL_STATUSES:
        alive = _pid_alive(status.get("worker_pid"))
        status["worker_alive"] = alive
        if alive is False:
            status.update(
                {
                    "ok": False,
                    "status": "failed",
                    "updated_at": now_utc(),
                    "error": "worker process is not running and no terminal status was written",
                    "stdout_tail": _tail_text(stdout_path(job_id)),
                    "stderr_tail": _tail_text(stderr_path(job_id)),
                }
            )
            _atomic_write_json(path, status)
    return status


def fetch_result(job_id: str) -> dict[str, Any]:
    status = get_status(job_id)
    if status.get("status") != "completed":
        return {
            "ok": False,
            "job_id": validate_job_id(job_id),
            "status": status.get("status"),
            "message": "job has not completed",
            "status_detail": status,
        }
    result = _read_json(result_path(job_id))
    result.setdefault("job_id", validate_job_id(job_id))
    result.setdefault("job_status", status)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("usage: tm_deep_dive_jobs.py worker <job_id> <ticker> <trade_date> [profile]")
    if argv[0] == "worker" and len(argv) in {4, 5}:
        profile = argv[4] if len(argv) == 5 else "deep"
        return run_worker(argv[1], argv[2], argv[3], profile=profile)
    raise SystemExit("usage: tm_deep_dive_jobs.py worker <job_id> <ticker> <trade_date> [profile]")


if __name__ == "__main__":
    raise SystemExit(main())
