#!/usr/bin/env python3
"""Inspect memory_answer trace JSONL records."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import tm_answer
import tm_core


DEFAULT_FAILURE_STATUSES = ("error", "conflict", "not_found")


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


def _row_run_id(row: dict[str, Any]) -> str | None:
    value = row.get("run_id")
    trace = row.get("trace")
    if not value and isinstance(trace, dict):
        value = trace.get("run_id")
    run_id = str(value or "").strip()
    return run_id or None


def load_trace_rows(
    path: Path | str = tm_answer.TRACE_LOG,
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
        "query_hash": _query_hash(row.get("query")),
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
    if include_query:
        item["query"] = tm_answer.redact_secrets(str(row.get("query") or ""))
    return item


def summarize_rows(
    rows: list[dict[str, Any]],
    invalid: list[dict[str, Any]] | None = None,
    *,
    latest: int = 5,
    include_query: bool = False,
    selected_run_id: str | None = None,
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
            "avg": round(sum(durations) / len(durations), 2) if durations else None,
            "max": round(max(durations), 2) if durations else None,
        },
        "evidence_gate": {
            "kept": gate_kept,
            "dropped": gate_dropped,
        },
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
    replay["summary"] = tm_answer.redact_secrets(str(row.get("summary") or ""))
    replay["warnings"] = [tm_answer.redact_secrets(str(w)) for w in (row.get("warnings") or [])]
    calls: list[dict[str, Any]] = []
    for call in trace.get("calls") or []:
        if not isinstance(call, dict):
            continue
        clean_call = dict(call)
        if "query" in clean_call:
            if include_query:
                clean_call["query"] = tm_answer.redact_secrets(str(clean_call["query"]))
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_answer_trace.py", description=__doc__)
    parser.add_argument("--log", default=str(tm_answer.TRACE_LOG), help="trace JSONL path")
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

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
