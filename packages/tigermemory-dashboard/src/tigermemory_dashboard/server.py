#!/usr/bin/env python3
"""Windows-local review UI for daily memory digest reports.
Inputs: CLI/API payloads, inbox or digest markdown, route decisions, proposal metadata, or Mem0 write requests.
Outputs: Rendered markdown, JSON status payloads, routed memory writes, proposal decisions, or review actions.
Depends-on (must-have): tm_core, tm_route/tm_memory_ops helpers, local git-managed files, and configured Mem0/OpenMemory endpoints.
"""
from __future__ import annotations

import argparse
import datetime as dt
import copy
import hashlib
import importlib
from importlib import resources
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import time
import re
import shutil
import types
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Optional

try:
    import _bootstrap_paths  # noqa: F401  -- checkout-only path bootstrap
except ImportError:  # pragma: no cover - installed package mode
    _bootstrap_paths = None

import tigermemory_core as tm_core
from tigermemory_core import llm_status as tm_llm_status
from tigermemory_core import runtime_events as tm_runtime_events
from tigermemory_core.roots import resolve_app_root


def _optional_import(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


tm_agent_doctor = _optional_import("tm_agent_doctor")
tm_answer_trace = _optional_import("tm_answer_trace")
tm_agent_connect = _optional_import("tigermemory_config.agent_connect")
tm_cron_apply = _optional_import("tm_cron_apply")
tm_dashboard_prefs = _optional_import("tm_dashboard_prefs")
tm_memory_ops = _optional_import("tm_memory_ops")
tm_memory_reflection = _optional_import("tm_memory_reflection")
tm_route_events = _optional_import("tm_route_events")
tm_review_tools = _optional_import("tm_review_tools")
tm_self_evolution = _optional_import("tm_self_evolution")
tm_tigerledger_review = _optional_import("tigerledger.review_server")
tm_update = _optional_import("tigermemory_update")

try:
    from fastapi import Body, FastAPI, HTTPException, Query, Request
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    print(f"Missing dependency: {exc}", file=sys.stderr)
    sys.exit(1)

VERSION = "0.2.0"
HOST = os.getenv("TM_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("TM_DASHBOARD_PORT", "9777"))
REPO_ROOT = tm_core.REPO_ROOT
UI_REPO_ROOT = REPO_ROOT
STATIC_DIR = pathlib.Path(str(resources.files("tigermemory_dashboard") / "static"))
DASHBOARD_GIT_STATUS_TIMEOUT = float(os.getenv("TM_DASHBOARD_GIT_STATUS_TIMEOUT", "12"))
PREFS_DB = (
    tm_dashboard_prefs.PREFS_DB
    if tm_dashboard_prefs is not None
    else pathlib.Path(os.getenv("USERPROFILE", str(pathlib.Path.home()))) / ".tigermemory" / "dashboard-prefs.sqlite3"
)
SESSION_FILE = pathlib.Path(os.getenv("USERPROFILE", str(pathlib.Path.home()))) / ".tigermemory" / "review-session.json"
COOKIE_NAME = "tm_review_session"
IDLE_EXIT_SECONDS = 24 * 60 * 60
LAST_REQUEST_AT = time.time()
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INBOX_ROUTE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-z0-9_-]+\.md$")
WRITE_ACTION_LOCK = threading.Lock()
PUBLIC_PATHS = {"/healthz", "/manifest.webmanifest", "/service-worker.js", "/offline.html", "/sw-reset", "/favicon.ico"}
PAGE_PREFIXES = ("/start", "/digest", "/daily", "/review", "/ledger", "/health", "/quality", "/settings", "/agent-tools", "/canvas", "/self-evolution", "/sw-reset")
DEFAULT_ALLOWED_HOSTS = {
    "127.0.0.1",
    "127.0.0.1:9777",
    "localhost",
    "localhost:9777",
}
ALLOWED_HOSTS = DEFAULT_ALLOWED_HOSTS | {
    item.strip().lower()
    for item in os.getenv("TM_DASHBOARD_ALLOWED_HOSTS", "").split(",")
    if item.strip()
}
LOCAL_HOSTS = {"127.0.0.1", "127.0.0.1:9777", "localhost", "localhost:9777"}
COOKIE_BOOTSTRAP_HOSTS = set(LOCAL_HOSTS)
ACTION_LABELS = {
    "archive": "归档",
    "promote_mem0": "写入 Mem0",
    "promote_to_mem0": "写入 Mem0",
    "promote_wiki": "写入 Wiki",
    "promote_to_wiki": "写入 Wiki",
    "keep": "保留观察",
    "keep_in_inbox": "保留观察",
}
TOPIC_PARTITIONS = {
    "brand": "brand",
    "investment": "investment",
    "operations": "operations",
    "production": "production",
    "systems": "systems",
    "person": "person",
    "selfevolution": "self-evolution",
    "cross": "systems",
}
PARTITION_OPTIONS = [
    ("systems", "系统/工具", "适合 MCP、HTTP、脚本、AI agent、运行流程和系统规则。"),
    ("operations", "运营/日报", "适合每日巡检、日报、审批、known debt 和运营闭环。"),
    ("investment", "投资", "适合持仓、策略、研报、交易规则和投资系统。"),
    ("production", "生产", "适合 ERP、供应链、生产流程和业务执行。"),
    ("brand", "品牌", "适合 IPFB、文案、设计、产品企划和品牌记忆。"),
    ("self-evolution", "自我进化", "适合 agent 事故、经验教训、规范演进和自我修正。"),
]
SLUG_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "route",
    "routed", "memory", "summary", "topic", "codex", "cascade", "openclaw",
    "claude", "code", "inbox", "wiki", "mem0", "post", "response", "closeout",
}
CANVAS_SOURCE_PATH = UI_REPO_ROOT / "wiki" / "operations" / "project-canvas.md"
API_CACHE_TTL = 30.0
CANVAS_CACHE_TTL = 25.0
CANVAS_CANDIDATE_CACHE_TTL = 60.0
SELF_EVOLUTION_CACHE_TTL = 300.0
DASHBOARD_PAGE_CACHE_TTL = float(os.getenv("TM_DASHBOARD_PAGE_CACHE_TTL", "45"))
CRON_INTAKE_CACHE_TTL = float(os.getenv("TM_DASHBOARD_CRON_INTAKE_CACHE_TTL", "30"))
QUALITY_CACHE_WARM_INTERVAL = float(os.getenv("TM_DASHBOARD_QUALITY_WARM_INTERVAL", "20"))
MEM0_DASHBOARD_CACHE_TTL = float(os.getenv("TM_DASHBOARD_MEM0_CACHE_TTL", "60"))
MEM0_DASHBOARD_STALE_TTL = float(os.getenv("TM_DASHBOARD_MEM0_STALE_TTL", "900"))
MEM0_DASHBOARD_COOLDOWN = float(os.getenv("TM_DASHBOARD_MEM0_COOLDOWN", "120"))
MEM0_DASHBOARD_FAILURE_THRESHOLD = int(os.getenv("TM_DASHBOARD_MEM0_FAILURE_THRESHOLD", "3"))
_API_CACHE: dict[str, dict[str, Any]] = {}
_API_CACHE_LOCK = threading.Lock()
_QUALITY_CACHE_WARMER_STARTED = False
_QUALITY_CACHE_WARMER_LOCK = threading.Lock()
_MEM0_DASHBOARD_CACHE: dict[str, dict[str, Any]] = {}
_MEM0_DASHBOARD_LOCK = threading.RLock()
_MEM0_DASHBOARD_FETCH_LOCK = threading.Lock()
_MEM0_DASHBOARD_STATE: dict[str, Any] = {
    "failures": 0,
    "open_until": 0.0,
    "last_error": "",
    "last_error_at": 0.0,
}


def register_dashboard_bind_host(host: str, port: int) -> None:
    """Allow the host:port pair selected by the CLI for local browser access."""
    global HOST, PORT
    HOST = host
    PORT = port
    normalized_host = (host or "").strip().lower()
    candidates: set[str] = set()
    if normalized_host in {"127.0.0.1", "localhost"}:
        candidates.update({"127.0.0.1", "localhost"})
        candidates.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    elif normalized_host == "0.0.0.0":
        candidates.update({"127.0.0.1", "localhost", "0.0.0.0"})
        candidates.update({f"127.0.0.1:{port}", f"localhost:{port}", f"0.0.0.0:{port}"})
    elif normalized_host:
        candidates.update({normalized_host, f"{normalized_host}:{port}"})
    ALLOWED_HOSTS.update(candidates)
    LOCAL_HOSTS.update(candidates)
    COOKIE_BOOTSTRAP_HOSTS.update(candidates)


class InboxActionRequest(BaseModel):
    path: str
    action: str
    reason: Optional[str] = None
    date: Optional[str] = None
    partition: Optional[str] = None
    slug: Optional[str] = None


class ProposalApplyRequest(BaseModel):
    date: str
    proposal_id: str


class ProposalRejectRequest(BaseModel):
    date: str
    proposal_id: str
    reason: str


class BatchArchiveRequest(BaseModel):
    date: str


class BatchInboxActionRequest(BaseModel):
    paths: list[str]
    action: str
    reason: Optional[str] = None
    partition: Optional[str] = None
    slug_prefix: Optional[str] = None


class PreferenceUpdateRequest(BaseModel):
    preferences: dict[str, Any]
    propose_wiki: bool = False


class StartLlmConfigRequest(BaseModel):
    provider: str = "deepseek"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    admin_model: Optional[str] = None
    test_connection: bool = True


class StartAgentConnectRequest(BaseModel):
    targets: list[str] = ["codex", "claude-code", "cursor", "hooks"]
    dry_run: bool = False


def today() -> str:
    return dt.datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return dt.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds")


def _read_tail_lines(path: pathlib.Path, *, limit: int = 8) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line for line in lines[-max(limit, 0):] if line.strip()]


def _dashboard_development_supervisor_status() -> dict[str, Any]:
    import tm_dev_supervisor_review as supervisor

    archive_root = supervisor.ARCHIVE_ROOT
    archive_today = archive_root / today()
    archive_count = 0
    latest_archives: list[str] = []
    if archive_root.exists():
        archives = sorted(
            (path for path in archive_root.glob("*/*.md") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        archive_count = len(archives)
        latest_archives = [str(path) for path in archives[:5]]

    can_check_windows_launchers = os.name == "nt"
    launcher_note = (
        "checked_in_current_process"
        if can_check_windows_launchers
        else "dashboard_runs_outside_windows; launcher paths are checked by the Windows wrapper"
    )

    return {
        "ok": True,
        "generated_at": _now_iso(),
        "runtime": {
            "platform": os.name,
            "windows_launcher_check": launcher_note,
        },
        "channels": {
            "formal_default": supervisor.OFFICIAL_CHANNEL,
            "draft": supervisor.API_TEST_CHANNEL,
        },
        "paths": {
            "policy": str(REPO_ROOT / "wiki" / "systems" / "tigermemory-development-supervisor.md"),
            "ledger": str(supervisor.LEDGER_PATH),
            "archive_root": str(archive_root),
            "archive_today": str(archive_today),
            "context_pack_root": str(supervisor.SUPERVISOR_STATE_DIR / "context-packs"),
            "stage_accept": str(REPO_ROOT / "tools" / "tm_stage_accept.py"),
        },
        "exists": {
            "ledger": supervisor.LEDGER_PATH.exists(),
            "archive_root": archive_root.exists(),
            "archive_today": archive_today.exists(),
            "official_launcher": supervisor.OFFICIAL_LAUNCHER.exists() if can_check_windows_launchers else None,
            "api_test_exe": supervisor.API_TEST_EXE.exists() if can_check_windows_launchers else None,
        },
        "archive_count": archive_count,
        "latest_archives": latest_archives,
        "ledger_tail": _read_tail_lines(supervisor.LEDGER_PATH, limit=8),
        "next_steps": [
            "把阶段关闭动作接入 tm_stage_accept.py typed evidence。",
            "把本只读状态接入 dashboard 页面，而不是只留 API。",
            "正式阶段验收仍必须使用 official_review；api_test 只能作为 draft_review。",
        ],
    }


def _run_cache_get(key: str, ttl_seconds: float) -> tuple[dict[str, Any], bool] | tuple[None, bool]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None, False
    with _API_CACHE_LOCK:
        entry = _API_CACHE.get(key)
        if not entry:
            return None, False
    now = time.time()
    age_ms = round((now - entry["generated_at_ts"]) * 1000, 1)
    if age_ms > ttl_seconds * 1000:
        with _API_CACHE_LOCK:
            _API_CACHE.pop(key, None)
        return None, False
    payload = copy.deepcopy(entry["payload"])
    payload["generated_at"] = entry["generated_at"]
    payload["cache"] = {
        "hit": True,
        "age_ms": age_ms,
        "source_updated_at": entry.get("source_updated_at", ""),
        "ttl_seconds": ttl_seconds,
        "cached_at": entry["generated_at"],
        "source": entry.get("source"),
        "source_path": entry.get("source_path", ""),
        "source_hash": entry.get("source_hash", ""),
    }
    payload["cached"] = True
    payload["stale"] = age_ms >= ttl_seconds * 1000 * 0.95
    payload["source"] = entry.get("source")
    if entry.get("source_path"):
        payload["source_path"] = entry.get("source_path", "")
    if entry.get("source_hash"):
        payload["source_hash"] = entry.get("source_hash", "")
    return payload, True


def _run_cache_set(
    key: str,
    payload: dict[str, Any],
    *,
    source: str,
    source_path: str = "",
    source_hash: str = "",
    source_updated_at: str = "",
    ttl_seconds: float,
) -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    now = time.time()
    entry = {
        "generated_at_ts": now,
        "generated_at": _now_iso(),
        "payload": dict(payload),
        "source": source,
        "source_path": source_path,
        "source_hash": source_hash,
        "source_updated_at": source_updated_at,
        "ttl_seconds": ttl_seconds,
    }
    with _API_CACHE_LOCK:
        _API_CACHE[key] = entry


def _run_cache_clear() -> None:
    with _API_CACHE_LOCK:
        _API_CACHE.clear()


def _mem0_dashboard_reset_for_tests() -> None:
    with _MEM0_DASHBOARD_LOCK:
        _MEM0_DASHBOARD_CACHE.clear()
        _MEM0_DASHBOARD_STATE.update({
            "failures": 0,
            "open_until": 0.0,
            "last_error": "",
            "last_error_at": 0.0,
        })


def _mem0_dashboard_cache_payload(
    entry: dict[str, Any],
    *,
    status: str,
    stale: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    age_ms = round((now - float(entry.get("generated_at_ts", now))) * 1000, 1)
    payload = copy.deepcopy(entry.get("payload") or {})
    payload["cached"] = True
    payload["stale"] = bool(stale)
    payload["mem0_guard"] = {
        "status": status,
        "age_ms": age_ms,
        "ttl_seconds": MEM0_DASHBOARD_CACHE_TTL,
        "stale_ttl_seconds": MEM0_DASHBOARD_STALE_TTL,
        "cached_at": entry.get("generated_at", ""),
    }
    if error:
        payload["error"] = error
        payload["mem0_guard"]["last_error"] = error
    return payload


def _mem0_dashboard_cache_get(cache_key: str, *, stale: bool = False) -> dict[str, Any] | None:
    now = time.time()
    with _MEM0_DASHBOARD_LOCK:
        entry = _MEM0_DASHBOARD_CACHE.get(cache_key)
        if not entry:
            return None
        age = now - float(entry.get("generated_at_ts", 0.0))
        ttl = MEM0_DASHBOARD_STALE_TTL if stale else MEM0_DASHBOARD_CACHE_TTL
        if age >= ttl:
            if stale:
                _MEM0_DASHBOARD_CACHE.pop(cache_key, None)
            return None
        return _mem0_dashboard_cache_payload(
            entry,
            status="stale-cache" if stale and age >= MEM0_DASHBOARD_CACHE_TTL else "cache",
            stale=stale and age >= MEM0_DASHBOARD_CACHE_TTL,
        )


def _mem0_dashboard_cache_set(cache_key: str, payload: dict[str, Any]) -> None:
    with _MEM0_DASHBOARD_LOCK:
        _MEM0_DASHBOARD_CACHE[cache_key] = {
            "generated_at_ts": time.time(),
            "generated_at": _now_iso(),
            "payload": copy.deepcopy(payload),
        }


def _mem0_dashboard_circuit_error() -> str | None:
    now = time.time()
    with _MEM0_DASHBOARD_LOCK:
        open_until = float(_MEM0_DASHBOARD_STATE.get("open_until") or 0.0)
        if now < open_until:
            remaining = max(1, int(open_until - now))
            last_error = str(_MEM0_DASHBOARD_STATE.get("last_error") or "recent failure")
            return f"Mem0 dashboard circuit open for {remaining}s after: {last_error}"
    return None


def _mem0_dashboard_record_success() -> None:
    with _MEM0_DASHBOARD_LOCK:
        _MEM0_DASHBOARD_STATE.update({
            "failures": 0,
            "open_until": 0.0,
            "last_error": "",
            "last_error_at": 0.0,
        })


def _mem0_dashboard_record_failure(exc: Exception) -> str:
    error = str(exc)
    now = time.time()
    with _MEM0_DASHBOARD_LOCK:
        failures = int(_MEM0_DASHBOARD_STATE.get("failures") or 0) + 1
        _MEM0_DASHBOARD_STATE["failures"] = failures
        _MEM0_DASHBOARD_STATE["last_error"] = error
        _MEM0_DASHBOARD_STATE["last_error_at"] = now
        if failures >= MEM0_DASHBOARD_FAILURE_THRESHOLD:
            _MEM0_DASHBOARD_STATE["open_until"] = now + MEM0_DASHBOARD_COOLDOWN
    return error


def _mem0_dashboard_error(error: str, *, latency_ms: float, status: str = "error") -> dict[str, Any]:
    return {
        "count": None,
        "results": [],
        "items": [],
        "error": error,
        "latency_ms": latency_ms,
        "mem0_guard": {
            "status": status,
            "ttl_seconds": MEM0_DASHBOARD_CACHE_TTL,
            "stale_ttl_seconds": MEM0_DASHBOARD_STALE_TTL,
            "cooldown_seconds": MEM0_DASHBOARD_COOLDOWN,
        },
    }


def _mem0_dashboard_json(
    cache_key: str,
    *,
    timeout: float,
    fetcher,
) -> dict[str, Any]:
    start = time.time()
    cached = _mem0_dashboard_cache_get(cache_key)
    if cached is not None:
        cached["latency_ms"] = round((time.time() - start) * 1000, 1)
        return cached

    circuit_error = _mem0_dashboard_circuit_error()
    if circuit_error:
        stale = _mem0_dashboard_cache_get(cache_key, stale=True)
        if stale is not None:
            stale["latency_ms"] = round((time.time() - start) * 1000, 1)
            stale["error"] = circuit_error
            stale["mem0_guard"]["status"] = "circuit-stale-cache"
            stale["mem0_guard"]["last_error"] = circuit_error
            return stale
        return _mem0_dashboard_error(
            circuit_error,
            latency_ms=round((time.time() - start) * 1000, 1),
            status="circuit-open",
        )

    acquired = _MEM0_DASHBOARD_FETCH_LOCK.acquire(blocking=False)
    if not acquired:
        stale = _mem0_dashboard_cache_get(cache_key, stale=True)
        if stale is not None:
            stale["latency_ms"] = round((time.time() - start) * 1000, 1)
            stale["mem0_guard"]["status"] = "inflight-stale-cache"
            return stale
        waited = _MEM0_DASHBOARD_FETCH_LOCK.acquire(timeout=max(0.1, min(float(timeout), 2.0)))
        if waited:
            _MEM0_DASHBOARD_FETCH_LOCK.release()
            cached = _mem0_dashboard_cache_get(cache_key)
            if cached is not None:
                cached["latency_ms"] = round((time.time() - start) * 1000, 1)
                cached["mem0_guard"]["status"] = "coalesced-cache"
                return cached
        return _mem0_dashboard_error(
            "Mem0 request already in flight",
            latency_ms=round((time.time() - start) * 1000, 1),
            status="inflight",
        )

    try:
        cached = _mem0_dashboard_cache_get(cache_key)
        if cached is not None:
            cached["latency_ms"] = round((time.time() - start) * 1000, 1)
            return cached
        raw = fetcher()
        payload = json.loads(raw)
        payload = dict(payload) if isinstance(payload, dict) else {}
        _mem0_dashboard_cache_set(cache_key, payload)
        _mem0_dashboard_record_success()
        payload["latency_ms"] = round((time.time() - start) * 1000, 1)
        payload["mem0_guard"] = {"status": "live", "ttl_seconds": MEM0_DASHBOARD_CACHE_TTL}
        return payload
    except Exception as exc:
        error = _mem0_dashboard_record_failure(exc)
        stale = _mem0_dashboard_cache_get(cache_key, stale=True)
        if stale is not None:
            stale["latency_ms"] = round((time.time() - start) * 1000, 1)
            stale["error"] = error
            stale["mem0_guard"]["status"] = "error-stale-cache"
            stale["mem0_guard"]["last_error"] = error
            return stale
        return _mem0_dashboard_error(
            error,
            latency_ms=round((time.time() - start) * 1000, 1),
        )
    finally:
        _MEM0_DASHBOARD_FETCH_LOCK.release()


def _worktree_dirty_state() -> dict[str, Any]:
    if not (REPO_ROOT / ".git").exists():
        return {"dirty": False, "status_count": 0, "sample": [], "error": None, "git_present": False}
    proc = _run(["git", "status", "--short"], timeout=DASHBOARD_GIT_STATUS_TIMEOUT)
    if proc.returncode != 0:
        return {
            "dirty": None,
            "status_count": 0,
            "sample": [],
            "error": proc.stderr.strip() or proc.stdout.strip() or "git status failed",
            "git_present": True,
        }
    rows = [line for line in proc.stdout.splitlines() if line.strip()]
    return {"dirty": len(rows) > 0, "status_count": len(rows), "sample": rows[:10], "error": None, "git_present": True}


def _file_signature(path: pathlib.Path) -> tuple[str, str]:
    stat = path.stat()
    updated = dt.datetime.fromtimestamp(stat.st_mtime, tz=tm_core.TZ_CN).isoformat(timespec="seconds")
    digest = hashlib.sha1()
    digest.update(path.read_bytes())
    return updated, digest.hexdigest()[:16]


def _action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action or "保留观察")


def _decision_root() -> pathlib.Path:
    return REPO_ROOT / ".tmp" / "review-ui-decisions"


def _kept_paths_path(date: str) -> pathlib.Path:
    if not DATE_RE.fullmatch(date):
        raise ValueError("date must be YYYY-MM-DD")
    return _decision_root() / date / "kept.json"


def _load_kept_paths(date: str) -> set[str]:
    path = _kept_paths_path(date)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, dict):
        paths = data.get("paths", [])
    else:
        paths = data
    return {str(item).replace("\\", "/") for item in paths if str(item).startswith("inbox/")}


def _mark_kept(date: str, inbox_path: str) -> dict[str, Any]:
    rel = _relpath(_repo_path(inbox_path))
    if not rel.startswith("inbox/"):
        raise ValueError("only inbox paths can be kept")
    path = _kept_paths_path(date)
    paths = sorted(_load_kept_paths(date) | {rel})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": date, "paths": paths, "updated_at": dt.datetime.now(tm_core.TZ_CN).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "action": "keep", "path": rel, "date": date, "hidden": True}


def _wsl_mount_to_windows_path(path: pathlib.Path) -> str | None:
    raw = path.as_posix()
    match = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", raw)
    if not match:
        return None
    drive = match.group(1).upper()
    tail = (match.group(2) or "").replace("/", "\\")
    return f"{drive}:\\{tail}" if tail else f"{drive}:\\"


def _windows_git_executable() -> str | None:
    configured = os.getenv("TM_DASHBOARD_WINDOWS_GIT")
    candidates = [
        configured,
        "/mnt/f/software/Git/cmd/git.exe",
        "/mnt/f/software/Git/bin/git.exe",
        "/mnt/c/Program Files/Git/cmd/git.exe",
        "/mnt/c/Program Files/Git/bin/git.exe",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).exists():
            return candidate
    return None


def _prepare_command(cmd: list[str]) -> tuple[list[str], pathlib.Path | None]:
    if not cmd or cmd[0] != "git" or os.name == "nt":
        return cmd, REPO_ROOT
    win_root = _wsl_mount_to_windows_path(REPO_ROOT)
    win_git = _windows_git_executable() if win_root else None
    if win_git:
        return [win_git, "-C", win_root, *cmd[1:]], None
    return cmd, REPO_ROOT


def _run(cmd: list[str], *, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    if cmd and cmd[0] == "git" and not (REPO_ROOT / ".git").exists():
        return subprocess.CompletedProcess(
            cmd,
            128,
            "",
            f"git metadata not found at dashboard root: {REPO_ROOT}",
        )
    prepared_cmd, cwd = _prepare_command(cmd)
    try:
        return subprocess.run(
            prepared_cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output.decode("utf-8", errors="replace") if isinstance(exc.output, bytes) else (exc.output or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        if not stderr:
            stderr = f"command timed out after {timeout}s: {' '.join(cmd)}"
        return subprocess.CompletedProcess(cmd, 124, stdout, stderr)


def _run_checked(cmd: list[str], *, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout: {(proc.stdout or '').strip()[:800]}\n"
            f"stderr: {(proc.stderr or '').strip()[:800]}"
        )
    return proc


def _blocking_dirty_paths(status_text: str, allowed_dirty_paths: list[str] | None = None) -> list[str]:
    allowed = {item.replace("\\", "/").lstrip("/") for item in (allowed_dirty_paths or [])}
    blocked: list[str] = []
    meta_prefixes = ("AGENTS.md", "schemas/", "index.md")
    for raw in status_text.splitlines():
        if not raw.strip():
            continue
        code = raw[:2]
        rel = raw[3:].replace("\\", "/") if len(raw) > 3 else raw[2:].strip().replace("\\", "/")
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[-1]
        if rel in allowed:
            continue
        if "U" in code or code in {"AA", "DD"}:
            blocked.append(raw)
            continue
        if any(rel == prefix or rel.startswith(prefix) for prefix in meta_prefixes):
            blocked.append(raw)
            continue
        if code[0] not in {" ", "?"}:
            blocked.append(raw)
    return blocked


def ensure_write_ready(allowed_dirty_paths: list[str] | None = None) -> None:
    status = _run_checked(["git", "status", "--short"])
    blocked = _blocking_dirty_paths(status.stdout, allowed_dirty_paths)
    if blocked:
        raise RuntimeError("dirty worktree has blocking paths: " + "; ".join(blocked[:8]))
    _run_checked(["git", "fetch", "origin", "master"], timeout=600)
    head = _run_checked(["git", "rev-parse", "HEAD"]).stdout.strip()
    origin = _run_checked(["git", "rev-parse", "origin/master"]).stdout.strip()
    if head == origin:
        return
    ahead_behind = _run_checked(["git", "rev-list", "--left-right", "--count", "HEAD...origin/master"])
    parts = ahead_behind.stdout.split()
    ahead = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else 0
    behind = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    if ahead == 0 and behind > 0:
        _run_checked(["git", "pull", "--ff-only", "origin", "master"], timeout=600)
        return
    if ahead > 0 and behind == 0:
        raise RuntimeError(
            "worktree has local commit(s) not pushed to origin/master; push them before review actions"
        )
    raise RuntimeError("worktree diverged from origin/master; resolve git state before review actions")


def git_sha() -> str:
    proc = _run(["git", "rev-parse", "--short", "HEAD"])
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _repo_path(raw: str) -> pathlib.Path:
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        path = candidate.resolve()
    else:
        rel = raw.replace("\\", "/").lstrip("/")
        path = (REPO_ROOT / rel).resolve()
    try:
        path.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError("path must stay inside repo") from exc
    return path


def _relpath(path: pathlib.Path | str) -> str:
    p = pathlib.Path(path)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return str(p.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def _topic_from_inbox_path(rel: str) -> str:
    stem = pathlib.PurePosixPath(rel).stem
    topic = stem.rsplit("-", 1)[-1]
    topics = getattr(tm_core, "TOPICS", set())
    if topic in topics:
        return topic
    return "cross"


def _partition_from_topic(topic: str) -> str:
    return TOPIC_PARTITIONS.get(topic, "systems")


def _ascii_slug_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text.lower()):
        if token in SLUG_STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= 6:
            break
    return tokens


def _suggest_wiki_slug(path: str, title: str, preview: str, partition: str) -> str:
    tokens = _ascii_slug_tokens(" ".join([title, preview, path]))
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:6]
    if tokens:
        base = "-".join(tokens[:5])
    else:
        stem = pathlib.PurePosixPath(path).stem.lower()
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{4}-", "", stem)
        base = re.sub(r"[^a-z0-9-]+", "-", stem).strip("-") or partition.replace("-", "")
    slug = re.sub(r"[^a-z0-9-]+", "-", f"{base}-{digest}").strip("-")[:80].strip("-")
    tm_core.validate_slug(slug)
    return slug


def _similar_tokens(text: str) -> set[str]:
    ascii_tokens = {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text.lower())
        if token not in SLUG_STOPWORDS
    }
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_tokens = {"".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1))}
    return ascii_tokens | {token for token in cjk_tokens if len(token) == 2}


