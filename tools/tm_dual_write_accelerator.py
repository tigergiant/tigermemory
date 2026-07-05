#!/usr/bin/env python3
"""Phase 1 acceleration checks for SQLite shadow dual-write.

Default mode is read-only. Use --live-canary to create short-lived test
memories and verify that each write path creates a local SQLite shadow row.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from typing import Any

import _bootstrap_paths  # noqa: F401

import tigermemory_core as tm_core
from tigermemory_core import TZ_CN
import tm_memory_eval
import tm_memory_ops
import tm_review_tools


REPO_ROOT = tm_core.REPO_ROOT
SCAN_ROOTS = ("tools", "packages", "deploy")
SCAN_PATTERNS = {
    "create": ("mem0_write(", "write_memory_with_review("),
    "update": ("mem0_update_content(", "method=\"PUT\"", 'method="PUT"'),
    "delete": ("mem0_delete(", "method=\"DELETE\"", 'method="DELETE"'),
    "raw_memories_api": ("/api/v1/memories",),
}
TIMER_DIRECT_WRITE_PATTERNS = (
    "/write_memory",
    "mem0_write(",
    "write_memory_with_review(",
    "session-fallback-generator.py --write",
    "tm_io.py mem0-write",
    "tm_io.py write-inbox",
)
TIMER_RUNTIME_EVENT_PATTERNS = (
    "tm_runtime_events.py",
    "runtime_event",
)
TIMER_REPORT_PATTERNS = (
    "tm_digest.py",
    "cron-daily-report",
    "cron-weekly-report",
    "cron-intake",
    "tm_memory_reflection",
)
DEFERRED_ENTRYPOINTS = (
    {
        "entrypoint": "tm-openai-mcp write_memory",
        "status": "archived",
        "phase1_gate": False,
        "reason": (
            "Deferred by user decision on 2026-07-05; the ChatGPT/OpenAI-facing "
            "facade has extra OAuth/public-gateway complexity and is out of the "
            "current SQLite-first migration gate."
        ),
    },
)


def _rel(path: pathlib.Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _git_head_short() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def scan_code_entrypoints() -> dict[str, list[dict[str, Any]]]:
    matches: dict[str, list[dict[str, Any]]] = {key: [] for key in SCAN_PATTERNS}
    for root_name in SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".sh", ".service"} or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                for kind, patterns in SCAN_PATTERNS.items():
                    if any(pattern in line for pattern in patterns):
                        matches[kind].append({
                            "path": _rel(path),
                            "line": line_no,
                            "text": line.strip()[:220],
                        })
    return matches


def service_env_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit in sorted((REPO_ROOT / "deploy" / "mcp").glob("*.service")):
        try:
            lines = unit.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        env_files = [
            line.split("=", 1)[1].strip()
            for line in lines
            if line.strip().startswith("EnvironmentFile=")
        ]
        rows.append({
            "unit": _rel(unit),
            "environment_files": env_files,
            "uses_openmemory_env": any("runtime/openmemory/.env" in value for value in env_files),
        })
    return rows


def deferred_entrypoints() -> list[dict[str, Any]]:
    return [dict(row) for row in DEFERRED_ENTRYPOINTS]


def _repo_script_from_exec_start(value: str) -> pathlib.Path | None:
    for marker in ("/opt/tigermemory/", "/root/tigermemory/", "%h/tigermemory/"):
        if marker in value:
            rel = value.split(marker, 1)[1].split()[0].strip().strip('"').strip("'")
            candidate = REPO_ROOT / rel
            return candidate if candidate.is_file() else None
    parts = value.split()
    for part in parts:
        clean = part.strip().strip('"').strip("'")
        if clean.startswith("tools/") or clean.startswith("deploy/"):
            candidate = REPO_ROOT / clean
            return candidate if candidate.is_file() else None
    return None


def timer_entrypoint_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for timer in sorted((REPO_ROOT / "deploy").rglob("*.timer")):
        try:
            timer_lines = timer.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        unit_name = ""
        for line in timer_lines:
            if line.strip().startswith("Unit="):
                unit_name = line.split("=", 1)[1].strip()
                break
        if not unit_name:
            unit_name = timer.with_suffix(".service").name
        service = timer.parent / unit_name
        service_text = ""
        exec_start: list[str] = []
        if service.exists():
            service_text = service.read_text(encoding="utf-8", errors="replace")
            exec_start = [
                line.split("=", 1)[1].strip()
                for line in service_text.splitlines()
                if line.strip().startswith("ExecStart=")
            ]
        script_texts: list[str] = []
        for value in exec_start:
            script = _repo_script_from_exec_start(value)
            if script is not None:
                try:
                    script_texts.append(script.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        combined = "\n".join([*timer_lines, service_text, *script_texts])
        if any(pattern in combined for pattern in TIMER_DIRECT_WRITE_PATTERNS):
            classification = "direct_memory_write"
        elif any(pattern in combined for pattern in TIMER_RUNTIME_EVENT_PATTERNS):
            classification = "runtime_event_only"
        elif any(pattern in combined for pattern in TIMER_REPORT_PATTERNS):
            classification = "report_or_digest_only"
        elif "tm-dashboard.service" in unit_name:
            classification = "service_warm_only"
        elif "sync" in unit_name.lower():
            classification = "repo_sync_only"
        else:
            classification = "unknown"
        rows.append({
            "timer": _rel(timer),
            "service": _rel(service),
            "service_exists": service.exists(),
            "exec_start": exec_start,
            "classification": classification,
            "needs_canary": classification in {"direct_memory_write", "unknown"},
        })
    return rows


def route_event_replay(days: int) -> dict[str, Any]:
    today = dt.datetime.now(TZ_CN).date()
    root = REPO_ROOT / ".tmp" / "memory-route-events"
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        date = (today - dt.timedelta(days=offset)).isoformat()
        path = root / date / "events.jsonl"
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item["_date"] = date
                rows.append(item)

    mem0_rows = [
        row for row in rows
        if str(row.get("outcome") or row.get("route") or row.get("flow_target") or "").lower() == "mem0"
        or str(row.get("flow_target") or "").lower() == "mem0"
    ]
    by_signature: dict[str, int] = {}
    for row in mem0_rows:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        target_ref = row.get("target_ref") if isinstance(row.get("target_ref"), dict) else {}
        agent = str(row.get("agent") or extra.get("agent") or target_ref.get("agent") or "unknown")
        topic = str(row.get("route") or extra.get("topic") or target_ref.get("topic") or "unknown")
        outcome = str(row.get("outcome") or "unknown")
        component = str(row.get("component") or "unknown")
        key = f"{agent}|{topic}|{component}|{outcome}"
        by_signature[key] = by_signature.get(key, 0) + 1
    return {
        "days": days,
        "event_count": len(rows),
        "mem0_event_count": len(mem0_rows),
        "mem0_signatures": [
            {"signature": key, "count": count}
            for key, count in sorted(by_signature.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _json_file(path: pathlib.Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object in {path}")
    return payload


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 2)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    value = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(float(value), 2)


def _gate(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


def _parse_iso_datetime(value: str) -> dt.datetime | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ_CN)
    return parsed.astimezone(TZ_CN)


def summarize_reconcile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return _gate("pending", reason="missing_reconcile_payload")
    reasons: list[str] = []
    if payload.get("ok") is not True:
        reasons.append("reconcile_not_ok")
    direct = payload.get("direct_readback") if isinstance(payload.get("direct_readback"), dict) else {}
    conservation = payload.get("conservation") if isinstance(payload.get("conservation"), dict) else {}
    sha_diff = payload.get("sha_diff") if isinstance(payload.get("sha_diff"), dict) else {}
    semantic = payload.get("semantic") if isinstance(payload.get("semantic"), dict) else {}
    if int(direct.get("missing") or 0) != 0:
        reasons.append("direct_readback_missing")
    if conservation.get("balanced") is not True:
        reasons.append("conservation_unbalanced")
    if int(sha_diff.get("symmetric_diff_count") or 0) != 0:
        reasons.append("sha_symmetric_diff")
    if semantic.get("status") == "downgraded":
        reasons.append("semantic_downgrade")
    status = "pass" if not reasons else "blocked"
    return _gate(
        status,
        reasons=reasons,
        source_count=(payload.get("counts") or {}).get("source") if isinstance(payload.get("counts"), dict) else None,
        db_count=(payload.get("counts") or {}).get("db") if isinstance(payload.get("counts"), dict) else None,
        direct_missing=direct.get("missing"),
        conservation_balanced=conservation.get("balanced"),
        sha_symmetric_diff_count=sha_diff.get("symmetric_diff_count"),
        semantic_status=semantic.get("status"),
    )


def run_reconcile_check(input_path: pathlib.Path, db_path: pathlib.Path, out_path: pathlib.Path | None = None) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "tm_local_memory.py"),
        "reconcile",
        "--input",
        str(input_path),
        "--db",
        str(db_path),
    ]
    if out_path is not None:
        cmd.extend(["--out", str(out_path)])
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    try:
        payload = _parse_json(proc.stdout.strip())
    except Exception:
        payload = {"ok": False, "error": proc.stderr.strip()[:500] or proc.stdout.strip()[:500]}
    payload["_returncode"] = proc.returncode
    return payload


def _load_shadow_rows(log_dir: pathlib.Path, days: int) -> list[dict[str, Any]]:
    today = dt.datetime.now(TZ_CN).date()
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        date = (today - dt.timedelta(days=offset)).isoformat()
        path = log_dir / f"{date}.jsonl"
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item["_date"] = date
                rows.append(item)
    return rows


def _load_route_event_rows(root: pathlib.Path, days: int) -> list[dict[str, Any]]:
    today = dt.datetime.now(TZ_CN).date()
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        date = (today - dt.timedelta(days=offset)).isoformat()
        path = root / date / "events.jsonl"
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item["_date"] = date
                rows.append(item)
    return rows


def summarize_shadow_reconcile(
    *,
    route_event_root: pathlib.Path,
    db_path: pathlib.Path,
    days: int,
    since: str | None = None,
) -> dict[str, Any]:
    rows = _load_route_event_rows(route_event_root, days)
    since_dt = _parse_iso_datetime(since) if since else None
    if since and since_dt is None:
        return _gate("blocked", reason="invalid_shadow_reconcile_since", since=since)
    ids: list[str] = []
    id_rows: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        if since_dt is not None:
            row_ts = _parse_iso_datetime(str(row.get("ts") or ""))
            if row_ts is None or row_ts < since_dt:
                continue
        flow = str(row.get("outcome") or row.get("route") or row.get("flow_target") or "").lower()
        if flow != "mem0" and str(row.get("flow_target") or "").lower() != "mem0":
            continue
        target_ref = row.get("target_ref") if isinstance(row.get("target_ref"), dict) else {}
        memory_id = str(target_ref.get("id") or row.get("memory_id") or "").strip()
        if tm_core.MEM0_UUID_RE.fullmatch(memory_id) and memory_id not in seen:
            seen.add(memory_id)
            ids.append(memory_id)
            id_rows[memory_id] = row
    if not ids:
        return _gate(
            "pending",
            reason="no_mem0_route_event_ids",
            days=days,
            since=since,
            route_event_root=str(route_event_root),
        )
    if not db_path.exists():
        return _gate("blocked", reason="local_db_missing", local_db=str(db_path), checked_ids=len(ids))
    conn = sqlite3.connect(str(db_path))
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        required = {"legacy_mem0_id", "state", "backend_origin", "shadow_state"}
        missing_columns = sorted(required - columns)
        if missing_columns:
            return _gate(
                "blocked",
                reason="local_db_schema_missing_columns",
                missing_columns=missing_columns,
                local_db=str(db_path),
                checked_ids=len(ids),
            )
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT legacy_mem0_id, state, backend_origin, shadow_state
            FROM memories
            WHERE legacy_mem0_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    finally:
        conn.close()
    found = {str(row[0]): {"state": row[1], "backend_origin": row[2], "shadow_state": row[3]} for row in rows}
    missing = [memory_id for memory_id in ids if memory_id not in found]
    wrong_origin = [
        memory_id for memory_id, row in found.items()
        if row.get("backend_origin") != "local-shadow"
    ]
    reasons: list[str] = []
    if missing:
        reasons.append("missing_local_shadow")
    if wrong_origin:
        reasons.append("wrong_backend_origin")
    return _gate(
        "pass" if not reasons else "blocked",
        reasons=reasons,
        checked_ids=len(ids),
        found_ids=len(found),
        missing_count=len(missing),
        missing_samples=missing[:10],
        missing_agents=dict(Counter(str(id_rows.get(memory_id, {}).get("agent") or "") for memory_id in missing).most_common()),
        missing_sources=dict(Counter(str(id_rows.get(memory_id, {}).get("source") or "") for memory_id in missing).most_common()),
        missing_topics=dict(Counter(str(id_rows.get(memory_id, {}).get("stored_topic") or "") for memory_id in missing).most_common()),
        missing_ts_range=[
            value for value in (
                min((str(id_rows.get(memory_id, {}).get("ts") or "") for memory_id in missing), default=""),
                max((str(id_rows.get(memory_id, {}).get("ts") or "") for memory_id in missing), default=""),
            ) if value
        ],
        wrong_origin_count=len(wrong_origin),
        wrong_origin_samples=wrong_origin[:10],
        days=days,
        since=since,
        local_db=str(db_path),
    )


def summarize_shadow_search_logs(log_dir: pathlib.Path, *, days: int, max_local_p95_ms: float) -> dict[str, Any]:
    rows = _load_shadow_rows(log_dir, days)
    if not rows:
        return _gate("pending", reason="missing_shadow_search_logs", log_dir=str(log_dir), days=days)
    latencies = [
        float(row.get("local_latency_ms"))
        for row in rows
        if isinstance(row.get("local_latency_ms"), (int, float))
    ]
    warning_count = sum(1 for row in rows if row.get("warnings"))
    local_empty_when_old_nonempty = sum(
        1 for row in rows
        if int(row.get("old_count") or 0) > 0 and int(row.get("local_count") or 0) == 0
    )
    intersection_empty_when_both_nonempty = sum(
        1 for row in rows
        if int(row.get("old_count") or 0) > 0
        and int(row.get("local_count") or 0) > 0
        and int(row.get("intersection_count") or 0) == 0
    )
    p95 = _percentile(latencies, 0.95)
    reasons: list[str] = []
    if p95 is not None and p95 > max_local_p95_ms:
        reasons.append("local_search_p95_too_high")
    if warning_count:
        reasons.append("shadow_search_warnings_present")
    if local_empty_when_old_nonempty:
        reasons.append("local_empty_for_old_hits")
    status = "pass" if not reasons else "blocked"
    return _gate(
        status,
        reasons=reasons,
        row_count=len(rows),
        warning_count=warning_count,
        local_empty_when_old_nonempty=local_empty_when_old_nonempty,
        intersection_empty_when_both_nonempty=intersection_empty_when_both_nonempty,
        local_latency_p95_ms=p95,
        max_local_p95_ms=max_local_p95_ms,
        days=days,
        log_dir=str(log_dir),
    )


def summarize_retrieval_eval_payload(
    payload: dict[str, Any],
    *,
    min_hit5_rate: float,
    max_p95_ms: float,
    missing_path_policy: str = "block",
) -> dict[str, Any]:
    if not payload:
        return _gate("pending", reason="missing_retrieval_eval_payload")
    hit5_rate = payload.get("quality_hit5_rate")
    if hit5_rate is None:
        hit5_rate = payload.get("hit5_rate")
    try:
        hit5_rate_float = float(hit5_rate)
    except (TypeError, ValueError):
        hit5_rate_float = -1.0
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    latencies = [
        float(row.get("latency_ms"))
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("latency_ms"), (int, float))
    ]
    if isinstance(payload.get("latency_p95_ms"), (int, float)):
        p95 = round(float(payload["latency_p95_ms"]), 2)
    else:
        p95 = _percentile(latencies, 0.95)
    reasons: list[str] = []
    if hit5_rate_float < min_hit5_rate:
        reasons.append("hit5_rate_below_threshold")
    if int(payload.get("runtime_unavailable_count") or 0) > 0:
        reasons.append("runtime_unavailable")
    if int(payload.get("contract_failure_count") or 0) > 0:
        reasons.append("contract_failures")
    missing_count = int(payload.get("expected_path_missing_count") or 0)
    if missing_count > 0 and missing_path_policy == "block":
        reasons.append("eval_expected_paths_missing")
    if int(payload.get("evaluated_case_count") or payload.get("case_count") or 0) == 0:
        reasons.append("no_evaluable_cases")
    if max_p95_ms > 0 and p95 is not None and p95 > max_p95_ms:
        reasons.append("retrieval_eval_p95_too_high")
    status = "pass" if not reasons else "blocked"
    return _gate(
        status,
        reasons=reasons,
        case_count=payload.get("case_count"),
        hit5_rate=hit5_rate_float,
        min_hit5_rate=min_hit5_rate,
        runtime_unavailable_count=payload.get("runtime_unavailable_count"),
        contract_failure_count=payload.get("contract_failure_count"),
        expected_path_missing_policy=missing_path_policy,
        expected_path_missing_count=missing_count,
        expected_path_missing_samples=payload.get("expected_path_missing_samples") or [],
        evaluated_case_count=payload.get("evaluated_case_count") or payload.get("case_count"),
        excluded_missing_expected_path_count=payload.get("excluded_missing_expected_path_count", 0),
        latency_p95_ms=p95,
        max_p95_ms=max_p95_ms,
    )


def _expected_path_exists(expected_path: str) -> bool:
    clean = expected_path.split("#", 1)[0]
    if clean.endswith("*"):
        prefix = clean[:-1]
        return any(path.as_posix().startswith(prefix) for path in REPO_ROOT.rglob("*") if path.is_file())
    return (REPO_ROOT / clean).exists()


def _missing_expected_paths(cases: list[Any]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for case in cases:
        absent = [
            path for path in getattr(case, "expected_paths", [])
            if path and not _expected_path_exists(path)
        ]
        if absent:
            missing.append({"id": getattr(case, "id", ""), "missing_expected_paths": absent})
    return missing


def run_retrieval_eval_check(
    cases_path: pathlib.Path,
    *,
    top_k: int,
    missing_path_policy: str = "block",
) -> dict[str, Any]:
    cases = tm_memory_eval.load_cases(cases_path)
    missing = _missing_expected_paths(cases)
    missing_ids = {str(row.get("id") or "") for row in missing}
    eval_cases = cases
    if missing_path_policy == "exclude" and missing_ids:
        eval_cases = [case for case in cases if getattr(case, "id", "") not in missing_ids]
    report = tm_memory_eval.evaluate(eval_cases, top_k=top_k)
    rows = report.get("results") if isinstance(report.get("results"), list) else []
    report["latency_p95_ms"] = _percentile(
        [
            float(row.get("latency_ms"))
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("latency_ms"), (int, float))
        ],
        0.95,
    )
    report["expected_path_missing_count"] = len(missing)
    report["expected_path_missing_samples"] = missing[:10]
    report["expected_path_missing_policy"] = missing_path_policy
    report["source_case_count"] = len(cases)
    report["evaluated_case_count"] = len(eval_cases)
    report["excluded_missing_expected_path_count"] = len(cases) - len(eval_cases)
    # Keep raw-query rows out of readiness output.
    report.pop("results", None)
    report.pop("probe_results", None)
    return report


def phase_readiness(args: argparse.Namespace) -> dict[str, Any]:
    gates: dict[str, dict[str, Any]] = {}
    if args.reconcile_report:
        try:
            gates["reconcile"] = summarize_reconcile_payload(_json_file(pathlib.Path(args.reconcile_report)))
        except Exception as exc:
            gates["reconcile"] = _gate("blocked", reason=f"reconcile_report_error: {exc}")
    elif args.reconcile_input:
        payload = run_reconcile_check(
            pathlib.Path(args.reconcile_input),
            pathlib.Path(args.local_db),
            pathlib.Path(args.reconcile_out) if args.reconcile_out else None,
        )
        gates["reconcile"] = summarize_reconcile_payload(payload)
    else:
        gates["reconcile"] = _gate("pending", reason="provide --reconcile-input or --reconcile-report")

    if args.check_shadow_reconcile:
        gates["shadow_reconcile"] = summarize_shadow_reconcile(
            route_event_root=pathlib.Path(args.route_event_root),
            db_path=pathlib.Path(args.local_db),
            days=args.days,
            since=args.shadow_reconcile_since,
        )

    gates["shadow_search"] = summarize_shadow_search_logs(
        pathlib.Path(args.shadow_log_dir),
        days=args.days,
        max_local_p95_ms=args.max_local_p95_ms,
    )

    if args.retrieval_eval_report:
        try:
            eval_payload = _json_file(pathlib.Path(args.retrieval_eval_report))
            gates["retrieval_eval"] = summarize_retrieval_eval_payload(
                eval_payload,
                min_hit5_rate=args.min_hit5_rate,
                max_p95_ms=args.max_eval_p95_ms,
                missing_path_policy=args.eval_missing_path_policy,
            )
        except Exception as exc:
            gates["retrieval_eval"] = _gate("blocked", reason=f"retrieval_eval_report_error: {exc}")
    elif args.run_retrieval_eval:
        try:
            eval_payload = run_retrieval_eval_check(
                pathlib.Path(args.retrieval_eval_cases),
                top_k=args.eval_top_k,
                missing_path_policy=args.eval_missing_path_policy,
            )
            gates["retrieval_eval"] = summarize_retrieval_eval_payload(
                eval_payload,
                min_hit5_rate=args.min_hit5_rate,
                max_p95_ms=args.max_eval_p95_ms,
                missing_path_policy=args.eval_missing_path_policy,
            )
        except Exception as exc:
            gates["retrieval_eval"] = _gate("blocked", reason=f"retrieval_eval_error: {exc}")
    else:
        gates["retrieval_eval"] = _gate("pending", reason="provide --run-retrieval-eval or --retrieval-eval-report")

    blockers = [name for name, gate in gates.items() if gate.get("status") == "blocked"]
    pending = [name for name, gate in gates.items() if gate.get("status") == "pending"]
    if blockers:
        overall = "blocked"
    elif pending:
        overall = "pending"
    else:
        overall = "pass"
    return {
        "generated_at": dt.datetime.now(TZ_CN).isoformat(),
        "overall_status": overall,
        "blockers": blockers,
        "pending": pending,
        "gates": gates,
    }


def _shadow_row(remote_id: str) -> dict[str, Any] | None:
    db_path = tm_core._local_db_path()
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, legacy_mem0_id, backend_origin, shadow_state, topic, source_agent, state,
                   content, content_sha256
            FROM memories
            WHERE legacy_mem0_id=?
            """,
            (remote_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _hard_delete_shadow(remote_id: str) -> int:
    db_path = tm_core._local_db_path()
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM memories WHERE legacy_mem0_id=?", (remote_id,))
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def _cleanup_remote_and_shadow(remote_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"remote_id": remote_id}
    try:
        out["remote_delete"] = json.loads(tm_core.mem0_delete([remote_id]))
    except Exception as exc:
        out["remote_delete"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:220]}
    out["local_hard_deleted"] = _hard_delete_shadow(remote_id)
    return out


