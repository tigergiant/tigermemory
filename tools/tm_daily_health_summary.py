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
import os
import pathlib
import re
import subprocess
import sys
from collections import Counter
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_KNOWN_DEBT = REPO_ROOT / "wiki/operations/daily-health-known-debt.md"
RESOLVED_STATUSES = {"resolved", "closed", "done"}
AUTOMATION_ID = "tigermemory-daily-health-scan"
AUTOMATION_CONTRACT_SCHEMA = "daily-health-automation-contract-v1"
DAILY_SUMMARY_SCHEMA = "daily-health-summary-v1"
DAILY_REPORT_VALIDATION_SCHEMA = "daily-health-report-validation-v1"
REQUIRED_DAILY_SUMMARY_FIELDS = (
    "schema_version",
    "health_color",
    "blocking_count",
    "known_debt_count",
    "new_problem_count",
    "health_probe",
    "known_debt_changes",
    "answer_eval",
    "answer_trace",
    "retrieval_eval_lexical",
    "retrieval_eval_hybrid",
    "commit_sha",
    "push_result",
)
REQUIRED_HEALTH_PROBE_FIELDS = (
    "mem0_reachable",
    "mem0_api_reachable",
    "mem0_api_latency_ms",
    "mem0_api_error",
)
SUMMARY_HEADINGS = ("## 机器可读摘要", "## Machine-readable Summary")
SOURCE_HEADINGS = ("## 来源", "## Sources")
AUTOMATION_CONTRACT_CHECKS = (
    {
        "id": "self_contract_audit",
        "description": "automation prompt asks the scan to audit this contract",
        "markers": ("tm_daily_health_summary.py automation-contract",),
    },
    {
        "id": "live_grounding",
        "description": "scan starts from live repo/runtime state",
        "markers": ("git pull --ff-only origin master", "git status", "tm_lessons.py search"),
    },
    {
        "id": "runtime_health_probe",
        "description": "scan records tm_http health with mem0 API signals",
        "markers": ("tm_http /health", "mem0_api_reachable", "mem0_api_latency_ms", "mem0_api_error"),
    },
    {
        "id": "answer_quality",
        "description": "memory_answer eval and trace checks are part of the scan",
        "markers": ("tm_answer_eval.py eval", "tm_answer_trace.py summary", "tm_answer_trace.py failures"),
    },
    {
        "id": "retrieval_quality",
        "description": "lexical smoke and hybrid production retrieval evals are separated",
        "markers": ("tm_memory_eval.py eval", "--recall hybrid", "--embedding-base-url http://127.0.0.1:19190/v1"),
    },
    {
        "id": "machine_summary",
        "description": "daily report uses reproducible intermediate JSON and a machine summary",
        "markers": (".tmp/daily-health", "tm_daily_health_summary.py assemble", "机器可读摘要"),
    },
    {
        "id": "report_validation",
        "description": "daily report machine summary is validated after writing",
        "markers": ("tm_daily_health_summary.py validate-report", "health_probe", "schema_version"),
    },
    {
        "id": "known_debt_flow",
        "description": "known debt is read and classified by state transition",
        "markers": ("daily-health-known-debt.md", "new / known / resolved / worsened"),
    },
    {
        "id": "writeback_closeout",
        "description": "scan commits, pushes, syncs WSL, and writes tigermemory closeout memory",
        "markers": ("git commit", "git push", "write_memory", "git pull --ff-only origin master"),
    },
)


def _read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def default_automation_path() -> pathlib.Path:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    return pathlib.Path(home) / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"


def default_daily_report_path(today: str | None = None) -> pathlib.Path:
    report_date = today or dt.date.today().isoformat()
    return REPO_ROOT / "wiki" / "operations" / "daily-health" / f"{report_date}.md"


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