_WIKI_TOKENS_CACHE_LOCK = threading.Lock()
_WIKI_TOKENS_CACHE: dict[str, tuple[float, float, set[str]]] = {}
_WIKI_TOKENS_CACHE_TTL = 5.0  # seconds

def _get_cached_wiki_tokens(path: pathlib.Path) -> set[str]:
    now = time.time()
    path_str = str(path)
    
    with _WIKI_TOKENS_CACHE_LOCK:
        if path_str in _WIKI_TOKENS_CACHE:
            mtime, cached_at, tokens = _WIKI_TOKENS_CACHE[path_str]
            if now - cached_at < _WIKI_TOKENS_CACHE_TTL:
                return tokens
                
    try:
        stat = path.stat()
        mtime = stat.st_mtime
    except OSError:
        return set()
        
    with _WIKI_TOKENS_CACHE_LOCK:
        if path_str in _WIKI_TOKENS_CACHE:
            old_mtime, _, tokens = _WIKI_TOKENS_CACHE[path_str]
            if old_mtime == mtime:
                _WIKI_TOKENS_CACHE[path_str] = (mtime, now, tokens)
                return tokens
                
    try:
        text = path.read_text(encoding="utf-8")[:4000]
        tokens = _similar_tokens(text)
    except OSError:
        tokens = set()
        
    with _WIKI_TOKENS_CACHE_LOCK:
        _WIKI_TOKENS_CACHE[path_str] = (mtime, now, tokens)
        
    return tokens


def _similar_wiki_targets(partition: str, title: str, preview: str, *, limit: int = 3) -> list[dict[str, str]]:
    wiki_dir = REPO_ROOT / "wiki" / partition
    query_tokens = _similar_tokens(f"{title} {preview}")
    if not query_tokens or not wiki_dir.exists():
        return []
    scored: list[tuple[int, pathlib.Path, set[str]]] = []
    for path in wiki_dir.glob("*.md"):
        if path.name == "index.md":
            continue
        tokens = _get_cached_wiki_tokens(path)
        if not tokens:
            continue
        overlap_set = query_tokens & tokens
        if not overlap_set:
            continue
        scored.append((len(overlap_set), path, overlap_set))
        
    scored.sort(key=lambda item: (-item[0], item[1].name))
    
    res = []
    for _score, path, overlap_set in scored[:limit]:
        reason = "关键词重合：" + "、".join(sorted(overlap_set)[:6])
        res.append({"path": _relpath(path), "reason": reason})
    return res


def _wiki_target_suggestions(row: dict[str, Any], *, include_similar: bool = True) -> dict[str, Any]:
    path = str(row.get("path") or "")
    topic = _topic_from_inbox_path(path)
    partition = _partition_from_topic(topic)
    title = str(row.get("title_cn") or row.get("cn_summary") or row.get("raw_summary") or "")
    preview = str(row.get("preview_cn") or row.get("cn_summary") or row.get("summary") or "")
    slug = _suggest_wiki_slug(path, title, preview, partition)
    reason = f"根据 inbox 文件名 topic={topic} 推断到 {partition} 分区，并用标题/预览生成不易重复的 slug。"
    alternatives = []
    for part, label, part_reason in PARTITION_OPTIONS:
        alt_slug = _suggest_wiki_slug(path, title, preview, part)
        alternatives.append({
            "partition": part,
            "slug": alt_slug,
            "label": label,
            "path": f"wiki/{part}/{alt_slug}.md",
            "reason": part_reason,
            "recommended": part == partition,
        })
    return {
        "partition": partition,
        "slug": slug,
        "path": f"wiki/{partition}/{slug}.md",
        "reason": reason,
        "alternatives": alternatives,
        "similar": _similar_wiki_targets(partition, title, preview) if include_similar else [],
    }


def _load_token() -> str | None:
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = data.get("token")
    return str(token) if token else None


def _ensure_token() -> str:
    token = _load_token()
    if token:
        return token
    token = secrets.token_hex(32)
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"token": token}, ensure_ascii=False, indent=2), encoding="utf-8")
    return token


def _dashboard_auth_token() -> str | None:
    return os.getenv("TM_DASHBOARD_TOKEN") or os.getenv("TM_MCP_API_KEY") or os.getenv("MEM0_API_KEY")


def _token_matches(value: str | None) -> bool:
    expected = _dashboard_auth_token()
    if not expected or not value:
        return False
    return secrets.compare_digest(str(value), str(expected))


def _authorization_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return None


def _is_local_host(request: Request) -> bool:
    return request.headers.get("host", "").split(",", 1)[0].strip().lower() in LOCAL_HOSTS


def _can_bootstrap_cookie(request: Request) -> bool:
    host = request.headers.get("host", "").split(",", 1)[0].strip().lower()
    return host in COOKIE_BOOTSTRAP_HOSTS


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        fm[key.strip()] = value.strip().strip('"')
    return fm, text[end + 5 :]


def _section(text: str, heading: str) -> str:
    marker = f"\n## {heading}\n"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end < 0 else text[start:end]


def _empty_self_evolution_payload(date: str, *, expected_count: int = 0) -> dict[str, Any]:
    return {
        "source": "digest" if expected_count else "none",
        "date": date,
        "event_count": expected_count,
        "counts": {},
        "outcome_pending": 0,
        "samples": [],
        "inbox_route": "AGENTS.md section 9.3 topic=selfevolution",
    }


def _build_self_evolution_payload(
    date: str,
    text: str | None = None,
    *,
    expected_count: int = 0,
) -> dict[str, Any]:
    try:
        from tm_self_evolution import collect_summary_for_date
    except Exception:
        return _empty_self_evolution_payload(date, expected_count=expected_count)
    try:
        payload = collect_summary_for_date(date, root=REPO_ROOT)
    except Exception:
        return _empty_self_evolution_payload(date, expected_count=expected_count)
    if not isinstance(payload, dict):
        return _empty_self_evolution_payload(date, expected_count=expected_count)
    payload = dict(payload)
    payload.setdefault("date", date)
    payload.setdefault("event_count", 0)
    payload.setdefault("counts", {})
    payload.setdefault("outcome_pending", 0)
    payload.setdefault("samples", [])
    payload.setdefault("inbox_route", "AGENTS.md section 9.3 topic=selfevolution")
    payload["source"] = "live"
    return payload


def _extract_mermaid_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.findall(r"```mermaid\s*\n(.*?)\n```", text, flags=re.S)]


def _parse_project_canvas_modules(text: str) -> list[dict[str, str]]:
    section = _section(text, "活跃模块")
    if not section:
        return []

    rows: list[dict[str, str]] = []
    in_table = False
    for raw in section.splitlines():
        line = raw.strip()
        if not line:
            if rows:
                break
            continue
        if line.startswith("| 模块 |") or line.startswith("|模块|"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|---"):
            continue
        if not line.startswith("|"):
            break
        cols = [item.strip() for item in line.strip("|").split("|")]
        if len(cols) < 4:
            continue
        module_name, status, updated, owner = (cols + ["", "", "", ""])[:4]
        if module_name.startswith("模块"):
            continue
        rows.append({"module": module_name, "status": status, "updated": updated, "owner": owner})
    return rows


def _load_session_rolling_summary_module():
    script_path = UI_REPO_ROOT / "tools" / "session-rolling-summary.py"
    source = script_path.read_text(encoding="utf-8")
    namespace = {
        "__file__": str(script_path),
        "__name__": "session_rolling_summary_canvas",
        "__package__": "",
    }
    exec(compile(source, str(script_path), "exec"), namespace)
    return types.SimpleNamespace(**namespace)


def _canvas_candidate_evidence_key(card: dict[str, Any]) -> str:
    card_id = str(card.get("id", "")).strip()
    if card_id:
        return f"memory:{card_id}"
    session_id = str(card.get("session_id", "")).strip()
    if session_id:
        return f"session:{session_id}"
    return "memory:unknown"


def _canvas_candidate_dedupe_key(card: dict[str, Any]) -> str:
    session_id = str(card.get("session_id", "")).strip()
    if session_id:
        return f"session:{session_id}"
    return _canvas_candidate_evidence_key(card)


def _local_inbox_handoff_items(limit: int = 50) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paths = sorted(_list_inbox_route_paths(), reverse=True)
    for path in paths[:limit]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "session-handoff" not in text:
            continue
        rel = _relpath(path)
        items.append({
            "id": f"inbox:{rel}",
            "content": text,
            "created_at": _inbox_route_date(path) or "",
            "source_path": rel,
        })
    return items


def _load_canvas_candidates(limit: int = 8) -> dict[str, Any]:
    cache_key = f"canvas-candidates:{limit}:{UI_REPO_ROOT}"
    cached, _ = _run_cache_get(cache_key, CANVAS_CANDIDATE_CACHE_TTL)
    if cached:
        cached = dict(cached)
        cached["candidate_cached"] = True
        return cached

    warnings: list[str] = []
    try:
        rolling_summary = _load_session_rolling_summary_module()
    except Exception as exc:
        payload = {
            "canvas_candidates": [],
            "candidate_count": 0,
            "candidate_source": "unavailable",
            "candidate_warnings": [f"待纳入星图候选解析模块加载失败：{exc}"],
            "candidate_cached": False,
        }
        _run_cache_set(cache_key, payload, source="canvas-candidates", ttl_seconds=CANVAS_CANDIDATE_CACHE_TTL)
        return payload

    try:
        payload = _mem0_payload("memory_type: session-handoff", size=50, timeout=2)
    except Exception as exc:
        payload = {"items": [], "count": 0, "error": str(exc)}
    if payload.get("error"):
        env_payload = _canvas_candidates_mem0_payload_from_env("memory_type: session-handoff", size=50, timeout=2)
        if env_payload is not None:
            payload = env_payload
    inbox_items = _local_inbox_handoff_items(limit=50)
    if payload.get("error") and not inbox_items:
        candidate_payload = {
            "canvas_candidates": [],
            "candidate_count": 0,
            "candidate_source": "mem0:session-handoff",
            "candidate_warnings": [f"待纳入星图候选读取失败：{payload['error']}"],
            "candidate_cached": False,
        }
        _run_cache_set(cache_key, candidate_payload, source="canvas-candidates", ttl_seconds=CANVAS_CANDIDATE_CACHE_TTL)
        return candidate_payload
    if payload.get("error"):
        warnings.append(f"Mem0 交接卡读取失败，已降级读取 inbox/wiki_proposal：{payload['error']}")

    cards: list[dict[str, Any]] = []
    cards_by_evidence: dict[str, dict[str, Any]] = {}
    seen_card_keys: set[str] = set()
    for item in [*_mem0_items(payload), *inbox_items]:
        try:
            card = rolling_summary.parse_card(item)
        except Exception as exc:
            warnings.append(f"跳过一张无法解析的交接卡：{exc}")
            continue
        if not card:
            continue
        dedupe_key = _canvas_candidate_dedupe_key(card)
        if dedupe_key in seen_card_keys:
            continue
        seen_card_keys.add(dedupe_key)
        cards.append(card)
        cards_by_evidence[_canvas_candidate_evidence_key(card)] = card

    try:
        raw_candidates = rolling_summary.build_canvas_update_candidates(cards)
    except Exception as exc:
        candidate_payload = {
            "canvas_candidates": [],
            "candidate_count": 0,
            "candidate_source": "mem0:session-handoff",
            "candidate_warnings": [f"待纳入星图候选构建失败：{exc}"],
            "candidate_cached": False,
        }
        _run_cache_set(cache_key, candidate_payload, source="canvas-candidates", ttl_seconds=CANVAS_CANDIDATE_CACHE_TTL)
        return candidate_payload

    candidates: list[dict[str, Any]] = []
    for raw in raw_candidates[:limit]:
        candidate = dict(raw)
        evidence_refs = [str(ref) for ref in candidate.get("evidence_refs", []) if str(ref).strip()]
        first_card = cards_by_evidence.get(evidence_refs[0]) if evidence_refs else None
        source = (
            str(candidate.get("source", "")).strip()
            or (str(first_card.get("source", "")).strip() if first_card else "")
            or (str(first_card.get("agent", "")).strip() if first_card else "")
            or "session-handoff"
        )
        candidate["evidence_refs"] = evidence_refs
        candidate["evidence_count"] = len(evidence_refs)
        candidate["review_state"] = candidate.get("review_state") or "建议纳入"
        candidate["source"] = source
        candidate["name"] = candidate.get("name") or candidate.get("target_module") or candidate.get("summary") or "project-canvas"
        candidates.append(candidate)

    candidate_payload = {
        "canvas_candidates": candidates,
        "candidate_count": len(candidates),
        "candidate_source": "mem0:session-handoff + inbox/wiki_proposal + session-rolling-summary.py",
        "candidate_warnings": warnings,
        "candidate_cached": False,
    }
    _run_cache_set(cache_key, candidate_payload, source="canvas-candidates", ttl_seconds=CANVAS_CANDIDATE_CACHE_TTL)
    return candidate_payload


def _canvas_candidates_mem0_payload_from_env(query: str, *, size: int, timeout: float) -> dict[str, Any] | None:
    base = (os.getenv("MEM0_URL") or "").strip().rstrip("/")
    if not base:
        return None
    params = urllib.parse.urlencode({
        "user_id": os.getenv("MEM0_USER_ID") or "tiger",
        "search_query": query,
        "page": 1,
        "size": size,
        "match_mode": "id_first",
    })

    def fetch() -> str:
        req = urllib.request.Request(f"{base}/api/v1/memories/?{params}", method="GET")
        api_key = (os.getenv("MEM0_API_KEY") or "").strip()
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    payload = _mem0_dashboard_json(
        f"mem0-env:{hashlib.sha256((base + '?' + params).encode('utf-8')).hexdigest()[:16]}",
        timeout=timeout,
        fetcher=fetch,
    )
    payload["count"] = payload.get("count", payload.get("total"))
    payload.setdefault("items", payload.get("results", []))
    if payload.get("error"):
        payload["error"] = f"MEM0_URL fallback failed: {payload['error']}"
    return payload


def _with_canvas_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    candidate_payload = _load_canvas_candidates(limit=8)
    payload.update(candidate_payload)
    return payload


def _load_canvas_payload() -> dict[str, Any]:
    if not CANVAS_SOURCE_PATH.exists():
        worktree = _worktree_dirty_state()
        payload = {
            "ok": False,
            "source_path": str(CANVAS_SOURCE_PATH),
            "error": "project canvas markdown missing",
            "mermaid_src": "",
            "updated": "",
            "active_modules": [],
        }
        start = time.time()
        payload["generated_at"] = _now_iso()
        payload["latency_ms"] = round((time.time() - start) * 1000, 1)
        payload["source"] = "project-canvas.md"
        payload["source_hash"] = ""
        payload["source_updated_at"] = None
        payload["fallback_mode"] = True
        payload["warnings"] = [
            "project canvas source file is missing",
            "请确认 wiki/operations/project-canvas.md 文件是否存在并可读",
        ]
        if worktree.get("dirty"):
            payload["warnings"].append(f"工作区存在未提交改动，可能影响画布读取准确性（{worktree['status_count']}项）")
        payload["cache"] = {
            "hit": False,
            "ttl_seconds": CANVAS_CACHE_TTL,
            "age_ms": 0,
            "cached_at": payload["generated_at"],
            "source": payload["source"],
            "source_path": str(CANVAS_SOURCE_PATH),
            "source_hash": payload["source_hash"],
        }
        payload["errors"] = []
        if worktree.get("error"):
            payload["errors"].append(worktree["error"])
        payload["cached"] = False
        payload["stale"] = False
        payload["repo_dirty"] = worktree.get("dirty")
        return _with_canvas_candidates(payload)

    source_updated, source_hash = _file_signature(CANVAS_SOURCE_PATH)
    cache_key = f"canvas:{CANVAS_SOURCE_PATH}:{source_hash}"
    cached, _ = _run_cache_get(cache_key, CANVAS_CACHE_TTL)
    if cached:
        worktree = _worktree_dirty_state()
        cached.setdefault("warnings", [])
        cached.setdefault("errors", [])
        cached["repo_dirty"] = worktree.get("dirty")
        if worktree.get("dirty"):
            warning = f"工作区存在未提交改动，可能影响画布读取准确性（{worktree['status_count']}项）"
            if warning not in cached["warnings"]:
                cached["warnings"].append(warning)
        if worktree.get("error") and worktree["error"] not in cached["errors"]:
            cached["errors"].append(worktree["error"])
        cached["cached"] = True
        return _with_canvas_candidates(cached)

    start = time.time()
    try:
        text = CANVAS_SOURCE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        payload = {
            "ok": False,
            "source_path": str(CANVAS_SOURCE_PATH),
            "error": f"failed to read project canvas: {exc}",
            "mermaid_src": "",
            "updated": "",
            "active_modules": [],
            "source": "project-canvas.md",
            "source_hash": source_hash,
            "source_updated_at": source_updated,
            "fallback_mode": True,
            "warnings": ["读取画布文件失败，回退为错误展示"],
            "errors": [str(exc)],
        }
        payload["generated_at"] = _now_iso()
        payload["latency_ms"] = round((time.time() - start) * 1000, 1)
        payload["cached"] = False
        payload["stale"] = False
        payload["repo_dirty"] = _worktree_dirty_state().get("dirty")
        payload["cache"] = {
            "hit": False,
            "ttl_seconds": CANVAS_CACHE_TTL,
            "age_ms": 0,
            "cached_at": payload["generated_at"],
            "source": payload["source"],
            "source_path": str(CANVAS_SOURCE_PATH),
            "source_hash": payload["source_hash"],
        }
        return _with_canvas_candidates(payload)

    fm, _body = _frontmatter(text)
    mermaid_blocks = _extract_mermaid_blocks(text)
    active_modules = _parse_project_canvas_modules(text)
    worktree = _worktree_dirty_state()
    warnings: list[str] = []
    if not mermaid_blocks:
        warnings.append("project canvas markdown 缺少 mermaid 区块，页面将以降级提示渲染")
    if worktree.get("dirty"):
        warnings.append(f"工作区存在未提交改动，可能影响画布读取准确性（{worktree['status_count']}项）")

    payload = {
        "ok": bool(mermaid_blocks),
        "source_path": str(CANVAS_SOURCE_PATH),
        "mermaid_src": "\n\n".join(mermaid_blocks),
        "updated": fm.get("updated", ""),
        "active_modules": active_modules,
        "error": None if mermaid_blocks else "No mermaid block found",
        "source": "project-canvas.md",
        "source_hash": source_hash,
        "source_updated_at": source_updated,
        "fallback_mode": not bool(mermaid_blocks),
        "warnings": warnings,
    }
    payload["generated_at"] = _now_iso()
    payload["latency_ms"] = round((time.time() - start) * 1000, 1)
    payload["cached"] = False
    payload["stale"] = False
    payload["repo_dirty"] = worktree.get("dirty")
    payload["errors"] = []
    if worktree.get("error"):
        payload["errors"].append(worktree["error"])
    payload["cache"] = {
        "hit": False,
        "ttl_seconds": CANVAS_CACHE_TTL,
        "age_ms": 0.0,
        "cached_at": payload["generated_at"],
        "source": payload["source"],
        "source_path": str(CANVAS_SOURCE_PATH),
        "source_hash": source_hash,
    }
    _run_cache_set(
        cache_key,
        payload,
        source=payload["source"],
        source_path=str(CANVAS_SOURCE_PATH),
        source_hash=source_hash,
        source_updated_at=source_updated,
        ttl_seconds=CANVAS_CACHE_TTL,
    )
    return _with_canvas_candidates(payload)


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _is_inbox_route_file(path: pathlib.Path) -> bool:
    if not path.is_file() or path.suffix != ".md":
        return False
    lower_name = path.name.lower()
    if lower_name in {"index.md", ".gitkeep"}:
        return False
    return bool(INBOX_ROUTE_FILE_RE.fullmatch(path.name))


def _list_inbox_route_paths() -> list[pathlib.Path]:
    inbox_dir = REPO_ROOT / "inbox"
    if not inbox_dir.is_dir():
        return []
    return sorted(path for path in inbox_dir.iterdir() if _is_inbox_route_file(path))


def _inbox_route_date(path: str | pathlib.Path | None) -> str | None:
    if not path:
        return None
    name = pathlib.PurePosixPath(path).name
    if not INBOX_ROUTE_FILE_RE.fullmatch(name):
        return None
    return name[:10]


def _summarize_inbox_rows(rows: list[dict[str, Any]], report_date: str) -> tuple[int, int]:
    today_rows = [row for row in rows if _inbox_route_date(row.get("path")) == report_date]
    return len(rows), len(today_rows)


def _row_matches_quality_dates(row: dict[str, Any], report_date: str, date_filter: set[str] | None = None) -> bool:
    row_date = _inbox_route_date(row.get("path"))
    if date_filter is not None:
        return row_date in date_filter
    return row_date == report_date


def _summarize_inbox_rows_for_dates(
    rows: list[dict[str, Any]],
    report_date: str,
    date_filter: set[str] | None = None,
) -> tuple[int, int]:
    matched_rows = [row for row in rows if _row_matches_quality_dates(row, report_date, date_filter)]
    if date_filter is not None:
        return len(matched_rows), len(matched_rows)
    return len(rows), len(matched_rows)


def _sum_trace_issues(status_counts: dict[str, Any]) -> int:
    if not isinstance(status_counts, dict):
        return 0
    return _parse_int(status_counts.get("fail"), 0) + _parse_int(status_counts.get("error"), 0)


def _quality_route_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(parsed, 0)


def _quality_date_bounds(report_date: str) -> tuple[dt.datetime, dt.datetime]:
    day = dt.date.fromisoformat(report_date)
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tm_core.TZ_CN)
    return start, start + dt.timedelta(days=1)


def _quality_live_mem0_count(report_date: str, mem0_status: dict[str, Any]) -> tuple[int | None, str]:
    status_error = mem0_status.get("error")
    try:
        start, end = _quality_date_bounds(report_date)
        params = urllib.parse.urlencode({
            "user_id": tm_core.mem0_user_id(),
            "page": 1,
            "size": 1,
            "from_date": int(start.timestamp()),
            "to_date": int(end.timestamp()),
            "sort_column": "created_at",
            "sort_direction": "desc",
        })
        url = f"{tm_core.mem0_base().rstrip('/')}/api/v1/memories/?{params}"
        data = _mem0_dashboard_json(
            f"mem0-date-count:{report_date}:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}",
            timeout=2.0,
            fetcher=lambda: tm_core.mem0_request(url, timeout=2.0),
        )
        if data.get("error") and not data.get("cached"):
            raise RuntimeError(str(data["error"]))
        total = data.get("total") if isinstance(data, dict) else None
        if isinstance(total, int):
            guard = data.get("mem0_guard") if isinstance(data, dict) else {}
            source = "Mem0 服务端日期过滤"
            if isinstance(guard, dict) and guard.get("status") != "live":
                source = "Mem0 缓存日期过滤"
            return total, f"{source} {report_date} 当日写入 {total} 条"
    except Exception as exc:
        if status_error:
            return None, f"Mem0 不可达：{_safe_str(status_error)}"
        if os.getenv("TM_DASHBOARD_MEM0_FULL_SCAN_FALLBACK") == "1":
            try:
                rows = tm_memory_ops.fetch_mem0_items_by_date_range(start, end, max_items=2000, page_size=100)
            except Exception as fallback_exc:
                return None, f"Mem0 今日写入统计失败：{_safe_str(fallback_exc)}"
            return len(rows), f"Mem0 实时读取 {report_date} 当日写入 {len(rows)} 条"
        return None, f"Mem0 今日写入统计失败：{_safe_str(exc)}"
    return None, "Mem0 今日写入统计未返回 total"