def _canary_text(name: str) -> str:
    marker = f"tm-dual-write-canary-{name}-{uuid.uuid4()}"
    return "\n".join([
        "---",
        "memory_type: session-handoff",
        "agent: codex",
        f"topic: {marker}",
        f"created_at: {dt.datetime.now(TZ_CN).strftime('%Y-%m-%d')}",
        "---",
        "",
        "## Task",
        f"TigerMemory dual-write canary event for {name}.",
        "",
        "## Decisions",
        "This event records that the checked entrypoint must preserve legacy_mem0_id mapping in the local SQLite shadow row.",
        "",
        "## Blockers",
        "None.",
        "",
        "## Handoff",
        "The verifier uses this marker to confirm shadow row creation, shadow state updates, and cleanup behavior.",
        "",
        "## Evidence Refs",
        marker,
    ])


def _routing_canary_text(name: str) -> str:
    marker = f"tm-dual-write-route-canary-{name}-{uuid.uuid4()}"
    return (
        f"2026-07-05 Codex observed TigerMemory {name} route behavior "
        f"during Phase 1 dual-write validation. marker={marker}"
    )


def _extract_id(payload: dict[str, Any]) -> str:
    value = str(payload.get("id") or payload.get("memory_id") or "").strip()
    if not tm_core.MEM0_UUID_RE.fullmatch(value):
        raise RuntimeError(f"missing remote id in response keys={sorted(payload.keys())}")
    return value


