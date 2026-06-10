"""Unified append-only runtime event ledger for TigerMemory services.

This ledger is a high-level system timeline. It stores operational outcomes
and references only; callers must not use it as a raw content archive.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
from typing import Any

from . import REPO_ROOT, TZ_CN

DEFAULT_EVENT_ROOT = REPO_ROOT / ".tmp" / "runtime-events"

_RAW_VALUE_KEYS = {
    "body",
    "content",
    "message",
    "prompt",
    "query",
    "raw",
    "request",
    "response",
    "text",
}
_SECRET_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "key",
    "password",
    "secret",
    "token",
}
_MAX_STRING = 500


def _now_local() -> dt.datetime:
    return dt.datetime.now(TZ_CN)


def _date_key(value: dt.datetime | None = None) -> str:
    return (value or _now_local()).astimezone(TZ_CN).strftime("%Y-%m-%d")


def _sha12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _event_root(event_root: pathlib.Path | str | None = None) -> pathlib.Path:
    if event_root is not None:
        return pathlib.Path(event_root)
    env_root = os.getenv("TM_RUNTIME_EVENTS_ROOT")
    if env_root:
        return pathlib.Path(env_root)
    return DEFAULT_EVENT_ROOT


def _disabled() -> bool:
    disabled = os.getenv("TM_RUNTIME_EVENTS_DISABLED", "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return True
    # Unit tests should not leak events into the real repo unless a test points
    # the ledger at a temporary root explicitly.
    return bool(os.getenv("PYTEST_CURRENT_TEST")) and not bool(os.getenv("TM_RUNTIME_EVENTS_ROOT"))


def relpath(path: pathlib.Path | str) -> str:
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(candidate).replace("\\", "/")


def _sanitize(value: Any, key: str | None = None) -> Any:
    normalized_key = (key or "").strip().lower()
    if normalized_key in _SECRET_KEYS or any(part in _SECRET_KEYS for part in normalized_key.split("_")):
        return "[redacted]"
    if isinstance(value, str):
        if normalized_key in _RAW_VALUE_KEYS:
            return {"len": len(value), "sha256_12": _sha12(value)}
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "...[truncated]"
    if isinstance(value, pathlib.Path):
        return relpath(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            child_key = str(raw_key)
            out[child_key] = _sanitize(raw_value, child_key)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, key) for item in list(value)[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_STRING]


def _write_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def record_event(
    *,
    event_type: str,
    service: str,
    component: str | None = None,
    ok: bool = True,
    severity: str | None = None,
    trace_id: str | None = None,
    duration_ms: float | None = None,
    actor: str | None = None,
    agent: str | None = None,
    route: str | None = None,
    outcome: str | None = None,
    target_ref: dict[str, Any] | None = None,
    source_log: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
    event_root: pathlib.Path | str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Append a unified runtime event.

    The function is best-effort for callers, but direct users still get
    exceptions if filesystem writes fail. Wrap it where events must not affect
    user-facing work.
    """
    if _disabled():
        return {"ok": False, "skipped": "disabled"}
    now = (now or _now_local()).astimezone(TZ_CN)
    date = _date_key(now)
    root = _event_root(event_root)
    clean_extra = _sanitize(extra or {})
    clean_ref = _sanitize(target_ref or {})
    clean_error = _sanitize(error, "error") if error else None
    base = "|".join([
        now.isoformat(),
        service,
        component or "",
        event_type,
        trace_id or "",
        agent or "",
        route or "",
        outcome or "",
    ])
    event_id = _sha12(base)
    level = severity or ("info" if ok else "error")
    row = {
        "event_id": event_id,
        "ts": now.isoformat(),
        "date": date,
        "service": service,
        "component": component,
        "event_type": event_type,
        "severity": level,
        "ok": bool(ok),
        "trace_id": trace_id,
        "duration_ms": duration_ms,
        "actor": actor,
        "agent": agent,
        "route": route,
        "outcome": outcome,
        "target_ref": clean_ref,
        "source_log": source_log,
        "error": clean_error,
        "extra": clean_extra,
    }
    path = root / date / "events.jsonl"
    _write_jsonl(path, row)
    return {
        "ok": True,
        "event_id": event_id,
        "date": date,
        "path": relpath(path),
    }


def load_events(
    *,
    dates: list[str] | tuple[str, ...] | set[str],
    event_root: pathlib.Path | str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    root = _event_root(event_root)
    rows: list[dict[str, Any]] = []
    for date in sorted({str(item) for item in dates if item}):
        path = root / date / "events.jsonl"
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    rows.sort(key=lambda item: str(item.get("ts") or ""))
    if limit is not None and limit >= 0:
        return rows[-limit:]
    return rows


def summarize_events(
    events: list[dict[str, Any]],
    *,
    dates: list[str] | tuple[str, ...] | set[str],
    event_root: pathlib.Path | str | None = None,
) -> dict[str, Any]:
    service_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    ok_counts = {"ok": 0, "failed": 0}
    event_dates: set[str] = set()
    for row in events:
        date = str(row.get("date") or "").strip()
        if date:
            event_dates.add(date)
        service = str(row.get("service") or "unknown")
        event_type = str(row.get("event_type") or "unknown")
        severity = str(row.get("severity") or "info")
        service_counts[service] = service_counts.get(service, 0) + 1
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        ok_counts["ok" if row.get("ok") else "failed"] += 1
    requested_dates = sorted({str(item) for item in dates if item})
    root = _event_root(event_root)
    return {
        "event_count": len(events),
        "service_counts": service_counts,
        "type_counts": type_counts,
        "severity_counts": severity_counts,
        "ok_counts": ok_counts,
        "dates_with_events": sorted(event_dates),
        "requested_dates": requested_dates,
        "missing_event_dates": [date for date in requested_dates if date not in event_dates],
        "source": relpath(root),
    }