def _quality_live_discard_count(report_date: str) -> tuple[int, str]:
    try:
        rows = tm_memory_reflection.discard_events_for_dates([report_date])
    except Exception as exc:
        return 0, f"discard 审计读取失败：{_safe_str(exc)}"
    return len(rows), f"discard 审计实时读取 {report_date} 当日 {len(rows)} 条"


def _quality_route_target_from_row(row: dict[str, Any]) -> str | None:
    target = str(row.get("route_target") or "").strip().lower()
    if target in {"mem0", "wiki", "inbox", "discard"}:
        return target
    action = " ".join(
        str(row.get(key) or "").strip().lower()
        for key in ("codex_recommended_action", "action", "route_label")
    )
    if any(marker in action for marker in ("wiki", "写入 wiki", "写入wiki")):
        return "wiki"
    if any(marker in action for marker in ("mem0", "即时记忆", "promote_to_mem0")):
        return "mem0"
    if any(marker in action for marker in ("discard", "归档", "忽略")):
        return "discard"
    if any(marker in action for marker in ("inbox", "人工", "keep_in_inbox", "转人工")):
        return "inbox"
    return None


def _quality_route_recommendation_counts(
    inbox_rows: list[dict[str, Any]],
    report_date: str,
    date_filter: set[str] | None = None,
) -> dict[str, int]:
    counts = {"mem0": 0, "wiki": 0, "inbox": 0, "discard": 0}
    for row in inbox_rows:
        if not _row_matches_quality_dates(row, report_date, date_filter):
            continue
        target = _quality_route_target_from_row(row)
        if target in counts:
            counts[target] += 1
    return counts


def _quality_cached_recommendation_counts(value: Any) -> dict[str, int]:
    counts = {"mem0": 0, "wiki": 0, "inbox": 0, "discard": 0}
    if not isinstance(value, dict):
        return counts
    for key in counts:
        counts[key] = _quality_route_value(value.get(key)) or 0
    return counts


def _build_quality_route_flow(
    counts: dict[str, Any],
    report_date: str,
    trace_summary: dict[str, Any],
    trace_rows: list[dict[str, Any]],
    inbox_rows: list[dict[str, Any]],
    *,
    digest_error: str | None = None,
    source_mode: str = "live",
    date_filter: set[str] | None = None,
    period_label: str = "今日",
    trace_period_label: str = "近 7 天",
) -> dict[str, Any]:
    trace_status = trace_summary.get("status_counts") if isinstance(trace_summary, dict) else {}
    if not isinstance(trace_status, dict):
        trace_status = {}
    issue_count = _sum_trace_issues(trace_status)
    issue_basis = [
        f"trace status: not_found={trace_status.get('not_found', 0)}, fail={trace_status.get('fail', 0)}, error={trace_status.get('error', 0)}"
    ]
    if digest_error:
        issue_basis.append(f"digest 解析异常：{_safe_str(digest_error)}")

    mem0_count = _quality_route_value(counts.get("mem0"))
    wiki_count = _quality_route_value(counts.get("wiki"))
    inbox_today = _quality_route_value(counts.get("inbox_today"))
    discard_count = _quality_route_value(counts.get("discard"))
    trace_count = len(trace_rows)
    route_total, inbox_today_rows = _summarize_inbox_rows_for_dates(inbox_rows, report_date, date_filter)
    review_entered = _quality_route_value(counts.get("review_entered"))
    inbox_pending = _quality_route_value(counts.get("inbox_pending"))
    if inbox_pending is None:
        inbox_pending = route_total
    if inbox_today is None:
        inbox_today = inbox_today_rows
    if not inbox_rows and inbox_pending is not None:
        route_total = inbox_pending

    recommended = _quality_route_recommendation_counts(inbox_rows, report_date, date_filter)
    if not sum(recommended.values()):
        recommended = _quality_cached_recommendation_counts(counts.get("route_recommendation_counts"))
    recommended_total = sum(recommended.values())
    route_event_counts = _quality_cached_recommendation_counts(counts.get("route_event_counts"))
    route_event_total = sum(route_event_counts.values())
    use_route_event_flow = route_event_total > 0
    use_recommended_flow = bool(recommended_total and source_mode != "range" and not use_route_event_flow)
    if use_route_event_flow:
        flow_mem0_count = route_event_counts["mem0"]
        flow_wiki_count = route_event_counts["wiki"]
        flow_inbox_count = route_event_counts["inbox"]
        flow_discard_count = route_event_counts["discard"]
        flow_source = "route_events"
    elif use_recommended_flow:
        flow_mem0_count: int | None = recommended["mem0"]
        flow_wiki_count: int | None = recommended["wiki"]
        flow_inbox_count: int | None = recommended["inbox"]
        flow_discard_count: int | None = recommended["discard"]
        flow_source = "route_recommendation"
    elif source_mode == "live":
        flow_mem0_count = mem0_count
        flow_wiki_count = wiki_count
        flow_inbox_count = inbox_pending
        flow_discard_count = discard_count
        flow_source = source_mode
    else:
        flow_mem0_count = mem0_count
        flow_wiki_count = wiki_count
        flow_inbox_count = review_entered if source_mode == "range" and review_entered is not None else inbox_today
        flow_discard_count = discard_count
        flow_source = source_mode

    mem0_status = "ok" if flow_mem0_count is not None else "warn"
    wiki_status = "ok" if flow_wiki_count is not None else "warn"
    inbox_status = "ok" if flow_inbox_count is not None else "warn"
    discard_status = "ok" if flow_discard_count is not None else "warn"
    issue_status = "warn" if issue_count else "ok"
    trace_basis = f"TM 溯源链状态{trace_period_label}"

    if use_route_event_flow:
        mem0_basis = f"{period_label}真实路由流水 route=mem0 {route_event_counts['mem0']} 条"
        wiki_basis = f"{period_label}真实路由流水 route=wiki {route_event_counts['wiki']} 条"
        discard_basis = f"{period_label}真实路由流水 route=discard {route_event_counts['discard']} 条"
    elif use_recommended_flow:
        mem0_basis = f"{period_label}待审 route_target=mem0 {recommended['mem0']} 条"
        wiki_basis = f"{period_label}待审 route_target=wiki {recommended['wiki']} 条"
        discard_basis = f"{period_label}待审 route_target=discard {recommended['discard']} 条"
    elif source_mode == "digest":
        mem0_basis = f"digest frontmatter mem0_count={_safe_str(counts.get('mem0')) or '0'}"
        trace_basis = f"TM 溯源链状态{trace_period_label}"
        if counts.get("wiki_count_source") == "missing":
            wiki_basis = "digest frontmatter 未含 wiki_count，当前不能给可信 Wiki 数"
        elif counts.get("wiki_count_source") == "wiki_proposal_inbox":
            wiki_basis = f"digest/wiki proposal 台账 {period_label}候选={_safe_str(counts.get('wiki')) or '0'}，不是已落正式 Wiki"
        else:
            wiki_basis = f"digest frontmatter wiki_count={_safe_str(counts.get('wiki')) or '0'}"
    else:
        mem0_basis = counts.get("mem0_basis") or f"Mem0 {period_label}写入实时读取"
        trace_basis = f"TM 溯源链本次查询{trace_period_label}"
        if counts.get("wiki_count_source") == "live_not_connected":
            wiki_basis = "实时 wiki route 事件暂未接入质量页，显示 0 不代表全天无 Wiki 写入"
        elif counts.get("wiki_count_source") == "wiki_proposal_inbox":
            wiki_basis = f"{period_label}日报/待审账本中的 Wiki 提案={_safe_str(counts.get('wiki')) or '0'}，不是已落正式 Wiki"
        elif counts.get("wiki_count_source") == "partial_missing":
            wiki_basis = "范围内部分日报缺 wiki_count，当前不能给可信 Wiki 数"
        elif counts.get("wiki") is None:
            wiki_basis = "实时 wiki 口径暂未接入"
        else:
            wiki_basis = f"fallback wiki_count={_safe_str(counts.get('wiki'))}"
    if not use_recommended_flow and not use_route_event_flow:
        discard_basis = (
            counts.get("discard_basis")
            or ("digest frontmatter discard_count 为准" if source_mode == "digest" else "discard 审计实时读取")
        )

    input_total = route_event_total if use_route_event_flow else recommended_total if use_recommended_flow else (
        (flow_mem0_count or 0) + (flow_wiki_count or 0) + (flow_inbox_count or 0) + (flow_discard_count or 0)
    )
    route_flow = {
        "today_total": input_total,
        "input_total": input_total,
        "source_mode": source_mode,
        "flow_source": flow_source,
        "digest_error": digest_error,
        "route_recommendation_counts": recommended,
        "sources": [
            {
                "key": "daily",
                "label": "日常分流",
                "value": flow_mem0_count,
                "status": mem0_status,
                "basis": mem0_basis,
            },
            {
                "key": "inbox",
                "label": "人工审核",
                "value": flow_inbox_count,
                "status": inbox_status,
                "basis": (
                    f"{period_label}真实路由流水 route=inbox {route_event_counts['inbox']} 条；当前待确认={inbox_pending}"
                    if use_route_event_flow
                    else
                    f"{period_label}待审 route_target=inbox {recommended['inbox']} 条"
                    if use_recommended_flow
                    else f"{period_label}进入每日审批唯一内容={review_entered}，当前待确认={inbox_pending}"
                    if source_mode == "range"
                    else f"当前待确认队列={inbox_pending}，不按 inbox 文件日期误算为今日 0"
                    if source_mode == "live"
                    else f"人工审核（live）{period_label}={inbox_today_rows}，统计日期={report_date}"
                ),
            },
            {
                "key": "trace",
                "label": "溯源链",
                "value": trace_count,
                "status": "ok",
                "basis": trace_basis,
            },
        ],
        "outputs": [
            {
                "key": "mem0",
                "label": "即时记忆",
                "description": "输入 Mem0 的增量样本",
                "value": flow_mem0_count,
                "status": mem0_status,
                "basis": mem0_basis,
            },
            {
                "key": "wiki",
                "label": "Wiki 提案",
                "description": "长期知识候选，待审核后进入正式 Wiki",
                "value": flow_wiki_count,
                "status": wiki_status,
                "basis": wiki_basis,
            },
            {
                "key": "inbox",
                "label": "人工审核",
                "description": "今日人工候选，历史待审来自 inbox_pending",
                "value": flow_inbox_count,
                "status": inbox_status,
                "basis": (
                    f"{period_label}真实路由流水 route=inbox {route_event_counts['inbox']} 条；当前待确认={inbox_pending}"
                    if use_route_event_flow
                    else
                    f"{period_label}待审 route_target=inbox {recommended['inbox']} 条；待审总候选={route_total}"
                    if use_recommended_flow
                    else f"{period_label}进入每日审批唯一内容={review_entered}，当前待确认={inbox_pending}"
                    if source_mode == "range"
                    else f"当前待确认队列={inbox_pending}，实时页面不按文件日期误算"
                    if source_mode == "live"
                    else f"人工审核总候选={route_total}，其中{period_label}={inbox_today}"
                ),
            },
            {
                "key": "discard",
                "label": "忽略归档",
                "description": "已弃审/忽略且不入库的内容",
                "value": flow_discard_count,
                "status": discard_status,
                "basis": discard_basis,
            },
            {
                "key": "issue",
                "label": "回答失败",
                "description": "trace 中失败/错误的条目；未找到只在状态分布展示",
                "value": issue_count,
                "status": issue_status,
                "basis": "; ".join(item for item in issue_basis if item),
            },
        ],
    }
    route_flow.update({
        "trace_count": trace_count,
        "mem0": flow_mem0_count,
        "wiki": flow_wiki_count,
        "inbox": flow_inbox_count,
        "discard": flow_discard_count,
        "issue": issue_count,
        "anomaly": issue_count,
        "manual_review": flow_inbox_count,
        "instant": flow_mem0_count,
        "long_term": flow_wiki_count,
        "long_term_knowledge": flow_wiki_count,
        "daily": flow_mem0_count,
        "summary": {
            "inbox_pending": route_total,
            "inbox_today": inbox_today,
        },
        "history": {
            "route_event_count": route_event_total,
            "route_event_counts": route_event_counts,
            "route_event_source": counts.get("route_event_source"),
            "route_event_dates": counts.get("route_event_dates", []),
            "route_event_missing_dates": counts.get("route_event_missing_dates", []),
            "route_event_error": counts.get("route_event_error"),
            "note": (
                f"{period_label}主图只展示已记录路由流水 {route_event_total} 条；"
                f"缺少 {len(counts.get('route_event_missing_dates', []) or [])} 天流水，历史日报和待审积压只作参考。"
                if route_event_total
                else f"{period_label}暂无路由流水；当前图使用旧日报和待审补算，不能当作完整分流事实。"
            ),
        },
        "period_label": period_label,
        "trace_period_label": trace_period_label,
    })
    return route_flow

def _count_inbox_files() -> int:
    return len(_list_inbox_route_paths())