def _parse_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"expected JSON response, got {raw[:220]!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected object response, got {type(payload).__name__}")
    return payload


def _run_tm_io(args: list[str], stdin_text: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "tm_io.py"), *args],
        cwd=REPO_ROOT,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tm_io {' '.join(args)} failed rc={proc.returncode}: {proc.stderr[:220]}")
    return _parse_json(proc.stdout.strip())


def _mcp_tool_result_payload(result: Any) -> dict[str, Any]:
    content = getattr(result, "content", None)
    if not content:
        raise RuntimeError("MCP tool result has no content")
    first = content[0]
    text = getattr(first, "text", None)
    if text is None and isinstance(first, dict):
        text = first.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"MCP tool result first content has no text: {type(first).__name__}")
    return _parse_json(text)


async def _call_tm_mcp_stdio_write_async(text: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = os.environ.copy()
    env.update({
        "TIGERMEMORY_PROFILE": tm_core.TIGERMEMORY_PROFILE_HYBRID,
        "TM_LOCAL_DUAL_WRITE": "1",
        "TM_MCP_TOOL_PROFILE": "memory",
    })
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "tools" / "tm_mcp.py"), "--stdio"],
        cwd=str(REPO_ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "write_memory",
                arguments={
                    "agent": "codex",
                    "topic": "systems",
                    "text": text,
                    "light": True,
                },
            )
    return _mcp_tool_result_payload(result)


