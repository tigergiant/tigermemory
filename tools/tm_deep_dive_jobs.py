"""Background job runner for TradingAgents single-stock deep dives."""

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

import tm_core


TERMINAL_STATUSES = {"completed", "failed"}
JOB_ID_RE = re.compile(r"^ta-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
TICKER_RE = re.compile(r"^[0-9A-Z._-]{3,20}$")
TRADE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def jobs_root() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get(
            "TRADINGAGENTS_JOB_ROOT",
            str(tm_core.REPO_ROOT / "runtime" / "tradingagents_jobs"),
        )
    )


def ta_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TRADINGAGENTS_ROOT", "/home/giant/workspaces/TradingAgents"))


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
    if not pid:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
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


def _base_status(job_id: str, ticker: str, trade_date: str, status: str) -> dict[str, Any]:
    return {
        "ok": status not in {"failed"},
        "job_id": job_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "status": status,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }


def start_job(ticker: str, trade_date: str) -> dict[str, Any]:
    ticker = validate_ticker(ticker)
    trade_date = validate_trade_date(trade_date)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    job_id = f"ta-{stamp}-{uuid.uuid4().hex[:8]}"
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=False)

    status = _base_status(job_id, ticker, trade_date, "starting")
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
    command = [sys.executable, str(pathlib.Path(__file__).resolve()), "worker", job_id, ticker, trade_date]
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
        "poll_after_sec": 30,
        "status_path": str(status_path(job_id)),
    }


def run_worker(job_id: str, ticker: str, trade_date: str) -> int:
    job_id = validate_job_id(job_id)
    ticker = validate_ticker(ticker)
    trade_date = validate_trade_date(trade_date)
    status = _base_status(job_id, ticker, trade_date, "running")
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
            [python_bin(), "tools/tm_adapter.py", ticker, trade_date],
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
                    "warnings": payload.get("warnings") or [],
                    "report_paths": payload.get("report_paths") or {},
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
        raise SystemExit("usage: tm_deep_dive_jobs.py worker <job_id> <ticker> <trade_date>")
    if argv[0] == "worker" and len(argv) == 4:
        return run_worker(argv[1], argv[2], argv[3])
    raise SystemExit("usage: tm_deep_dive_jobs.py worker <job_id> <ticker> <trade_date>")


if __name__ == "__main__":
    raise SystemExit(main())