def _parse_inbox_rows(text: str) -> list[dict[str, Any]]:
    sec = _section(text, "📝 inbox 决策区")
    rows: list[dict[str, Any]] = []
    current_group = ""
    current: dict[str, Any] | None = None
    for line in sec.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            current_group = stripped[4:]
            continue
        if stripped.startswith("- `inbox/"):
            if current:
                rows.append(current)
            path = stripped.split("`", 2)[1]
            current = {"path": path, "group": current_group, "stale_archive": "兜底 archive" in stripped}
            continue
        if current is None:
            continue
        if "已停留" in stripped:
            current["age_days"] = _parse_int(stripped.split("已停留", 1)[1].split("天", 1)[0].strip())
        elif stripped.startswith("- 中文标题："):
            current["title_cn"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- 中文预览："):
            current["preview_cn"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- 中文摘要："):
            current["cn_summary"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- 中文说明："):
            current["cn_summary"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- 原文预览："):
            current["raw_summary"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- 内容摘要："):
            current["raw_summary"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- Codex 推荐操作："):
            current["codex_recommended_action"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- Codex 推荐理由："):
            current["codex_recommended_reason"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- cron 建议动作："):
            current["action"] = stripped.split("：", 1)[1]
        elif stripped.startswith("- 建议理由："):
            current["reason"] = stripped.split("：", 1)[1]
    if current:
        rows.append(current)
    for row in rows:
        row.setdefault("title_cn", row.get("cn_summary") or row.get("summary", ""))
        row.setdefault("preview_cn", row.get("cn_summary") or row.get("summary", ""))
        row.setdefault("raw_summary", row.get("summary", ""))
        row.setdefault("cn_summary", "未提供中文摘要：请写入 agent 在正文首行补一句中文概括。")
        row.setdefault("codex_recommended_action", _action_label(str(row.get("action") or "keep")))
        row.setdefault("codex_recommended_reason", row.get("reason") or "暂无推荐理由")
        row["wiki_target"] = _wiki_target_suggestions(row)
    return rows


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _coerce_route_flags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, (tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    stripped = str(value).strip()
    return [stripped] if stripped else []


def _audit_inbox_records(date: str, *, fast: bool = False) -> list[Any]:
    if tm_memory_reflection is None or not hasattr(tm_memory_reflection, "audit_inbox"):
        return []
    return list(tm_memory_reflection.audit_inbox(
        date=date,
        inbox_dir=REPO_ROOT / "inbox",
        proposal_root=REPO_ROOT / ".tmp" / "cron-proposals",
        use_llm=not fast,
    ))


def _live_inbox_rows_from_records(
    date: str,
    records: list[Any],
    *,
    fast: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept_paths = _load_kept_paths(date)
    rows: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for record in records:
        record_path = _relpath(pathlib.Path(record.path)) if pathlib.Path(record.path).is_absolute() else record.path
        row = {
            "path": record_path,
            "group": "实时待确认内容",
            "stale_archive": record.stale_archive,
            "age_days": record.age_days,
            "title_cn": record.title_cn,
            "preview_cn": record.preview_cn,
            "cn_summary": record.summary_cn,
            "raw_summary": record.summary,
            "summary": record.summary,
            "action": record.action,
            "reason": record.reason,
            "codex_recommended_action": record.codex_recommended_action,
            "codex_recommended_reason": record.codex_recommended_reason,
            "route_target": _record_value(record, "route_target"),
            "route_label": _record_value(record, "route_label"),
            "route_confidence": _record_value(record, "route_confidence"),
            "route_reason": _record_value(record, "route_reason"),
            "route_flags": _coerce_route_flags(_record_value(record, "route_flags", [])),
            "route_hard_rule": _record_value(record, "route_hard_rule"),
            "wiki_target": _wiki_target_suggestions({
                "path": record_path,
                "topic": record.topic,
                "title_cn": record.title_cn,
                "preview_cn": record.preview_cn,
                "cn_summary": record.summary_cn,
                "raw_summary": record.summary,
            }, include_similar=not fast),
        }
        if "auto-generated-investment-log" in row["route_flags"]:
            row["hidden_reason"] = "auto-generated-investment-log"
            hidden.append(row)
        elif "legacy_session_handoff" in row["route_flags"]:
            row["hidden_reason"] = "legacy_session_handoff"
            hidden.append(row)
        elif getattr(record, "knowledge_target", "") == "wiki_proposal" or getattr(record, "proposal_kind", "") == "wiki":
            row["hidden_reason"] = "wiki_proposal_ledger"
            hidden.append(row)
        elif record_path in kept_paths:
            hidden.append(row)
        else:
            rows.append(row)
    return rows, hidden


def _live_inbox_rows(date: str, *, fast: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = _audit_inbox_records(date, fast=fast)
    return _live_inbox_rows_from_records(date, records, fast=fast)


def _live_wiki_proposal_ledger(records: list[Any]) -> list[dict[str, Any]]:
    if not hasattr(tm_memory_reflection, "inbox_wiki_proposal_ledger"):
        return []
    ledger = tm_memory_reflection.inbox_wiki_proposal_ledger(records)
    return ledger if isinstance(ledger, list) else []


def _parse_proposals(text: str) -> list[dict[str, Any]]:
    sec = _section(text, "🧠 Proposed Changes")
    proposals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_diff = False
    capsule_labels = {
        "问题": "problem",
        "证据": "evidence",
        "约束": "constraints",
        "方案": "solution",
        "验收": "acceptance",
        "回滚": "rollback",
        "是否需要虎哥确认": "needs_tiger_confirmation",
        "缺失": "missing",
    }
    for line in sec.splitlines():
        stripped = line.strip()
        if stripped.startswith("#### proposal-"):
            if current:
                proposals.append(current)
            current = {"id": stripped.split()[1], "type": "other", "diff": []}
            in_diff = False
        elif current and stripped.startswith("```diff"):
            in_diff = True
        elif current and stripped == "```":
            in_diff = False
        elif current and stripped.startswith("**类型**："):
            current["type"] = stripped.split("：", 1)[1]
        elif current and stripped.startswith("**触发证据**："):
            current["trigger"] = stripped.split("：", 1)[1]
        elif current and stripped.startswith("**影响范围**："):
            current["impact"] = stripped.split("：", 1)[1]
        elif current and stripped.startswith("**Spec Capsule**："):
            current["spec_capsule"] = {
                "status": stripped.split("：", 1)[1],
                "items": {},
                "missing": [],
            }
        elif current and "spec_capsule" in current and stripped.startswith("- "):
            body = stripped[2:]
            for label, key in capsule_labels.items():
                prefix = f"{label}："
                if body.startswith(prefix):
                    value = body.split("：", 1)[1]
                    if key == "missing":
                        current["spec_capsule"]["missing"] = [item.strip() for item in value.split(",") if item.strip()]
                    else:
                        current["spec_capsule"]["items"][key] = value
                    break
        elif current and in_diff and (line.startswith("+") or line.startswith("-") or line.startswith("diff ")):
            current.setdefault("diff", []).append(line[:300])
    if current:
        proposals.append(current)
    return proposals


def parse_digest(date: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        raise ValueError("date must be YYYY-MM-DD")
    path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{date}.md"
    if not path.exists():
        raise FileNotFoundError(f"digest not found: {_relpath(path)}")
    text = path.read_text(encoding="utf-8")
    fm, _body = _frontmatter(text)
    fm_wiki = _parse_int(fm.get("wiki_count"))
    if fm.get("wiki_count") is None:
        fm_wiki = None
        fm_wiki_source = "missing"
    else:
        fm_wiki_source = "frontmatter"
    self_evolution_count = _parse_int(fm.get("self_evolution_count"))
    self_evolution = _build_self_evolution_payload(date, text, expected_count=self_evolution_count)
    report_rows = _parse_inbox_rows(text)
    inbox_records = _audit_inbox_records(date)
    visible_rows, hidden_rows = _live_inbox_rows_from_records(date, inbox_records)
    wiki_proposal_ledger = _live_wiki_proposal_ledger(inbox_records)
    live_inbox_count = len(visible_rows) + len(hidden_rows)
    stale_count = sum(1 for row in visible_rows if row.get("stale_archive"))
    promote_count = sum(1 for row in visible_rows if row.get("action") in {"promote_to_mem0", "promote_to_wiki"})
    frontmatter_wiki_proposal_count = _parse_int(fm.get("wiki_proposal_inbox_count"))
    wiki_proposal_count = max(
        frontmatter_wiki_proposal_count,
        sum(int(row.get("count") or 0) for row in wiki_proposal_ledger),
    )
    if fm_wiki is None:
        fm_wiki = wiki_proposal_count
        fm_wiki_source = "wiki_proposal_inbox"
    return {
        "date": date,
        "path": _relpath(path),
        "frontmatter": fm,
        "counts": {
            "mem0": _parse_int(fm.get("mem0_count")),
            "inbox": live_inbox_count,
            "inbox_pending": len(visible_rows) + len(hidden_rows),
            "inbox_today": 0,
            "wiki": fm_wiki,
            "wiki_count_source": fm_wiki_source,
            "discard": _parse_int(fm.get("discard_count")),
            "issue": 0,
            "proposal": _parse_int(fm.get("proposal_count")),
            "self_evolution": self_evolution_count,
            "stale_archive": stale_count,
            "promote": promote_count,
            "review_hidden": len(hidden_rows),
            "report_inbox": len(report_rows),
            "wiki_proposal_inbox": wiki_proposal_count,
            "wiki_proposal_groups": len(wiki_proposal_ledger),
        },
        "decision": _section(text, "⚡ 今日要决策").strip(),
        "summary": _section(text, "摘要").strip(),
        "discard_candidates": _section(text, "🔍 discard 误判候选").strip(),
        "inbox_rows": visible_rows,
        "hidden_inbox_rows": hidden_rows,
        "report_inbox_rows": report_rows,
        "wiki_proposal_ledger": wiki_proposal_ledger,
        "proposals": _parse_proposals(text),
        "self_evolution": self_evolution,
        "metrics": _section(text, "📈 自评指标").strip(),
        "appendix": _section(text, "📚 附录").strip(),
    }


def _parse_quality_digest_snapshot(date: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        raise ValueError("date must be YYYY-MM-DD")
    path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{date}.md"
    if not path.exists():
        raise FileNotFoundError(f"digest not found: {_relpath(path)}")
    updated, digest_hash = _file_signature(path)
    cache_key = f"quality-digest-snapshot:{date}:{digest_hash}"
    cached, _ = _run_cache_get(cache_key, API_CACHE_TTL * 10)
    if cached:
        return cached
    text = path.read_text(encoding="utf-8")
    fm, _body = _frontmatter(text)
    report_rows = _parse_inbox_rows(text)
    fm_wiki = _parse_int(fm.get("wiki_count"))
    if fm.get("wiki_count") is None:
        fm_wiki = _parse_int(fm.get("wiki_proposal_inbox_count"))
        fm_wiki_source = "wiki_proposal_inbox"
    else:
        fm_wiki_source = "frontmatter"
    snapshot = {
        "date": date,
        "path": _relpath(path),
        "source_updated_at": updated,
        "source_hash": digest_hash,
        "frontmatter": fm,
        "counts": {
            "mem0": _parse_int(fm.get("mem0_count")),
            "inbox": 0,
            "inbox_pending": 0,
            "inbox_today": 0,
            "wiki": fm_wiki,
            "wiki_count_source": fm_wiki_source,
            "discard": _parse_int(fm.get("discard_count")),
            "issue": 0,
            "proposal": _parse_int(fm.get("proposal_count")),
            "self_evolution": _parse_int(fm.get("self_evolution_count")),
            "stale_archive": _parse_int(fm.get("stale_archive_count")),
            "promote": _parse_int(fm.get("promote_candidate_count")),
            "review_hidden": 0,
            "report_inbox": len(report_rows),
            "review_entered": len({str(row.get("path") or "") for row in report_rows if row.get("path")}),
        },
        "inbox_rows": [],
        "hidden_inbox_rows": [],
        "report_inbox_rows": report_rows,
    }
    _run_cache_set(
        cache_key,
        snapshot,
        source="quality-digest-snapshot",
        source_path=str(path),
        source_hash=digest_hash,
        source_updated_at=updated,
        ttl_seconds=API_CACHE_TTL * 10,
    )
    return snapshot


def _live_digest_fallback(date: str, reason: str = "", *, fast: bool = False) -> dict[str, Any]:
    inbox_records = _audit_inbox_records(date, fast=fast)
    visible_rows, hidden_rows = _live_inbox_rows_from_records(date, inbox_records, fast=fast)
    wiki_proposal_ledger = _live_wiki_proposal_ledger(inbox_records)
    stale_count = sum(1 for row in visible_rows if row.get("stale_archive"))
    promote_count = sum(1 for row in visible_rows if row.get("action") in {"promote_to_mem0", "promote_to_wiki"})
    self_evolution = _build_self_evolution_payload(date)
    wiki_proposal_count = sum(int(row.get("count") or 0) for row in wiki_proposal_ledger)
    return {
        "date": date,
        "path": "",
        "frontmatter": {},
        "self_evolution": self_evolution,
        "counts": {
            "mem0": None,
            "inbox": len(visible_rows) + len(hidden_rows),
            "inbox_pending": len(visible_rows) + len(hidden_rows),
            "inbox_today": 0,
            "wiki": None,
            "issue": 0,
            "discard": 0,
            "proposal": 0,
            "self_evolution": self_evolution.get("event_count", 0),
            "stale_archive": stale_count,
            "promote": promote_count,
            "review_hidden": len(hidden_rows),
            "report_inbox": 0,
            "wiki_proposal_inbox": wiki_proposal_count,
            "wiki_proposal_groups": len(wiki_proposal_ledger),
        },
        "decision": "日报尚未生成，当前页面直接读取实时待确认内容。",
        "summary": reason or "日报尚未生成，当前页面直接读取实时待确认内容。",
        "discard_candidates": "- none",
        "inbox_rows": visible_rows,
        "hidden_inbox_rows": hidden_rows,
        "report_inbox_rows": [],
        "wiki_proposal_ledger": wiki_proposal_ledger,
        "proposals": [],
        "metrics": "日报尚未生成；待确认内容来自当前 inbox/ 实时扫描。",
        "appendix": "",
        "live_fallback": True,
    }


def daily_review_data(date: str) -> dict[str, Any]:
    start = time.time()
    start_date = date
    if not DATE_RE.fullmatch(start_date):
        raise ValueError("date must be YYYY-MM-DD")

    digest_path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{start_date}.md"
    digest = None
    default_source = _relpath(digest_path)
    if digest_path.exists():
        cache_source_updated_at, cache_source_hash = _file_signature(digest_path)
    else:
        cache_source_updated_at, cache_source_hash = None, ""
    cache_key = f"api:digest:{start_date}:{cache_source_hash or 'missing'}"
    cached, _ = _run_cache_get(cache_key, API_CACHE_TTL)
    if cached:
        worktree = _worktree_dirty_state()
        cached.setdefault("warnings", [])
        cached.setdefault("errors", [])
        cached["repo_dirty"] = worktree.get("dirty")
        if worktree.get("dirty"):
            warning = f"工作区存在未提交改动，可能影响日报快照准确性（{worktree['status_count']}项）"
            if warning not in cached["warnings"]:
                cached["warnings"].append(warning)
        if worktree.get("error") and worktree["error"] not in cached["errors"]:
            cached["errors"].append(worktree["error"])
        return cached
    worktree = _worktree_dirty_state()
    warnings: list[str] = []
    errors: list[str] = []
    source = default_source
    source_path = str(digest_path)
    source_updated_at = None
    source_hash = ""

    if worktree.get("error"):
        errors.append(worktree["error"])

    try:
        digest = parse_digest(start_date)
        source_updated_at, source_hash = cache_source_updated_at, cache_source_hash
    except FileNotFoundError as exc:
        digest = _live_digest_fallback(start_date, str(exc), fast=start_date == today())
        warnings.append(f"日报文件缺失：{exc}")
        warnings.append("已回退到实时 inbox + proposal 列表展示")
    except Exception as exc:
        digest = _live_digest_fallback(start_date, str(exc), fast=start_date == today())
        warnings.append(f"日报解析异常：{exc}，已回退到实时 inbox + proposal 列表展示")
        errors.append(str(exc))

    mem0_status = _mem0_payload("", size=1, timeout=1.0)
    if mem0_status.get("error"):
        warnings.append(f"Mem0 不可达：{mem0_status.get('error')}")

    if worktree.get("dirty"):
        warnings.append(f"工作区存在未提交改动，可能影响日报快照准确性（{worktree['status_count']}项）")

    digest["source"] = source
    digest["source_path"] = source_path
    digest["source_updated_at"] = source_updated_at
    digest["source_hash"] = source_hash
    digest["generated_at"] = _now_iso()
    digest["latency_ms"] = round((time.time() - start) * 1000, 1)
    digest["fallback"] = bool(digest.get("live_fallback", False))
    digest["cache"] = {
        "hit": False,
        "ttl_seconds": API_CACHE_TTL,
        "age_ms": 0,
        "cached_at": digest["generated_at"],
        "source": source,
        "source_path": source_path,
        "source_hash": source_hash,
    }
    digest["cached"] = False
    digest["stale"] = False
    digest["repo_dirty"] = worktree.get("dirty")
    digest["warnings"] = warnings + digest.get("warnings", [])
    digest["errors"] = errors + digest.get("errors", [])
    digest["source_details"] = {
        "path": source,
        "updated_at": source_updated_at,
        "hash": source_hash,
    }
    digest["mem0_status"] = {
        "ok": mem0_status.get("error") is None,
        "count": mem0_status.get("count"),
        "latency_ms": mem0_status.get("latency_ms"),
        "error": mem0_status.get("error"),
    }
    _run_cache_set(
        cache_key,
        digest,
        source=source,
        source_path=source_path,
        source_hash=source_hash,
        source_updated_at=source_updated_at or "",
        ttl_seconds=API_CACHE_TTL,
    )
    return digest


def cron_intake_data(date: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        raise ValueError("date must be YYYY-MM-DD")
    codex_home = pathlib.Path(os.getenv("CODEX_HOME", str(pathlib.Path.home() / ".codex")))
    return tm_memory_reflection.build_cron_intake(
        date=date,
        operations_dir=REPO_ROOT / "wiki" / "operations",
        codex_home=codex_home,
    )


def cached_cron_intake_data(date: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        raise ValueError("date must be YYYY-MM-DD")
    codex_home = pathlib.Path(os.getenv("CODEX_HOME", str(pathlib.Path.home() / ".codex")))
    cache_key = f"api:cron-intake:{date}:{REPO_ROOT}:{codex_home}"
    cached, _ = _run_cache_get(cache_key, CRON_INTAKE_CACHE_TTL)
    if cached:
        return cached

    payload = cron_intake_data(date)
    _run_cache_set(
        cache_key,
        payload,
        source="cron-intake",
        source_path=str(REPO_ROOT / "wiki" / "operations"),
        source_hash="",
        source_updated_at="",
        ttl_seconds=CRON_INTAKE_CACHE_TTL,
    )
    return payload


def available_digest_dates() -> list[str]:
    root = REPO_ROOT / "wiki" / "operations"
    dates: list[str] = []
    for path in root.glob("daily-memory-digest-*.md"):
        date = path.stem.removeprefix("daily-memory-digest-")
        if DATE_RE.fullmatch(date):
            dates.append(date)
    return sorted(set(dates), reverse=True)


def default_digest_date() -> str | None:
    dates = available_digest_dates()
    current = today()
    if current in dates:
        return current
    return dates[0] if dates else None


def _mem0_payload(query: str, *, size: int = 5, timeout: float | None = None) -> dict[str, Any]:
    effective_timeout = float(timeout if timeout is not None else tm_core.MEM0_READ_TIMEOUT)
    params = urllib.parse.urlencode({
        "user_id": tm_core.mem0_user_id(),
        "search_query": query,
        "page": 1,
        "size": size,
        "match_mode": "id_first",
    })
    url = f"{tm_core.mem0_base()}/api/v1/memories/?{params}"

    def fetch() -> str:
        if timeout is None:
            return tm_core.mem0_search(query, size=size)
        return tm_core.mem0_request(url, timeout=effective_timeout)

    payload = _mem0_dashboard_json(
        f"mem0-search:{hashlib.sha256((query + '|' + str(size)).encode('utf-8')).hexdigest()[:16]}",
        timeout=effective_timeout,
        fetcher=fetch,
    )
    payload["count"] = payload.get("count", payload.get("total"))
    payload.setdefault("items", payload.get("results", []))
    return payload


def _mem0_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items") or payload.get("results") or []
    return [item for item in items if isinstance(item, dict)]


def _get_mem0_approximate_count() -> int | None:
    payload = _mem0_payload("", size=1, timeout=2)
    count = payload.get("count", payload.get("total"))
    if isinstance(count, int):
        return count
    try:
        return int(count)
    except (TypeError, ValueError):
        return None


def _digest_frontmatter_counts(date: str) -> dict[str, int]:
    path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{date}.md"
    if not path.exists():
        raise FileNotFoundError(f"digest not found: {_relpath(path)}")
    fm, _body = _frontmatter(path.read_text(encoding="utf-8"))
    return {
        "mem0": _parse_int(fm.get("mem0_count")),
        "inbox": _parse_int(fm.get("inbox_count")),
        "discard": _parse_int(fm.get("discard_count")),
        "wiki": _parse_int(fm.get("wiki_count")),
        "issue": 0,
    }


def _get_7day_digest_trend() -> list[dict[str, Any]]:
    base = dt.datetime.strptime(today(), "%Y-%m-%d").date()
    rows: list[dict[str, Any]] = []
    for offset in range(6, -1, -1):
        date = (base - dt.timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            counts = _digest_frontmatter_counts(date)
            available = True
        except Exception:
            counts = {}
            available = False
        rows.append({
            "date": date,
            "mem0": counts.get("mem0"),
            "inbox": counts.get("inbox"),
            "discard": counts.get("discard"),
            "available": available,
        })
    return rows


def dashboard_memory_overview() -> dict[str, Any]:
    wiki_root = REPO_ROOT / "wiki"
    wiki_count = sum(1 for path in wiki_root.rglob("*.md") if path.is_file()) if wiki_root.is_dir() else 0
    return {
        "ok": True,
        "wiki_pages": wiki_count,
        "inbox_pending": _count_inbox_files(),
        "mem0_approximate": _get_mem0_approximate_count(),
        "trend_7d": _get_7day_digest_trend(),
    }


def _extract_frontmatter_value(text: str, key: str) -> str:
    marker = f"{key}:"
    for line in text.splitlines()[:20]:
        if line.strip().startswith(marker):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def _recent_handoff_cards(limit: int = 5) -> list[dict[str, Any]]:
    payload = _mem0_payload("memory_type: session-handoff", size=limit, timeout=2)
    cards: list[dict[str, Any]] = []
    for item in _mem0_items(payload)[:limit]:
        text = str(item.get("content") or item.get("text") or item.get("memory") or "")
        if "## Task" in text:
            task_lines = _section(f"\n{text}", "Task").strip().splitlines()
            title = task_lines[0][:120] if task_lines else text[:120]
        else:
            title = text[:120]
        cards.append({
            "type": "handoff",
            "agent": _extract_frontmatter_value(text, "agent") or item.get("metadata_", {}).get("source") or "agent",
            "source": item.get("metadata_", {}).get("source") or _extract_frontmatter_value(text, "source") or "mem0",
            "created_at": item.get("created_at") or item.get("created_at_local") or "",
            "title": title,
        })
    return cards


def _recent_agent_commits(limit: int = 20) -> list[dict[str, Any]]:
    proc = _run(["git", "log", f"-{limit}", "--date=iso-strict", "--pretty=%h%x09%ad%x09%s"], timeout=10)
    if proc.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        sha, when, subject = parts
        match = re.match(r"\[([^\]]+)\]", subject)
        agent = match.group(1) if match else "unknown"
        if agent in seen:
            continue
        seen.add(agent)
        rows.append({"type": "commit", "agent": agent, "created_at": when, "title": subject, "sha": sha})
    return rows[:8]


def _ce_plugin_last_write() -> dict[str, Any] | None:
    payload = _mem0_payload("tigermemory-ce", size=1, timeout=2)
    items = _mem0_items(payload)
    if not items:
        return None
    item = items[0]
    return {
        "type": "ce-plugin",
        "agent": "tigermemory-ce",
        "created_at": item.get("created_at") or item.get("created_at_local") or "",
        "title": str(item.get("content") or item.get("text") or item.get("memory") or "")[:120],
    }


def dashboard_agent_recent_activity() -> dict[str, Any]:
    items = _recent_agent_commits() + _recent_handoff_cards()
    items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    ce = _ce_plugin_last_write()
    if ce:
        items.insert(0, ce)
    return {"ok": True, "items": items[:12]}


def _fact_from_inbox_path(path: str) -> dict[str, Any]:
    resolved = _repo_path(path)
    if not _relpath(resolved).startswith("inbox/"):
        raise ValueError("only inbox paths are supported")
    return {
        "id": _relpath(resolved),
        "source_type": "inbox",
        "source_id": _relpath(resolved),
        "text": resolved.read_text(encoding="utf-8") if resolved.exists() else "",
        "topic": _topic_from_inbox_path(_relpath(resolved)),
    }


def _wiki_target_for_inbox_path(path: str) -> dict[str, Any]:
    fact = _fact_from_inbox_path(path)
    fm, body = _frontmatter(str(fact.get("text") or ""))
    proposed = _wiki_target_from_frontmatter(fm)
    if proposed:
        return proposed
    row = {
        "path": fact["source_id"],
        "title_cn": fm.get("title_cn") or fm.get("summary_cn") or "",
        "preview_cn": fm.get("preview_cn") or fm.get("summary_cn") or body[:300],
        "raw_summary": body[:300],
    }
    return _wiki_target_suggestions(row)


def _wiki_target_from_frontmatter(fm: dict[str, str]) -> dict[str, Any] | None:
    partition = str(fm.get("wiki_partition") or "").strip().strip("/")
    slug = str(fm.get("wiki_slug_hint") or "").strip().strip("/")
    slug = slug.removesuffix(".md")
    valid_partitions = {item[0] for item in PARTITION_OPTIONS}
    if not partition or partition not in valid_partitions or not slug:
        return None
    tm_core.validate_slug(slug)
    return {
        "partition": partition,
        "slug": slug,
        "path": f"wiki/{partition}/{slug}.md",
        "reason": "使用 inbox frontmatter 中的 wiki_partition/wiki_slug_hint 作为已审核目标页。",
        "alternatives": [],
        "similar": [],
    }


def commit_and_push_paths(paths: list[str], message: str) -> str:
    rels = _committable_paths(paths)
    if not rels:
        raise RuntimeError("no paths to commit")
    _run_checked(["git", "add", "--", *rels])
    _run_checked(["git", "commit", "-m", message, "--", *rels], timeout=600)
    sha = _run_checked(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    _run_checked(["git", "push", "origin", "master"], timeout=600)
    return sha


def _committable_paths(paths: list[str]) -> list[str]:
    rels: list[str] = []
    seen: set[str] = set()
    for path in paths:
        rel = _relpath(path)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        if (REPO_ROOT / rel).exists() or _git_tracks_path(rel):
            rels.append(rel)
    return sorted(rels)


def _git_tracks_path(rel: str) -> bool:
    if not (REPO_ROOT / ".git").exists():
        return False
    return _run(["git", "ls-files", "--error-unmatch", "--", rel], timeout=10).returncode == 0


def execute_archive(path: str, *, batch: bool = False, actual_action: str = "archive") -> dict[str, Any]:
    if not batch:
        ensure_write_ready([path])
    fact = _fact_from_inbox_path(path)
    fact["review_action"] = actual_action
    tm_review_tools.tm_core.REPO_ROOT = REPO_ROOT
    result = tm_review_tools.execute_delete(fact)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "archive failed", "result": result}
    archived = _relpath(result.get("archived_to", ""))
    if not batch:
        sha = commit_and_push_paths(
            [fact["source_id"], archived],
            f"[codex] archive: digest inbox {pathlib.PurePosixPath(fact['source_id']).name}",
        )
        result["commit_sha"] = sha
    return {
        "ok": True,
        "result": result,
        "archived_to": archived,
        "source_cache_to": result.get("source_cache_to"),
        "commit_sha": result.get("commit_sha"),
    }


def _inbox_date_from_path(path: str) -> str:
    rel = _relpath(_repo_path(path))
    match = re.match(r"^inbox/(\d{4}-\d{2}-\d{2})-\d{4}-", rel)
    return match.group(1) if match else today()


def _audit_record_for_inbox_path(path: str) -> Any:
    rel = _relpath(_repo_path(path))
    for record in _audit_inbox_records(_inbox_date_from_path(rel), fast=True):
        record_path = _relpath(pathlib.Path(record.path)) if pathlib.Path(record.path).is_absolute() else str(record.path)
        if record_path.replace("\\", "/") == rel:
            return record
    raise ValueError(f"inbox audit record not found: {rel}")


def _one_line(value: Any, fallback: str = "-") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _clip_markdown(value: str, limit: int = 5000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n...（已截断；完整原文仍在原始路径或 inbox 归档摘要中）"


def _investment_archive_page_path(write_date: str) -> pathlib.Path:
    if not DATE_RE.fullmatch(write_date):
        raise ValueError("date must be YYYY-MM-DD")
    return REPO_ROOT / "wiki" / "investment" / "proposal-archive" / f"{write_date}.md"


def _investment_archive_base_body(write_date: str) -> str:
    return (
        f"# Investment Proposal Archive {write_date}\n\n"
        "## 摘要\n\n"
        "本页汇总从每日审核台直接归档的 investment 类 Wiki proposal。"
        "这些条目已经进入 TigerMemory 可检索归档，但不等同于正式投资结论或交易指令。\n\n"
        "## 归档记录\n\n"
        "## 来源\n\n"
        "- Review UI investment archive action.\n"
    )


def _render_investment_archive_entry(fact: dict[str, Any], record: Any, triage: dict[str, Any], archived_at: str) -> str:
    source_id = str(fact.get("source_id") or fact.get("id") or "")
    text = str(fact.get("text") or "")
    _fm, body = _frontmatter(text)
    title = _one_line(getattr(record, "title_cn", "") or pathlib.PurePosixPath(source_id).name)
    preview = _one_line(getattr(record, "preview_cn", "") or getattr(record, "summary_cn", ""))
    raw_excerpt = _clip_markdown(body or text)
    original_paths = triage.get("original_paths") if isinstance(triage.get("original_paths"), list) else []
    risk_flags = []
    if triage.get("contains_trade_action"):
        risk_flags.append("含买卖动作")
    if triage.get("contains_account_data"):
        risk_flags.append("含账户/交易数据")
    if triage.get("contains_private_signal"):
        risk_flags.append("含私域线索")
    return (
        f"<!-- investment-proposal-archive-entry: {source_id} -->\n"
        f"### {title}\n\n"
        f"- 来源 inbox：`{source_id}`\n"
        f"- 归档时间：{archived_at}\n"
        f"- 投资分类：{_one_line(triage.get('investment_doc_type_label') or triage.get('investment_doc_type'))}\n"
        f"- 处理层级：{_one_line(triage.get('investment_review_label') or triage.get('investment_review_level'))}\n"
        f"- 证据强度：{_one_line(triage.get('evidence_level'))}\n"
        f"- 建议 Wiki：{_one_line(triage.get('investment_target_path'))}\n"
        f"- 建议证据归档：{_one_line(triage.get('investment_storage_path'))}\n"
        f"- 标的/月度：{_one_line(triage.get('symbol'))} / {_one_line(triage.get('decision_month'))}\n"
        f"- 归档边界：保留原件；只复制/追加；不移动、不改名、不删除投研运行文件\n"
        f"- 风险标签：{_one_line('、'.join(risk_flags))}\n"
        f"- 原始路径：{_one_line('；'.join(str(item) for item in original_paths if item))}\n\n"
        f"#### 摘要\n\n{preview}\n\n"
        "<details>\n<summary>原始 inbox 摘要片段</summary>\n\n"
        f"{raw_excerpt}\n\n"
        "</details>\n"
        f"<!-- /investment-proposal-archive-entry -->\n"
    )


def _upsert_investment_archive_entry(page_path: pathlib.Path, source_id: str, entry: str) -> str:
    if page_path.exists():
        text = page_path.read_text(encoding="utf-8")
        _old_fm, body = _frontmatter(text)
        body = body.lstrip()
        if "## 归档记录" not in body:
            body = body.rstrip() + "\n\n## 归档记录\n\n"
    else:
        body = _investment_archive_base_body(page_path.stem)
    pattern = re.compile(
        rf"<!-- investment-proposal-archive-entry: {re.escape(source_id)} -->.*?<!-- /investment-proposal-archive-entry -->\n?",
        re.DOTALL,
    )
    body = pattern.sub("", body).rstrip()
    source_heading = "\n## 来源"
    if source_heading in body:
        before, after = body.split(source_heading, 1)
        body = before.rstrip() + "\n\n" + entry.rstrip() + source_heading + after
    else:
        body = body.rstrip() + "\n\n" + entry.rstrip() + "\n"
    fm = (
        "owner: codex\n"
        "status: active\n"
        f"title: \"Investment Proposal Archive {page_path.stem}\"\n"
        f"aliases: [\"{page_path.stem} 投资提案归档\", \"investment proposal archive {page_path.stem}\"]\n"
    )
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(tm_core.render_wiki_body(fm, body.rstrip() + "\n", today()), encoding="utf-8", newline="\n")
    return _relpath(page_path)


def execute_investment_archive(path: str, *, batch: bool = False) -> dict[str, Any]:
    if not batch:
        ensure_write_ready([path])
    fact = _fact_from_inbox_path(path)
    record = _audit_record_for_inbox_path(path)
    if getattr(record, "topic", "") != "investment" and getattr(record, "wiki_partition", "") != "investment":
        return {"ok": False, "error": "investment_archive only supports investment wiki proposals"}
    if not hasattr(tm_memory_reflection, "investment_triage_for_wiki_proposal_row"):
        return {"ok": False, "error": "investment triage helper unavailable"}
    triage = tm_memory_reflection.investment_triage_for_wiki_proposal_row(record)
    archived_at = dt.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds")
    entry = _render_investment_archive_entry(fact, record, triage, archived_at)
    page_rel = _upsert_investment_archive_entry(
        _investment_archive_page_path(_inbox_date_from_path(path)),
        fact["source_id"],
        entry,
    )
    fact["review_action"] = "investment_archive"
    archive = tm_review_tools.execute_delete(fact)
    result = {
        "ok": bool(archive.get("ok")),
        "path": path,
        "archive": archive,
        "investment_archived_to": page_rel,
        "investment_triage": triage,
        "changed_paths": [page_rel],
    }
    if not archive.get("ok"):
        result["error"] = archive.get("error") or "archive failed after investment archive"
    if not batch and result["ok"]:
        result["commit_sha"] = commit_and_push_paths(
            [page_rel, fact["source_id"], _relpath(archive.get("archived_to", ""))],
            f"[codex] archive: investment proposal {pathlib.PurePosixPath(fact['source_id']).name}",
        )
    return result


def execute_batch_archive(date: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        return {"ok": False, "error": "date must be YYYY-MM-DD"}
    ensure_write_ready()
    rows = [
        row
        for row in tm_memory_reflection.audit_inbox(
            date=date,
            inbox_dir=REPO_ROOT / "inbox",
            proposal_root=REPO_ROOT / ".tmp" / "cron-proposals",
        )
        if row.stale_archive
    ]
    changed: list[str] = []
    results: list[dict[str, Any]] = []
    for row in rows:
        fact = _fact_from_inbox_path(row.path)
        fact["review_action"] = "archive"
        tm_review_tools.tm_core.REPO_ROOT = REPO_ROOT
        result = tm_review_tools.execute_delete(fact)
        results.append(result)
        if result.get("ok"):
            changed.extend([fact["source_id"], _relpath(result.get("archived_to", ""))])
    if not changed:
        return {"ok": True, "archived": [], "commit_sha": None}
    sha = commit_and_push_paths(changed, f"[codex] archive: digest stale inbox {date}")
    return {"ok": True, "archived": [row.path for row in rows], "commit_sha": sha, "results": results}


def _validate_batch_paths(paths: list[str]) -> list[str]:
    unique = []
    seen: set[str] = set()
    for raw in paths:
        rel = _relpath(_repo_path(raw))
        if not rel.startswith("inbox/"):
            raise ValueError("batch action only supports inbox paths")
        if rel not in seen:
            unique.append(rel)
            seen.add(rel)
    if not unique:
        raise ValueError("select at least one inbox item")
    if len(unique) > 50:
        raise ValueError("batch action is limited to 50 inbox items")
    return unique


def _batch_wiki_slug(prefix: str, path: str, index: int, total: int) -> str:
    clean_prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.lower()).strip("-")
    if not clean_prefix:
        raise ValueError("slug_prefix must contain lowercase letters, digits, or hyphens")
    stem = pathlib.PurePosixPath(path).stem.lower()
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{4}-", "", stem)
    suffix = re.sub(r"[^a-z0-9-]+", "-", stem).strip("-") or f"item-{index + 1}"
    if total == 1:
        slug = clean_prefix
    else:
        slug = f"{clean_prefix}-{index + 1}-{suffix}"
    slug = slug[:80].strip("-")
    tm_core.validate_slug(slug)
    return slug


def execute_batch_inbox_action(req: BatchInboxActionRequest) -> dict[str, Any]:
    paths = _validate_batch_paths(req.paths)
    if req.action not in {"archive", "promote_mem0", "promote_wiki", "investment_archive"}:
        return {"ok": False, "error": f"unsupported batch action: {req.action}"}

    ensure_write_ready(paths)
    tm_review_tools.tm_core.REPO_ROOT = REPO_ROOT
    changed: list[str] = []
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, path in enumerate(paths):
        try:
            fact = _fact_from_inbox_path(path)
            if req.action == "archive":
                fact["review_action"] = "archive"
                result = tm_review_tools.execute_delete(fact)
            elif req.action == "investment_archive":
                result = execute_investment_archive(path, batch=True)
            elif req.action == "promote_mem0":
                promote = tm_review_tools.execute_promote_mem0(fact, req.partition)
                if not promote.get("ok"):
                    result = promote
                else:
                    fact["review_action"] = "promote_mem0"
                    archive = tm_review_tools.execute_delete(fact)
                    result = {**promote, "archive": archive}
                    if not archive.get("ok"):
                        result["ok"] = False
                        result["error"] = archive.get("error") or "archive failed after promote_mem0"
            else:
                if req.partition and req.slug_prefix:
                    partition = req.partition
                    slug = _batch_wiki_slug(req.slug_prefix or "", path, index, len(paths))
                else:
                    target = _wiki_target_for_inbox_path(path)
                    partition = str(target["partition"])
                    slug = str(target["slug"])
                promote = tm_review_tools.execute_promote(fact, partition, slug, commit=False)
                if not promote.get("ok"):
                    result = promote
                else:
                    fact["review_action"] = "promote_wiki"
                    archive = tm_review_tools.execute_delete(fact)
                    result = {**promote, "archive": archive, "partition": partition, "slug": slug}
                    if not archive.get("ok"):
                        result["ok"] = False
                        result["error"] = archive.get("error") or "archive failed after promote_wiki"
            result["path"] = path
            results.append(result)
            archive_result = result.get("archive") if isinstance(result.get("archive"), dict) else result
            if archive_result.get("ok"):
                changed.extend(result.get("changed_paths") or [])
                changed.extend([fact["source_id"], _relpath(archive_result.get("archived_to", ""))])
            if not result.get("ok"):
                failures.append({"path": path, "error": result.get("error") or "operation failed"})
        except Exception as exc:
            failures.append({"path": path, "error": str(exc)})

    commit_sha = None
    if changed:
        commit_sha = commit_and_push_paths(
            changed,
            f"[codex] archive: review UI batch {req.action} {len(changed) // 2} inbox",
        )
    return {
        "ok": not failures,
        "action": req.action,
        "count": len(paths),
        "success_count": len(paths) - len(failures),
        "failure_count": len(failures),
        "failures": failures,
        "results": results,
        "commit_sha": commit_sha,
        "error": "; ".join(f"{item['path']}: {item['error']}" for item in failures) if failures else None,
    }


def execute_inbox_action(req: InboxActionRequest) -> dict[str, Any]:
    if req.action == "archive":
        return execute_archive(req.path)
    if req.action == "keep":
        return _mark_kept(req.date or today(), req.path)
    if req.action == "promote_wiki":
        if req.partition and req.slug:
            partition = req.partition
            slug = req.slug
        else:
            target = _wiki_target_for_inbox_path(req.path)
            partition = str(target["partition"])
            slug = str(target["slug"])
        ensure_write_ready([req.path])
        tm_review_tools.tm_core.REPO_ROOT = REPO_ROOT
        fact = _fact_from_inbox_path(req.path)
        result = tm_review_tools.execute_promote(fact, partition, slug, commit=False)
        if not result.get("ok"):
            return {"ok": False, "result": result, "error": result.get("error") or "promote_wiki failed"}
        archived = execute_archive(fact["source_id"], batch=True, actual_action="promote_wiki")
        if not archived.get("ok"):
            return {"ok": False, "result": result, "error": archived.get("error") or "promoted but archive failed"}
        result["archived_to"] = archived.get("archived_to")
        commit_sha = commit_and_push_paths(
            [*(result.get("changed_paths") or []), fact["source_id"], str(archived.get("archived_to") or "")],
            f"[codex] archive: promoted inbox {pathlib.PurePosixPath(fact['source_id']).name}",
        )
        result["archive_commit_sha"] = commit_sha
        result["commit_sha"] = commit_sha
        return {"ok": True, "result": result, "commit_sha": commit_sha}
    if req.action == "promote_mem0":
        ensure_write_ready([req.path])
        tm_review_tools.tm_core.REPO_ROOT = REPO_ROOT
        fact = _fact_from_inbox_path(req.path)
        result = tm_review_tools.execute_promote_mem0(fact, req.partition)
        if not result.get("ok"):
            return {"ok": False, "result": result, "error": result.get("error") or "promote_mem0 failed"}
        archived = execute_archive(fact["source_id"], batch=True, actual_action="promote_mem0")
        if not archived.get("ok"):
            return {"ok": False, "result": result, "error": archived.get("error") or "promoted but archive failed"}
        result["archived_to"] = archived.get("archived_to")
        result["archive_commit_sha"] = commit_and_push_paths(
            [fact["source_id"], str(archived.get("archived_to") or "")],
            f"[codex] archive: promoted inbox {pathlib.PurePosixPath(fact['source_id']).name}",
        )
        return {"ok": True, "result": result, "commit_sha": result.get("archive_commit_sha")}
    return {"ok": False, "error": f"unsupported action: {req.action}"}


def _write_action_context(func, args: tuple[Any, ...]) -> tuple[str, int]:
    if args:
        req = args[0]
        action = getattr(req, "action", None) or getattr(func, "__name__", "unknown")
        paths = getattr(req, "paths", None)
        if isinstance(paths, list):
            return str(action), len(paths)
        if getattr(req, "path", None):
            return str(action), 1
        if getattr(req, "date", None):
            return str(action), 1
        return str(action), 0
    return getattr(func, "__name__", "unknown"), 0


def _locked_write_action(func, *args):
    action, count = _write_action_context(func, args)
    start = time.monotonic()
    try:
        with WRITE_ACTION_LOCK:
            result = func(*args)
    except Exception as exc:
        _run_cache_clear()
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        try:
            tm_runtime_events.record_event(
                event_type="dashboard_write_action",
                service="tm-dashboard",
                component="write_action",
                ok=False,
                severity="error",
                duration_ms=elapsed_ms,
                actor="human",
                outcome=action,
                error=str(exc),
                extra={"action": action, "count": count},
                source_log="systemd:tm-dashboard.service",
            )
        except Exception:
            pass
        print(
            f"write action error action={action} count={count} elapsed_ms={elapsed_ms} error={exc}",
            file=sys.stderr,
            flush=True,
        )
        raise
    _run_cache_clear()
    elapsed_ms = round((time.monotonic() - start) * 1000, 1)
    ok = result.get("ok") if isinstance(result, dict) else None
    try:
        commit_sha = result.get("commit_sha") if isinstance(result, dict) else None
        tm_runtime_events.record_event(
            event_type="dashboard_write_action",
            service="tm-dashboard",
            component="write_action",
            ok=bool(ok),
            severity="info" if ok else "error",
            duration_ms=elapsed_ms,
            actor="human",
            outcome=action,
            target_ref={"commit_sha": commit_sha} if isinstance(commit_sha, str) and commit_sha else {},
            error=str(result.get("error") or "") if isinstance(result, dict) and result.get("error") else None,
            extra={"action": action, "count": count},
            source_log="systemd:tm-dashboard.service",
        )
    except Exception:
        pass
    print(
        f"write action done action={action} count={count} ok={ok} elapsed_ms={elapsed_ms}",
        file=sys.stderr,
        flush=True,
    )
    return result


def proposal_apply(date: str, proposal_id: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        return {"ok": False, "error": "date must be YYYY-MM-DD"}
    proposal = tm_cron_apply.Proposal(proposal_id=proposal_id, proposal_type="other", apply_checked=True)
    proposals = tm_cron_apply.load_report_proposals(date)
    if proposal_id in proposals:
        proposal.proposal_type = proposals[proposal_id].proposal_type
    return {"ok": True, "applied": tm_cron_apply.apply_one(date, proposal)}


def proposal_reject(date: str, proposal_id: str, reason: str) -> dict[str, Any]:
    if not DATE_RE.fullmatch(date):
        return {"ok": False, "error": "date must be YYYY-MM-DD"}
    return {"ok": True, "rejected": tm_cron_apply.reject_one(date, proposal_id, reason)}


def _get_opposite_sha(is_wsl: bool) -> str | None:
    import subprocess
    import shutil
    if not (REPO_ROOT / ".git").exists():
        return None
    if is_wsl:
        # Current is WSL; optionally probe a Windows checkout when configured.
        windows_root = os.environ.get("TIGERMEMORY_WINDOWS_ROOT")
        if windows_root and shutil.which("git.exe"):
            try:
                res = subprocess.run(
                    ["git.exe", "-C", windows_root, "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=0.5
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        # Public builds must not guess a maintainer-specific Windows mount.
        # Set TIGERMEMORY_WINDOWS_ROOT when this host has a second checkout to probe.
        return None
    else:
        # Current is Windows, target is to probe WSL (~/tigermemory)
        if shutil.which("wsl"):
            try:
                res = subprocess.run(
                    ["wsl", "git", "-C", "~/tigermemory", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=0.5
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
    return None


def _dashboard_fast_agent_doctor() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for label, fn in [
        ("worktree", _dashboard_worktree_check),
        ("tm_http", lambda: tm_agent_doctor.check_tm_http(timeout=0.3)),
        ("mem0_api", lambda: tm_agent_doctor.check_mem0(timeout=0.5)),
        ("public_ask_llm", tm_agent_doctor.check_public_ask_llm),
    ]:
        try:
            checks.append(fn())
        except Exception as exc:
            checks.append({
                "name": label,
                "status": "warn",
                "ok": False,
                "error": str(exc)[:200],
            })
    hard_fail = [check for check in checks if check.get("status") == "fail"]
    warnings = [check for check in checks if check.get("status") == "warn"]
    return {
        "schema_version": "tm-agent-doctor-dashboard-fast-v1",
        "generated_at": _now_iso(),
        "ok": not hard_fail,
        "status": "fail" if hard_fail else ("warn" if warnings else "ok"),
        "mode": "dashboard_fast",
        "checks": checks,
        "summary": {
            "fail_count": len(hard_fail),
            "warn_count": len(warnings),
            "ok_count": sum(1 for check in checks if check.get("status") == "ok"),
        },
        "recommended_action": "健康页使用轻量探针；完整检查请打开 AI 连接页的一键体检。",
    }


def _is_wsl_runtime() -> bool:
    import platform

    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except Exception:
        return True


def _dashboard_worktree_check() -> dict[str, Any]:
    start = time.time()
    dirty = _worktree_dirty_state()
    repo_root = str(REPO_ROOT)
    runtime_profile = tm_core.tigermemory_profile()
    runtime_side = "WSL" if _is_wsl_runtime() else "Windows"

    def git_text(args: list[str], *, timeout: float = 0.5) -> str:
        proc = _run(args, timeout=timeout)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    branch = git_text(["git", "branch", "--show-current"])
    head = git_text(["git", "rev-parse", "--short", "HEAD"])
    upstream = git_text(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]) or "origin/master"
    ahead, behind = 0, 0
    if upstream:
        counts = git_text(["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
        parts = counts.split()
        if len(parts) == 2:
            try:
                ahead, behind = int(parts[0]), int(parts[1])
            except ValueError:
                ahead, behind = 0, 0
    status = "warn" if dirty.get("dirty") or ahead or behind else "ok"
    if dirty.get("error"):
        status = "warn"
    return {
        "name": "worktree",
        "status": status,
        "ok": not dirty.get("error"),
        "head": head,
        "branch": branch,
        "upstream": upstream,
        "repo_root": repo_root,
        "runtime_profile": runtime_profile,
        "runtime_side": runtime_side,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": dirty.get("status_count", 0),
        "blockers": [],
        "paths": dirty.get("sample", []),
        "latency_ms": round((time.time() - start) * 1000, 1),
        "error": dirty.get("error"),
    }


def dashboard_health_summary() -> dict[str, Any]:
    cache_key = "api:health:summary"
    cached, _ = _run_cache_get(cache_key, API_CACHE_TTL)
    if cached:
        cached = copy.deepcopy(cached)
        cached.setdefault("warnings", [])
        cached.setdefault("errors", [])
        cached["cached"] = True
        if cached.get("cache"):
            cached["cache"]["hit"] = True
            cached["cache"]["cached_at"] = cached.get("generated_at", cached["cache"].get("cached_at"))
        worktree = _worktree_dirty_state()
        if worktree.get("dirty") is not None:
            cached["repo_dirty"] = worktree["dirty"]
        if worktree.get("dirty"):
            warning = f"工作区存在未提交改动，可能影响告警准确性（{worktree['status_count']}项）"
            if warning not in cached["warnings"]:
                cached["warnings"].append(warning)
        if worktree.get("error"):
            cached.setdefault("errors", []).append(worktree["error"])
        return cached

    start = time.time()
    is_wsl = _is_wsl_runtime()

    source_path = pathlib.Path(__file__).resolve()
    source_path_str = str(source_path)
    source_updated_at, source_hash = _file_signature(source_path)
    report = _dashboard_fast_agent_doctor()
    commits = [
        line.strip()
        for line in _run(["git", "log", "--oneline", "-5"], timeout=10).stdout.splitlines()
        if line.strip()
    ]
    origin = _run(["git", "rev-parse", "--short", "origin/master"], timeout=10)
    digest_path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{today()}.md"
    checks_by_name = {
        str(check.get("name")): check
        for check in report.get("checks", [])
        if isinstance(check, dict)
    }
    tm_http_check = checks_by_name.get("tm_http", {})
    mem0_check = checks_by_name.get("mem0_api", {})
    tm_mcp_probe = _probe_url("http://127.0.0.1:9766/healthz", timeout=0.3)
    runtime_profile = tm_core.tigermemory_profile()
    local_profile = runtime_profile == tm_core.TIGERMEMORY_PROFILE_LOCAL
    tm_http_status = str(tm_http_check.get("status") or "warn")
    mem0_status = str(mem0_check.get("status") or "warn")
    tm_mcp_status = "ok" if tm_mcp_probe["ok"] else "warn"
    if local_profile:
        if tm_http_status != "ok":
            tm_http_status = "optional"
        if mem0_status != "ok":
            mem0_status = "optional"
        if tm_mcp_status != "ok":
            tm_mcp_status = "optional"
    services = [
        {
            "name": "Dashboard",
            "icon": "layout-dashboard",
            "port": f":{PORT}",
            "status": "ok",
            "status_label": "正常",
            "source": "local-dashboard-runtime",
            "source_path": str(source_path),
            "source_updated_at": source_updated_at,
            "source_hash": source_hash,
            "latency_ms": None,
            "detail": f"v{VERSION}",
        },
        {
            "name": "tm-http",
            "icon": "server",
            "port": ":8790",
            "status": tm_http_status,
            "status_label": _status_label(tm_http_status),
            "source": "tm_http:/health",
            "source_path": "http://127.0.0.1:8790/health",
            "latency_ms": tm_http_check.get("latency_ms"),
            "detail": "local 模式可选" if local_profile and tm_http_status == "optional" else str(tm_http_check.get("url") or "http://127.0.0.1:8790/health"),
        },
        {
            "name": "tm-mcp",
            "icon": "network",
            "port": ":9766",
            "status": tm_mcp_status,
            "status_label": _status_label(tm_mcp_status),
            "source": "tm_mcp:/healthz",
            "source_path": "http://127.0.0.1:9766/healthz",
            "latency_ms": tm_mcp_probe.get("latency_ms"),
            "detail": "local 模式可选" if local_profile and tm_mcp_status == "optional" else (tm_mcp_probe.get("error") or "healthz"),
        },
        {
            "name": "Mem0",
            "icon": "database",
            "port": ":8765",
            "status": mem0_status,
            "status_label": _status_label(mem0_status),
            "source": "tm_mem0",
            "source_path": "http://127.0.0.1:8765",
            "latency_ms": mem0_check.get("latency_ms"),
            "detail": "local 模式使用 SQLite" if local_profile and mem0_status == "optional" else "OpenMemory API",
        },
        {
            "name": "OpenClaw",
            "icon": "message-square",
            "port": "socket",
            "status": "optional" if local_profile else "warn",
            "status_label": "可选" if local_profile else "待接入",
            "latency_ms": None,
            "detail": "local 模式可选" if local_profile else "dashboard 探针待接入",
        },
    ]

    warnings: list[str] = []
    for check in services:
        if check.get("status") == "fail":
            warnings.append(f"{check.get('name')} 状态故障")
        elif check.get("status") == "warn":
            warnings.append(f"{check.get('name')} 处于告警状态")
    worktree = _worktree_dirty_state()
    if worktree.get("dirty"):
        warnings.append(f"工作区存在未提交改动，可能影响告警准确性（{worktree['status_count']}项）")

    result = {
        "ok": report.get("status") != "fail",
        "generated_at": _now_iso(),
        "dashboard": {
            "version": VERSION,
            "host": HOST,
            "port": PORT,
            "git_sha": git_sha(),
            "origin_master": origin.stdout.strip() if origin.returncode == 0 else "unknown",
            "is_wsl": is_wsl,
            "opposite_sha": _get_opposite_sha(is_wsl),
            "runtime_profile": runtime_profile,
        },
        "services": services,
        "agent_doctor": report,
        "source_update": dashboard_update_status(),
        "recent_commits": commits,
        "daily_digest": {
            "date": today(),
            "path": _relpath(digest_path),
            "exists": digest_path.exists(),
        },
        "source": "dashboard-runtime",
        "source_path": source_path_str,
        "source_updated_at": source_updated_at,
        "source_hash": source_hash,
        "latency_ms": round((time.time() - start) * 1000, 1),
        "repo_dirty": worktree.get("dirty"),
        "cache": {
            "hit": False,
            "ttl_seconds": API_CACHE_TTL,
            "age_ms": 0,
            "cached_at": _now_iso(),
            "source": "dashboard-runtime",
            "source_path": source_path_str,
            "source_hash": source_hash,
        },
        "cached": False,
        "stale": False,
        "fallback": False,
        "warnings": warnings,
        "errors": [worktree["error"]] if worktree.get("error") else [],
    }
    _run_cache_set(
        cache_key,
        result,
        source="dashboard-runtime",
        source_path=source_path_str,
        source_hash=source_hash,
        source_updated_at=source_updated_at,
        ttl_seconds=API_CACHE_TTL,
    )
    return result


def dashboard_update_status() -> dict[str, Any]:
    if tm_update is None or not hasattr(tm_update, "get_update_status"):
        return {
            "ok": False,
            "source_mode": "unknown",
            "reason": "tigermemory_update_unavailable",
            "recommended_action": "当前安装缺少 tigermemory-update 包。",
        }
    return tm_update.get_update_status(resolve_app_root(), refresh_remote=False)


def _status_label(status: str) -> str:
    return {"ok": "正常", "warn": "警告", "fail": "故障", "optional": "可选"}.get(status, status or "未知")


def _probe_url(url: str, *, timeout: int = 2) -> dict[str, Any]:
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            ok = 200 <= resp.status < 300
            try:
                body = json.loads(resp.read().decode("utf-8"))
            except Exception:
                body = {}
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error": str(exc)[:160],
        }
    return {
        "ok": ok,
        "url": url,
        "latency_ms": round((time.time() - start) * 1000, 1),
        "body": body,
    }


QUALITY_RANGE_ALIASES = {
    "": "today",
    "today": "today",
    "day": "today",
    "1d": "today",
    "7d": "7d",
    "7": "7d",
    "week": "7d",
    "30d": "30d",
    "30": "30d",
    "month": "30d",
    "1m": "30d",
}
QUALITY_RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30}
QUALITY_RANGE_LABELS = {"today": "今日", "7d": "近 7 天", "30d": "近 1 个月"}
QUALITY_RANGE_TRACE_LABELS = {"today": "近 24 小时", "7d": "近 7 天", "30d": "近 30 天"}


def _quality_range_key(value: str | None) -> str:
    return QUALITY_RANGE_ALIASES.get(str(value or "today").strip().lower(), "today")


def _quality_range_dates(report_date: str, range_key: str) -> list[str]:
    key = _quality_range_key(range_key)
    end = dt.date.fromisoformat(report_date)
    days = QUALITY_RANGE_DAYS[key]
    start = end - dt.timedelta(days=days - 1)
    return [(start + dt.timedelta(days=offset)).isoformat() for offset in range(days)]


def _quality_range_meta(report_date: str, range_key: str) -> dict[str, Any]:
    key = _quality_range_key(range_key)
    dates = _quality_range_dates(report_date, key)
    return {
        "key": key,
        "label": QUALITY_RANGE_LABELS[key],
        "trace_label": QUALITY_RANGE_TRACE_LABELS[key],
        "days": QUALITY_RANGE_DAYS[key],
        "start_date": dates[0],
        "end_date": dates[-1],
        "dates": dates,
    }


def _quality_route_event_summary(dates: list[str]) -> dict[str, Any]:
    event_root = REPO_ROOT / ".tmp" / "memory-route-events"
    try:
        events = tm_route_events.load_route_events(dates=dates, event_root=event_root)
        summary = tm_route_events.summarize_route_events(events, dates=dates, event_root=event_root)
        summary["ok"] = True
        return summary
    except Exception as exc:
        return {
            "ok": False,
            "event_count": 0,
            "flow_counts": {"mem0": 0, "wiki": 0, "inbox": 0, "discard": 0},
            "outcome_counts": {},
            "agent_counts": {},
            "dates_with_events": [],
            "requested_dates": list(dates),
            "missing_event_dates": list(dates),
            "source": _relpath(event_root),
            "error": _safe_str(exc),
        }


def _attach_quality_route_history(
    counts: dict[str, Any],
    dates: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    summary = _quality_route_event_summary(dates)
    flow_counts = summary.get("flow_counts") if isinstance(summary.get("flow_counts"), dict) else {}
    counts["route_event_counts"] = _quality_cached_recommendation_counts(flow_counts)
    counts["route_event_total"] = int(summary.get("event_count") or 0)
    counts["route_event_source"] = summary.get("source")
    counts["route_event_dates"] = summary.get("dates_with_events", [])
    counts["route_event_missing_dates"] = summary.get("missing_event_dates", [])
    if not summary.get("ok"):
        counts["route_event_error"] = summary.get("error") or "route event ledger unavailable"
        if warnings is not None:
            warnings.append(f"路由流水读取失败：{counts['route_event_error']}")
    return summary


def _load_quality_trace_summary(
    *,
    since_hours: float,
    dates: list[str] | None = None,
) -> dict[str, Any]:
    summary, _rows = _load_quality_trace_summary_with_rows(since_hours=since_hours, dates=dates)
    return summary


def _load_quality_trace_summary_with_rows(
    *,
    since_hours: float,
    dates: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows, invalid = tm_answer_trace.load_trace_rows(since_hours=since_hours)
    feedback_rows, feedback_invalid = tm_answer_trace.load_feedback_events(since_hours=since_hours, dates=dates)
    feedback_summary = tm_answer_trace.summarize_feedback_events(feedback_rows, feedback_invalid)
    kwargs: dict[str, Any] = {"latest": 10}
    if feedback_summary.get("event_count") or feedback_summary.get("invalid_row_count"):
        kwargs["feedback_summary"] = feedback_summary
    return tm_answer_trace.summarize_rows(rows, invalid, **kwargs), rows


RETRIEVAL_EXPERIMENT_ENVS = (
    "TM_EMBED_SUMMARY_WEIGHT",
    "TM_HYBRID_MAP_ARM",
    "TM_ANSWER_WIKI_MAP_BRIDGE",
    "TM_ANSWER_WIKI_MAP",
)


def _env_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "enabled", "yes", "force"}


def _retrieval_flag_snapshot() -> dict[str, Any]:
    flags = {name: os.getenv(name, "") for name in RETRIEVAL_EXPERIMENT_ENVS}
    try:
        summary_weight = float(flags["TM_EMBED_SUMMARY_WEIGHT"] or 0)
    except (TypeError, ValueError):
        summary_weight = 0.0
    return {
        "flags": flags,
        "summary_vector_enabled": summary_weight > 0,
        "hybrid_map_arm_enabled": _env_truthy(flags["TM_HYBRID_MAP_ARM"]),
        "answer_map_bridge_enabled": _env_truthy(flags["TM_ANSWER_WIKI_MAP_BRIDGE"]),
        "planner_wiki_map_enabled": _env_truthy(flags["TM_ANSWER_WIKI_MAP"]),
    }


def _latest_p310_matrix_report(matrix: str, warnings: list[str] | None = None) -> dict[str, Any] | None:
    root = REPO_ROOT / ".tmp"
    if not root.exists():
        return None
    candidates: list[pathlib.Path] = []
    for folder in root.glob("p310*"):
        path = folder / f"{matrix}.json"
        if path.exists():
            candidates.append(path)
    if not candidates:
        return None
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"{matrix} holdout 证据读取失败：{path} ({exc})")
        return None
    return {
        "matrix": matrix,
        "artifact": str(path),
        "updated_at": dt.datetime.fromtimestamp(path.stat().st_mtime, tz=tm_core.TZ_CN).isoformat(),
        "case_count": report.get("case_count"),
        "passed": report.get("passed"),
        "expected_path_case_count": report.get("expected_path_case_count"),
        "answer_evidence_hit": report.get("answer_evidence_hit"),
        "evidence_gate_hit": report.get("evidence_gate_hit"),
        "map_hit_but_evidence_miss": report.get("map_hit_but_evidence_miss"),
        "map_leak_reason_category_counts": report.get("map_leak_reason_category_counts") or {},
        "answer_evidence_hit_by_bucket": report.get("answer_evidence_hit_by_bucket") or {},
    }


def _number_delta(lhs: Any, rhs: Any) -> int | float | None:
    if not isinstance(lhs, (int, float)) or not isinstance(rhs, (int, float)):
        return None
    return lhs - rhs


def _dashboard_retrieval_release_status() -> dict[str, Any]:
    warnings: list[str] = []
    flags = _retrieval_flag_snapshot()
    production = _latest_p310_matrix_report("production", warnings)
    map_arm = _latest_p310_matrix_report("map_arm", warnings)
    answer_delta = _number_delta(
        map_arm.get("answer_evidence_hit") if map_arm else None,
        production.get("answer_evidence_hit") if production else None,
    )
    leak_delta = _number_delta(
        map_arm.get("map_hit_but_evidence_miss") if map_arm else None,
        production.get("map_hit_but_evidence_miss") if production else None,
    )
    has_gain = isinstance(answer_delta, (int, float)) and answer_delta > 0
    has_leak_reduction = isinstance(leak_delta, (int, float)) and leak_delta < 0
    default_enabled = bool(flags["hybrid_map_arm_enabled"])
    if warnings:
        decision = "artifact_error"
        summary = "最近 holdout 证据文件读取失败，不能据此判断默认化。"
    elif not map_arm:
        decision = "no_recent_map_arm_evidence"
        summary = "还没有最近的 map arm holdout 证据。"
    elif not production:
        decision = "needs_production_baseline"
        summary = "已有 map arm 证据，但缺最近的 production 对照。"
    elif has_gain and has_leak_reduction:
        if default_enabled:
            decision = "service_default_enabled"
            summary = "map arm 当前服务环境已默认开启；最近 holdout 有净收益，并减少了找到但没进证据的漏点。"
        else:
            decision = "default_candidate"
            summary = "map arm 在最近 holdout 里有净收益，并减少了找到但没进证据的漏点。"
    elif has_gain:
        decision = "candidate_with_leak_risk"
        summary = "map arm 在最近 holdout 里有净收益，但漏点没有同步下降。"
    else:
        decision = "keep_opt_in"
        summary = "map arm 最近 holdout 没有稳定净收益，继续保持实验开关。"
    return {
        "schema_version": "retrieval-release-status-v1",
        "generated_at": _now_iso(),
        "flags": flags,
        "latest": {
            "production": production,
            "map_arm": map_arm,
        },
        "deltas": {
            "answer_evidence_hit": answer_delta,
            "map_hit_but_evidence_miss": leak_delta,
        },
        "decision": decision,
        "summary": summary,
        "warnings": warnings,
        "rollback": "把 TM_HYBRID_MAP_ARM 设为 0 或移除该环境变量，然后重启使用 memory_answer 的服务。",
        "default_enabled": default_enabled,
        "notes": [
            "本卡只读展示最近 holdout 证据，不会自动改服务环境。",
            "旧 TM_ANSWER_WIKI_MAP 仍必须默认关闭。",
            "summary vector 当前没有 holdout 净收益，不随 map arm 一起默认开启。",
        ],
    }


def _dashboard_memory_quality_single(date: str | None = None) -> dict[str, Any]:
    report_date = date or today()
    cache_key = f"api:quality:memory:{report_date}"
    cached, _ = _run_cache_get(cache_key, API_CACHE_TTL)
    if cached:
        cached = copy.deepcopy(cached)
        cached.setdefault("warnings", [])
        cached.setdefault("errors", [])
        cached["cached"] = True
        worktree = _worktree_dirty_state()
        cached["repo_dirty"] = worktree.get("dirty")
        if worktree.get("dirty"):
            warning = f"工作区存在未提交改动，可能影响质量面板准确性（{worktree['status_count']}项）"
            if warning not in cached["warnings"]:
                cached["warnings"].append(warning)
        if worktree.get("error"):
            cached["repo_dirty"] = True
            cached.setdefault("errors", []).append(worktree["error"])
        cached_counts = cached.get("counts")
        if not isinstance(cached_counts, dict):
            cached_counts = {}
        cached_counts.setdefault("inbox_today", 0)
        cached_counts.setdefault("inbox_pending", cached_counts.get("inbox", 0))
        cached_counts.setdefault("issue", _sum_trace_issues(cached.get("trace_summary", {}).get("status_counts", {})) if isinstance(cached.get("trace_summary", {}), dict) else 0)
        if cached.get("digest_available"):
            cached_counts.setdefault("discard", 0)
        else:
            cached_counts.setdefault("wiki_count_source", "live_not_connected")
            if cached_counts.get("discard") is None:
                cached_counts["discard"] = 0
        cached["route_history"] = _attach_quality_route_history(cached_counts, [report_date], cached["warnings"])
        cached["counts"] = cached_counts
        cached["retrieval_release"] = _dashboard_retrieval_release_status()
        trace_summary = cached.get("trace_summary") if isinstance(cached.get("trace_summary"), dict) else {}
        trace_latest = trace_summary.get("latest") if isinstance(trace_summary.get("latest", []), list) else []
        source_mode = "digest" if cached.get("digest_available") else "live"
        cached["route_flow"] = _build_quality_route_flow(
            counts=cached_counts,
            report_date=report_date,
            trace_summary=trace_summary,
            trace_rows=trace_latest,
            inbox_rows=[],
            digest_error=cached.get("digest_error"),
            source_mode=source_mode,
        )
        cached["flow"] = cached["route_flow"]
        cached.setdefault("range", _quality_range_meta(report_date, "today"))
        return cached

    start = time.time()
    worktree = _worktree_dirty_state()
    digest: dict[str, Any] | None = None
    digest_error = None
    source_mode = "live"
    source = "live-inbox"
    source_path = ""
    source_updated_at = None
    source_hash = ""
    warnings: list[str] = []
    if worktree.get("error"):
        warnings.append(worktree["error"])
    mem0_status = _mem0_payload("", size=1, timeout=0.5)
    if mem0_status.get("error"):
        warnings.append(f"Mem0 不可达：{mem0_status.get('error')}")
    try:
        digest = parse_digest(report_date)
        source = f"wiki/operations/daily-memory-digest-{report_date}.md"
        source_path = str(REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{report_date}.md")
        source_updated_at, source_hash = _file_signature(pathlib.Path(source_path))
        source_mode = "digest"
    except Exception as exc:
        digest_error = str(exc)
        warnings.append(f"日报读取失败：{digest_error}，已回退到实时 inbox + proposal 列表")
        digest = _live_digest_fallback(report_date, reason=digest_error or "digest not available", fast=report_date == today())
    trace_summary, rows = _load_quality_trace_summary_with_rows(since_hours=24 * 7)
    inbox_rows = list(digest.get("inbox_rows", [])) + list(digest.get("hidden_inbox_rows", [])) if isinstance(digest, dict) else []
    inbox_total, inbox_today = _summarize_inbox_rows(inbox_rows, report_date)
    counts = dict(digest.get("counts") or {})
    if not isinstance(counts, dict):
        counts = {}
    counts["inbox_pending"] = inbox_total
    counts["inbox_today"] = inbox_today
    trace_issue_count = _sum_trace_issues(trace_summary.get("status_counts") if isinstance(trace_summary, dict) else {})
    counts["issue"] = trace_issue_count
    if source_mode == "digest":
        counts.setdefault("wiki", 0)
    else:
        mem0_count, mem0_basis = _quality_live_mem0_count(report_date, mem0_status)
        discard_count, discard_basis = _quality_live_discard_count(report_date)
        counts["mem0"] = mem0_count
        counts["mem0_basis"] = mem0_basis
        counts["wiki"] = None
        counts["wiki_count_source"] = "live_not_connected"
        counts["discard"] = discard_count
        counts["discard_basis"] = discard_basis
    route_event_summary = _attach_quality_route_history(counts, [report_date], warnings)
    fallback_mode = source_mode != "digest"
    route_flow = _build_quality_route_flow(
        counts=counts,
        report_date=report_date,
        trace_summary=trace_summary,
        trace_rows=rows,
        inbox_rows=inbox_rows,
        digest_error=digest_error,
        source_mode=source_mode,
    )
    counts["route_recommendation_counts"] = route_flow.get("route_recommendation_counts")
    counts["flow_source"] = route_flow.get("flow_source")
    counts = {
        "mem0": counts.get("mem0"),
        "inbox": counts.get("inbox"),
        "discard": counts.get("discard"),
        "review_hidden": counts.get("review_hidden", 0),
        "proposal": counts.get("proposal", 0),
        "self_evolution": counts.get("self_evolution", 0),
        "stale_archive": counts.get("stale_archive", 0),
        "promote": counts.get("promote", 0),
        "report_inbox": counts.get("report_inbox", 0),
        "inbox_pending": counts.get("inbox_pending", inbox_total),
        "inbox_today": counts.get("inbox_today", inbox_today),
        "wiki": counts.get("wiki"),
        "wiki_count_source": counts.get("wiki_count_source"),
        "mem0_basis": counts.get("mem0_basis"),
        "discard_basis": counts.get("discard_basis"),
        "issue": counts.get("issue"),
        "route_recommendation_counts": counts.get("route_recommendation_counts"),
        "flow_source": counts.get("flow_source"),
        "route_event_counts": counts.get("route_event_counts"),
        "route_event_total": counts.get("route_event_total"),
        "route_event_source": counts.get("route_event_source"),
        "route_event_dates": counts.get("route_event_dates"),
        "route_event_missing_dates": counts.get("route_event_missing_dates"),
        "route_event_error": counts.get("route_event_error"),
    }
    if worktree.get("dirty"):
        warnings.append(f"工作区存在未提交改动，可能影响质量面板准确性（{worktree['status_count']}项）")
    result = {
        "ok": True,
        "date": report_date,
        "range": _quality_range_meta(report_date, "today"),
        "source": source,
        "source_path": source_path,
        "source_updated_at": source_updated_at,
        "source_hash": source_hash,
        "generated_at": _now_iso(),
        "latency_ms": round((time.time() - start) * 1000, 1),
        "digest_available": source_mode == "digest",
        "digest_error": digest_error,
        "fallback": fallback_mode,
        "fallback_mode": fallback_mode,
        "counts": counts,
        "discard_candidates": digest.get("discard_candidates", ""),
        "route_flow": route_flow,
        "flow": route_flow,
        "route_history": route_event_summary,
        "retrieval_release": _dashboard_retrieval_release_status(),
        "trace_latency_supported": True,
        "trace_summary": trace_summary,
        "mem0_status": {
            "ok": mem0_status.get("error") is None,
            "count": mem0_status.get("count"),
            "latency_ms": mem0_status.get("latency_ms"),
            "error": mem0_status.get("error"),
        },
        "stale": False,
        "cached": False,
        "cache": {
            "hit": False,
            "ttl_seconds": API_CACHE_TTL,
            "age_ms": 0,
            "cached_at": _now_iso(),
            "source": source,
            "source_path": source_path,
            "source_hash": source_hash,
        },
        "repo_dirty": worktree.get("dirty"),
        "warnings": warnings,
        "errors": [worktree["error"]] if worktree.get("error") else [],
    }
    _run_cache_set(
        cache_key,
        result,
        source=source,
        source_path=source_path,
        source_hash=source_hash,
        source_updated_at=source_updated_at or "",
        ttl_seconds=API_CACHE_TTL,
    )
    return result


def _dashboard_memory_quality_range(date: str | None = None, range_key: str | None = None) -> dict[str, Any]:
    report_date = date or today()
    key = _quality_range_key(range_key)
    meta = _quality_range_meta(report_date, key)
    if key == "today":
        result = _dashboard_memory_quality_single(report_date)
        result["range"] = meta
        return result

    cache_key = f"api:quality:memory:{report_date}:{key}"
    cached, _ = _run_cache_get(cache_key, API_CACHE_TTL)
    if cached:
        cached = copy.deepcopy(cached)
        cached.setdefault("warnings", [])
        cached.setdefault("errors", [])
        worktree = _worktree_dirty_state()
        cached["repo_dirty"] = worktree.get("dirty")
        if worktree.get("dirty"):
            warning = f"工作区存在未提交改动，可能影响质量面板准确性（{worktree['status_count']}项）"
            if warning not in cached["warnings"]:
                cached["warnings"].append(warning)
        if worktree.get("error"):
            cached["repo_dirty"] = True
            cached.setdefault("errors", []).append(worktree["error"])
        cached["cached"] = True
        cached.setdefault("range", meta)
        cached_counts = cached.get("counts") if isinstance(cached.get("counts"), dict) else {}
        cached["route_history"] = _attach_quality_route_history(cached_counts, list(meta["dates"]), cached["warnings"])
        cached["counts"] = cached_counts
        cached["retrieval_release"] = _dashboard_retrieval_release_status()
        flow = cached.get("route_flow") if isinstance(cached.get("route_flow"), dict) else cached.get("flow")
        if isinstance(flow, dict):
            flow["history"] = {
                "route_event_count": cached_counts.get("route_event_total", 0),
                "route_event_counts": cached_counts.get("route_event_counts", {"mem0": 0, "wiki": 0, "inbox": 0, "discard": 0}),
                "route_event_source": cached_counts.get("route_event_source"),
                "route_event_dates": cached_counts.get("route_event_dates", []),
                "route_event_missing_dates": cached_counts.get("route_event_missing_dates", []),
                "route_event_error": cached_counts.get("route_event_error"),
                "note": (
                    f"{meta['label']}主图只展示已记录路由流水 {cached_counts.get('route_event_total', 0)} 条；"
                    f"缺少 {len(cached_counts.get('route_event_missing_dates', []) or [])} 天流水，历史日报和待审积压只作参考。"
                    if cached_counts.get("route_event_total", 0)
                    else f"{meta['label']}暂无路由流水；当前图使用旧日报和待审补算，不能当作完整分流事实。"
                ),
            }
            cached["route_flow"] = flow
            cached["flow"] = flow
        return cached

    start = time.time()
    worktree = _worktree_dirty_state()
    warnings: list[str] = []
    digest_errors: list[str] = []
    if worktree.get("error"):
        warnings.append(worktree["error"])

    mem0_status = _mem0_payload("", size=1, timeout=0.5)
    if mem0_status.get("error"):
        warnings.append(f"Mem0 不可达：{mem0_status.get('error')}")

    counts: dict[str, Any] = {
        "mem0": 0,
        "inbox": 0,
        "discard": 0,
        "review_hidden": 0,
        "proposal": 0,
        "self_evolution": 0,
        "stale_archive": 0,
        "promote": 0,
        "report_inbox": 0,
        "review_entered": 0,
        "wiki": 0,
        "wiki_count_source": "frontmatter",
    }
    source_hash_parts: list[str] = []
    source_paths: list[str] = []
    source_updated_at = None
    available_dates: list[str] = []
    missing_dates: list[str] = []
    inbox_rows: list[dict[str, Any]] = []
    report_inbox_rows: list[dict[str, Any]] = []

    for day in meta["dates"]:
        try:
            digest = _parse_quality_digest_snapshot(day)
            available_dates.append(day)
            path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{day}.md"
            updated = str(digest.get("source_updated_at") or "")
            digest_hash = str(digest.get("source_hash") or "")
            if not digest_hash:
                updated, digest_hash = _file_signature(path)
            source_paths.append(str(path))
            source_hash_parts.append(f"{day}:{digest_hash}")
            source_updated_at = updated or source_updated_at
        except Exception as exc:
            missing_dates.append(day)
            digest_errors.append(f"{day}: {exc}")
            if day != today():
                continue
            digest = _live_digest_fallback(day, reason=str(exc), fast=True)

        day_counts = dict(digest.get("counts") or {}) if isinstance(digest, dict) else {}
        for name in (
            "mem0",
            "inbox",
            "discard",
            "review_hidden",
            "proposal",
            "self_evolution",
            "stale_archive",
            "promote",
            "report_inbox",
            "review_entered",
            "wiki",
        ):
            counts[name] = int(counts.get(name) or 0) + _parse_int(day_counts.get(name))
        if day_counts.get("wiki_count_source") == "missing":
            counts["wiki_count_source"] = "partial_missing"
        elif (
            day_counts.get("wiki_count_source") == "wiki_proposal_inbox"
            and counts.get("wiki_count_source") == "frontmatter"
        ):
            counts["wiki_count_source"] = "wiki_proposal_inbox"
        inbox_rows.extend(list(digest.get("inbox_rows", [])))
        inbox_rows.extend(list(digest.get("hidden_inbox_rows", [])))
        report_inbox_rows.extend(list(digest.get("report_inbox_rows", [])))

    if today() in meta["dates"]:
        current_digest = _live_digest_fallback(today(), reason="quality range current pending snapshot", fast=True)
        current_rows = list(current_digest.get("inbox_rows", [])) + list(current_digest.get("hidden_inbox_rows", []))
        current_counts = dict(current_digest.get("counts") or {})
        counts["inbox"] = _parse_int(current_counts.get("inbox"))
        counts["inbox_pending"] = _parse_int(current_counts.get("inbox_pending"))
        counts["inbox_today"] = _parse_int(current_counts.get("inbox_pending"))
        counts["review_hidden"] = _parse_int(current_counts.get("review_hidden"))
        inbox_rows.extend(current_rows)
        if today() in missing_dates:
            report_inbox_rows.extend(current_rows)

    if today() in meta["dates"] and today() in missing_dates:
        mem0_count, mem0_basis = _quality_live_mem0_count(today(), mem0_status)
        discard_count, discard_basis = _quality_live_discard_count(today())
        if mem0_count is None:
            if counts.get("mem0"):
                counts["mem0_basis"] = f"{mem0_basis}；已保留日报累计 mem0_count={counts.get('mem0')}，今日实时增量未计入"
            else:
                counts["mem0"] = None
                counts["mem0_basis"] = mem0_basis
        else:
            counts["mem0"] = int(counts.get("mem0") or 0) + mem0_count
            counts["mem0_basis"] = mem0_basis
        counts["discard"] = int(counts.get("discard") or 0) + discard_count
        counts["discard_basis"] = discard_basis

    if missing_dates:
        warnings.append(f"{meta['label']}缺少 {len(missing_dates)} 天日报，已按可用日期和今日实时数据聚合；缺失日期不会按 0 冒充完整结果。")
    if counts.get("wiki_count_source") == "partial_missing" and not counts.get("wiki"):
        counts["wiki"] = None

    trace_summary, rows = _load_quality_trace_summary_with_rows(
        since_hours=24 * int(meta["days"]),
        dates=list(meta["dates"]),
    )
    unique_review_paths = {
        str(row.get("path") or "")
        for row in report_inbox_rows
        if row.get("path")
    }
    counts["review_entered"] = len(unique_review_paths)
    deduped_inbox_rows: list[dict[str, Any]] = []
    seen_inbox_paths: set[str] = set()
    for row in inbox_rows:
        row_path = str(row.get("path") or "")
        dedupe_key = row_path or json.dumps(row, sort_keys=True, ensure_ascii=False)
        if dedupe_key in seen_inbox_paths:
            continue
        seen_inbox_paths.add(dedupe_key)
        deduped_inbox_rows.append(row)
    inbox_rows = deduped_inbox_rows
    date_filter = set(meta["dates"])
    inbox_total, inbox_matched = _summarize_inbox_rows_for_dates(inbox_rows, report_date, date_filter)
    if not counts.get("inbox_pending"):
        counts["inbox"] = inbox_total
        counts["inbox_pending"] = inbox_total
        counts["inbox_today"] = inbox_matched
    counts["issue"] = _sum_trace_issues(trace_summary.get("status_counts") if isinstance(trace_summary, dict) else {})
    route_event_summary = _attach_quality_route_history(counts, list(meta["dates"]), warnings)

    source_mode = "range"
    digest_error = "; ".join(digest_errors[:3]) if digest_errors else None
    route_flow = _build_quality_route_flow(
        counts=counts,
        report_date=report_date,
        trace_summary=trace_summary,
        trace_rows=rows,
        inbox_rows=inbox_rows,
        digest_error=digest_error,
        source_mode=source_mode,
        date_filter=date_filter,
        period_label=str(meta["label"]),
        trace_period_label=str(meta["trace_label"]),
    )
    counts["route_recommendation_counts"] = route_flow.get("route_recommendation_counts")
    counts["flow_source"] = route_flow.get("flow_source")
    counts = {
        "mem0": counts.get("mem0"),
        "inbox": counts.get("inbox"),
        "discard": counts.get("discard"),
        "review_hidden": counts.get("review_hidden", 0),
        "proposal": counts.get("proposal", 0),
        "self_evolution": counts.get("self_evolution", 0),
        "stale_archive": counts.get("stale_archive", 0),
        "promote": counts.get("promote", 0),
        "report_inbox": counts.get("report_inbox", 0),
        "review_entered": counts.get("review_entered", 0),
        "inbox_pending": counts.get("inbox_pending", inbox_total),
        "inbox_today": counts.get("inbox_today", inbox_matched),
        "wiki": counts.get("wiki"),
        "wiki_count_source": counts.get("wiki_count_source"),
        "mem0_basis": counts.get("mem0_basis"),
        "discard_basis": counts.get("discard_basis"),
        "issue": counts.get("issue"),
        "route_recommendation_counts": counts.get("route_recommendation_counts"),
        "flow_source": counts.get("flow_source"),
        "route_event_counts": counts.get("route_event_counts"),
        "route_event_total": counts.get("route_event_total"),
        "route_event_source": counts.get("route_event_source"),
        "route_event_dates": counts.get("route_event_dates"),
        "route_event_missing_dates": counts.get("route_event_missing_dates"),
        "route_event_error": counts.get("route_event_error"),
    }
    if worktree.get("dirty"):
        warnings.append(f"工作区存在未提交改动，可能影响质量面板准确性（{worktree['status_count']}项）")

    source_hash = hashlib.sha256("|".join(source_hash_parts).encode("utf-8")).hexdigest()[:16] if source_hash_parts else ""
    result = {
        "ok": True,
        "date": report_date,
        "range": meta,
        "source": f"{meta['label']}聚合",
        "source_path": ";".join(source_paths),
        "source_updated_at": source_updated_at,
        "source_hash": source_hash,
        "available_dates": available_dates,
        "missing_dates": missing_dates,
        "generated_at": _now_iso(),
        "latency_ms": round((time.time() - start) * 1000, 1),
        "digest_available": bool(available_dates),
        "digest_error": digest_error,
        "fallback": bool(missing_dates),
        "fallback_mode": bool(missing_dates),
        "counts": counts,
        "discard_candidates": "",
        "route_flow": route_flow,
        "flow": route_flow,
        "route_history": route_event_summary,
        "retrieval_release": _dashboard_retrieval_release_status(),
        "trace_latency_supported": True,
        "trace_summary": trace_summary,
        "mem0_status": {
            "ok": mem0_status.get("error") is None,
            "count": mem0_status.get("count"),
            "latency_ms": mem0_status.get("latency_ms"),
            "error": mem0_status.get("error"),
        },
        "stale": False,
        "cached": False,
        "cache": {
            "hit": False,
            "ttl_seconds": API_CACHE_TTL,
            "age_ms": 0,
            "cached_at": _now_iso(),
            "source": f"{meta['label']}聚合",
            "source_path": ";".join(source_paths),
            "source_hash": source_hash,
        },
        "repo_dirty": worktree.get("dirty"),
        "warnings": warnings,
        "errors": [worktree["error"]] if worktree.get("error") else [],
    }
    _run_cache_set(
        cache_key,
        result,
        source=result["source"],
        source_path=result["source_path"],
        source_hash=source_hash,
        source_updated_at=source_updated_at or "",
        ttl_seconds=API_CACHE_TTL,
    )
    return result


def dashboard_memory_quality(date: str | None = None, range_key: str | None = None) -> dict[str, Any]:
    return _dashboard_memory_quality_range(date, range_key)


def get_user_preferences() -> dict[str, Any]:
    return tm_dashboard_prefs.get_user_preferences(PREFS_DB)


def propose_preferences_wiki(prefs: dict[str, Any]) -> dict[str, Any]:
    import tm_mcp

    payload = tm_dashboard_prefs.preference_page_payload(prefs)
    return tm_mcp.propose_wiki_page(**payload)


def update_user_preferences(updates: dict[str, Any], *, propose_wiki: bool = False) -> dict[str, Any]:
    result = tm_dashboard_prefs.update_user_preferences(updates, PREFS_DB)
    proposal = None
    if propose_wiki:
        proposal = propose_preferences_wiki(result["preferences"])
    return {**result, "wiki_proposal": proposal}


def _clean_llm_env_value(value: str | None, *, field: str) -> str:
    text = (value or "").strip()
    if "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError(f"{field} contains unsupported characters")
    return text


def _normalized_start_llm_config(req: StartLlmConfigRequest) -> dict[str, str]:
    provider = (req.provider or "deepseek").strip().lower()
    provider_aliases = {
        "deepseek": "deepseek",
        "openai": "openai_compatible",
        "openai-compatible": "openai_compatible",
        "openai_compatible": "openai_compatible",
    }
    provider = provider_aliases.get(provider, "")
    if provider not in {"deepseek", "openai_compatible"}:
        raise ValueError("请选择 DeepSeek 或 OpenAI-compatible provider")

    existing = tm_llm_status.llm_env_file_values(REPO_ROOT)
    api_key = _clean_llm_env_value(req.api_key, field="api_key")
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or existing.get("DEEPSEEK_API_KEY", "").strip()
    default_base = tm_core.DEFAULT_DEEPSEEK_ENDPOINT if provider == "deepseek" else "https://api.openai.com/v1/chat/completions"
    default_model = tm_core.DEFAULT_DEEPSEEK_MODEL if provider == "deepseek" else "gpt-4o-mini"
    default_admin_model = tm_core.DEFAULT_DEEPSEEK_ADMIN_MODEL if provider == "deepseek" else default_model
    base_url = _clean_llm_env_value(req.base_url, field="base_url") or default_base
    model = _clean_llm_env_value(req.model, field="model") or default_model
    requested_admin_model = _clean_llm_env_value(req.admin_model, field="admin_model")
    if requested_admin_model:
        admin_model = requested_admin_model
    elif provider == "deepseek":
        admin_model = existing.get("DEEPSEEK_ADMIN_MODEL", default_admin_model)
    else:
        admin_model = model

    if not api_key:
        raise ValueError("请先填写 API Key")
    tm_core.check_transport_security(base_url)
    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "admin_model": admin_model,
    }


def test_start_llm_config(req: StartLlmConfigRequest) -> dict[str, Any]:
    try:
        config = _normalized_start_llm_config(req)
        body = json.dumps(
            {
                "model": config["model"],
                "messages": [{"role": "user", "content": "Reply OK."}],
                "temperature": 0,
                "max_tokens": 8,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            config["base_url"],
            data=body,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        timeout = max(3, min(30, int(os.getenv("TM_START_LLM_TEST_TIMEOUT", "12"))))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            response.read(512)
        return {
            "ok": True,
            "provider": config["provider"],
            "model": config["model"],
            "status_code": status_code,
            "message": "模型连通性测试通过",
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status_code": exc.code,
            "error": f"模型连通性测试失败：HTTP {exc.code}",
        }
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return {"ok": False, "error": f"模型连通性测试失败：{reason}"}
    except TimeoutError:
        return {"ok": False, "error": "模型连通性测试超时，请检查地址或网络代理"}
    except Exception as exc:
        return {"ok": False, "error": f"模型连通性测试失败：{exc}"}


def _write_runtime_env_updates(path: pathlib.Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _value = line.split("=", 1)
        normalized = key.strip()
        if normalized in updates:
            output.append(f"{normalized}={updates[normalized]}")
            written.add(normalized)
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def save_start_llm_config(req: StartLlmConfigRequest) -> dict[str, Any]:
    config = _normalized_start_llm_config(req)
    connection_test = None
    if req.test_connection:
        connection_test = test_start_llm_config(req)
        if not connection_test.get("ok"):
            raise ValueError(str(connection_test.get("error") or "模型连通性测试失败"))
    env_path = tm_llm_status.llm_env_path(REPO_ROOT)
    updates = {
        "TIGERMEMORY_LLM_PROVIDER": config["provider"],
        "DEEPSEEK_BASE_URL": config["base_url"],
        "DEEPSEEK_MODEL": config["model"],
        "DEEPSEEK_ADMIN_MODEL": config["admin_model"],
    }
    if _clean_llm_env_value(req.api_key, field="api_key"):
        updates["DEEPSEEK_API_KEY"] = config["api_key"]

    _write_runtime_env_updates(env_path, updates)
    for key, value in updates.items():
        os.environ[key] = value
    status = tm_llm_status.llm_status_payload(REPO_ROOT)
    return {
        "ok": True,
        "provider": config["provider"],
        "env_path": _relpath(env_path),
        "llm_status": status,
        "connection_test": connection_test,
        "message": "已保存到本机 TigerMemory 配置",
    }


def _agent_software(
    id: str,
    label: str,
    *,
    support: str = "planned",
    target: str = "",
    commands: tuple[str, ...] = (),
    paths: tuple[tuple[str, ...], ...] = (),
    glob_paths: tuple[tuple[str, ...], ...] = (),
) -> dict[str, Any]:
    return {
        "id": id,
        "label": label,
        "support": support,
        "target": target,
        "commands": commands,
        "paths": paths,
        "glob_paths": glob_paths,
    }


AGENT_SOFTWARE_CATALOG: tuple[dict[str, Any], ...] = (
    # TigerMemory one-click project-rule targets.
    _agent_software(
        "codex",
        "Codex",
        support="supported",
        target="codex",
        commands=("codex",),
        paths=(("USERPROFILE", ".codex"), ("APPDATA", "npm", "codex.cmd")),
    ),
    _agent_software(
        "claude-code",
        "Claude Code",
        support="supported",
        target="claude-code",
        commands=("claude",),
        paths=(
            ("USERPROFILE", ".claude"),
            ("LOCALAPPDATA", "ClaudeCodeOfficial"),
            ("APPDATA", "npm", "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"),
        ),
    ),
    _agent_software(
        "cursor",
        "Cursor",
        support="supported",
        target="cursor",
        paths=(("APPDATA", "Cursor"), ("LOCALAPPDATA", "Programs", "Cursor", "Cursor.exe"), ("USERPROFILE", ".cursor")),
    ),
    # AI-native IDEs and coding agents.
    _agent_software("gemini", "Gemini CLI", commands=("gemini",), paths=(("USERPROFILE", ".gemini"), ("APPDATA", "npm", "gemini.cmd"))),
    _agent_software("antigravity", "Antigravity", paths=(("USERPROFILE", ".gemini", "antigravity"), ("APPDATA", "Antigravity"), ("LOCALAPPDATA", "Antigravity"))),
    _agent_software("windsurf", "Windsurf", paths=(("APPDATA", "Windsurf"), ("LOCALAPPDATA", "Programs", "Windsurf"), ("USERPROFILE", ".codeium"))),
    _agent_software("opencode", "OpenCode", commands=("opencode",), paths=(("USERPROFILE", ".opencode"), ("APPDATA", "opencode"), ("LOCALAPPDATA", "opencode"))),
    _agent_software("resonmix", "Resonmix", paths=(("APPDATA", "Resonmix"), ("LOCALAPPDATA", "Resonmix"), ("USERPROFILE", ".resonmix"))),
    _agent_software("trae", "Trae", paths=(("APPDATA", "Trae"), ("LOCALAPPDATA", "Programs", "Trae"), ("USERPROFILE", ".trae"))),
    _agent_software("zcode", "Zcode", paths=(("APPDATA", "Zcode"), ("LOCALAPPDATA", "Programs", "Zcode"), ("USERPROFILE", ".zcode"))),
    _agent_software("zed", "Zed", commands=("zed",), paths=(("APPDATA", "Zed"), ("LOCALAPPDATA", "Programs", "Zed"), ("USERPROFILE", ".zed"))),
    _agent_software("qoder", "Qoder", commands=("qoder",), paths=(("APPDATA", "Qoder"), ("LOCALAPPDATA", "Programs", "Qoder"), ("USERPROFILE", ".qoder"))),
    _agent_software("kiro", "Kiro", commands=("kiro",), paths=(("APPDATA", "Kiro"), ("LOCALAPPDATA", "Programs", "Kiro"), ("USERPROFILE", ".kiro"))),
    _agent_software("augment-code", "Augment Code", paths=(("APPDATA", "Augment"), ("USERPROFILE", ".augment")), glob_paths=(("USERPROFILE", ".vscode", "extensions", "augment*.vscode-augment-*"),)),
    _agent_software("devin", "Devin", paths=(("APPDATA", "Devin"), ("LOCALAPPDATA", "Programs", "Devin"), ("USERPROFILE", ".devin"))),
    _agent_software("replit-agent", "Replit Agent", commands=("replit",), paths=(("APPDATA", "Replit"), ("USERPROFILE", ".replit"))),
    # Open-source and terminal coding agents.
    _agent_software("aider", "Aider", commands=("aider",), paths=(("USERPROFILE", ".aider.conf.yml"), ("USERPROFILE", ".aider.model.settings.yml"))),
    _agent_software("qwen-code", "Qwen Code", commands=("qwen", "qwen-code"), paths=(("USERPROFILE", ".qwen"), ("APPDATA", "npm", "qwen.cmd"))),
    _agent_software("goose", "Goose", commands=("goose",), paths=(("USERPROFILE", ".config", "goose"), ("APPDATA", "goose"))),
    _agent_software("openhands", "OpenHands", commands=("openhands",), paths=(("USERPROFILE", ".openhands"), ("APPDATA", "OpenHands"))),
    # Mainstream IDEs and editors.
    _agent_software("vscode", "Visual Studio Code", commands=("code",), paths=(("APPDATA", "Code"), ("LOCALAPPDATA", "Programs", "Microsoft VS Code", "Code.exe"), ("USERPROFILE", ".vscode"))),
    _agent_software("vscode-insiders", "Visual Studio Code Insiders", commands=("code-insiders",), paths=(("APPDATA", "Code - Insiders"), ("LOCALAPPDATA", "Programs", "Microsoft VS Code Insiders", "Code - Insiders.exe"), ("USERPROFILE", ".vscode-insiders"))),
    _agent_software("visual-studio", "Visual Studio", commands=("devenv",), paths=(("ProgramFiles", "Microsoft Visual Studio"), ("ProgramFiles(x86)", "Microsoft Visual Studio"))),
    _agent_software("jetbrains-toolbox", "JetBrains Toolbox", paths=(("APPDATA", "JetBrains", "Toolbox"), ("LOCALAPPDATA", "JetBrains", "Toolbox"))),
    _agent_software("intellij-idea", "IntelliJ IDEA", glob_paths=(("APPDATA", "JetBrains", "IntelliJIdea*"), ("LOCALAPPDATA", "JetBrains", "IntelliJIdea*"))),
    _agent_software("pycharm", "PyCharm", glob_paths=(("APPDATA", "JetBrains", "PyCharm*"), ("LOCALAPPDATA", "JetBrains", "PyCharm*"))),
    _agent_software("webstorm", "WebStorm", glob_paths=(("APPDATA", "JetBrains", "WebStorm*"), ("LOCALAPPDATA", "JetBrains", "WebStorm*"))),
    _agent_software("goland", "GoLand", glob_paths=(("APPDATA", "JetBrains", "GoLand*"), ("LOCALAPPDATA", "JetBrains", "GoLand*"))),
    _agent_software("clion", "CLion", glob_paths=(("APPDATA", "JetBrains", "CLion*"), ("LOCALAPPDATA", "JetBrains", "CLion*"))),
    _agent_software("rider", "Rider", glob_paths=(("APPDATA", "JetBrains", "Rider*"), ("LOCALAPPDATA", "JetBrains", "Rider*"))),
    _agent_software("phpstorm", "PhpStorm", glob_paths=(("APPDATA", "JetBrains", "PhpStorm*"), ("LOCALAPPDATA", "JetBrains", "PhpStorm*"))),
    _agent_software("rustrover", "RustRover", glob_paths=(("APPDATA", "JetBrains", "RustRover*"), ("LOCALAPPDATA", "JetBrains", "RustRover*"))),
    _agent_software("android-studio", "Android Studio", commands=("studio",), paths=(("ProgramFiles", "Android", "Android Studio")), glob_paths=(("APPDATA", "Google", "AndroidStudio*"), ("LOCALAPPDATA", "Google", "AndroidStudio*"))),
    _agent_software("xcode", "Xcode", commands=("xcodebuild",)),
    _agent_software("eclipse", "Eclipse", commands=("eclipse",), paths=(("USERPROFILE", "eclipse"), ("APPDATA", "Eclipse"))),
    _agent_software("netbeans", "NetBeans", commands=("netbeans",), paths=(("APPDATA", "NetBeans"),), glob_paths=(("ProgramFiles", "NetBeans*"),)),
    _agent_software("sublime-text", "Sublime Text", commands=("subl",), paths=(("APPDATA", "Sublime Text"), ("ProgramFiles", "Sublime Text"))),
    _agent_software("notepadpp", "Notepad++", paths=(("APPDATA", "Notepad++"), ("ProgramFiles", "Notepad++"))),
    _agent_software("vim", "Vim", commands=("vim", "gvim"), paths=(("ProgramFiles", "Vim"), ("USERPROFILE", "vimfiles"))),
    _agent_software("neovim", "Neovim", commands=("nvim",), paths=(("LOCALAPPDATA", "nvim"), ("LOCALAPPDATA", "Programs", "Neovim"))),
    _agent_software("emacs", "Emacs", commands=("emacs",), paths=(("USERPROFILE", ".emacs.d"), ("APPDATA", ".emacs.d"))),
    _agent_software("vscodium", "VSCodium", commands=("codium",), paths=(("APPDATA", "VSCodium"), ("LOCALAPPDATA", "Programs", "VSCodium"))),
    _agent_software("rstudio", "RStudio", commands=("rstudio",), paths=(("APPDATA", "RStudio"), ("ProgramFiles", "RStudio"))),
    _agent_software("jupyter", "Jupyter", commands=("jupyter", "jupyter-lab", "jupyter-notebook"), paths=(("USERPROFILE", ".jupyter"),)),
    # Editor extensions and domestic AI coding assistants.
    _agent_software("github-copilot", "GitHub Copilot", glob_paths=(("USERPROFILE", ".vscode", "extensions", "github.copilot-*"), ("USERPROFILE", ".cursor", "extensions", "github.copilot-*"))),
    _agent_software("continue-dev", "Continue", glob_paths=(("USERPROFILE", ".vscode", "extensions", "continue.continue-*"), ("USERPROFILE", ".cursor", "extensions", "continue.continue-*"))),
    _agent_software("cline", "Cline", glob_paths=(("USERPROFILE", ".vscode", "extensions", "cline.cline-*"), ("USERPROFILE", ".vscode", "extensions", "saoudrizwan.claude-dev-*"), ("USERPROFILE", ".cursor", "extensions", "cline.cline-*"))),
    _agent_software("roo-code", "Roo Code", glob_paths=(("USERPROFILE", ".vscode", "extensions", "rooveterinaryinc.roo-cline-*"), ("USERPROFILE", ".cursor", "extensions", "rooveterinaryinc.roo-cline-*"))),
    _agent_software("kilo-code", "Kilo Code", glob_paths=(("USERPROFILE", ".vscode", "extensions", "kilocode.kilo-code-*"), ("USERPROFILE", ".cursor", "extensions", "kilocode.kilo-code-*"))),
    _agent_software("sourcegraph-cody", "Sourcegraph Cody", glob_paths=(("USERPROFILE", ".vscode", "extensions", "sourcegraph.cody-ai-*"), ("USERPROFILE", ".cursor", "extensions", "sourcegraph.cody-ai-*"))),
    _agent_software("tabnine", "Tabnine", glob_paths=(("USERPROFILE", ".vscode", "extensions", "tabnine.tabnine-vscode-*"), ("USERPROFILE", ".cursor", "extensions", "tabnine.tabnine-vscode-*"))),
    _agent_software("codeium", "Codeium", paths=(("USERPROFILE", ".codeium"),), glob_paths=(("USERPROFILE", ".vscode", "extensions", "codeium.codeium-*"), ("USERPROFILE", ".cursor", "extensions", "codeium.codeium-*"))),
    _agent_software("tongyi-lingma", "Tongyi Lingma", commands=("lingma",), paths=(("APPDATA", "Lingma"), ("USERPROFILE", ".lingma")), glob_paths=(("USERPROFILE", ".vscode", "extensions", "*lingma*"), ("USERPROFILE", ".cursor", "extensions", "*lingma*"))),
    _agent_software("baidu-comate", "Baidu Comate", paths=(("APPDATA", "BaiduComate"), ("USERPROFILE", ".comate")), glob_paths=(("USERPROFILE", ".vscode", "extensions", "*comate*"), ("USERPROFILE", ".cursor", "extensions", "*comate*"))),
    _agent_software("tencent-codebuddy", "Tencent CodeBuddy", commands=("codebuddy",), paths=(("APPDATA", "CodeBuddy"), ("USERPROFILE", ".codebuddy")), glob_paths=(("USERPROFILE", ".vscode", "extensions", "*codebuddy*"), ("USERPROFILE", ".cursor", "extensions", "*codebuddy*"))),
    _agent_software("codegeex", "CodeGeeX", paths=(("APPDATA", "CodeGeeX"), ("USERPROFILE", ".codegeex")), glob_paths=(("USERPROFILE", ".vscode", "extensions", "*codegeex*"), ("USERPROFILE", ".cursor", "extensions", "*codegeex*"))),
    _agent_software("huawei-codearts", "Huawei CodeArts", paths=(("APPDATA", "CodeArts"), ("USERPROFILE", ".codearts"), ("ProgramFiles", "Huawei", "CodeArts IDE"))),
)


def _agent_scan_path(parts: tuple[str, ...]) -> pathlib.Path | None:
    if not parts:
        return None
    base = os.environ.get(parts[0])
    if not base:
        return None
    return pathlib.Path(base).joinpath(*parts[1:])


def _agent_scan_glob(parts: tuple[str, ...]) -> list[pathlib.Path]:
    candidate = _agent_scan_path(parts)
    if candidate is None:
        return []
    parent = candidate.parent
    if not parent.exists():
        return []
    return sorted(parent.glob(candidate.name))[:3]


def _scan_installed_agent_software() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in AGENT_SOFTWARE_CATALOG:
        signals: list[str] = []
        for command in item.get("commands", ()):
            if shutil.which(str(command)):
                signals.append(f"command:{command}")
        for path_parts in item.get("paths", ()):
            candidate = _agent_scan_path(tuple(path_parts))
            if candidate and candidate.exists():
                signals.append(str(candidate))
        for glob_parts in item.get("glob_paths", ()):
            for candidate in _agent_scan_glob(tuple(glob_parts)):
                signals.append(str(candidate))
                if len(signals) >= 3:
                    break
        installed = bool(signals)
        rows.append(
            {
                "id": item["id"],
                "label": item["label"],
                "installed": installed,
                "support": item["support"],
                "target": item.get("target", ""),
                "detected_signals": signals[:3],
            }
        )
    installed_rows = [row for row in rows if row["installed"]]
    return {
        "items": rows,
        "installed_count": len(installed_rows),
        "supported_installed_count": sum(1 for row in installed_rows if row["support"] == "supported"),
        "planned_installed_count": sum(1 for row in installed_rows if row["support"] != "supported"),
        "known_count": len(rows),
    }


def _agent_connect_status_payload() -> dict[str, Any]:
    software = _scan_installed_agent_software()
    if tm_agent_connect is None:
        return {
            "ok": False,
            "action": "status",
            "error": "tigermemory_config.agent_connect is not installed",
            "targets": [],
            "installed_agents": software["items"],
            "software_scan": {key: value for key, value in software.items() if key != "items"},
        }
    result = tm_agent_connect.status_agent_connect(repo_root=REPO_ROOT)
    result["installed_agents"] = software["items"]
    result["software_scan"] = {key: value for key, value in software.items() if key != "items"}
    return result


def apply_start_agent_connect(req: StartAgentConnectRequest) -> dict[str, Any]:
    if tm_agent_connect is None:
        return {"ok": False, "error": "tigermemory_config.agent_connect is not installed", "targets": []}
    targets = req.targets or ["codex", "claude-code", "cursor", "hooks"]
    return tm_agent_connect.apply_agent_connect(
        targets,
        yes=True,
        dry_run=bool(req.dry_run),
        repo_root=REPO_ROOT,
    )


def _render_template(template_name: str, replacements: dict[str, str]) -> str:
    path = STATIC_DIR / template_name
    html = path.read_text(encoding="utf-8")
    parts = STATIC_DIR / "_components"
    html = html.replace("__HEADER__", (parts / "header.html").read_text(encoding="utf-8"))
    html = html.replace("__STYLE__", (parts / "style.css").read_text(encoding="utf-8"))
    html = html.replace("__GIT_SHA__", git_sha())
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


def _template() -> str:
    return _render_template("review.html", {})


def _no_store(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _render_health_page(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    react_entry = STATIC_DIR / "react" / "health" / "health.html"
    if react_entry.exists():
        html = react_entry.read_text(encoding="utf-8")
        return html.replace("__TM_HEALTH_JSON__", payload).replace("__GIT_SHA__", git_sha())
    return _render_template("health.html", {"__HEALTH_JSON__": payload})


def _render_quality_page(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    react_entry = STATIC_DIR / "react" / "quality" / "quality.html"
    if react_entry.exists():
        html = react_entry.read_text(encoding="utf-8")
        return html.replace("__TM_QUALITY_JSON__", payload).replace("__GIT_SHA__", git_sha())
    return _render_template("quality.html", {"__QUALITY_JSON__": payload})


def _render_settings_page(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _render_template("settings.html", {"__SETTINGS_JSON__": payload})


def _render_ledger_page() -> str:
    return _render_template("ledger.html", {})


def _require_tigerledger_review():
    if tm_tigerledger_review is None:
        raise HTTPException(status_code=503, detail="TigerLedger review module is not installed")
    return tm_tigerledger_review


def _render_canvas_page(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _render_template("canvas.html", {"__CANVAS_JSON__": payload})


def _render_self_evolution_page(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _render_template("self-evolution.html", {"__SELF_EVOLUTION_JSON__": payload})


def _start_shell() -> dict[str, Any]:
    try:
        preferences = get_user_preferences().get("preferences", {})
    except Exception:
        preferences = {"communication_depth": "A"}
    return {
        "ok": True,
        "profile": tm_core.tigermemory_profile(),
        "preferences": preferences,
        "llm_status": tm_llm_status.llm_status_payload(REPO_ROOT),
        "agent_connect": _agent_connect_status_payload(),
        "generated_at": dt.datetime.now(tm_core.TZ_CN).isoformat(),
        "commands": [
            {"label": "初始化本地模式", "command": "tm init"},
            {"label": "查看当前模式", "command": "tm profile show"},
            {"label": "检查 AI 接入", "command": "tm agent status"},
            {"label": "搜索入门规则", "command": 'tm search --scope wiki --query "agent behavior rules"'},
            {"label": "离线查看证据", "command": 'tm ask --offline --query "agent behavior rules" --scope wiki'},
            {"label": "打开控制台", "command": "tm dashboard"},
        ],
    }


def _render_start_page(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    react_entry = STATIC_DIR / "react" / "start" / "index.html"
    if react_entry.exists():
        html = react_entry.read_text(encoding="utf-8")
        return html.replace("__TM_START_JSON__", payload).replace("__GIT_SHA__", git_sha())
    return _render_template("start.html", {"__START_JSON__": payload})


def _digest_shell(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "loading": True,
        "counts": {},
        "self_evolution": {
            "source": "none",
            "date": date,
            "event_count": 0,
            "counts": {},
            "outcome_pending": 0,
            "samples": [],
            "inbox_route": "AGENTS.md section 9.3 topic=selfevolution",
        },
        "decision": "",
        "summary": "",
        "discard_candidates": "- none",
        "inbox_rows": [],
        "hidden_inbox_rows": [],
        "report_inbox_rows": [],
        "proposals": [],
        "metrics": "正在加载今日待确认数据...",
        "appendix": "",
    }


def _render_digest_page_html(date: str) -> str:
    data = json.dumps(daily_review_data(date), ensure_ascii=False).replace("</", "<\\/")
    try:
        intake = cached_cron_intake_data(date)
    except Exception as exc:
        intake = {
            "status": "error",
            "date": date,
            "summary": "cron 承接卡读取失败",
            "warnings": [str(exc)],
            "reports": [],
            "action_items": [],
        }
    intake_data = json.dumps(intake, ensure_ascii=False).replace("</", "<\\/")
    react_entry = STATIC_DIR / "react" / "digest" / "digest.html"
    if react_entry.exists():
        html = react_entry.read_text(encoding="utf-8")
        return (
            html.replace("__TM_DIGEST_JSON__", data)
            .replace("__TM_CRON_INTAKE_JSON__", intake_data)
            .replace("__GIT_SHA__", git_sha())
        )
    return _template().replace("__DIGEST_JSON__", data).replace("__CRON_INTAKE_JSON__", intake_data)


def _render_digest_page(date: str) -> HTMLResponse:
    digest_path = REPO_ROOT / "wiki" / "operations" / f"daily-memory-digest-{date}.md"
    _, source_hash = _file_signature(digest_path) if digest_path.exists() else (None, "missing")
    cache_key = f"page:digest:{date}:{source_hash}"
    cached, _ = _run_cache_get(cache_key, DASHBOARD_PAGE_CACHE_TTL)
    if cached and cached.get("html"):
        return _no_store(HTMLResponse(cached["html"]))

    html = _render_digest_page_html(date)
    _run_cache_set(
        cache_key,
        {"html": html},
        source="digest-page",
        source_path=str(digest_path),
        source_hash=source_hash,
        source_updated_at="",
        ttl_seconds=DASHBOARD_PAGE_CACHE_TTL,
    )
    return _no_store(HTMLResponse(html))


def _health_shell() -> dict[str, Any]:
    return {
        "ok": True,
        "loading": True,
        "generated_at": "正在加载...",
        "dashboard": {"version": VERSION, "git_sha": git_sha(), "port": PORT},
        "services": [],
        "memory_overview": {"ok": True, "wiki_pages": 0, "inbox_pending": 0, "mem0_approximate": None, "trend_7d": []},
        "agent_doctor": {"checks": [], "summary": {"ok_count": 0, "warn_count": 0, "fail_count": 0}},
        "recent_commits": [],
        "daily_digest": {"exists": False, "date": today(), "path": ""},
    }


def _quality_shell() -> dict[str, Any]:
    return {
        "memory": {
            "ok": True,
            "loading": True,
            "date": today(),
            "counts": {},
            "discard_candidates": "- none",
            "trace_latency_supported": True,
            "trace_summary": {"duration_ms": {}, "status_counts": {}, "latest": []},
        },
        "communication": {"status": "loading", "summary": "正在加载记忆质量数据..."},
    }


def self_evolution_data(date: str | None = None) -> dict[str, Any]:
    report_date = date or today()
    if not DATE_RE.fullmatch(report_date):
        raise ValueError("date must be YYYY-MM-DD")
    cache_key = f"api:self-evolution:{report_date}"
    cached, _ = _run_cache_get(cache_key, SELF_EVOLUTION_CACHE_TTL)
    if cached:
        return cached

    start = time.time()
    summary = _build_self_evolution_payload(report_date)
    proposals_payload = tm_self_evolution.build_repeated_event_proposals(report_date, root=REPO_ROOT)
    proposals = proposals_payload.get("proposals", [])
    baseline = dict(tm_self_evolution.build_baseline_snapshot(report_date, root=REPO_ROOT))
    sample_status = baseline.get("sample_status") or ["unknown"]
    event_counts = (baseline.get("events") or {}).get("counts") or {}
    telemetry = baseline.get("telemetry") or {}
    sources = baseline.get("sources") or {}
    event_sources = sources.get("events") or summary.get("sources") or []
    telemetry_sources = sources.get("telemetry") or telemetry.get("sources") or []
    source_warnings: list[str] = []
    external_sources = [
        item for item in [*event_sources, *telemetry_sources]
        if str(item.get("label", "")).startswith("external:")
    ]
    if external_sources and not any(int(item.get("event_count") or item.get("tool_calls") or item.get("session_closes") or 0) for item in external_sources):
        source_warnings.append("已配置外部 self-evolution evidence root，但当前窗口未读到外部事件或遥测。")
    baseline["status"] = "ok" if sample_status == ["ok"] else ",".join(sample_status)
    baseline["counts"] = {
        "total_events": (baseline.get("events") or {}).get("total", 0),
        "hook_blocked": event_counts.get("hook_blocked", 0),
        "lesson_searched": event_counts.get("lesson_searched", 0),
        "handoff_missing": event_counts.get("handoff_missing", 0),
        "tool_calls": telemetry.get("tool_calls", 0),
        "session_closes": telemetry.get("session_closes", 0),
    }
    payload = {
        "ok": True,
        "date": report_date,
        "generated_at": _now_iso(),
        "mode": "propose_only",
        "summary": summary,
        "proposal_summary": {
            "total": len(proposals),
            "eligible": sum(1 for item in proposals if item.get("eligible_for_inbox")),
            "min_repeats": tm_self_evolution.DEFAULT_MIN_REPEATS,
            "min_confidence": tm_self_evolution.DEFAULT_MIN_CONFIDENCE,
        },
        "proposal_run": {
            "run_id": proposals_payload.get("run_id"),
            "window": proposals_payload.get("window", {}),
        },
        "proposals": proposals[:12],
        "baseline": baseline,
        "evidence_sources": {
            "events": event_sources,
            "telemetry": telemetry_sources,
            "env": tm_self_evolution.EXTRA_EVIDENCE_ROOTS_ENV,
        },
        "warnings": source_warnings,
        "errors": [],
        "latency_ms": round((time.time() - start) * 1000, 1),
        "cached": False,
        "stale": False,
        "source": "self-evolution evidence",
        "cache": {
            "hit": False,
            "ttl_seconds": SELF_EVOLUTION_CACHE_TTL,
            "cached_at": _now_iso(),
            "source": "self-evolution evidence",
        },
    }
    _run_cache_set(cache_key, payload, source=payload["source"], ttl_seconds=SELF_EVOLUTION_CACHE_TTL)
    return payload


def _self_evolution_shell() -> dict[str, Any]:
    cached, _ = _run_cache_get(f"api:self-evolution:{today()}", SELF_EVOLUTION_CACHE_TTL)
    if cached:
        return cached
    return {
        "ok": True,
        "loading": True,
        "date": today(),
        "generated_at": "正在加载...",
        "mode": "propose_only",
        "summary": _empty_self_evolution_payload(today()),
        "proposal_summary": {"total": 0, "eligible": 0, "min_repeats": 3, "min_confidence": 0.75},
        "proposals": [],
        "baseline": {"status": "loading", "counts": {}, "rates": {}},
        "evidence_sources": {"events": [], "telemetry": [], "env": tm_self_evolution.EXTRA_EVIDENCE_ROOTS_ENV},
        "warnings": [],
        "errors": [],
    }


def _settings_shell() -> dict[str, Any]:
    return {
        "ok": True,
        "loading": True,
        "path": tm_dashboard_prefs._relpath(PREFS_DB),
        "preferences": dict(tm_dashboard_prefs.DEFAULT_PREFERENCES),
        "defaults": tm_dashboard_prefs.DEFAULT_PREFERENCES,
    }


def _render_placeholder_page(title: str, subtitle: str, active: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return _render_template(
        "placeholder.html",
        {
            "__TITLE__": title,
            "__SUBTITLE__": subtitle,
            "__ACTIVE_PAGE__": active,
            "__PAGE_JSON__": payload,
        },
    )


app = FastAPI()


def _is_cookie_bootstrap_page(path: str) -> bool:
    return path == "/" or any(path == prefix or path.startswith(prefix) for prefix in PAGE_PREFIXES)


@app.middleware("http")
async def guard_request(request: Request, call_next):
    global LAST_REQUEST_AT
    LAST_REQUEST_AT = time.time()
    host = request.headers.get("host", "").lower()
    if host and host not in ALLOWED_HOSTS:
        return JSONResponse({"ok": False, "error": "forbidden host"}, status_code=403)
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static/"):
        return await call_next(request)
    if _token_matches(_authorization_token(request)):
        return await call_next(request)
    query_token = request.query_params.get("token")
    if _token_matches(query_token) and request.method == "GET" and _is_cookie_bootstrap_page(request.url.path):
        try:
            redirect_url = str(request.url.remove_query_params(["token"]))
        except AttributeError:  # pragma: no cover - compatibility guard
            redirect_url = request.url.path
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.set_cookie(COOKIE_NAME, _ensure_token(), httponly=True, samesite="lax")
        return response
    token = _ensure_token()
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie != token:
        if _can_bootstrap_cookie(request) and request.method == "GET" and _is_cookie_bootstrap_page(request.url.path):
            if request.url.path == "/":
                response = RedirectResponse(url="/digest", status_code=302)
            else:
                response = await call_next(request)
            response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
            return response
        return JSONResponse({"ok": False, "error": "missing or invalid session"}, status_code=401)
    return await call_next(request)


@app.get("/")
async def root():
    return RedirectResponse(url="/digest", status_code=302)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": VERSION, "git_sha": git_sha(), "port": PORT}


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "tiger" / "tigerlogo.png", media_type="image/png")


@app.get("/service-worker.js")
async def service_worker():
    return _no_store(FileResponse(STATIC_DIR / "service-worker.js", media_type="application/javascript"))


@app.get("/sw-reset")
async def sw_reset():
    html = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>TigerMemory 缓存重置</title></head>
<body style="font-family: sans-serif; padding: 24px;">
<h1>正在刷新 TigerMemory 今日待确认缓存</h1>
<p>页面会自动清理旧 service worker 和旧缓存，然后返回今日待确认。</p>
<script>
(async () => {
  try {
    const registrations = await navigator.serviceWorker?.getRegistrations?.() || [];
    await Promise.all(registrations.map(reg => reg.unregister()));
    if (window.caches) {
      const keys = await caches.keys();
      await Promise.all(keys.map(key => caches.delete(key)));
    }
  } finally {
    location.replace('/digest');
  }
})();
</script>
</body>
</html>"""
    return _no_store(HTMLResponse(html))


@app.get("/offline.html")
async def offline_page():
    return FileResponse(STATIC_DIR / "offline.html", media_type="text/html")


@app.get("/static/{path:path}")
async def static_file(path: str):
    candidate = (STATIC_DIR / path).resolve()
    try:
        candidate.relative_to(STATIC_DIR)
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid static path"}, status_code=404)
    if not candidate.exists() or not candidate.is_file():
        return JSONResponse({"ok": False, "error": "static file not found"}, status_code=404)
    return FileResponse(candidate)


@app.get("/daily")
async def daily_today():
    return RedirectResponse(url="/digest", status_code=302)


@app.get("/daily/{date}")
async def daily_date(date: str):
    return RedirectResponse(url=f"/digest/{date}", status_code=302)


@app.get("/start")
async def start_page():
    return _no_store(HTMLResponse(_render_start_page(_start_shell())))


@app.get("/review")
async def review_page():
    return _no_store(HTMLResponse(_template()))


@app.get("/ledger")
async def ledger_page():
    return _no_store(HTMLResponse(_render_ledger_page()))


@app.get("/api/ledger/health")
async def api_ledger_health():
    module = _require_tigerledger_review()
    return await run_in_threadpool(module.health)


@app.get("/api/ledger/review/summary")
async def api_ledger_review_summary(month: str | None = None):
    module = _require_tigerledger_review()
    return await run_in_threadpool(module.review_summary, month)


@app.get("/api/ledger/review/entries")
async def api_ledger_review_entries(
    month: str | None = None,
    status: str = Query("pending", pattern="^(pending|approved|skipped|deleted|all)$"),
    kind: str | None = Query(None, pattern="^(expense|income)$"),
    category: str | None = None,
    source_agent: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    module = _require_tigerledger_review()
    return await run_in_threadpool(
        module.review_entries,
        month=month,
        status=status,
        kind=kind,
        category=category,
        source_agent=source_agent,
        q=q,
        limit=limit,
        offset=offset,
    )


@app.post("/api/ledger/review/entries/{entry_id}/approve")
async def api_ledger_approve_entry(entry_id: int):
    module = _require_tigerledger_review()
    return await run_in_threadpool(module.approve_entry, entry_id)


@app.post("/api/ledger/review/entries/{entry_id}/skip")
async def api_ledger_skip_entry(entry_id: int, payload: dict[str, Any] | None = Body(default=None)):
    module = _require_tigerledger_review()
    try:
        req = module.EntrySkipRequest(**(payload or {}))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await run_in_threadpool(module.skip_entry, entry_id, req)


@app.post("/api/ledger/review/entries/{entry_id}/restore")
async def api_ledger_restore_entry(entry_id: int):
    module = _require_tigerledger_review()
    return await run_in_threadpool(module.restore_entry, entry_id)


@app.post("/api/ledger/review/entries/{entry_id}/edit")
async def api_ledger_edit_entry(entry_id: int, payload: dict[str, Any] | None = Body(default=None)):
    module = _require_tigerledger_review()
    try:
        req = module.EntryUpdateRequest(**(payload or {}))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await run_in_threadpool(module.edit_entry, entry_id, req)


@app.get("/api/health/summary")
async def api_health_summary():
    try:
        return await run_in_threadpool(dashboard_health_summary)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/update/status")
async def api_update_status():
    try:
        return await run_in_threadpool(dashboard_update_status)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/health/memory-overview")
async def api_health_memory_overview():
    try:
        return await run_in_threadpool(dashboard_memory_overview)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/runtime/events")
async def api_runtime_events(days: int = 1, limit: int = 50):
    try:
        bounded_days = max(1, min(int(days), 30))
        bounded_limit = max(0, min(int(limit), 200))
        today_date = dt.datetime.now(tm_core.TZ_CN).date()
        dates = [(today_date - dt.timedelta(days=offset)).isoformat() for offset in range(bounded_days)]
        events = await run_in_threadpool(tm_runtime_events.load_events, dates=dates, limit=bounded_limit)
        summary = tm_runtime_events.summarize_events(events, dates=dates)
        return {"ok": True, "summary": summary, "events": events}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _fetch_investment_dashboard_json(source_url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(source_url, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {"ok": False, "error": "unexpected payload"}
    except Exception as url_exc:
        powershell = pathlib.Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if not powershell.exists():
            powershell = pathlib.Path("powershell.exe")
        ps_command = (
            "$ProgressPreference='SilentlyContinue'; "
            f"Invoke-RestMethod -Uri '{source_url}' -TimeoutSec 5 | ConvertTo-Json -Compress -Depth 8"
        )
        try:
            raw = subprocess.check_output(
                [str(powershell), "-NoProfile", "-Command", ps_command],
                text=True,
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.STDOUT,
                timeout=8,
            )
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {"ok": False, "error": "unexpected powershell payload"}
        except Exception as ps_exc:
            return {"ok": False, "error": f"{url_exc}; powershell fallback: {ps_exc}"}


def _investment_trading_node_status() -> dict[str, Any]:
    source_url = "http://127.0.0.1:8888/api/trading-node/status"
    raw = _fetch_investment_dashboard_json(source_url)
    if not raw.get("ok"):
        return {
            "ok": False,
            "status": "unreachable",
            "error": str(raw.get("error") or "investment dashboard unreachable"),
            "source": source_url,
            "dashboard_url": "http://127.0.0.1:8888/#miniqmt",
        }
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return {
            "ok": False,
            "status": "invalid",
            "error": "investment dashboard returned unexpected payload",
            "source": source_url,
            "dashboard_url": "http://127.0.0.1:8888/#miniqmt",
        }
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    adapter = data.get("adapter") if isinstance(data.get("adapter"), dict) else {}
    health = adapter.get("health") if isinstance(adapter.get("health"), dict) else {}
    tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
    return {
        "ok": bool(raw.get("ok", True)),
        "status": data.get("status") or "unknown",
        "date": data.get("date"),
        "account_scope": "B_qmt MiniQMT simulation",
        "dashboard_url": "http://127.0.0.1:8888/#miniqmt",
        "source": source_url,
        "blockers": data.get("blockers") if isinstance(data.get("blockers"), list) else [],
        "summary": {
            "intent_count": summary.get("intent_count"),
            "active_order_count": summary.get("active_order_count"),
            "order_count": summary.get("order_count"),
            "trade_count": summary.get("trade_count"),
            "reconcile_failed_count": summary.get("reconcile_failed_count"),
        },
        "adapter": {
            "ok": health.get("ok"),
            "simulation_mode": health.get("simulation_mode"),
            "query_ready": health.get("query_ready"),
            "can_place_orders": (health.get("capabilities") or {}).get("can_place_orders")
            if isinstance(health.get("capabilities"), dict)
            else None,
            "can_cancel_orders": (health.get("capabilities") or {}).get("can_cancel_orders")
            if isinstance(health.get("capabilities"), dict)
            else None,
        },
        "tasks": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "last_result": item.get("last_result"),
                "next_run_time": item.get("next_run_time"),
            }
            for item in tasks
            if isinstance(item, dict)
        ],
    }


@app.get("/api/investment/trading-node/status")
async def api_investment_trading_node_status():
    return await run_in_threadpool(_investment_trading_node_status)


@app.get("/api/quality/memory")
async def api_quality_memory(date: Optional[str] = None, range_key: Optional[str] = Query(None, alias="range")):
    try:
        if range_key is not None:
            return await run_in_threadpool(dashboard_memory_quality, date, range_key)
        return await run_in_threadpool(dashboard_memory_quality, date)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/quality/communication")
async def api_quality_communication():
    return {
        "ok": True,
        "status": "not_enabled",
        "summary": "沟通质量 Tab v1 只占位；渐进降频计数器和输出 lint 规则待后续阶段启用。",
    }


@app.get("/api/self-evolution/{date}")
async def api_self_evolution(date: str):
    try:
        return await run_in_threadpool(self_evolution_data, date)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/settings/preferences")
async def api_get_preferences():
    try:
        return await run_in_threadpool(get_user_preferences)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/settings/preferences")
async def api_update_preferences(req: PreferenceUpdateRequest):
    try:
        return update_user_preferences(req.preferences, propose_wiki=req.propose_wiki)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/start/llm-status")
async def api_start_llm_status():
    try:
        return await run_in_threadpool(tm_llm_status.llm_status_payload, REPO_ROOT)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/start/llm-test")
async def api_start_llm_test(req: StartLlmConfigRequest):
    try:
        return await run_in_threadpool(test_start_llm_config, req)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/start/llm-config")
async def api_start_llm_config(req: StartLlmConfigRequest):
    try:
        return await run_in_threadpool(save_start_llm_config, req)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/start/agent-connect/status")
async def api_start_agent_connect_status():
    try:
        return await run_in_threadpool(_agent_connect_status_payload)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "targets": []}


@app.post("/api/start/agent-connect/apply")
async def api_start_agent_connect_apply(req: StartAgentConnectRequest):
    try:
        return await run_in_threadpool(apply_start_agent_connect, req)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "targets": []}


# =====================================================================
# 🔌 【只读接口】智能体接入与体检板块 (Agent Tools) APIs & Pages
# =====================================================================

@app.get("/agent-tools")
async def agent_tools_page():
    """渲染并返回智能体接入与体检 Dashboard v2 主页面"""
    return HTMLResponse(_render_template("agent-tools.html", {}))


@app.get("/api/agent/status")
async def api_agent_status(request: Request):
    """
    【只读接口】探测本地 Cursor 与 Claude Desktop 的连接状态。
    探测配置文件中是否已经注入了 tigermemory 连接，100% 零写操作，安全防弹。
    """
    import platform

    system_os = platform.system()
    try:
        import tm_agent_connect
    except Exception as exc:
        empty = {"exists": False, "connected": False, "path": "", "error": str(exc)}
        return {
            "ok": True,
            "os": system_os,
            "cursor": dict(empty),
            "claude": dict(empty),
            "warning": "agent connection helper is not available in this snapshot",
        }

    paths = tm_agent_connect.detect_config_paths()
    cursor_path = paths.get("cursor")
    claude_path = paths.get("claude_desktop")

    cursor_info = {"exists": False, "connected": False, "path": ""}
    try:
        if cursor_path and cursor_path.exists():
            cursor_info["exists"] = True
            cursor_info["path"] = str(cursor_path)
            content = json.loads(cursor_path.read_text(encoding="utf-8"))
            if "mcpServers" in content and "tigermemory" in content["mcpServers"]:
                cursor_info["connected"] = True
    except Exception as exc:
        cursor_info["error"] = str(exc)

    claude_info = {"exists": False, "connected": False, "path": ""}
    try:
        if claude_path and claude_path.exists():
            claude_info["exists"] = True
            claude_info["path"] = str(claude_path)
            content = json.loads(claude_path.read_text(encoding="utf-8"))
            if "mcpServers" in content and "tigermemory" in content["mcpServers"]:
                claude_info["connected"] = True
    except Exception as exc:
        claude_info["error"] = str(exc)

    return {
        "ok": True,
        "os": system_os,
        "cursor": cursor_info,
        "claude": claude_info
    }


@app.get("/api/agent/recent-activity")
async def api_agent_recent_activity():
    try:
        return await run_in_threadpool(dashboard_agent_recent_activity)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "items": []}


@app.get("/api/agent/doctor")
async def api_agent_doctor(skip_l2: bool = True):
    """
    【只读接口】调用 tm_agent_doctor 核心检查函数进行一键体检。
    默认跳过慢速出站的 L2 评测以加快控制台前端加载响应。
    """
    import tm_agent_doctor
    try:
        report = await run_in_threadpool(
            tm_agent_doctor.run_agent_doctor,
            query="dashboard health status",
            include_l2=not skip_l2
        )
        return {"ok": True, "report": report}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/development-supervisor/status")
async def api_development_supervisor_status():
    try:
        return await run_in_threadpool(_dashboard_development_supervisor_status)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _dashboard_agent_eval_payload(skip_mem0: bool) -> dict[str, Any]:
    import tm_eval_runner

    cases = tm_eval_runner.load_or_create_eval_suite("default")
    if not cases:
        return {"ok": False, "error": "评测集用例为空"}

    results = []
    wiki_ranks = []
    wiki_durations = []
    mem0_matches = []
    mem0_durations = []
    mem0_active = not skip_mem0

    for case in cases:
        wiki_eval = tm_eval_runner.run_wiki_eval(case)
        wiki_rank, wiki_ms = wiki_eval[:2]
        wiki_degraded = bool(wiki_eval[2]) if len(wiki_eval) > 2 else False
        wiki_ranks.append(wiki_rank)
        wiki_durations.append(wiki_ms)

        if mem0_active:
            matched, mem0_ms = tm_eval_runner.run_mem0_eval(case)
            if mem0_ms == 0.0:
                mem0_active = False
        else:
            matched, mem0_ms = False, 0.0
        mem0_matches.append(matched)
        mem0_durations.append(mem0_ms)

        results.append({
            "id": case["id"],
            "description": case["description"],
            "query": case["query"],
            "wiki_rank": wiki_rank,
            "wiki_latency_ms": round(wiki_ms, 1),
            "wiki_degraded": wiki_degraded,
            "mem0_match": matched,
            "mem0_latency_ms": round(mem0_ms, 1) if mem0_active else 0.0,
            "mem0_status": "SUCCESS" if matched else ("FAILED" if mem0_active else "OFFLINE"),
        })

    total = len(cases)
    wiki_recall_1 = sum(1 for r in wiki_ranks if r == 1) / total
    wiki_recall_3 = sum(1 for r in wiki_ranks if 0 < r <= 3) / total
    wiki_recall_5 = sum(1 for r in wiki_ranks if 0 < r <= 5) / total
    avg_wiki_latency = sum(wiki_durations) / total
    mem0_accuracy = sum(1 for m in mem0_matches if m) / total if mem0_active else 0.0
    avg_mem0_latency = sum(mem0_durations) / total if mem0_active else 0.0

    return {
        "ok": True,
        "total_cases": total,
        "results": results,
        "wiki": {
            "recall_1": round(wiki_recall_1, 3),
            "recall_3": round(wiki_recall_3, 3),
            "recall_5": round(wiki_recall_5, 3),
            "avg_latency_ms": round(avg_wiki_latency, 1),
        },
        "mem0": {
            "active": mem0_active,
            "accuracy": round(mem0_accuracy, 3) if mem0_active else 0.0,
            "avg_latency_ms": round(avg_mem0_latency, 1) if mem0_active else 0.0,
        },
    }


@app.get("/api/agent/eval")
async def api_agent_eval(skip_mem0: bool = True):
    """
    【只读接口】在后台调用 tm_eval_runner 执行 Wiki FTS 和 Mem0 的双通道召回评测，
    并把科学的统计指标和用例细节转换为纯净的 JSON 返回。
    """
    try:
        return await run_in_threadpool(_dashboard_agent_eval_payload, skip_mem0)
    except ImportError as exc:
        return {
            "ok": False,
            "error": f"评测模块不可用：{exc}",
            "hint": "请检查 tigermemory_eval 包是否已安装到 dashboard 运行环境",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/health")
async def health_page():
    return _no_store(HTMLResponse(_render_health_page(_health_shell())))


@app.get("/quality")
async def quality_page():
    return _no_store(HTMLResponse(_render_quality_page(_quality_shell())))


@app.get("/canvas")
async def canvas_page():
    if CANVAS_SOURCE_PATH.exists():
        _, source_hash = _file_signature(CANVAS_SOURCE_PATH)
    else:
        source_hash = "missing"
    cache_key = f"page:canvas:{source_hash}"
    cached, _ = _run_cache_get(cache_key, DASHBOARD_PAGE_CACHE_TTL)
    if cached and cached.get("html"):
        return _no_store(HTMLResponse(cached["html"]))

    html = _render_canvas_page(_load_canvas_payload())
    _run_cache_set(
        cache_key,
        {"html": html},
        source="canvas-page",
        source_path=str(CANVAS_SOURCE_PATH),
        source_hash=source_hash,
        source_updated_at="",
        ttl_seconds=DASHBOARD_PAGE_CACHE_TTL,
    )
    return _no_store(HTMLResponse(html))


@app.get("/api/canvas")
async def api_canvas():
    try:
        return await run_in_threadpool(_load_canvas_payload)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/self-evolution")
async def self_evolution_page():
    return _no_store(HTMLResponse(_render_self_evolution_page(_self_evolution_shell())))


@app.get("/settings")
async def settings_page():
    return _no_store(HTMLResponse(_render_settings_page(_settings_shell())))


@app.get("/api/digest/{date}")
async def api_digest(date: str):
    try:
        if not DATE_RE.fullmatch(date):
            raise ValueError("date must be YYYY-MM-DD")
        digest = await run_in_threadpool(daily_review_data, date)
        return _no_store(JSONResponse({"ok": True, "digest": digest}))
    except Exception as exc:
        return _no_store(JSONResponse({"ok": False, "error": str(exc)}, status_code=404))


@app.get("/api/cron/intake/{date}")
async def api_cron_intake(date: str):
    try:
        intake = await run_in_threadpool(cached_cron_intake_data, date)
        return _no_store(JSONResponse({"ok": True, "intake": intake}))
    except Exception as exc:
        return _no_store(JSONResponse({"ok": False, "error": str(exc)}, status_code=400))


@app.get("/digest")
async def digest_entry():
    date = today()
    return await run_in_threadpool(_render_digest_page, date)


@app.get("/digest/{date}")
async def digest_page(date: str):
    if not DATE_RE.fullmatch(date):
        return _no_store(HTMLResponse(f"<h1>Digest unavailable</h1><pre>date must be YYYY-MM-DD</pre>", status_code=404))
    return await run_in_threadpool(_render_digest_page, date)


@app.post("/api/inbox/action")
async def api_inbox_action(req: InboxActionRequest):
    try:
        return await run_in_threadpool(_locked_write_action, execute_inbox_action, req)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/batch/archive-stale")
async def api_batch_archive(req: BatchArchiveRequest):
    try:
        return await run_in_threadpool(_locked_write_action, execute_batch_archive, req.date)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/inbox/batch-action")
async def api_inbox_batch_action(req: BatchInboxActionRequest):
    try:
        return await run_in_threadpool(_locked_write_action, execute_batch_inbox_action, req)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/proposal/apply")
async def api_proposal_apply(req: ProposalApplyRequest):
    try:
        return await run_in_threadpool(_locked_write_action, proposal_apply, req.date, req.proposal_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/proposal/reject")
async def api_proposal_reject(req: ProposalRejectRequest):
    try:
        return await run_in_threadpool(_locked_write_action, proposal_reject, req.date, req.proposal_id, req.reason)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def maybe_idle_exit() -> None:
    if time.time() - LAST_REQUEST_AT > IDLE_EXIT_SECONDS:
        print("idle exit", file=sys.stderr)
        os._exit(0)


def start_idle_watcher() -> None:
    def watch() -> None:
        while True:
            time.sleep(60)
            maybe_idle_exit()

    threading.Thread(target=watch, daemon=True).start()


def _dashboard_background_enabled() -> bool:
    return os.getenv("TM_DASHBOARD_BACKGROUND", "1").strip().lower() not in {"0", "false", "no", "off"}


def _warm_quality_cache_once() -> dict[str, Any]:
    start = time.monotonic()
    report_date = today()
    warmed: list[dict[str, Any]] = []
    for range_key in ("today", "7d", "30d"):
        if range_key == "today":
            payload = dashboard_memory_quality(report_date)
        else:
            payload = dashboard_memory_quality(report_date, range_key)
        warmed.append({
            "range": range_key,
            "ok": bool(payload.get("ok")),
            "cached": payload.get("cached", False),
            "latency_ms": payload.get("latency_ms"),
        })
    return {
        "ok": all(item["ok"] for item in warmed),
        "date": report_date,
        "cached": all(item["cached"] for item in warmed),
        "ranges": warmed,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
    }


def start_quality_cache_warmer(interval_seconds: float | None = None) -> bool:
    global _QUALITY_CACHE_WARMER_STARTED
    if os.getenv("PYTEST_CURRENT_TEST") or not _dashboard_background_enabled():
        return False
    if tm_memory_reflection is None or not hasattr(tm_memory_reflection, "audit_inbox"):
        return False
    interval = max(5.0, float(interval_seconds or QUALITY_CACHE_WARM_INTERVAL))
    with _QUALITY_CACHE_WARMER_LOCK:
        if _QUALITY_CACHE_WARMER_STARTED:
            return False
        _QUALITY_CACHE_WARMER_STARTED = True

    def warm_loop() -> None:
        while True:
            try:
                result = _warm_quality_cache_once()
                try:
                    tm_runtime_events.record_event(
                        event_type="quality_cache_warm",
                        service="tm-dashboard",
                        component="quality_cache",
                        ok=bool(result.get("ok")),
                        severity="info" if result.get("ok") else "warn",
                        duration_ms=result.get("latency_ms"),
                        outcome="cached" if result.get("cached") else "refreshed",
                        extra={
                            "date": result.get("date"),
                            "cached": result.get("cached"),
                            "ranges": result.get("ranges"),
                        },
                        source_log="systemd:tm-dashboard.service",
                    )
                except Exception:
                    pass
                print(
                    f"quality cache warm ok={result['ok']} date={result.get('date')} "
                    f"cached={result.get('cached')} latency_ms={result['latency_ms']}",
                    file=sys.stderr,
                )
            except Exception as exc:
                try:
                    tm_runtime_events.record_event(
                        event_type="quality_cache_warm",
                        service="tm-dashboard",
                        component="quality_cache",
                        ok=False,
                        severity="error",
                        error=str(exc),
                        source_log="systemd:tm-dashboard.service",
                    )
                except Exception:
                    pass
                print(f"quality cache warm failed: {exc}", file=sys.stderr)
            time.sleep(interval)

    threading.Thread(target=warm_loop, daemon=True, name="quality-cache-warmer").start()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run tigermemory Memory Ops dashboard")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-open", action="store_true", help="start the dashboard without opening the browser")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        print("dashboard only binds 127.0.0.1/localhost/0.0.0.0", file=sys.stderr)
        return 2
    import uvicorn

    register_dashboard_bind_host(args.host, args.port)
    start_idle_watcher()
    start_quality_cache_warmer()
    open_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    start_url = f"http://{open_host}:{args.port}/start"
    print(f"dashboard_url={start_url}")
    if args.no_open:
        print("browser=disabled")
    else:
        print("browser=opening")

        def open_start_page() -> None:
            time.sleep(0.8)
            try:
                webbrowser.open(start_url)
            except Exception:
                pass

        threading.Thread(target=open_start_page, daemon=True, name="dashboard-start-opener").start()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
