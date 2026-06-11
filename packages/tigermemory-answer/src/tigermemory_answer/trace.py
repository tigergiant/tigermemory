#!/usr/bin/env python3
"""Inspect and replay memory_answer trace JSONL records.

This module provides the trace CLI and reusable helpers for loading
memory_answer trace rows, summarizing status/latency, replaying one trace, and
listing recent failures. Query text is redacted through tigermemory_answer's
shared secret-redaction helper before any replay output exposes it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import tigermemory_core as tm_core
from tigermemory_answer import TRACE_LOG, redact_secrets


DEFAULT_FAILURE_STATUSES = ("error", "conflict", "not_found")
FEEDBACK_SCHEMA_VERSION = "memory-answer-feedback-v1"
FEEDBACK_SUMMARY_SCHEMA_VERSION = "memory-answer-feedback-summary-v1"
FEEDBACK_LOG = tm_core.REPO_ROOT / "runtime" / "memory_answer_feedback" / "events.jsonl"
FEEDBACK_ACTION_TOKENS = {"clicked", "ignored", "selected"}
FEEDBACK_SURFACE_TOKENS = {"cli", "dashboard", "review_ui", "unknown"}
FEEDBACK_SCORE_BUCKET_TOKENS = {"high", "mid", "low", "unknown"}
FEEDBACK_USE_HINT_TOKENS = {"background_only", "candidate_for_evidence", "read_next", "unknown"}
RECOMMENDATION_STATUS_TOKENS = {
    "error",
    "fallback",
    "invalid",
    "missing",
    "no_eligible_candidates",
    "no_selected_evidence",
    "no_trace_data",
    "not_attempted",
    "ok",
    "unknown",
}
RECOMMENDATION_REASON_TOKENS = {
    "current",
    "freshness",
    "policy",
    "recency",
    "relevance",
    "stale",
    "unknown",
    "weak_filtered",
}
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_FEEDBACK_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tm_core.TZ_CN)
    return parsed.astimezone(tm_core.TZ_CN)


def _query_hash(query: Any) -> str:
    return hashlib.sha256(str(query or "").encode("utf-8")).hexdigest()[:12]


def _row_query_hash(row: dict[str, Any]) -> str:
    value = str(row.get("query_hash") or "").strip()
    return value or _query_hash(row.get("query"))


def _row_run_id(row: dict[str, Any]) -> str | None:
    value = row.get("run_id")
    trace = row.get("trace")
    if not value and isinstance(trace, dict):
        value = trace.get("run_id")
    run_id = str(value or "").strip()
    return run_id or None


def _now_iso() -> str:
    return dt.datetime.now(tm_core.TZ_CN).replace(microsecond=0).isoformat()


def _safe_event_ts(value: Any) -> str:
    if value in (None, ""):
        return _now_iso()
    parsed = _parse_ts(value)
    if not parsed:
        raise ValueError(f"invalid feedback ts: {value!r}")
    return parsed.replace(microsecond=0).isoformat()


def _safe_identifier_token(value: Any) -> str | None:
    token = str(value or "").strip()
    if not token or not _SAFE_IDENTIFIER_RE.fullmatch(token):
        return None
    return token


def _safe_repo_rel_path(value: Any) -> str | None:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith(("~", "/", "//")) or re.match(r"^[A-Za-z]:", raw):
        return None
    if "://" in raw:
        return None
    parts: list[str] = []
    for part in Path(raw).parts:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    normalized = "/".join(parts)
    if not normalized.startswith(("wiki/", "sources/")):
        return None
    if normalized.startswith(("wiki/person/", "sources/person/")):
        return None
    forbidden = {"runtime", ".tmp", "tests", "review-artifacts"}
    if any(part in forbidden for part in Path(normalized).parts):
        return None
    return normalized


def _feedback_reason_categories(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = [str(value).strip()]
    categories: list[str] = []
    for item in raw_items:
        if not item:
            continue
        token = _safe_metric_token(item, allowed=RECOMMENDATION_REASON_TOKENS)
        if token not in categories:
            categories.append(token)
    return categories


def build_feedback_event(
    *,
    surface: Any,
    action: Any,
    trace_id: Any,
    query: Any = None,
    query_hash: Any = None,
    run_id: Any = None,
    target_path: Any = None,
    source_evidence_id: Any = None,
    source_evidence_path: Any = None,
    candidate_rank: Any = None,
    score_bucket: Any = None,
    reason_categories: Any = None,
    use_hint: Any = None,
    event_id: Any = None,
    ts: Any = None,
) -> dict[str, Any]:
    action_token = str(action or "").strip().lower()
    if action_token not in FEEDBACK_ACTION_TOKENS:
        raise ValueError(f"invalid feedback action: {action!r}")
    trace_id_token = _safe_identifier_token(trace_id)
    if not trace_id_token:
        raise ValueError(f"invalid trace_id: {trace_id!r}")
    surface_token = _safe_metric_token(surface, allowed=FEEDBACK_SURFACE_TOKENS)
    event: dict[str, Any] = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "ts": _safe_event_ts(ts),
        "event_id": _safe_identifier_token(event_id) or uuid.uuid4().hex,
        "surface": surface_token,
        "action": action_token,
        "trace_id": trace_id_token,
        "telemetry_only": True,
    }
    if query_hash is None and query not in (None, ""):
        query_hash = _query_hash(query)
    if query_hash not in (None, ""):
        query_hash_token = str(query_hash).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{12}", query_hash_token):
            raise ValueError(f"invalid query_hash: {query_hash!r}")
        event["query_hash"] = query_hash_token
    run_id_token = _safe_identifier_token(run_id)
    if run_id_token:
        event["run_id"] = run_id_token
    if target_path not in (None, ""):
        safe_target_path = _safe_repo_rel_path(target_path)
        if not safe_target_path:
            raise ValueError(f"invalid target_path: {target_path!r}")
        event["target_path"] = safe_target_path
    source_evidence_path_token = None
    if source_evidence_path not in (None, ""):
        source_evidence_path_token = _safe_repo_rel_path(source_evidence_path)
        if not source_evidence_path_token:
            raise ValueError(f"invalid source_evidence_path: {source_evidence_path!r}")
        event["source_evidence_path"] = source_evidence_path_token
    source_evidence_id_token = _safe_identifier_token(source_evidence_id)
    if source_evidence_id_token:
        event["source_evidence_id"] = source_evidence_id_token
    if candidate_rank not in (None, ""):
        try:
            rank = int(candidate_rank)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid candidate_rank: {candidate_rank!r}") from exc
        if rank > 0:
            event["candidate_rank"] = rank
    if score_bucket not in (None, ""):
        event["score_bucket"] = _safe_metric_token(score_bucket, allowed=FEEDBACK_SCORE_BUCKET_TOKENS)
    categories = _feedback_reason_categories(reason_categories)
    if categories:
        event["reason_categories"] = categories
    if use_hint not in (None, ""):
        event["use_hint"] = _safe_metric_token(use_hint, allowed=FEEDBACK_USE_HINT_TOKENS)
    return event


def append_feedback_event(event: dict[str, Any], path: Path | str = FEEDBACK_LOG) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TypeError("feedback event must be a dict")
    sanitized = build_feedback_event(
        surface=event.get("surface"),
        action=event.get("action"),
        trace_id=event.get("trace_id"),
        query=event.get("query"),
        query_hash=event.get("query_hash"),
        run_id=event.get("run_id"),
        target_path=event.get("target_path"),
        source_evidence_id=event.get("source_evidence_id"),
        source_evidence_path=event.get("source_evidence_path"),
        candidate_rank=event.get("candidate_rank"),
        score_bucket=event.get("score_bucket"),
        reason_categories=event.get("reason_categories"),
        use_hint=event.get("use_hint"),
        event_id=event.get("event_id"),
        ts=event.get("ts"),
    )
    feedback_path = Path(path)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    with feedback_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(sanitized, ensure_ascii=False, sort_keys=True) + "\n")
    return sanitized


def load_feedback_events(
    path: Path | str = FEEDBACK_LOG,
    *,
    since_hours: float | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    dates: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    feedback_path = Path(path)
    if not feedback_path.exists():
        return [], []
    cutoff: dt.datetime | None = None
    if since_hours and since_hours > 0:
        cutoff = dt.datetime.now(tm_core.TZ_CN) - dt.timedelta(hours=since_hours)
    wanted_run_id = str(run_id or "").strip()
    wanted_trace_id = str(trace_id or "").strip()
    wanted_dates = {str(date).strip() for date in (dates or []) if _SAFE_FEEDBACK_DATE_RE.fullmatch(str(date).strip())}

    events: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    with feedback_path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid.append({"line_no": line_no, "error": str(exc)})
                continue
            if not isinstance(row, dict):
                invalid.append({"line_no": line_no, "error": "row is not a JSON object"})
                continue
            try:
                event = build_feedback_event(
                    surface=row.get("surface"),
                    action=row.get("action"),
                    trace_id=row.get("trace_id"),
                    query_hash=row.get("query_hash"),
                    run_id=row.get("run_id"),
                    target_path=row.get("target_path"),
                    source_evidence_id=row.get("source_evidence_id"),
                    source_evidence_path=row.get("source_evidence_path"),
                    candidate_rank=row.get("candidate_rank"),
                    score_bucket=row.get("score_bucket"),
                    reason_categories=row.get("reason_categories"),
                    use_hint=row.get("use_hint"),
                    event_id=row.get("event_id"),
                    ts=row.get("ts"),
                )
            except ValueError as exc:
                invalid.append({"line_no": line_no, "error": str(exc)})
                continue
            ts = _parse_ts(event.get("ts"))
            if cutoff and ts and ts < cutoff:
                continue
            if wanted_dates:
                if not ts:
                    continue
                event_date = ts.astimezone(tm_core.TZ_CN).date().isoformat()
                if event_date not in wanted_dates:
                    continue
            if wanted_run_id and event.get("run_id") != wanted_run_id:
                continue
            if wanted_trace_id and event.get("trace_id") != wanted_trace_id:
                continue
            events.append(event)
    return events, invalid


def summarize_feedback_events(
    events: list[dict[str, Any]],
    invalid: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    action_counts = Counter()
    surface_counts = Counter()
    score_bucket_counts = Counter()
    use_hint_counts = Counter()
    reason_category_counts = Counter()
    trace_ids: set[str] = set()

    for event in events:
        action_counts[_safe_metric_token(event.get("action"), allowed=FEEDBACK_ACTION_TOKENS)] += 1
        surface_counts[_safe_metric_token(event.get("surface"), allowed=FEEDBACK_SURFACE_TOKENS)] += 1
        score_bucket_counts[_safe_metric_token(event.get("score_bucket"), allowed=FEEDBACK_SCORE_BUCKET_TOKENS)] += 1
        use_hint_counts[_safe_metric_token(event.get("use_hint"), allowed=FEEDBACK_USE_HINT_TOKENS)] += 1
        trace_id = _safe_identifier_token(event.get("trace_id"))
        if trace_id:
            trace_ids.add(trace_id)
        for reason in event.get("reason_categories") or []:
            reason_category_counts[_safe_metric_token(reason, allowed=RECOMMENDATION_REASON_TOKENS)] += 1

    summary = {
        "schema_version": FEEDBACK_SUMMARY_SCHEMA_VERSION,
        "event_count": len(events),
        "trace_count": len(trace_ids),
        "invalid_row_count": len(invalid or []),
        "action_counts": dict(sorted(action_counts.items())),
        "surface_counts": dict(sorted(surface_counts.items())),
        "score_bucket_counts": dict(sorted(score_bucket_counts.items())),
        "use_hint_counts": dict(sorted(use_hint_counts.items())),
        "reason_category_counts": dict(sorted(reason_category_counts.items())),
    }
    return summary


def load_trace_rows(
    path: Path | str = TRACE_LOG,
    *,
    since_hours: float | None = None,
    run_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trace_path = Path(path)
    if not trace_path.exists():
        return [], []
    cutoff: dt.datetime | None = None
    if since_hours and since_hours > 0:
        cutoff = dt.datetime.now(tm_core.TZ_CN) - dt.timedelta(hours=since_hours)
    wanted_run_id = str(run_id or "").strip()

    rows: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    with trace_path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid.append({"line_no": line_no, "error": str(exc)})
                continue
            if not isinstance(row, dict):
                invalid.append({"line_no": line_no, "error": "row is not a JSON object"})
                continue
            ts = _parse_ts(row.get("ts"))
            if cutoff and ts and ts < cutoff:
                continue
            if wanted_run_id and _row_run_id(row) != wanted_run_id:
                continue
            row["_line_no"] = line_no
            rows.append(row)
    return rows, invalid


def latest_run_id(rows: list[dict[str, Any]]) -> str | None:
    for row in reversed(rows):
        run_id = _row_run_id(row)
        if run_id:
            return run_id
    return None


def select_rows(
    rows: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    latest_run: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    if latest_run:
        selected_run_id = latest_run_id(rows)
    else:
        selected_run_id = str(run_id or "").strip() or None
    if not selected_run_id:
        return ([], None) if latest_run else (rows, None)
    return [row for row in rows if _row_run_id(row) == selected_run_id], selected_run_id


def _duration_ms(row: dict[str, Any]) -> float | None:
    trace = row.get("trace")
    if isinstance(trace, dict) and isinstance(trace.get("duration_ms"), (int, float)):
        return float(trace["duration_ms"])
    return None


def _to_non_negative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _safe_metric_token(value: Any, *, allowed: set[str], default: str = "unknown") -> str:
    token = str(value or "").strip().lower()
    if not token:
        return default
    return token if token in allowed else default


def _safe_status_token(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "missing"
    return _safe_metric_token(value, allowed=RECOMMENDATION_STATUS_TOKENS, default="unknown")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def _query_class(row: dict[str, Any]) -> str:
    trace = row.get("trace")
    if isinstance(trace, dict) and trace.get("query_class"):
        return str(trace.get("query_class"))
    return "unknown"


def _llm_state(row: dict[str, Any]) -> str:
    trace = row.get("trace")
    if not isinstance(trace, dict):
        return "missing"
    calls = trace.get("calls")
    if not isinstance(calls, list):
        return "missing"
    llm_calls = [
        call for call in calls
        if isinstance(call, dict) and str(call.get("purpose") or "") == "memory_answer"
    ]
    if not llm_calls:
        return "skipped"
    if any(call.get("ok") is False for call in llm_calls):
        return "failed"
    return "ok"


def compact_row(row: dict[str, Any], *, include_query: bool = False) -> dict[str, Any]:
    trace = row.get("trace") if isinstance(row.get("trace"), dict) else {}
    warnings = row.get("warnings") if isinstance(row.get("warnings"), list) else []
    evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
    run_id = _row_run_id(row)
    item: dict[str, Any] = {
        "trace_id": str(row.get("trace_id") or ""),
        "ts": str(row.get("ts") or ""),
        "status": str(row.get("status") or "unknown"),
        "query_hash": _row_query_hash(row),
        "query_class": _query_class(row),
        "duration_ms": _duration_ms(row),
        "evidence_count": len(evidence),
        "claim_count": len(row.get("claims") or []),
        "warning_count": len(warnings),
        "llm": _llm_state(row),
        "selected_evidence": trace.get("selected_evidence") or [],
    }
    if run_id:
        item["run_id"] = run_id
    if include_query and row.get("query") is not None:
        item["query"] = redact_secrets(str(row.get("query") or ""))
    return item


def summarize_rows(
    rows: list[dict[str, Any]],
    invalid: list[dict[str, Any]] | None = None,
    *,
    latest: int = 5,
    include_query: bool = False,
    selected_run_id: str | None = None,
    feedback_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    durations = [value for row in rows if (value := _duration_ms(row)) is not None]
    trace_present_count = sum(1 for row in rows if isinstance(row.get("trace"), dict))
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    query_class_counts = Counter(_query_class(row) for row in rows)
    llm_counts = Counter(_llm_state(row) for row in rows)
    run_ids = [_row_run_id(row) for row in rows]
    run_id_counts = Counter(run_id for run_id in run_ids if run_id)
    gate_dropped = 0
    gate_kept = 0
    for row in rows:
        trace = row.get("trace")
        gate = trace.get("evidence_gate") if isinstance(trace, dict) else None
        if not isinstance(gate, list):
            continue
        for item in gate:
            if not isinstance(item, dict):
                continue
            if item.get("keep"):
                gate_kept += 1
            else:
                gate_dropped += 1
    recommendation_shown_count = 0
    recommendation_candidate_count = 0
    recommendation_boost_attempted_count = 0
    recommendation_used_as_evidence_count = 0
    recommendation_blocked_by_gate_count = 0
    related_status_counts = Counter()
    boost_status_counts = Counter()
    noisy_reasons = Counter()
    compact_rows: list[dict[str, Any]] = []

    for row in rows:
        related_candidates = 0
        boost_candidates = 0
        accepted_count = 0
        rejected_count = 0
        candidate_list: list[dict[str, Any]] = []
        related_status = "missing"
        boost_status = "missing"
        trace = row.get("trace")
        if isinstance(trace, dict):
            related = trace.get("related_evidence_candidates")
            related_trace = related if isinstance(related, dict) else {}
            boost = trace.get("recommendation_boosted_candidates")
            boost_trace = boost if isinstance(boost, dict) else {}
            related_candidates = _to_non_negative_int(related_trace.get("candidate_count"))
            boost_candidates = _to_non_negative_int(boost_trace.get("candidate_count"))
            related_status = _safe_status_token(related_trace.get("status"))
            boost_status = _safe_status_token(boost_trace.get("status"))

            if related_candidates > 0:
                recommendation_shown_count += 1
            recommendation_candidate_count += related_candidates

            if boost_candidates > 0:
                recommendation_boost_attempted_count += 1

            accepted_count = _to_non_negative_int(boost_trace.get("accepted_count"))
            rejected_count = _to_non_negative_int(boost_trace.get("rejected_count"))
            candidate_list = boost_trace.get("candidates")
            if isinstance(candidate_list, list):
                if accepted_count == 0:
                    accepted_count = sum(
                        1 for item in candidate_list
                        if isinstance(item, dict) and str(item.get("action") or "") == "accepted_to_evidence"
                    )
                if rejected_count == 0:
                    rejected_count = sum(
                        1 for item in candidate_list
                        if isinstance(item, dict) and str(item.get("action") or "") == "rejected_by_gate"
                    )
                for item in candidate_list:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("action") or "") != "rejected_by_gate":
                        continue
                    reason = _safe_metric_token(
                        item.get("reason_category"),
                        allowed=RECOMMENDATION_REASON_TOKENS,
                        default="unknown",
                    )
                    if reason:
                        noisy_reasons[reason] += 1
            recommendation_used_as_evidence_count += accepted_count
            recommendation_blocked_by_gate_count += rejected_count

        related_status_counts[related_status] += 1
        boost_status_counts[boost_status] += 1
        compact_rows.append({
            "trace_id": str(row.get("trace_id") or ""),
            "related_candidate_count": related_candidates,
            "boost_candidate_count": boost_candidates,
        })

    recommendation_quality = {
        "recommendation_shown_count": recommendation_shown_count,
        "recommendation_candidate_count": recommendation_candidate_count,
        "recommendation_boost_attempted_count": recommendation_boost_attempted_count,
        "recommendation_used_as_evidence_count": recommendation_used_as_evidence_count,
        "recommendation_blocked_by_gate_count": recommendation_blocked_by_gate_count,
        "status_counts": {
            "sidecar": dict(sorted(related_status_counts.items())),
            "boost": dict(sorted(boost_status_counts.items())),
        },
        "top_noisy_reasons": [
            {"reason_category": reason, "count": count}
            for reason, count in sorted(noisy_reasons.items(), key=lambda item: (-item[1], item[0]))[:3]
        ],
        "rows": compact_rows[-5:],
    }
    if isinstance(feedback_summary, dict) and feedback_summary:
        recommendation_quality["feedback_summary"] = feedback_summary
    return {
        "row_count": len(rows),
        "invalid_row_count": len(invalid or []),
        "trace_present_count": trace_present_count,
        "trace_missing_count": len(rows) - trace_present_count,
        "selected_run_id": selected_run_id,
        "run_id_counts": dict(sorted(run_id_counts.items())),
        "run_id_missing_count": sum(1 for run_id in run_ids if not run_id),
        "status_counts": dict(sorted(status_counts.items())),
        "query_class_counts": dict(sorted(query_class_counts.items())),
        "llm_counts": dict(sorted(llm_counts.items())),
        "duration_ms": {
            "count": len(durations),
            "min": round(min(durations), 2) if durations else None,
            "avg": round(sum(durations) / len(durations), 2) if durations else None,
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "max": round(max(durations), 2) if durations else None,
        },
        "evidence_gate": {
            "kept": gate_kept,
            "dropped": gate_dropped,
        },
        "recommendation_quality": recommendation_quality,
        "latest": [
            compact_row(row, include_query=include_query)
            for row in (rows[-latest:] if latest > 0 else [])
        ],
    }


def find_by_trace_id(rows: list[dict[str, Any]], trace_id: str) -> dict[str, Any] | None:
    for row in reversed(rows):
        if str(row.get("trace_id") or "") == trace_id:
            return row
    return None


def failure_rows(
    rows: list[dict[str, Any]],
    statuses: tuple[str, ...] = DEFAULT_FAILURE_STATUSES,
) -> list[dict[str, Any]]:
    wanted = {status.strip() for status in statuses if status.strip()}
    return [row for row in rows if str(row.get("status") or "unknown") in wanted]


def replay_row(row: dict[str, Any], *, include_query: bool = True) -> dict[str, Any]:
    trace = row.get("trace") if isinstance(row.get("trace"), dict) else {}
    evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
    replay = compact_row(row, include_query=include_query)
    replay["summary"] = redact_secrets(str(row.get("summary") or ""))
    replay["warnings"] = [redact_secrets(str(w)) for w in (row.get("warnings") or [])]
    calls: list[dict[str, Any]] = []
    for call in trace.get("calls") or []:
        if not isinstance(call, dict):
            continue
        clean_call = dict(call)
        if "query" in clean_call:
            if include_query:
                clean_call["query"] = redact_secrets(str(clean_call["query"]))
            else:
                clean_call.pop("query", None)
        calls.append(clean_call)
    replay["calls"] = calls
    replay["evidence_gate"] = trace.get("evidence_gate") or []
    replay["authority_scores"] = trace.get("authority_scores") or []
    replay["conflict_scan"] = trace.get("conflict_scan")
    replay["evidence"] = [
        {
            "id": item.get("id"),
            "source": item.get("source"),
            "path": item.get("path"),
            "title": item.get("title"),
            "score": item.get("score"),
            "authority": item.get("authority"),
            "relevance": item.get("relevance"),
            "source_role": item.get("source_role"),
        }
        for item in evidence
        if isinstance(item, dict)
    ]
    return replay


def _print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def cmd_summary(args: argparse.Namespace) -> int:
    if args.run_id and args.latest_run:
        raise SystemExit("--run-id and --latest-run are mutually exclusive")
    rows, invalid = load_trace_rows(args.log, since_hours=args.since_hours)
    rows, selected_run_id = select_rows(rows, run_id=args.run_id, latest_run=args.latest_run)
    report = summarize_rows(
        rows,
        invalid,
        latest=args.latest,
        include_query=args.include_query,
        selected_run_id=selected_run_id,
    )
    if args.json:
        _print_json(report)
    else:
        selection = f" run_id={selected_run_id}" if selected_run_id else ""
        print(
            f"rows={report['row_count']} invalid={report['invalid_row_count']}{selection} "
            f"status={report['status_counts']} llm={report['llm_counts']} "
            f"gate={report['evidence_gate']} duration={report['duration_ms']}"
        )
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    rows, _invalid = load_trace_rows(args.log)
    row = find_by_trace_id(rows, args.trace_id)
    if not row:
        print(f"trace_id not found: {args.trace_id}", file=sys.stderr)
        return 1
    report = replay_row(row, include_query=not args.no_query)
    if args.json:
        _print_json(report)
    else:
        print(
            f"trace_id={report['trace_id']} status={report['status']} "
            f"query_class={report['query_class']} duration_ms={report['duration_ms']}"
        )
        if report.get("query"):
            print(f"query={report['query']}")
        print(f"summary={report['summary']}")
        print(f"evidence_count={report['evidence_count']} warnings={report['warning_count']}")
        for item in report["evidence"]:
            print(
                f"- {item.get('id')} {item.get('source')} {item.get('path')} "
                f"authority={item.get('authority')} relevance={item.get('relevance')}"
            )
    return 0


def cmd_failures(args: argparse.Namespace) -> int:
    if args.run_id and args.latest_run:
        raise SystemExit("--run-id and --latest-run are mutually exclusive")
    statuses = tuple(args.status.split(",")) if args.status else DEFAULT_FAILURE_STATUSES
    rows, invalid = load_trace_rows(args.log, since_hours=args.since_hours)
    rows, selected_run_id = select_rows(rows, run_id=args.run_id, latest_run=args.latest_run)
    failures = failure_rows(rows, statuses=statuses)
    selected = failures[-max(args.limit, 0):]
    report = {
        "row_count": len(rows),
        "invalid_row_count": len(invalid),
        "selected_run_id": selected_run_id,
        "run_id_counts": dict(sorted(Counter(run_id for row in rows if (run_id := _row_run_id(row))).items())),
        "run_id_missing_count": sum(1 for row in rows if not _row_run_id(row)),
        "failure_count": len(failures),
        "statuses": list(statuses),
        "failures": [compact_row(row, include_query=args.include_query) for row in selected],
    }
    if args.json:
        _print_json(report)
    else:
        selection = f" run_id={selected_run_id}" if selected_run_id else ""
        print(f"failures={report['failure_count']} statuses={','.join(statuses)}{selection}")
        for item in report["failures"]:
            print(
                f"- {item['trace_id']} {item['status']} {item['query_class']} "
                f"evidence={item['evidence_count']} warnings={item['warning_count']} "
                f"query_hash={item['query_hash']}"
        )
    return 0


def cmd_feedback_record(args: argparse.Namespace) -> int:
    event = build_feedback_event(
        surface=args.surface,
        action=args.action,
        trace_id=args.trace_id,
        query=args.query,
        query_hash=args.query_hash,
        run_id=args.run_id,
        target_path=args.target_path,
        source_evidence_id=args.source_evidence_id,
        source_evidence_path=args.source_evidence_path,
        candidate_rank=args.candidate_rank,
        score_bucket=args.score_bucket,
        reason_categories=args.reason_categories,
        use_hint=args.use_hint,
        event_id=args.event_id,
        ts=args.ts,
    )
    stored = append_feedback_event(event, path=args.feedback_log)
    if args.json:
        _print_json({"ok": True, "path": str(Path(args.feedback_log)), "event": stored})
    else:
        print(f"ok: appended feedback event {stored['event_id']} to {args.feedback_log}")
    return 0


def cmd_feedback_summary(args: argparse.Namespace) -> int:
    events, invalid = load_feedback_events(
        args.feedback_log,
        since_hours=args.since_hours,
        run_id=args.run_id,
        trace_id=args.trace_id,
        dates=args.dates,
    )
    report = summarize_feedback_events(events, invalid)
    if args.json:
        _print_json(report)
    else:
        print(
            f"events={report['event_count']} traces={report['trace_count']} "
            f"actions={report['action_counts']} surfaces={report['surface_counts']} "
            f"score_buckets={report['score_bucket_counts']} hints={report['use_hint_counts']}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_answer_trace.py", description=__doc__)
    parser.add_argument("--log", default=str(TRACE_LOG), help="trace JSONL path")
    parser.add_argument("--feedback-log", default=str(FEEDBACK_LOG), help="feedback JSONL path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    summary = sub.add_parser("summary", help="summarize trace status and latency")
    summary.add_argument("--since-hours", type=float, default=None)
    summary.add_argument("--run-id", default=None, help="summarize one eval/run id")
    summary.add_argument("--latest-run", action="store_true", help="summarize the latest non-empty run id")
    summary.add_argument("--latest", type=int, default=5)
    summary.add_argument("--include-query", action="store_true")
    summary.add_argument("--json", action="store_true")
    summary.set_defaults(func=cmd_summary)

    replay = sub.add_parser("replay", help="show one trace by trace_id")
    replay.add_argument("--trace-id", required=True)
    replay.add_argument("--no-query", action="store_true", help="omit stored query text")
    replay.add_argument("--json", action="store_true")
    replay.set_defaults(func=cmd_replay)

    failures = sub.add_parser("failures", help="list recent non-ok traces")
    failures.add_argument("--since-hours", type=float, default=None)
    failures.add_argument("--run-id", default=None, help="list failures for one eval/run id")
    failures.add_argument("--latest-run", action="store_true", help="list failures for the latest non-empty run id")
    failures.add_argument("--limit", type=int, default=20)
    failures.add_argument("--status", default=",".join(DEFAULT_FAILURE_STATUSES))
    failures.add_argument("--include-query", action="store_true")
    failures.add_argument("--json", action="store_true")
    failures.set_defaults(func=cmd_failures)

    feedback = sub.add_parser("feedback", help="record or summarize explicit feedback events")
    feedback_sub = feedback.add_subparsers(dest="feedback_cmd", required=True)

    feedback_record = feedback_sub.add_parser("record", help="append one feedback event")
    feedback_record.add_argument("--surface", required=True)
    feedback_record.add_argument("--action", required=True)
    feedback_record.add_argument("--trace-id", required=True)
    feedback_record.add_argument("--query", default=None, help="raw query for query_hash only; not persisted")
    feedback_record.add_argument("--query-hash", default=None)
    feedback_record.add_argument("--run-id", default=None)
    feedback_record.add_argument("--target-path", default=None)
    feedback_record.add_argument("--source-evidence-id", default=None)
    feedback_record.add_argument("--source-evidence-path", default=None)
    feedback_record.add_argument("--candidate-rank", default=None)
    feedback_record.add_argument("--score-bucket", default=None)
    feedback_record.add_argument("--reason-category", dest="reason_categories", action="append", default=None)
    feedback_record.add_argument("--use-hint", default=None)
    feedback_record.add_argument("--event-id", default=None)
    feedback_record.add_argument("--ts", default=None)
    feedback_record.add_argument("--json", action="store_true")
    feedback_record.set_defaults(func=cmd_feedback_record)

    feedback_summary = feedback_sub.add_parser("summary", help="summarize feedback events")
    feedback_summary.add_argument("--since-hours", type=float, default=None)
    feedback_summary.add_argument("--run-id", default=None)
    feedback_summary.add_argument("--trace-id", default=None)
    feedback_summary.add_argument("--dates", default=None, help="comma-separated YYYY-MM-DD dates")
    feedback_summary.add_argument("--json", action="store_true")
    feedback_summary.set_defaults(func=cmd_feedback_summary)

    args = parser.parse_args()
    if getattr(args, "cmd", None) == "feedback" and getattr(args, "dates", None):
        args.dates = [item.strip() for item in str(args.dates).split(",") if item.strip()]
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