def audit_automation_contract(text: str, *, path: pathlib.Path | str | None = None) -> dict[str, Any]:
    normalized = text.lower()
    checks: list[dict[str, Any]] = []
    for check in AUTOMATION_CONTRACT_CHECKS:
        missing_markers = [
            marker for marker in check["markers"]
            if marker.lower() not in normalized
        ]
        checks.append({
            "id": check["id"],
            "description": check["description"],
            "ok": not missing_markers,
            "missing_markers": missing_markers,
        })
    missing = [check for check in checks if not check["ok"]]
    return {
        "schema_version": AUTOMATION_CONTRACT_SCHEMA,
        "automation_id": AUTOMATION_ID,
        "path": str(path) if path else None,
        "status": "ok" if not missing else "fail",
        "check_count": len(checks),
        "passed_count": len(checks) - len(missing),
        "missing_count": len(missing),
        "missing": missing,
        "checks": checks,
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


def extract_machine_summary(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    lines = text.splitlines()
    summary_index: int | None = None
    source_index: int | None = None
    errors: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped in SUMMARY_HEADINGS and summary_index is None:
            summary_index = idx
        if stripped in SOURCE_HEADINGS and source_index is None:
            source_index = idx
    if summary_index is None:
        return None, ["missing machine-readable summary section"]
    if source_index is not None and source_index < summary_index:
        errors.append("machine-readable summary must appear before sources")
    end_index = source_index if source_index is not None and source_index > summary_index else len(lines)
    for line in lines[summary_index + 1 : end_index]:
        raw = line.strip()
        if not raw or raw.startswith("```"):
            continue
        if not raw.startswith("{"):
            continue
        try:
            summary = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, [*errors, f"invalid machine summary JSON: {exc}"]
        if not isinstance(summary, dict):
            return None, [*errors, "machine summary JSON must be an object"]
        return summary, errors
    return None, [*errors, "missing machine summary JSON object"]


def validate_daily_report(text: str, *, path: pathlib.Path | str | None = None) -> dict[str, Any]:
    missing_sections: list[str] = []
    if not any(heading in text for heading in SOURCE_HEADINGS):
        missing_sections.append("sources")
    summary, errors = extract_machine_summary(text)
    missing_fields: list[str] = []
    nested_missing_fields: list[str] = []
    if summary is None:
        missing_sections.append("machine-readable summary")
    else:
        missing_fields = [key for key in REQUIRED_DAILY_SUMMARY_FIELDS if key not in summary]
        if summary.get("schema_version") != DAILY_SUMMARY_SCHEMA:
            errors.append(f"schema_version must be {DAILY_SUMMARY_SCHEMA}")
        health_probe = summary.get("health_probe")
        if not isinstance(health_probe, dict):
            nested_missing_fields.append("health_probe")
        else:
            nested_missing_fields.extend(
                f"health_probe.{key}"
                for key in REQUIRED_HEALTH_PROBE_FIELDS
                if key not in health_probe
            )
    status = "ok" if not (missing_sections or missing_fields or nested_missing_fields or errors) else "fail"
    return {
        "schema_version": DAILY_REPORT_VALIDATION_SCHEMA,
        "path": str(path) if path else None,
        "status": status,
        "missing_sections": sorted(set(missing_sections)),
        "missing_fields": missing_fields,
        "nested_missing_fields": nested_missing_fields,
        "errors": errors,
        "summary_present": summary is not None,
        "summary_keys": sorted(summary.keys()) if isinstance(summary, dict) else [],
    }


def cmd_known_debt(args: argparse.Namespace) -> int:
    rows = load_known_debt(REPO_ROOT / args.file)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    report = summarize_known_debt(rows, today=today)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


def cmd_automation_contract(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path) if args.path else default_automation_path()
    try:
        text = _read_text(path)
        report = audit_automation_contract(text, path=path)
    except FileNotFoundError:
        report = {
            "schema_version": AUTOMATION_CONTRACT_SCHEMA,
            "automation_id": AUTOMATION_ID,
            "path": str(path),
            "status": "fail",
            "check_count": len(AUTOMATION_CONTRACT_CHECKS),
            "passed_count": 0,
            "missing_count": len(AUTOMATION_CONTRACT_CHECKS),
            "missing": [{"id": "automation_file", "missing_markers": [str(path)]}],
            "checks": [],
        }
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    elif report["status"] == "ok":
        sys.stdout.write(f"ok: {report['automation_id']} contract passed ({report['passed_count']}/{report['check_count']})\n")
    else:
        sys.stdout.write(f"fail: {report['automation_id']} contract missing {report['missing_count']} checks\n")
        for item in report["missing"]:
            sys.stdout.write(f"- {item['id']}: {', '.join(item.get('missing_markers') or [])}\n")
    return 0 if report["status"] == "ok" else 1


def _optional_json(path: str | None) -> dict[str, Any] | None:
    return load_json_report(REPO_ROOT / path) if path else None


def cmd_assemble(args: argparse.Namespace) -> int:
    known_debt = summarize_known_debt(
        load_known_debt(REPO_ROOT / args.known_debt_file),
        today=dt.date.fromisoformat(args.today) if args.today else dt.date.today(),
    )
    health_probe = _optional_json(args.health_json)
    summary = {
        "schema_version": DAILY_SUMMARY_SCHEMA,
        "health_color": args.health_color,
        "blocking_count": args.blocking_count,
        "known_debt_count": known_debt["active_count"],
        "new_problem_count": args.new_problem_count,
        "health_probe": None if health_probe is None else {
            "mem0_reachable": health_probe.get("mem0_reachable"),
            "mem0_api_reachable": health_probe.get("mem0_api_reachable"),
            "mem0_api_latency_ms": health_probe.get("mem0_api_latency_ms"),
            "mem0_api_error": health_probe.get("mem0_api_error"),
        },
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


def cmd_validate_report(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path) if args.path else default_daily_report_path(args.today)
    try:
        report = validate_daily_report(_read_text(path), path=path)
    except FileNotFoundError:
        report = {
            "schema_version": DAILY_REPORT_VALIDATION_SCHEMA,
            "path": str(path),
            "status": "fail",
            "missing_sections": [],
            "missing_fields": [],
            "nested_missing_fields": [],
            "errors": [f"report not found: {path}"],
            "summary_present": False,
            "summary_keys": [],
        }
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    elif report["status"] == "ok":
        sys.stdout.write(f"ok: {report['path']} machine summary valid\n")
    else:
        sys.stdout.write(f"fail: {report['path']} machine summary invalid\n")
        for key in ("missing_sections", "missing_fields", "nested_missing_fields", "errors"):
            values = report.get(key) or []
            if values:
                sys.stdout.write(f"- {key}: {', '.join(values)}\n")
    return 0 if report["status"] == "ok" else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_daily_health_summary.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    debt_p = sub.add_parser("known-debt", help="summarize daily-health known debt")
    debt_p.add_argument("--file", default="wiki/operations/daily-health-known-debt.md")
    debt_p.add_argument("--today", default=None, help="YYYY-MM-DD override for tests")
    debt_p.set_defaults(func=cmd_known_debt)

    audit_p = sub.add_parser("automation-contract", help="audit Codex daily-health automation prompt contract")
    audit_p.add_argument("--path", default=None, help="automation.toml path; defaults to the local Codex automation")
    audit_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    audit_p.set_defaults(func=cmd_automation_contract)

    assemble_p = sub.add_parser("assemble", help="assemble daily-health machine summary JSON")
    assemble_p.add_argument("--health-color", default="yellow")
    assemble_p.add_argument("--blocking-count", type=int, default=0)
    assemble_p.add_argument("--new-problem-count", type=int, default=0)
    assemble_p.add_argument("--known-debt-new", type=int, default=0)
    assemble_p.add_argument("--known-debt-known", type=int, default=0)
    assemble_p.add_argument("--known-debt-resolved", type=int, default=0)
    assemble_p.add_argument("--known-debt-worsened", type=int, default=0)
    assemble_p.add_argument("--known-debt-file", default="wiki/operations/daily-health-known-debt.md")
    assemble_p.add_argument("--health-json", default=None, help="optional tm_http /health JSON file")
    assemble_p.add_argument("--answer-eval", default=None)
    assemble_p.add_argument("--answer-trace-summary", default=None)
    assemble_p.add_argument("--answer-trace-failures", default=None)
    assemble_p.add_argument("--retrieval-lexical", default=None)
    assemble_p.add_argument("--retrieval-hybrid", default=None)
    assemble_p.add_argument("--commit-sha", default=None)
    assemble_p.add_argument("--push-result", default="pending")
    assemble_p.add_argument("--today", default=None, help="YYYY-MM-DD override for tests")
    assemble_p.set_defaults(func=cmd_assemble)

    validate_p = sub.add_parser("validate-report", help="validate a daily-health report machine summary")
    validate_p.add_argument("--path", default=None, help="daily report path; defaults to today's report")
    validate_p.add_argument("--today", default=None, help="YYYY-MM-DD override when --path is omitted")
    validate_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    validate_p.set_defaults(func=cmd_validate_report)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