def _call_tm_mcp_stdio_write(text: str) -> dict[str, Any]:
    return asyncio.run(_call_tm_mcp_stdio_write_async(text))


def _shadow_matches(shadow: dict[str, Any] | None, *, state: str | None = None, shadow_state: str | None = None) -> bool:
    if not shadow or shadow.get("backend_origin") != "local-shadow":
        return False
    if state is not None and shadow.get("state") != state:
        return False
    if shadow_state is not None and shadow.get("shadow_state") != shadow_state:
        return False
    return True


def _read_runtime_events(root: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/events.jsonl")):
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _set_env(updates: dict[str, str]) -> dict[str, str | None]:
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def run_fault_drill() -> list[dict[str, Any]]:
    """Run local-only Phase 1 failure drills without touching production DB."""
    results: list[dict[str, Any]] = []
    original_request = tm_core.mem0_request
    original_base = tm_core.mem0_base
    original_key = tm_core.mem0_key
    original_user = tm_core.mem0_user_id

    with tempfile.TemporaryDirectory(prefix="tm-dual-write-fault-") as raw_tmp:
        tmp = pathlib.Path(raw_tmp)
        event_root = tmp / "runtime-events"
        remote_id = "11111111-2222-4333-8444-555555555555"

        def fake_remote_success(_url: str, **_kwargs: Any) -> str:
            return json.dumps({"id": remote_id, "ok": True})

        def fake_remote_down(_url: str, **_kwargs: Any) -> str:
            raise RuntimeError("Mem0 unreachable: simulated outage")

        try:
            tm_core.mem0_base = lambda: "http://127.0.0.1:8765"  # type: ignore[assignment]
            tm_core.mem0_key = lambda: "test-key"  # type: ignore[assignment]
            tm_core.mem0_user_id = lambda: "tiger"  # type: ignore[assignment]

            bad_db_path = tmp / "not-a-sqlite-file"
            bad_db_path.mkdir()
            previous = _set_env({
                "TIGERMEMORY_PROFILE": tm_core.TIGERMEMORY_PROFILE_HYBRID,
                "TM_LOCAL_DUAL_WRITE": "1",
                "TIGERMEMORY_LOCAL_DB": str(bad_db_path),
                "TM_RUNTIME_EVENTS_ROOT": str(event_root),
            })
            try:
                tm_core.mem0_request = fake_remote_success  # type: ignore[assignment]
                raw = tm_core.mem0_write(
                    "codex",
                    "systems",
                    "fault drill shadow write failure must not block remote",
                    metadata_extra={"fault_drill": "shadow_write_failure"},
                )
                payload = _parse_json(raw)
                events = _read_runtime_events(event_root)
                failure_events = [
                    row for row in events
                    if row.get("event_type") == "memory_local_dual_write"
                    and row.get("outcome") == "shadow_write_failed"
                ]
                results.append({
                    "name": "shadow_write_failure_non_blocking",
                    "ok": payload.get("id") == remote_id and bool(failure_events),
                    "remote_id": payload.get("id"),
                    "runtime_event_recorded": bool(failure_events),
                    "production_db_touched": False,
                })
            except Exception as exc:
                results.append({"name": "shadow_write_failure_non_blocking", "ok": False, "error": str(exc)[:500]})
            finally:
                _restore_env(previous)

            previous = _set_env({
                "TIGERMEMORY_PROFILE": tm_core.TIGERMEMORY_PROFILE_HYBRID,
                "TM_LOCAL_DUAL_WRITE": "1",
                "TIGERMEMORY_LOCAL_DB": str(tmp / "remote-down.sqlite"),
                "TM_RUNTIME_EVENTS_ROOT": str(event_root),
            })
            try:
                tm_core.mem0_request = fake_remote_down  # type: ignore[assignment]
                try:
                    tm_core.mem0_write(
                        "codex",
                        "systems",
                        "fault drill remote outage should preserve old failure behavior",
                        metadata_extra={"fault_drill": "remote_down"},
                    )
                    results.append({"name": "remote_down_preserves_fail_closed", "ok": False, "error": "write unexpectedly succeeded"})
                except RuntimeError as exc:
                    db_path = pathlib.Path(os.environ["TIGERMEMORY_LOCAL_DB"])
                    results.append({
                        "name": "remote_down_preserves_fail_closed",
                        "ok": "simulated outage" in str(exc) and not db_path.exists(),
                        "local_db_created": db_path.exists(),
                        "error": str(exc)[:220],
                    })
            finally:
                _restore_env(previous)

            previous = _set_env({
                "TIGERMEMORY_PROFILE": tm_core.TIGERMEMORY_PROFILE_LOCAL,
                "TIGERMEMORY_LOCAL_DB": str(tmp / "local-wal.sqlite"),
            })
            try:
                raw = tm_core.mem0_write(
                    "codex",
                    "systems",
                    "fault drill local wal schema and readback",
                    metadata_extra={"fault_drill": "local_wal"},
                )
                payload = _parse_json(raw)
                conn = sqlite3.connect(os.environ["TIGERMEMORY_LOCAL_DB"])
                try:
                    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                    count = conn.execute("SELECT COUNT(1) FROM memories WHERE id=?", (payload["id"],)).fetchone()[0]
                    outbox_count = conn.execute("SELECT COUNT(1) FROM outbox").fetchone()[0]
                finally:
                    conn.close()
                results.append({
                    "name": "local_wal_schema_readback",
                    "ok": journal_mode == "wal" and count == 1 and outbox_count == 0,
                    "journal_mode": journal_mode,
                    "row_count": count,
                    "outbox_count": outbox_count,
                })
            finally:
                _restore_env(previous)
        finally:
            tm_core.mem0_request = original_request  # type: ignore[assignment]
            tm_core.mem0_base = original_base  # type: ignore[assignment]
            tm_core.mem0_key = original_key  # type: ignore[assignment]
            tm_core.mem0_user_id = original_user  # type: ignore[assignment]

    return results


def run_live_canary(http_url: str) -> list[dict[str, Any]]:
    if tm_core.tigermemory_profile() != tm_core.TIGERMEMORY_PROFILE_HYBRID:
        raise RuntimeError("live canary requires TIGERMEMORY_PROFILE=hybrid")
    results: list[dict[str, Any]] = []

    def record(name: str, raw_payload: dict[str, Any]) -> str:
        remote_id = _extract_id(raw_payload)
        shadow = _shadow_row(remote_id)
        cleanup = _cleanup_remote_and_shadow(remote_id)
        results.append({
            "name": name,
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow),
            "shadow": shadow,
            "cleanup": cleanup,
        })
        return remote_id

    def record_error(name: str, exc: Exception) -> None:
        results.append({
            "name": name,
            "shadow_ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        })

    raw = tm_core.mem0_write(
        "codex",
        "systems",
        _canary_text("tm_core_mem0_write"),
        metadata_extra={"canary_entrypoint": "tm_core_mem0_write"},
    )
    record("tm_core.mem0_write", json.loads(raw))

    payload = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        _canary_text("tm_memory_ops_write_memory_with_review"),
        light=True,
        total_budget_s=None,
    )
    if payload.get("route") != "mem0":
        results.append({"name": "tm_memory_ops.write_memory_with_review", "shadow_ok": False, "error": payload})
    else:
        record("tm_memory_ops.write_memory_with_review", payload)

    body = json.dumps({
        "agent": "codex",
        "topic": "systems",
        "text": _canary_text("tm_http_write_memory"),
        "light": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        http_url.rstrip("/") + "/write_memory",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + tm_core.mcp_api_key(),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        record("tm-http /write_memory", json.loads(resp.read().decode("utf-8")))

    try:
        record(
            "tm-mcp stdio write_memory",
            _call_tm_mcp_stdio_write(_canary_text("tm_mcp_stdio_write_memory")),
        )
    except Exception as exc:
        record_error("tm-mcp stdio write_memory", exc)

    try:
        record(
            "tm_io mem0-write",
            _run_tm_io(
                ["mem0-write", "--agent", "codex", "--topic", "systems"],
                _canary_text("tm_io_mem0_write"),
            ),
        )
    except Exception as exc:
        record_error("tm_io mem0-write", exc)

    try:
        payload = _run_tm_io(
            [
                "write-inbox",
                "--agent",
                "codex",
                "--topic",
                "systems",
                "--title",
                "Dual write canary",
            ],
            _routing_canary_text("tm_io_write_inbox"),
        )
        if payload.get("route") != "mem0":
            results.append({
                "name": "tm_io write-inbox natural route",
                "shadow_ok": None,
                "route": payload.get("route"),
                "covered": False,
                "reason": "router did not select mem0; no dual-write write was expected",
                "route_result": payload,
            })
        else:
            record("tm_io write-inbox natural route", payload)
    except Exception as exc:
        record_error("tm_io write-inbox natural route", exc)

    try:
        result = tm_review_tools.execute_promote_mem0(
            {
                "id": "tm-dual-write-canary-review-promote",
                "topic": "systems",
                "text": _canary_text("tm_review_tools_execute_promote_mem0"),
                "source_type": "canary",
            },
            topic="systems",
        )
        if not result.get("ok") or not result.get("memory_id"):
            results.append({"name": "tm_review_tools.execute_promote_mem0", "shadow_ok": False, "error": result})
        else:
            record("tm_review_tools.execute_promote_mem0", {"id": result["memory_id"]})
    except Exception as exc:
        record_error("tm_review_tools.execute_promote_mem0", exc)

    try:
        create_payload = _parse_json(tm_core.mem0_write(
            "codex",
            "systems",
            _canary_text("tm_core_mem0_update_content_seed"),
            metadata_extra={"canary_entrypoint": "tm_core_mem0_update_content"},
        ))
        remote_id = _extract_id(create_payload)
        new_content = _canary_text("tm_core_mem0_update_content_replacement")
        tm_core.mem0_update_content(remote_id, new_content)
        shadow = _shadow_row(remote_id)
        cleanup = _cleanup_remote_and_shadow(remote_id)
        results.append({
            "name": "tm_core.mem0_update_content",
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow, shadow_state="mem0_updated") and shadow.get("content") == new_content,
            "shadow": shadow,
            "cleanup": cleanup,
        })
    except Exception as exc:
        record_error("tm_core.mem0_update_content", exc)

    try:
        create_payload = _run_tm_io(
            ["mem0-write", "--agent", "codex", "--topic", "systems"],
            _canary_text("tm_io_mem0_update_content_seed"),
        )
        remote_id = _extract_id(create_payload)
        new_content = _canary_text("tm_io_mem0_update_content_replacement")
        _run_tm_io(["mem0-update-content", "--id", remote_id], new_content)
        shadow = _shadow_row(remote_id)
        cleanup = _cleanup_remote_and_shadow(remote_id)
        results.append({
            "name": "tm_io mem0-update-content",
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow, shadow_state="mem0_updated") and shadow.get("content") == new_content,
            "shadow": shadow,
            "cleanup": cleanup,
        })
    except Exception as exc:
        record_error("tm_io mem0-update-content", exc)

    try:
        create_payload = _parse_json(tm_core.mem0_write(
            "codex",
            "systems",
            _canary_text("tm_core_mem0_delete_seed"),
            metadata_extra={"canary_entrypoint": "tm_core_mem0_delete"},
        ))
        remote_id = _extract_id(create_payload)
        delete_payload = _parse_json(tm_core.mem0_delete([remote_id]))
        shadow = _shadow_row(remote_id)
        results.append({
            "name": "tm_core.mem0_delete",
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow, state="deleted", shadow_state="mem0_deleted"),
            "shadow": shadow,
            "delete": delete_payload,
            "cleanup": {"local_hard_deleted": _hard_delete_shadow(remote_id)},
        })
    except Exception as exc:
        record_error("tm_core.mem0_delete", exc)

    try:
        create_payload = _parse_json(tm_core.mem0_write(
            "codex",
            "systems",
            _canary_text("tm_review_tools_delete_seed"),
            metadata_extra={"canary_entrypoint": "tm_review_tools_mem0_delete_by_id"},
        ))
        remote_id = _extract_id(create_payload)
        deleted = tm_review_tools._mem0_delete_by_id(remote_id)
        shadow = _shadow_row(remote_id)
        results.append({
            "name": "tm_review_tools._mem0_delete_by_id",
            "remote_id": remote_id,
            "shadow_ok": bool(deleted) and _shadow_matches(shadow, state="deleted", shadow_state="mem0_deleted"),
            "shadow": shadow,
            "cleanup": {"local_hard_deleted": _hard_delete_shadow(remote_id)},
        })
    except Exception as exc:
        record_error("tm_review_tools._mem0_delete_by_id", exc)

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--days", type=int, default=14, help="route event replay window")
    parser.add_argument("--live-canary", action="store_true", help="create and clean live canary writes")
    parser.add_argument("--fault-drill", action="store_true", help="run local-only failure drills on temporary DBs")
    parser.add_argument("--readiness", action="store_true", help="summarize Phase 1/2 reconcile and retrieval gates")
    parser.add_argument("--http-url", default="http://127.0.0.1:8790", help="tm-http base URL for live canary")
    parser.add_argument("--local-db", default=str(tm_core._local_db_path()), help="local SQLite DB for readiness reconcile")
    parser.add_argument("--route-event-root", default=str(REPO_ROOT / ".tmp" / "memory-route-events"), help="route event root for shadow reconcile")
    parser.add_argument("--reconcile-input", default=None, help="OpenMemory export JSONL for readiness reconcile")
    parser.add_argument("--reconcile-report", default=None, help="existing tm_local_memory reconcile JSON report")
    parser.add_argument("--reconcile-out", default=None, help="optional path to write reconcile report when --reconcile-input is used")
    parser.add_argument("--shadow-log-dir", default=str(REPO_ROOT / ".tmp" / "search-shadow"), help="shadow-search log dir")
    parser.add_argument("--check-shadow-reconcile", action="store_true", help="check recent mem0 route ids against local-shadow rows")
    parser.add_argument("--shadow-reconcile-since", default=None, help="only check route events at or after this ISO timestamp")
    parser.add_argument("--max-local-p95-ms", type=float, default=500.0, help="max allowed local shadow-search p95")
    parser.add_argument("--run-retrieval-eval", action="store_true", help="run tm_memory_eval in-process for readiness")
    parser.add_argument("--retrieval-eval-report", default=None, help="existing retrieval eval JSON report")
    parser.add_argument("--retrieval-eval-cases", default=str(REPO_ROOT / "tests" / "fixtures" / "memory_eval_cases.jsonl"))
    parser.add_argument("--eval-top-k", type=int, default=5)
    parser.add_argument("--eval-missing-path-policy", choices=["block", "exclude"], default="block")
    parser.add_argument("--min-hit5-rate", type=float, default=1.0)
    parser.add_argument("--max-eval-p95-ms", type=float, default=0.0, help="optional retrieval eval p95 gate; 0 disables")
    args = parser.parse_args(argv)

    result: dict[str, Any] = {
        "generated_at": dt.datetime.now(TZ_CN).isoformat(),
        "repo_head": _git_head_short(),
        "profile": tm_core.tigermemory_profile(),
        "dual_write_enabled": tm_core._local_dual_write_enabled(),
        "local_db": str(tm_core._local_db_path()),
        "code_entrypoints": scan_code_entrypoints(),
        "service_env": service_env_audit(),
        "deferred_entrypoints": deferred_entrypoints(),
        "timer_entrypoints": timer_entrypoint_audit(),
        "route_event_replay": route_event_replay(args.days),
    }
    if args.fault_drill:
        result["fault_drill"] = run_fault_drill()
    if args.readiness:
        result["readiness"] = phase_readiness(args)
    if args.live_canary:
        result["live_canary"] = run_live_canary(args.http_url)

    if args.json:
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
        return 0

    print(f"generated_at: {result['generated_at']}")
    print(f"repo_head: {result['repo_head']} profile={result['profile']} dual_write={result['dual_write_enabled']}")
    print(f"local_db: {result['local_db']}")
    print("\nservice_env:")
    for row in result["service_env"]:
        print(f"- {row['unit']}: openmemory_env={row['uses_openmemory_env']} files={row['environment_files']}")
    print("\ndeferred_entrypoints:")
    for row in result["deferred_entrypoints"]:
        print(
            f"- {row['entrypoint']}: status={row['status']} "
            f"phase1_gate={row['phase1_gate']} reason={row['reason']}"
        )
    print("\ntimer_entrypoints:")
    for row in result["timer_entrypoints"]:
        print(
            f"- {row['timer']} -> {row['service']}: "
            f"classification={row['classification']} needs_canary={row['needs_canary']}"
        )
    print("\nroute_event_replay:")
    replay = result["route_event_replay"]
    print(f"- days={replay['days']} events={replay['event_count']} mem0_events={replay['mem0_event_count']}")
    for row in replay["mem0_signatures"][:20]:
        print(f"  - {row['count']} {row['signature']}")
    if args.fault_drill:
        print("\nfault_drill:")
        for row in result["fault_drill"]:
            suffix = f" error={row.get('error')}" if row.get("error") else ""
            print(f"- {row['name']}: ok={row.get('ok')}{suffix}")
    if args.readiness:
        readiness = result["readiness"]
        print("\nreadiness:")
        print(
            f"- overall={readiness['overall_status']} "
            f"blockers={readiness['blockers']} pending={readiness['pending']}"
        )
        for name, gate in readiness["gates"].items():
            detail = gate.get("reason") or gate.get("reasons") or ""
            print(f"  - {name}: status={gate.get('status')} detail={detail}")
    if args.live_canary:
        print("\nlive_canary:")
        for row in result["live_canary"]:
            suffix = ""
            if row.get("error"):
                suffix = f" error={row.get('error')}"
            if row.get("route"):
                suffix += f" route={row.get('route')}"
            if row.get("reason"):
                suffix += f" reason={row.get('reason')}"
            print(
                f"- {row['name']}: shadow_ok={row.get('shadow_ok')} "
                f"remote_id={row.get('remote_id')} cleanup={row.get('cleanup')}{suffix}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
