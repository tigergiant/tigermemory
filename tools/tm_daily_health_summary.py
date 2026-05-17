#!/usr/bin/env python3
"""Build machine-readable daily-health summaries from existing check outputs.

This tool is intentionally aggregation-only. It does not call LLMs, run
retrieval evals, or write reports; daily scans run the existing checks first
and feed their JSON outputs here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_KNOWN_DEBT = REPO_ROOT / "wiki/operations/daily-health-known-debt.md"
RESOLVED_STATUSES = {"resolved", "closed", "done"}


def _read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_json_report(path: pathlib.Path | str) -> dict[str, Any]:
    """Load a JSON object, tolerating diagnostic lines before the final JSON."""
    text = _read_text(pathlib.Path(path)).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        lines = text.splitlines()
        last_obj: dict[str, Any] | None = None
        for index, line in enumerate(lines):
            if not line.lstrip().startswith("{"):
                continue
            candidate_text = "\n".join(lines[index:])
            try:
                candidate = json.loads(candidate_text)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                last_obj = candidate
        if last_obj is None:
            raise
        data = last_obj
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def _split_table_row(line: str) -> list[str]:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return [re.sub(r"<br\s*/?>", " ", cell, flags=re.IGNORECASE) for cell in cells]


def load_known_debt(path: pathlib.Path = DEFAULT_KNOWN_DEBT) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for raw in _read_text(path).splitlines():
        line = raw.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = _split_table_row(line)
        if not cells:
            continue
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        if "id" in cells and "status" in cells:
            header = cells
            continue
        if not header or len(cells) < len(header):
            continue
        rows.append({header[i]: cells[i] for i in range(len(header))})
    return rows


def summarize_known_debt(rows: list[dict[str, str]], *, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.date.today()
    status_counts = Counter((row.get("status") or "unknown").strip() or "unknown" for row in rows)
    active_rows = [
        row for row in rows
        if (row.get("status") or "unknown").strip() not in RESOLVED_STATUSES
    ]
    overdue: list[str] = []
    due_soon: list[str] = []
    for row in active_rows:
        raw_date = (row.get("review_by_date") or "").strip()
        try:
            review_date = dt.date.fromisoformat(raw_date)
        except ValueError:
            continue
        if review_date < today:
            overdue.append(row.get("id") or "")
        elif review_date <= today + dt.timedelta(days=7):
            due_soon.append(row.get("id") or "")
    return {
        "total": len(rows),
        "active_count": len(active_rows),
        "resolved_count": len(rows) - len(active_rows),
        "by_status": dict(sorted(status_counts.items())),
        "review_overdue_count": len([item for item in overdue if item]),
        "review_due_soon_count": len([item for item in due_soon if item]),
        "review_overdue_ids": [item for item in overdue if item],
        "review_due_soon_ids": [item for item in due_soon if item],
    }


def compact_answer_eval(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    failure_ids = [
        str(item.get("id") or "")
        for item in report.get("failures") or []
        if isinstance(item, dict) and item.get("id")
    ]
    keys = (
        "case_count",
        "status_correct",
        "expected_evidence_case_count",
        "expected_evidence_hit",
        "claim_support_rate",
        "not_found_precision",
        "expected_conflict_case_count",
        "conflict_correct",
    )
    out = {key: report.get(key) for key in keys if key in report}
    out["failure_count"] = len(failure_ids)
    out["failure_ids"] = failure_ids
    return out


def compact_trace_summary(summary: dict[str, Any] | None, failures: dict[str, Any] | None) -> dict[str, Any] | None:
    if not summary and not failures:
        return None
    summary = summary or {}
    failures = failures or {}
    return {
        "row_count": summary.get("row_count"),
        "invalid_row_count": summary.get("invalid_row_count"),
        "trace_present_count": summary.get("trace_present_count"),
        "status_counts": summary.get("status_counts") or {},
        "llm_counts": summary.get("llm_counts") or {},
        "failure_count": failures.get("failure_count", 0),
    }


def compact_retrieval_eval(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    keys = (
        "case_count",
        "hit1",
        "hit3",
        "hit1_rate",
        "hit3_rate",
        "runtime_unavailable_count",
        "probe_case_count",
        "probe_hit1",
        "probe_hit3",
        "recall",
        "top_k",
    )
    return {key: report.get(key) for key in keys if key in report}


def current_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def cmd_known_debt(args: argparse.Namespace) -> int:
    rows = load_known_debt(REPO_ROOT / args.file)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    report = summarize_known_debt(rows, today=today)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


def _optional_json(path: str | None) -> dict[str, Any] | None:
    return load_json_report(REPO_ROOT / path) if path else None


def cmd_assemble(args: argparse.Namespace) -> int:
    known_debt = summarize_known_debt(
        load_known_debt(REPO_ROOT / args.known_debt_file),
        today=dt.date.fromisoformat(args.today) if args.today else dt.date.today(),
    )
    summary = {
        "schema_version": "daily-health-summary-v1",
        "health_color": args.health_color,
        "blocking_count": args.blocking_count,
        "known_debt_count": known_debt["active_count"],
        "new_problem_count": args.new_problem_count,
        "known_debt_changes": {
            "new": args.known_debt_new,
            "known": args.known_debt_known,
            "resolved": args.known_debt_resolved,
            "worsened": args.known_debt_worsened,
        },
        "known_debt": known_debt,
        "answer_eval": compact_answer_eval(_optional_json(args.answer_eval)),
        "answer_trace": compact_trace_summary(
            _optional_json(args.answer_trace_summary),
            _optional_json(args.answer_trace_failures),
        ),
        "retrieval_eval_lexical": compact_retrieval_eval(_optional_json(args.retrieval_lexical)),
        "retrieval_eval_hybrid": compact_retrieval_eval(_optional_json(args.retrieval_hybrid)),
        "commit_sha": args.commit_sha or current_commit_sha(),
        "push_result": args.push_result,
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_daily_health_summary.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    debt_p = sub.add_parser("known-debt", help="summarize daily-health known debt")
    debt_p.add_argument("--file", default="wiki/operations/daily-health-known-debt.md")
    debt_p.add_argument("--today", default=None, help="YYYY-MM-DD override for tests")
    debt_p.set_defaults(func=cmd_known_debt)

    assemble_p = sub.add_parser("assemble", help="assemble daily-health machine summary JSON")
    assemble_p.add_argument("--health-color", default="yellow")
    assemble_p.add_argument("--blocking-count", type=int, default=0)
    assemble_p.add_argument("--new-problem-count", type=int, default=0)
    assemble_p.add_argument("--known-debt-new", type=int, default=0)
    assemble_p.add_argument("--known-debt-known", type=int, default=0)
    assemble_p.add_argument("--known-debt-resolved", type=int, default=0)
    assemble_p.add_argument("--known-debt-worsened", type=int, default=0)
    assemble_p.add_argument("--known-debt-file", default="wiki/operations/daily-health-known-debt.md")
    assemble_p.add_argument("--answer-eval", default=None)
    assemble_p.add_argument("--answer-trace-summary", default=None)
    assemble_p.add_argument("--answer-trace-failures", default=None)
    assemble_p.add_argument("--retrieval-lexical", default=None)
    assemble_p.add_argument("--retrieval-hybrid", default=None)
    assemble_p.add_argument("--commit-sha", default=None)
    assemble_p.add_argument("--push-result", default="pending")
    assemble_p.add_argument("--today", default=None, help="YYYY-MM-DD override for tests")
    assemble_p.set_defaults(func=cmd_assemble)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
