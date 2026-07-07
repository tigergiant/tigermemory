#!/usr/bin/env python3
"""Append-only route outcome ledger for write_memory.

The discard quarantine keeps reviewable excerpts for dropped content. This
ledger is different: it records every final route outcome without copying the
raw memory text, so dashboard counters can be traced back to write-time facts.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
from typing import Any

import tigermemory_core as tm_core
from tigermemory_core import runtime_events as tm_runtime_events
import tm_route

REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_EVENT_ROOT = REPO_ROOT / ".tmp" / "memory-route-events"

ROUTE_TO_FLOW = {
    "mem0": "mem0",
    "wiki_proposal": "wiki",
    "wiki": "wiki",
    "human_review": "inbox",
    "retry_error": "inbox",
    "inbox": "inbox",
    "discard": "discard",
}


def _now_local() -> dt.datetime:
    return dt.datetime.now(tm_core.TZ_CN)


def _event_date(now: dt.datetime | None = None) -> str:
    return (now or _now_local()).astimezone(tm_core.TZ_CN).strftime("%Y-%m-%d")


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _relpath(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _write_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    tm_runtime_events.append_jsonl_row(
        path,
        row,
        timeout_env="TM_ROUTE_EVENTS_LOCK_TIMEOUT_SEC",
        stale_env="TM_ROUTE_EVENTS_LOCK_STALE_SEC",
    )


def _event_root(event_root: pathlib.Path | str | None = None) -> pathlib.Path:
    if event_root is not None:
        return pathlib.Path(event_root)
    env_root = os.getenv("TM_ROUTE_EVENTS_ROOT")
    if env_root:
        return pathlib.Path(env_root)
    return DEFAULT_EVENT_ROOT


def _disabled(event_root: pathlib.Path | str | None = None) -> bool:
    disabled = os.getenv("TM_ROUTE_EVENTS_DISABLED", "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return True
    # Unit tests must not leak synthetic routing failures into the real repo
    # ledger unless the test explicitly points the ledger at a temporary root.
    return (
        event_root is None
        and bool(os.getenv("PYTEST_CURRENT_TEST"))
        and not bool(os.getenv("TM_ROUTE_EVENTS_ROOT"))
    )


def _target_ref(result: dict[str, Any]) -> dict[str, Any]:
    ref: dict[str, Any] = {}
    for key in ("id", "path", "commit_sha", "url"):
        value = result.get(key)
        if isinstance(value, str) and value:
            ref[key] = value
    return ref


def record_route_event(
    *,
    agent: str,
    requested_topic: str,
    storage_topic: str,
    text: str,
    decision: tm_route.RouteDecision,
    result: dict[str, Any],
    outcome: str,
    source: str = "write_memory",
    event_root: pathlib.Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Append one final route outcome and return lightweight file metadata."""
    now = (now or _now_local()).astimezone(tm_core.TZ_CN)
    date = _event_date(now)
    if _disabled(event_root):
        route = str(result.get("route") or decision.route or "").strip().lower() or "unknown"
        final_outcome = str(outcome or result.get("outcome") or route).strip().lower() or route
        flow_target = ROUTE_TO_FLOW.get(final_outcome) or ROUTE_TO_FLOW.get(route) or "inbox"
        return {
            "ok": False,
            "skipped": "disabled",
            "date": date,
            "outcome": final_outcome,
            "flow_target": flow_target,
        }
    root = _event_root(event_root)
    route = str(result.get("route") or decision.route or "").strip().lower() or "unknown"
    final_outcome = str(outcome or result.get("outcome") or route).strip().lower() or route
    flow_target = ROUTE_TO_FLOW.get(final_outcome) or ROUTE_TO_FLOW.get(route) or "inbox"
    event_id = _sha12(
        f"{now.isoformat()}|{agent}|{requested_topic}|{storage_topic}|"
        f"{final_outcome}|{_sha12(text)}"
    )
    row = {
        "event_id": event_id,
        "ts": now.isoformat(),
        "date": date,
        "source": source,
        "agent": agent,
        "requested_topic": requested_topic,
        "stored_topic": storage_topic,
        "topic_inferred": decision.topic_inferred,
        "route": route,
        "outcome": final_outcome,
        "flow_target": flow_target,
        "knowledge_target": decision.knowledge_target,
        "target_confidence": decision.target_confidence,
        "score": decision.score,
        "is_transient": decision.is_transient,
        "is_sensitive": decision.is_sensitive,
        "needs_human_review": decision.needs_human_review,
        "unreviewed": decision.unreviewed,
        "issues": decision.issues,
        "reasons": decision.reasons,
        "review_reason": decision.review_reason,
        "wiki_partition": decision.wiki_partition,
        "wiki_slug_hint": decision.wiki_slug_hint,
        "wiki_action": decision.wiki_action,
        "text_len": len(text),
        "text_sha256_12": _sha12(text),
        "target_ref": _target_ref(result),
    }
    path = root / date / "events.jsonl"
    _write_jsonl(path, row)
    return {
        "ok": True,
        "event_id": event_id,
        "date": date,
        "outcome": final_outcome,
        "flow_target": flow_target,
        "path": _relpath(path),
    }


def load_route_events(
    *,
    dates: list[str] | set[str] | tuple[str, ...],
    event_root: pathlib.Path | None = None,
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
    return rows


def summarize_route_events(
    events: list[dict[str, Any]],
    *,
    dates: list[str] | set[str] | tuple[str, ...],
    event_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    flow_counts = {"mem0": 0, "wiki": 0, "inbox": 0, "discard": 0}
    outcome_counts: dict[str, int] = {}
    agent_counts: dict[str, int] = {}
    event_dates: set[str] = set()
    for row in events:
        date = str(row.get("date") or "").strip()
        if date:
            event_dates.add(date)
        target = str(row.get("flow_target") or "").strip().lower()
        if target in flow_counts:
            flow_counts[target] += 1
        outcome = str(row.get("outcome") or row.get("route") or "unknown")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        agent = str(row.get("agent") or "unknown")
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
    requested_dates = sorted({str(item) for item in dates if item})
    root = _event_root(event_root)
    return {
        "event_count": len(events),
        "flow_counts": flow_counts,
        "outcome_counts": outcome_counts,
        "agent_counts": agent_counts,
        "dates_with_events": sorted(event_dates),
        "requested_dates": requested_dates,
        "missing_event_dates": [date for date in requested_dates if date not in event_dates],
        "source": _relpath(root),
    }
