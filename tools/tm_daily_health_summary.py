#!/usr/bin/env python3
"""Build machine-readable daily-health summaries from existing check outputs.

This tool is intentionally aggregation-only. It does not call LLMs, run
retrieval evals, or write reports; daily scans run the existing checks first
and feed their JSON outputs here.
Inputs: Local repo state, service health endpoints, lessons/wiki pages, Mem0 records, or dashboard preference files.
Outputs: Doctor/audit/onboarding/metrics reports, local UI helper effects, or JSON diagnostics.
Depends-on (must-have): tm_core helpers, local filesystem/git state, and configured local services when the command asks for live checks.
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

import tm_core


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_KNOWN_DEBT = REPO_ROOT / "wiki/operations/daily-health-known-debt.md"
RESOLVED_STATUSES = {"resolved", "closed", "done"}
AUTOMATION_ID = "tigermemory-daily-health-scan"
AUTOMATION_CONTRACT_SCHEMA = "daily-health-automation-contract-v1"
DAILY_SUMMARY_SCHEMA = "daily-health-summary-v1"
DAILY_TREND_SCHEMA = "daily-health-trend-v1"
DAILY_REPORT_VALIDATION_SCHEMA = "daily-health-report-validation-v1"
PROMPT_AUDIT_SCHEMA = "daily-health-prompt-audit-v1"
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
    "prompt_audit",
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
REQUIRED_CHINESE_REPORT_MARKERS = (
    "## 中文总览",
    "已验证",
    "推断",
    "待确认",
    "规划",
)
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
        "id": "report_language",
        "description": "human-facing report and closeout are Chinese-first or Chinese-English bilingual",
        "markers": ("中文优先", "Chinese-first", "中英双文"),
    },
    {
        "id": "answer_quality",
        "description": "memory_answer eval and trace checks are grouped by the current scan run id",
        "markers": ("tm_answer_eval.py eval", "tm_answer_trace.py summary", "tm_answer_trace.py failures", "--run-id", "--status error"),
    },
    {
        "id": "role_prompt_audit",
        "description": "scan audits agent role prompts and routing prompts against current tigermemory architecture",
        "markers": (
            "tm_daily_health_summary.py prompt-audit",
            "requested_topic",
            "topic taxonomy",
            "ChatGPT facade",
            "agent role prompts",
            "agent identity coverage",
        ),
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
        "id": "trend_history",
        "description": "scan builds a compact trend from historical daily-health machine summaries",
        "markers": ("tm_daily_health_summary.py trend", "daily-trend.json", "daily_trend"),
    },
    {
        "id": "report_validation",
        "description": "daily report machine summary is validated after writing",
        "markers": ("tm_daily_health_summary.py validate-report", "health_probe", "schema_version", "--require-daily-trend"),
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
PROMPT_AUDIT_TARGETS = (
    {
        "id": "root-agent-rules",
        "path": "AGENTS.md",
        "description": "repo-wide agent contract and ChatGPT delayed MCP discovery",
        "markers": (
            "Codex / ChatGPT 延迟加载 MCP 规则",
            "tool_search",
            "write_memory(agent=\"chatgpt\"",
            "topic 归属（内容决定 topic，不是 agent 固定）",
        ),
    },
    {
        "id": "agent-onboarding-snapshot",
        "path": "tools/tm_persona.py",
        "description": "deterministic get_agent_onboarding source",
        "markers": (
            "get_agent_onboarding",
            "write_memory",
            "verify_memory_id",
            "AGENTS.md",
        ),
    },
    {
        "id": "agent-access-policy",
        "path": "wiki/systems/tigermemory-agent-access.md",
        "description": "agent access list and role boundary page",
        "markers": (
            "chatgpt",
            "codex",
            "write_memory",
            "get_agent_onboarding",
        ),
    },
    {
        "id": "chatgpt-openai-facade",
        "path": "tools/tm_mcp_openai.py",
        "description": "ChatGPT connector instructions and tool descriptions",
        "markers": (
            "Narrow connector for tigermemory",
            "Pick topic by the user's domain",
            "IPFB/brand/copywriting/campaign/product/WeChat content",
            "server adds today's date",
            "preserve_requested_topic=True",
        ),
    },
    {
        "id": "memory-routing-llm",
        "path": "tools/tm_route.py",
        "description": "LLM route prompt and topic taxonomy",
        "markers": (
            "requested_topic",
            "强先验",
            "IPFB、品牌、文案、企划、商品、波段、公众号、微信图文、营销活动属于 brand",
            "systems",
            "operations",
        ),
    },
    {
        "id": "memory-write-orchestration",
        "path": "tools/tm_memory_ops.py",
        "description": "write_memory storage topic and route audit metadata",
        "markers": (
            "preserve_requested_topic",
            "route_requested_topic",
            "stored_topic",
            "topic mismatch",
        ),
    },
    {
        "id": "chatgpt-access-doc",
        "path": "wiki/systems/chatgpt-mcp-access.md",
        "description": "human-facing ChatGPT connector runbook",
        "markers": (
            "ChatGPT",
            "requested_topic",
            "topic_inferred",
            "公众号",
            "brand",
        ),
    },
    {
        "id": "memory-answer-evidence-policy",
        "path": "wiki/systems/memory-answer-evidence-policy.md",
        "description": "Memory Answer evidence authority and trace privacy policy",
        "markers": (
            "canonical_wiki",
            "mem0_recent",
            "TM_ANSWER_TRACE_RAW_QUERY",
            "--disable-trace-write",
        ),
    },
)
ROLE_IDENTITY_COVERAGE_PATH = "wiki/systems/tigermemory-agent-access.md"


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


def audit_role_prompts(*, root: pathlib.Path = REPO_ROOT) -> dict[str, Any]:
    """Audit deterministic agent prompts and role docs for current architecture markers."""
    checks: list[dict[str, Any]] = []
    for target in PROMPT_AUDIT_TARGETS:
        rel = str(target["path"])
        path = root / rel
        if not path.exists():
            checks.append({
                "id": target["id"],
                "path": rel,
                "description": target["description"],
                "ok": False,
                "missing_markers": list(target["markers"]),
                "error": "missing file",
            })
            continue
        text = _read_text(path)
        missing = [
            marker for marker in target["markers"]
            if marker not in text
        ]
        checks.append({
            "id": target["id"],
            "path": rel,
            "description": target["description"],
            "ok": not missing,
            "missing_markers": missing,
            "error": None,
        })
    role_doc = root / ROLE_IDENTITY_COVERAGE_PATH
    agent_names = sorted(tm_core.AGENTS)
    regular_wiki_agents = sorted(getattr(tm_core, "_ALL_REGULAR_AGENTS", ()))
    role_doc_text = _read_text(role_doc) if role_doc.exists() else ""
    missing_agents = [
        agent for agent in agent_names
        if agent not in role_doc_text
    ]
    checks.append({
        "id": "agent-identity-coverage",
        "path": ROLE_IDENTITY_COVERAGE_PATH,
        "description": "agent access policy covers every identity from tm_core.AGENTS",
        "ok": role_doc.exists() and not missing_agents,
        "missing_markers": missing_agents,
        "error": None if role_doc.exists() else "missing file",
    })
    missing_checks = [check for check in checks if not check["ok"]]
    return {
        "schema_version": PROMPT_AUDIT_SCHEMA,
        "status": "ok" if not missing_checks else "fail",
        "check_count": len(checks),
        "passed_count": len(checks) - len(missing_checks),
        "missing_count": len(missing_checks),
        "agent_count": len(agent_names),
        "agents": agent_names,
        "regular_wiki_agents": regular_wiki_agents,
        "missing": missing_checks,
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
        "run_id",
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
        "selected_run_id": summary.get("selected_run_id") or failures.get("selected_run_id"),
        "run_id_counts": summary.get("run_id_counts") or {},
        "run_id_missing_count": summary.get("run_id_missing_count"),
        "status_counts": summary.get("status_counts") or {},
        "llm_counts": summary.get("llm_counts") or {},
        "duration_ms": summary.get("duration_ms") or {},
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


def compact_prompt_audit(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    missing_ids = [
        str(item.get("id") or "")
        for item in report.get("missing") or []
        if isinstance(item, dict) and item.get("id")
    ]
    compact = {
        "schema_version": report.get("schema_version"),
        "status": report.get("status"),
        "check_count": report.get("check_count"),
        "passed_count": report.get("passed_count"),
        "missing_count": report.get("missing_count"),
        "missing_ids": missing_ids,
    }
    if report.get("agent_count") is not None:
        compact["agent_count"] = report.get("agent_count")
    return compact


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    top = _number(numerator)
    bottom = _number(denominator)
    if top is None or bottom in (None, 0):
        return None
    return round(top / bottom, 4)


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _report_path_label(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def load_daily_report_summaries(
    reports_dir: pathlib.Path | str | None = None,
    *,
    days: int = 14,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load machine summaries from tracked daily-health reports."""
    root = pathlib.Path(reports_dir) if reports_dir else REPO_ROOT / "wiki/operations/daily-health"
    if not root.exists():
        return [], [{"path": str(root), "error": "reports directory not found"}]
    candidates = [
        path for path in sorted(root.glob("*.md"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
    ]
    if days > 0:
        candidates = candidates[-days:]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for path in candidates:
        try:
            summary, summary_errors = extract_machine_summary(_read_text(path))
        except Exception as exc:  # pragma: no cover - defensive for corrupt files
            errors.append({"date": path.stem, "path": _report_path_label(path), "error": str(exc)})
            continue
        if summary is None:
            errors.append({
                "date": path.stem,
                "path": _report_path_label(path),
                "errors": summary_errors,
            })
            continue
        if summary_errors:
            errors.append({
                "date": path.stem,
                "path": _report_path_label(path),
                "errors": summary_errors,
            })
        rows.append({
            "date": path.stem,
            "path": _report_path_label(path),
            "summary": summary,
        })
    return rows, errors


def _compact_daily_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    answer_eval = summary.get("answer_eval") if isinstance(summary.get("answer_eval"), dict) else {}
    answer_trace = summary.get("answer_trace") if isinstance(summary.get("answer_trace"), dict) else {}
    lexical = summary.get("retrieval_eval_lexical") if isinstance(summary.get("retrieval_eval_lexical"), dict) else {}
    hybrid = summary.get("retrieval_eval_hybrid") if isinstance(summary.get("retrieval_eval_hybrid"), dict) else {}
    health = summary.get("health_probe") if isinstance(summary.get("health_probe"), dict) else {}
    prompt_audit = summary.get("prompt_audit") if isinstance(summary.get("prompt_audit"), dict) else {}
    known_debt_changes = summary.get("known_debt_changes") if isinstance(summary.get("known_debt_changes"), dict) else {}
    answer_status_rate = _ratio(answer_eval.get("status_correct"), answer_eval.get("case_count"))
    return {
        "date": row.get("date"),
        "path": row.get("path"),
        "health_color": summary.get("health_color"),
        "blocking_count": _int(summary.get("blocking_count")),
        "new_problem_count": _int(summary.get("new_problem_count")),
        "known_debt_count": _int(summary.get("known_debt_count")),
        "known_debt_changes": {
            "new": _int(known_debt_changes.get("new")) or 0,
            "known": _int(known_debt_changes.get("known")) or 0,
            "resolved": _int(known_debt_changes.get("resolved")) or 0,
            "worsened": _int(known_debt_changes.get("worsened")) or 0,
        },
        "mem0_api_reachable": health.get("mem0_api_reachable"),
        "mem0_api_latency_ms": _number(health.get("mem0_api_latency_ms")),
        "answer_status_rate": answer_status_rate,
        "answer_status_correct": _int(answer_eval.get("status_correct")),
        "answer_case_count": _int(answer_eval.get("case_count")),
        "answer_failure_count": _int(answer_eval.get("failure_count")) or 0,
        "answer_trace_failure_count": _int(answer_trace.get("failure_count")) or 0,
        "answer_trace_p95_ms": _number(_nested(answer_trace, "duration_ms", "p95")),
        "prompt_audit_status": prompt_audit.get("status"),
        "retrieval_lexical_hit3_rate": _ratio(lexical.get("hit3"), lexical.get("case_count")),
        "retrieval_hybrid_hit3_rate": _ratio(hybrid.get("hit3"), hybrid.get("case_count")),
        "commit_sha": summary.get("commit_sha"),
        "push_result": summary.get("push_result"),
    }


def _latest_number(rows: list[dict[str, Any]], key: str) -> float | int | None:
    for row in reversed(rows):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def _max_number(rows: list[dict[str, Any]], key: str) -> float | int | None:
    values = [
        value for row in rows
        if isinstance((value := row.get(key)), (int, float)) and not isinstance(value, bool)
    ]
    return max(values) if values else None


def _min_number(rows: list[dict[str, Any]], key: str) -> float | int | None:
    values = [
        value for row in rows
        if isinstance((value := row.get(key)), (int, float)) and not isinstance(value, bool)
    ]
    return min(values) if values else None


def build_daily_health_trend(
    reports_dir: pathlib.Path | str | None = None,
    *,
    days: int = 14,
) -> dict[str, Any]:
    rows, errors = load_daily_report_summaries(reports_dir, days=days)
    days_compact = [_compact_daily_summary(row) for row in rows]
    color_counts = Counter(str(row.get("health_color") or "unknown") for row in days_compact)
    problem_days = [
        str(row["date"])
        for row in days_compact
        if (row.get("health_color") not in (None, "green", "ok"))
        or (row.get("blocking_count") or 0) > 0
        or (row.get("new_problem_count") or 0) > 0
        or (row.get("answer_failure_count") or 0) > 0
        or (row.get("answer_trace_failure_count") or 0) > 0
        or row.get("prompt_audit_status") not in (None, "ok")
        or row.get("mem0_api_reachable") is False
    ]
    latest = days_compact[-1] if days_compact else None
    return {
        "schema_version": DAILY_TREND_SCHEMA,
        "report_count": len(days_compact),
        "date_range": {
            "start": days_compact[0]["date"] if days_compact else None,
            "end": days_compact[-1]["date"] if days_compact else None,
        },
        "latest": latest,
        "health_color_counts": dict(sorted(color_counts.items())),
        "totals": {
            "blocking_count": sum(row.get("blocking_count") or 0 for row in days_compact),
            "new_problem_count": sum(row.get("new_problem_count") or 0 for row in days_compact),
            "known_debt_new": sum(row["known_debt_changes"]["new"] for row in days_compact),
            "known_debt_resolved": sum(row["known_debt_changes"]["resolved"] for row in days_compact),
            "known_debt_worsened": sum(row["known_debt_changes"]["worsened"] for row in days_compact),
        },
        "answer_eval": {
            "latest_status_rate": _latest_number(days_compact, "answer_status_rate"),
            "min_status_rate": _min_number(days_compact, "answer_status_rate"),
            "failure_days": [
                row["date"] for row in days_compact if (row.get("answer_failure_count") or 0) > 0
            ],
        },
        "answer_trace": {
            "latest_failure_count": _latest_number(days_compact, "answer_trace_failure_count"),
            "total_failure_count": sum(row.get("answer_trace_failure_count") or 0 for row in days_compact),
            "latest_p95_ms": _latest_number(days_compact, "answer_trace_p95_ms"),
            "max_p95_ms": _max_number(days_compact, "answer_trace_p95_ms"),
        },
        "retrieval_eval": {
            "lexical_latest_hit3_rate": _latest_number(days_compact, "retrieval_lexical_hit3_rate"),
            "lexical_min_hit3_rate": _min_number(days_compact, "retrieval_lexical_hit3_rate"),
            "hybrid_latest_hit3_rate": _latest_number(days_compact, "retrieval_hybrid_hit3_rate"),
            "hybrid_min_hit3_rate": _min_number(days_compact, "retrieval_hybrid_hit3_rate"),
        },
        "health_probe": {
            "latest_mem0_api_reachable": latest.get("mem0_api_reachable") if latest else None,
            "latest_mem0_api_latency_ms": latest.get("mem0_api_latency_ms") if latest else None,
            "max_mem0_api_latency_ms": _max_number(days_compact, "mem0_api_latency_ms"),
            "unreachable_days": [
                row["date"] for row in days_compact if row.get("mem0_api_reachable") is False
            ],
        },
        "problem_day_count": len(problem_days),
        "problem_days": problem_days,
        "errors": errors,
        "days": days_compact,
    }


def compact_daily_trend(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else None
    return {
        "schema_version": report.get("schema_version"),
        "report_count": report.get("report_count"),
        "date_range": report.get("date_range"),
        "latest": latest,
        "health_color_counts": report.get("health_color_counts") or {},
        "totals": report.get("totals") or {},
        "answer_eval": report.get("answer_eval") or {},
        "answer_trace": report.get("answer_trace") or {},
        "retrieval_eval": report.get("retrieval_eval") or {},
        "health_probe": report.get("health_probe") or {},
        "problem_day_count": report.get("problem_day_count"),
        "problem_days": (report.get("problem_days") or [])[-7:],
        "error_count": len(report.get("errors") or []),
    }


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


def validate_daily_report(
    text: str,
    *,
    path: pathlib.Path | str | None = None,
    require_daily_trend: bool = False,
) -> dict[str, Any]:
    missing_sections: list[str] = []
    if not any(heading in text for heading in SOURCE_HEADINGS):
        missing_sections.append("sources")
    missing_language_markers = [
        marker for marker in REQUIRED_CHINESE_REPORT_MARKERS
        if marker not in text
    ]
    if missing_language_markers:
        missing_sections.append("chinese-first language contract")
    summary, errors = extract_machine_summary(text)
    missing_fields: list[str] = []
    nested_missing_fields: list[str] = []
    if summary is None:
        missing_sections.append("machine-readable summary")
    else:
        missing_fields = [key for key in REQUIRED_DAILY_SUMMARY_FIELDS if key not in summary]
        if summary.get("schema_version") != DAILY_SUMMARY_SCHEMA:
            errors.append(f"schema_version must be {DAILY_SUMMARY_SCHEMA}")
        if require_daily_trend and "daily_trend" not in summary:
            missing_fields.append("daily_trend")
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
        "missing_language_markers": missing_language_markers,
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


def cmd_prompt_audit(args: argparse.Namespace) -> int:
    report = audit_role_prompts()
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    elif report["status"] == "ok":
        sys.stdout.write(f"ok: prompt audit passed ({report['passed_count']}/{report['check_count']})\n")
    else:
        sys.stdout.write(f"fail: prompt audit missing {report['missing_count']} checks\n")
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
        "prompt_audit": compact_prompt_audit(_optional_json(args.prompt_audit)),
        "retrieval_eval_lexical": compact_retrieval_eval(_optional_json(args.retrieval_lexical)),
        "retrieval_eval_hybrid": compact_retrieval_eval(_optional_json(args.retrieval_hybrid)),
        "daily_trend": compact_daily_trend(_optional_json(args.daily_trend)),
        "commit_sha": args.commit_sha or current_commit_sha(),
        "push_result": args.push_result,
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def cmd_trend(args: argparse.Namespace) -> int:
    report = build_daily_health_trend(args.reports_dir, days=args.days)
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        latest = report.get("latest") or {}
        date_range = report.get("date_range") or {}
        sys.stdout.write(
            f"reports={report['report_count']} range={date_range.get('start')}..{date_range.get('end')} "
            f"latest={latest.get('date')} color={latest.get('health_color')} "
            f"problems={report['problem_day_count']} "
            f"answer_p95={_nested(report, 'answer_trace', 'latest_p95_ms')} "
            f"hybrid_hit3_rate={_nested(report, 'retrieval_eval', 'hybrid_latest_hit3_rate')}\n"
        )
    return 0


def cmd_validate_report(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path) if args.path else default_daily_report_path(args.today)
    try:
        report = validate_daily_report(_read_text(path), path=path, require_daily_trend=args.require_daily_trend)
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

    prompt_audit_p = sub.add_parser("prompt-audit", help="audit agent role prompts and routing prompts")
    prompt_audit_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    prompt_audit_p.set_defaults(func=cmd_prompt_audit)

    trend_p = sub.add_parser("trend", help="summarize historical daily-health machine summaries")
    trend_p.add_argument("--reports-dir", default=None, help="daily-health report directory; defaults to wiki/operations/daily-health")
    trend_p.add_argument("--days", type=int, default=14, help="number of latest dated reports to include; <=0 means all")
    trend_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    trend_p.set_defaults(func=cmd_trend)

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
    assemble_p.add_argument("--prompt-audit", default=None)
    assemble_p.add_argument("--retrieval-lexical", default=None)
    assemble_p.add_argument("--retrieval-hybrid", default=None)
    assemble_p.add_argument("--daily-trend", default=None)
    assemble_p.add_argument("--commit-sha", default=None)
    assemble_p.add_argument("--push-result", default="pending")
    assemble_p.add_argument("--today", default=None, help="YYYY-MM-DD override for tests")
    assemble_p.set_defaults(func=cmd_assemble)

    validate_p = sub.add_parser("validate-report", help="validate a daily-health report machine summary")
    validate_p.add_argument("--path", default=None, help="daily report path; defaults to today's report")
    validate_p.add_argument("--today", default=None, help="YYYY-MM-DD override when --path is omitted")
    validate_p.add_argument("--require-daily-trend", action="store_true", help="require the P5 daily_trend field")
    validate_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    validate_p.set_defaults(func=cmd_validate_report)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
