#!/usr/bin/env python3
"""Run answer-level eval cases for tigermemory memory_answer.

This module provides the answer eval CLI and reusable helpers for loading JSONL
cases, running them through memory_answer_core, and summarizing status,
evidence, claim-support, not_found, and conflict metrics.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import tigermemory_core as tm_core
from tigermemory_answer import memory_answer_core, normalize_run_id


ALLOWED_CASE_SOURCES = {"real_failure", "patrol", "system_contract", "paper_seed_tmp"}
BASELINE_CASE_SOURCES = {"real_failure", "patrol", "system_contract"}
ALLOWED_EVAL_DIMENSIONS = {
    "static_state_recall",
    "dynamic_state_tracking",
    "workflow_knowledge",
    "environment_gotcha",
    "premise_awareness",
    "current_state",
    "historical_lookback",
    "multi_target_aggregation",
    "stale_obsolete",
    "action_grounding_seed",
}
ALLOWED_FRESHNESS_MODES = {"current", "historical", "stale_sensitive", "not_applicable"}


class _LegacyAnswerCompat:
    memory_answer_core = staticmethod(memory_answer_core)
    normalize_run_id = staticmethod(normalize_run_id)


tm_answer = _LegacyAnswerCompat()


def load_cases(path: str, *, allow_paper_seed_tmp: bool = False) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    source_path = Path(path)
    allow_paper_seed_tmp = allow_paper_seed_tmp or ".tmp" in source_path.parts
    with source_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if "id" not in item or "query" not in item:
                raise ValueError(f"{path}:{line_no}: case requires id and query")
            case_source = item.get("case_source")
            if case_source is not None:
                case_source = str(case_source)
                if case_source not in ALLOWED_CASE_SOURCES:
                    raise ValueError(
                        f"{path}:{line_no}: invalid case_source {case_source!r}; "
                        f"expected {sorted(ALLOWED_CASE_SOURCES)}"
                    )
                if case_source == "paper_seed_tmp" and not allow_paper_seed_tmp:
                    raise ValueError(
                        f"{path}:{line_no}: case_source=paper_seed_tmp requires an experimental .tmp fixture"
                    )
                if case_source != "paper_seed_tmp" and case_source not in BASELINE_CASE_SOURCES:
                    raise ValueError(
                        f"{path}:{line_no}: case_source {case_source!r} is not allowed in the baseline eval"
                    )
            eval_dimension = item.get("eval_dimension")
            if eval_dimension is not None:
                eval_dimension = str(eval_dimension)
                if eval_dimension not in ALLOWED_EVAL_DIMENSIONS:
                    raise ValueError(
                        f"{path}:{line_no}: invalid eval_dimension {eval_dimension!r}; "
                        f"expected {sorted(ALLOWED_EVAL_DIMENSIONS)}"
                    )
            freshness_mode = item.get("freshness_mode")
            if freshness_mode is not None:
                freshness_mode = str(freshness_mode)
                if freshness_mode not in ALLOWED_FRESHNESS_MODES:
                    raise ValueError(
                        f"{path}:{line_no}: invalid freshness_mode {freshness_mode!r}; "
                        f"expected {sorted(ALLOWED_FRESHNESS_MODES)}"
                    )
            expected_warning = item.get("expected_warning")
            if expected_warning is not None and not (
                isinstance(expected_warning, str)
                or (
                    isinstance(expected_warning, list)
                    and all(isinstance(v, str) for v in expected_warning)
                )
            ):
                raise ValueError(
                    f"{path}:{line_no}: expected_warning must be a string or list[str]"
                )
            expected_trace_flags = item.get("expected_trace_flags")
            if expected_trace_flags is not None and not (
                isinstance(expected_trace_flags, list)
                and all(isinstance(v, str) for v in expected_trace_flags)
            ):
                raise ValueError(
                    f"{path}:{line_no}: expected_trace_flags must be a list[str]"
                )
            cases.append(item)
    return cases


def default_run_id() -> str:
    stamp = dt.datetime.now(tm_core.TZ_CN).strftime("%Y%m%d-%H%M%S")
    return f"answer-eval-{stamp}-{uuid.uuid4().hex[:8]}"


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _trace_has_flag(trace: Any, flag: str) -> bool:
    if isinstance(trace, dict):
        if flag in trace:
            return True
        return any(_trace_has_flag(value, flag) for value in trace.values())
    if isinstance(trace, list):
        return any(_trace_has_flag(value, flag) for value in trace)
    return False


def eval_case(case: dict[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
    expected_trace_flags = _as_string_list(case.get("expected_trace_flags"))
    result = tm_answer.memory_answer_core(
        str(case["query"]),
        scope=str(case.get("scope", "auto")),
        top_k=int(case.get("top_k", 5)),
        max_evidence=int(case.get("max_evidence", 6)),
        include_trace=bool(expected_trace_flags),
        run_id=run_id,
    )
    expected_status = case.get("expected_status")
    expected_paths = [str(p) for p in case.get("expected_evidence_paths", [])]
    evidence_paths = [str(e.get("path")) for e in result.get("evidence", [])]
    answer_parts = [
        str(result.get("answer") or ""),
        str(result.get("summary") or ""),
    ]
    for claim in result.get("claims") or []:
        if isinstance(claim, dict):
            answer_parts.append(str(claim.get("text") or ""))
    for evidence in result.get("evidence") or []:
        if isinstance(evidence, dict):
            answer_parts.append(str(evidence.get("excerpt") or ""))
    answer_text = "\n".join(answer_parts)
    must_contain = [str(s) for s in case.get("must_contain", [])]
    expected_warning = _as_string_list(case.get("expected_warning"))
    warnings = [str(item) for item in result.get("warnings") or []]
    warning_text = "\n".join(warnings)
    warning_hit = True if not expected_warning else all(term in warning_text for term in expected_warning)
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    trace_flags_hit = True if not expected_trace_flags else all(_trace_has_flag(trace, flag) for flag in expected_trace_flags)

    status_ok = expected_status is None or result.get("status") == expected_status
    evidence_hit = True if not expected_paths else any(path in evidence_paths for path in expected_paths)
    must_contain_hit = True if not must_contain else all(term in answer_text for term in must_contain)
    claims = result.get("claims") or []
    supported_claims = [
        claim for claim in claims
        if isinstance(claim, dict) and claim.get("support")
    ]
    return {
        "id": case["id"],
        "query": case["query"],
        "expected_status": expected_status,
        "status": result.get("status"),
        "status_ok": status_ok,
        "expected_evidence_paths": expected_paths,
        "evidence_paths": evidence_paths,
        "expected_evidence_hit": evidence_hit,
        "must_contain": must_contain,
        "must_contain_hit": must_contain_hit,
        "case_source": case.get("case_source"),
        "case_source_ref": case.get("case_source_ref"),
        "eval_dimension": case.get("eval_dimension"),
        "freshness_mode": case.get("freshness_mode"),
        "expected_warning": expected_warning,
        "warning_hit": warning_hit,
        "expected_trace_flags": expected_trace_flags,
        "trace_flags_hit": trace_flags_hit,
        "claim_count": len(claims),
        "supported_claim_count": len(supported_claims),
        "trace_id": result.get("trace_id"),
        "run_id": result.get("run_id"),
        "warnings": warnings,
    }


def _group_counts(results: list[dict[str, Any]], field: str, predicate: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        label = item.get(field) or "__unset__"
        counts.setdefault(str(label), 0)
        counts[str(label)] += int(bool(item[predicate]))
    return counts


def _group_case_counts(results: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        label = item.get(field) or "__unset__"
        counts[str(label)] = counts.get(str(label), 0) + 1
    return counts


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(results)
    status_correct = sum(1 for item in results if item["status_ok"])
    evidence_expected = [item for item in results if item["expected_evidence_paths"]]
    evidence_hit = sum(1 for item in evidence_expected if item["expected_evidence_hit"])
    contain_expected = [item for item in results if item["must_contain"]]
    contain_hit = sum(1 for item in contain_expected if item["must_contain_hit"])
    total_claims = sum(item["claim_count"] for item in results)
    supported_claims = sum(item["supported_claim_count"] for item in results)
    predicted_not_found = [item for item in results if item["status"] == "not_found"]
    expected_not_found_hits = sum(1 for item in predicted_not_found if item["expected_status"] == "not_found")
    expected_conflicts = [item for item in results if item["expected_status"] == "conflict"]
    conflict_correct = sum(1 for item in expected_conflicts if item["status"] == "conflict")
    case_count_by_dimension = _group_case_counts(results, "eval_dimension")
    status_correct_by_dimension = _group_counts(results, "eval_dimension", "status_ok")
    evidence_hit_by_dimension: dict[str, int] = {}
    warning_hit_by_dimension: dict[str, int] = {}
    case_count_by_source = _group_case_counts(results, "case_source")
    status_correct_by_source = _group_counts(results, "case_source", "status_ok")
    evidence_hit_by_source: dict[str, int] = {}
    warning_hit_by_source: dict[str, int] = {}
    case_count_by_freshness = _group_case_counts(results, "freshness_mode")
    status_correct_by_freshness = _group_counts(results, "freshness_mode", "status_ok")
    evidence_hit_by_freshness: dict[str, int] = {}
    warning_hit_by_freshness: dict[str, int] = {}
    for item in results:
        dimension = str(item.get("eval_dimension") or "__unset__")
        source = str(item.get("case_source") or "__unset__")
        freshness = str(item.get("freshness_mode") or "__unset__")
        if item["expected_evidence_paths"] and item["expected_evidence_hit"]:
            evidence_hit_by_dimension[dimension] = evidence_hit_by_dimension.get(dimension, 0) + 1
            evidence_hit_by_source[source] = evidence_hit_by_source.get(source, 0) + 1
            evidence_hit_by_freshness[freshness] = evidence_hit_by_freshness.get(freshness, 0) + 1
        if item["expected_warning"] and item["warning_hit"]:
            warning_hit_by_dimension[dimension] = warning_hit_by_dimension.get(dimension, 0) + 1
            warning_hit_by_source[source] = warning_hit_by_source.get(source, 0) + 1
            warning_hit_by_freshness[freshness] = warning_hit_by_freshness.get(freshness, 0) + 1
    return {
        "case_count": case_count,
        "status_correct": status_correct,
        "status_correct_rate": status_correct / case_count if case_count else 0.0,
        "expected_evidence_case_count": len(evidence_expected),
        "expected_evidence_hit": evidence_hit,
        "expected_evidence_hit_rate": evidence_hit / len(evidence_expected) if evidence_expected else 0.0,
        "must_contain_case_count": len(contain_expected),
        "must_contain_hit": contain_hit,
        "must_contain_hit_rate": contain_hit / len(contain_expected) if contain_expected else 0.0,
        "claim_support_rate": supported_claims / total_claims if total_claims else 0.0,
        "not_found_precision": expected_not_found_hits / len(predicted_not_found) if predicted_not_found else 0.0,
        "expected_conflict_case_count": len(expected_conflicts),
        "conflict_correct": conflict_correct,
        "conflict_correct_rate": conflict_correct / len(expected_conflicts) if expected_conflicts else 1.0,
        "case_count_by_dimension": case_count_by_dimension,
        "status_correct_by_dimension": status_correct_by_dimension,
        "evidence_hit_by_dimension": evidence_hit_by_dimension,
        "warning_hit_by_dimension": warning_hit_by_dimension,
        "case_count_by_source": case_count_by_source,
        "status_correct_by_source": status_correct_by_source,
        "evidence_hit_by_source": evidence_hit_by_source,
        "warning_hit_by_source": warning_hit_by_source,
        "case_count_by_freshness": case_count_by_freshness,
        "status_correct_by_freshness": status_correct_by_freshness,
        "evidence_hit_by_freshness": evidence_hit_by_freshness,
        "warning_hit_by_freshness": warning_hit_by_freshness,
        "stale_penalty_count": case_count_by_dimension.get("stale_obsolete", 0),
        "action_seed_count": case_count_by_dimension.get("action_grounding_seed", 0),
    }


def cmd_eval(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases, allow_paper_seed_tmp=args.allow_paper_seed_tmp)
    run_id = tm_answer.normalize_run_id(args.run_id) or default_run_id()
    results = [eval_case(case, run_id=run_id) for case in cases]
    summary = summarize(results)
    failures = [
        item for item in results
        if not (
            item["status_ok"]
            and item["expected_evidence_hit"]
            and item["must_contain_hit"]
            and item["warning_hit"]
            and item["trace_flags_hit"]
        )
    ]
    report = {
        **summary,
        "run_id": run_id,
        "failures": failures,
    }
    if not args.compact:
        report["results"] = results
    else:
        report["failures"] = [
            {key: value for key, value in item.items() if key != "query"}
            for item in failures
        ]
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        print(
            f"cases={report['case_count']} "
            f"status={report['status_correct']}/{report['case_count']} "
            f"evidence={report['expected_evidence_hit']}/{report['expected_evidence_case_count']} "
            f"claim_support_rate={report['claim_support_rate']:.2f} "
            f"not_found_precision={report['not_found_precision']:.2f} "
            f"conflict={report['conflict_correct']}/{report['expected_conflict_case_count']}"
        )
        if failures:
            print(f"failures={len(failures)}")
            for item in failures[:10]:
                print(f"- {item['id']}: expected={item['expected_status']} actual={item['status']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_answer_eval.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    eval_p = sub.add_parser("eval", help="run memory_answer cases")
    eval_p.add_argument("--cases", default="tests/fixtures/memory_answer_cases.jsonl")
    eval_p.add_argument("--json", action="store_true")
    eval_p.add_argument("--compact", action="store_true", help="omit per-case successes; keep summary and failures")
    eval_p.add_argument("--run-id", default=None, help="optional run id shared by all memory_answer trace rows")
    eval_p.add_argument(
        "--allow-paper-seed-tmp",
        action="store_true",
        help="allow experimental paper_seed_tmp cases from .tmp fixtures",
    )
    eval_p.set_defaults(func=cmd_eval)
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
