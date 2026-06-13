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
import statistics
import sys
import uuid
from pathlib import Path
from typing import Any

import tigermemory_core as tm_core
from tigermemory_answer import memory_answer_core, normalize_run_id

try:
    import tm_llm_wiki_map
except Exception:  # pragma: no cover - optional runtime tool path
    tm_llm_wiki_map = None  # type: ignore[assignment]


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
ALLOWED_QUERY_INTENT_BUCKETS = {
    "topic_locator",
    "tail_detail",
    "workflow_fact",
    "negative",
    "unspecified",
}


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
            query_intent_bucket = item.get("query_intent_bucket")
            if query_intent_bucket is not None:
                query_intent_bucket = str(query_intent_bucket)
                if query_intent_bucket not in ALLOWED_QUERY_INTENT_BUCKETS:
                    raise ValueError(
                        f"{path}:{line_no}: invalid query_intent_bucket {query_intent_bucket!r}; "
                        f"expected {sorted(ALLOWED_QUERY_INTENT_BUCKETS)}"
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


def _query_intent_bucket(case: dict[str, Any]) -> str:
    bucket = str(case.get("query_intent_bucket") or "unspecified")
    if bucket not in ALLOWED_QUERY_INTENT_BUCKETS:
        return "unspecified"
    return bucket


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


def eval_case(
    case: dict[str, Any],
    *,
    run_id: str | None = None,
    write_trace: bool = True,
) -> dict[str, Any]:
    expected_trace_flags = _as_string_list(case.get("expected_trace_flags"))
    result = tm_answer.memory_answer_core(
        str(case["query"]),
        scope=str(case.get("scope", "auto")),
        top_k=int(case.get("top_k", 5)),
        max_evidence=int(case.get("max_evidence", 6)),
        include_trace=bool(expected_trace_flags),
        run_id=run_id,
        write_trace=write_trace,
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


def _normalize_case_path(path: Any) -> str:
    text = str(path or "").strip().strip('"').strip("'").replace("\\", "/")
    if not text:
        return ""
    repo = str(tm_core.REPO_ROOT).replace("\\", "/").rstrip("/")
    lower = text.lower()
    repo_lower = repo.lower()
    if lower.startswith(repo_lower + "/"):
        text = text[len(repo) + 1:]
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _repo_path_exists(path: str) -> bool:
    rel = _normalize_case_path(path)
    if not rel:
        return False
    return (tm_core.REPO_ROOT / rel).exists()


def _rank_for_expected(paths: list[str], expected_paths: list[str]) -> int | None:
    normalized_expected = {_normalize_case_path(path) for path in expected_paths if _normalize_case_path(path)}
    if not normalized_expected:
        return None
    for index, path in enumerate(paths, 1):
        if _normalize_case_path(path) in normalized_expected:
            return index
    return None


def _paths_from_hits(hits: list[dict[str, Any]]) -> list[str]:
    return [_normalize_case_path(hit.get("path")) for hit in hits if hit.get("path")]


def _anchor_queries_for_expected_paths(expected_paths: list[str]) -> list[str]:
    queries: list[str] = []
    for path in expected_paths:
        rel = _normalize_case_path(path)
        if not rel:
            continue
        queries.append(rel)
        name = Path(rel).stem
        if name:
            queries.append(name)
            queries.append(name.replace("-", " "))
    unique: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.strip()
        if key and key not in seen:
            unique.append(key)
            seen.add(key)
    return unique[:8]


def _anchor_rank_for_expected(expected_paths: list[str], top_k: int) -> int | None:
    best: int | None = None
    for query in _anchor_queries_for_expected_paths(expected_paths):
        paths = _paths_from_hits(
            tm_core.search_wiki_hybrid(query, size=top_k, include_sources=True, include_inbox=False, explain=True)
        )
        rank = _rank_for_expected(paths, expected_paths)
        if rank is not None:
            best = rank if best is None else min(best, rank)
    return best


def _answer_text(result: dict[str, Any]) -> str:
    parts = [
        str(result.get("answer") or ""),
        str(result.get("summary") or ""),
    ]
    for claim in result.get("claims") or []:
        if isinstance(claim, dict):
            parts.append(str(claim.get("text") or ""))
    for evidence in result.get("evidence") or []:
        if isinstance(evidence, dict):
            parts.append(str(evidence.get("excerpt") or ""))
    return "\n".join(parts)


def _trace_has_expected_validity(trace: dict[str, Any], markers: list[str]) -> bool:
    if not markers:
        return True
    haystack = json.dumps(trace.get("validity") or {}, ensure_ascii=False)
    haystack += "\n" + json.dumps(trace.get("evidence_gate") or [], ensure_ascii=False)
    return all(marker in haystack for marker in markers)


def _primary_scope_from_trace(trace: dict[str, Any]) -> str | None:
    for call in trace.get("calls") or []:
        if isinstance(call, dict) and call.get("primary_scope"):
            return str(call.get("primary_scope"))
    return None


def _planner_llm_used(trace: dict[str, Any]) -> bool:
    for call in trace.get("calls") or []:
        if not isinstance(call, dict):
            continue
        if call.get("tool") == "DeepSeek" and call.get("purpose") == "memory_query_plan":
            return True
    planner = trace.get("planner") if isinstance(trace, dict) else {}
    return isinstance(planner, dict) and planner.get("planner_source") == "llm"


def _evidence_gate_paths(
    trace: dict[str, Any],
    *,
    keep: bool | None = None,
    selected: bool | None = None,
) -> list[str]:
    gate = trace.get("evidence_gate") if isinstance(trace, dict) else []
    if not isinstance(gate, list):
        return []
    paths: list[str] = []
    for item in gate:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        if keep is not None and bool(item.get("keep")) is not keep:
            continue
        if selected is not None and bool(item.get("selected")) is not selected:
            continue
        paths.append(_normalize_case_path(item.get("path")))
    return paths


def _map_rank_band(rank: int | None) -> str:
    if rank is None:
        return "map_miss"
    if rank <= 10:
        return "top10"
    if rank <= 30:
        return "top11_30"
    if rank <= 80:
        return "top31_80"
    return "after80"


def _gate_entry_for_expected(trace: dict[str, Any], expected_paths: list[str]) -> dict[str, Any] | None:
    normalized_expected = {_normalize_case_path(path) for path in expected_paths if _normalize_case_path(path)}
    if not normalized_expected:
        return None
    gate = trace.get("evidence_gate") if isinstance(trace, dict) else []
    if not isinstance(gate, list):
        return None
    for item in gate:
        if isinstance(item, dict) and _normalize_case_path(item.get("path")) in normalized_expected:
            return item
    return None


def _gate_reason_category(
    entry: dict[str, Any] | None,
    *,
    map_rank: int | None,
    missing_expected_paths: list[str],
) -> str:
    if missing_expected_paths:
        return "missing_knowledge"
    if entry is None:
        return "not_in_gate" if map_rank is not None else "map_miss"
    if entry.get("selected"):
        return "selected"
    validity = str(entry.get("validity") or "").lower()
    reason = str(entry.get("reason") or entry.get("validity_reason") or "").lower()
    haystack = f"{validity}\n{reason}"
    if "conflict" in haystack:
        return "conflict"
    if any(token in haystack for token in ("stale", "obsolete", "old", "unknown_date", "historical")):
        return "stale_or_date"
    if "authority" in haystack:
        return "authority"
    if any(token in haystack for token in ("relevance", "match", "weak", "low_quality", "low quality")):
        return "relevance"
    if entry.get("keep") is False:
        return "gate_rejected_unknown"
    if entry.get("keep") is True:
        return "kept_not_selected"
    return "unknown"


def _map_bridge_bucket(
    *,
    expected_paths: list[str],
    missing_expected_paths: list[str],
    trace_present: bool,
    map_rank: int | None,
    gate_rank: int | None,
    gate_selected_rank: int | None,
    gate_rejected_rank: int | None,
    evidence_rank: int | None,
    passed: bool,
    prompt_budget_truncated: bool,
) -> str:
    if not expected_paths:
        return "no_expected_path"
    if missing_expected_paths:
        return "missing_knowledge"
    if map_rank is None:
        return "map_miss"
    if not trace_present:
        return "trace_missing"
    if evidence_rank is not None:
        if not passed and prompt_budget_truncated:
            return "map_to_evidence_ok_answer_budget_risk"
        return "map_to_evidence_ok"
    if gate_rank is None:
        if map_rank <= 10:
            return "map_top10_not_in_gate"
        if map_rank <= 30:
            return "map_top30_not_in_gate"
        return "map_deep_rank_not_in_gate"
    if gate_rejected_rank is not None:
        return "evidence_gate_rejected"
    if gate_selected_rank is None:
        return "evidence_selection_topk_miss"
    return "map_hit_evidence_miss_unknown"


def _append_unique_path(paths: list[str], seen: set[str], value: Any) -> None:
    path = _normalize_case_path(value)
    if path and path not in seen:
        paths.append(path)
        seen.add(path)


def _candidate_paths_from_payload(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        candidates = payload.get("candidates")
    else:
        candidates = payload
    if not isinstance(candidates, list):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            _append_unique_path(paths, seen, item.get("path"))
    return paths


def _recommendation_candidate_paths(result: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for payload in (
        trace.get("related_evidence_candidates"),
        result.get("related_evidence_candidates"),
        trace.get("recommendation_boosted_candidates"),
    ):
        for path in _candidate_paths_from_payload(payload):
            _append_unique_path(paths, seen, path)
    return paths


def _recommendation_evidence_paths(trace: dict[str, Any], evidence_paths: list[str]) -> list[str]:
    evidence_set = {_normalize_case_path(path) for path in evidence_paths if _normalize_case_path(path)}
    boosted = trace.get("recommendation_boosted_candidates") if isinstance(trace, dict) else {}
    candidates = boosted.get("candidates") if isinstance(boosted, dict) else []
    if not isinstance(candidates, list):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        path = _normalize_case_path(item.get("path"))
        accepted = (
            item.get("action") == "accepted_to_evidence"
            or item.get("gate_outcome") == "evidence_gate_passed"
        )
        if accepted and path in evidence_set:
            _append_unique_path(paths, seen, path)
    return paths


def _compact_diagnosis_row(row: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id",
        "passed",
        "failure_layer",
        "failure_reasons",
        "status",
        "expected_status",
        "diagnosis_category",
        "eval_dimension",
        "freshness_mode",
        "case_source",
        "case_source_ref",
        "query_intent_bucket",
        "runtime_dependency",
        "actionability_expectation",
        "expected_evidence_paths",
        "missing_expected_paths",
        "outside_partition_paths",
        "lexical_rank",
        "hybrid_rank",
        "anchor_rank",
        "evidence_gate_rank",
        "evidence_gate_selected_rank",
        "evidence_gate_rejected_rank",
        "evidence_gate_keep",
        "evidence_gate_selected",
        "evidence_gate_reason_category",
        "evidence_gate_validity",
        "evidence_gate_bridge_source",
        "evidence_gate_authority",
        "evidence_gate_relevance",
        "answer_evidence_rank",
        "raw_retrieval_hit",
        "map_candidate_hit",
        "map_hit@10",
        "map_hit@30",
        "map_hit@80",
        "map_rank",
        "map_rank_band",
        "map_bridge_bucket",
        "map_hit_but_evidence_miss",
        "map_compensated_hit",
        "recommendation_candidate_rank",
        "recommendation_candidate_hit@5",
        "recommendation_evidence_hit",
        "prompt_budget_truncated",
        "not_in_map",
        "planner_llm_hit",
        "planner_compensated_hit",
        "expected_rank",
        "primary_scope",
        "expected_primary_scope",
        "trace_present",
        "trace_id",
        "run_id",
    ]
    return {key: row.get(key) for key in keep if key in row}


def diagnose_case(
    case: dict[str, Any],
    *,
    run_id: str | None = None,
    write_trace: bool = False,
    top_k_probe: int | None = None,
    map_probe_k: int | None = None,
) -> dict[str, Any]:
    """Run one answer case and attribute failures to the first likely layer."""
    expected_paths = [_normalize_case_path(path) for path in case.get("expected_evidence_paths", [])]
    forbidden_paths = [_normalize_case_path(path) for path in case.get("forbidden_evidence_paths", [])]
    query_intent_bucket = _query_intent_bucket(case)
    top_k = min(max(int(case.get("top_k", 5)), 1), 10)
    probe_k = min(max(int(top_k_probe or case.get("top_k_probe", 10)), 1), 20)
    map_k = min(max(int(map_probe_k or case.get("map_probe_k", 80)), 1), 120)
    query = str(case["query"])

    result = tm_answer.memory_answer_core(
        query,
        scope=str(case.get("scope", "auto")),
        top_k=top_k,
        max_evidence=int(case.get("max_evidence", 6)),
        include_trace=True,
        run_id=run_id,
        write_trace=write_trace,
    )
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    trace_present = isinstance(result.get("trace"), dict)
    lexical_hits = tm_core.search_wiki(query, size=probe_k, include_sources=True, include_inbox=False, explain=True)
    hybrid_hits = tm_core.search_wiki_hybrid(query, size=probe_k, include_sources=True, include_inbox=False, explain=True)
    lexical_paths = _paths_from_hits(lexical_hits)
    hybrid_paths = _paths_from_hits(hybrid_hits)
    map_error: str | None = None
    map_hits: list[dict[str, Any]] = []
    if tm_llm_wiki_map is not None:
        try:
            map_path = tm_core.REPO_ROOT / "runtime" / "llm_wiki" / "wiki_map.jsonl"
            map_hits = tm_llm_wiki_map.map_recall(query, limit=map_k, map_path=map_path)
        except Exception as exc:
            map_error = f"{type(exc).__name__}: {exc}"
    else:
        map_error = "tm_llm_wiki_map_unavailable"
    map_paths = [_normalize_case_path(hit.get("path")) for hit in map_hits if isinstance(hit, dict)]
    gate_paths = _evidence_gate_paths(trace)
    gate_selected_paths = _evidence_gate_paths(trace, selected=True)
    gate_rejected_paths = _evidence_gate_paths(trace, keep=False)
    evidence_paths = [_normalize_case_path(e.get("path")) for e in result.get("evidence", []) if isinstance(e, dict)]
    recommendation_paths = _recommendation_candidate_paths(result, trace)
    recommendation_evidence_paths = _recommendation_evidence_paths(trace, evidence_paths)

    missing_expected_paths = [path for path in expected_paths if not _repo_path_exists(path)]
    lexical_rank = _rank_for_expected(lexical_paths, expected_paths)
    hybrid_rank = _rank_for_expected(hybrid_paths, expected_paths)
    map_rank = _rank_for_expected(map_paths, expected_paths)
    anchor_rank = _anchor_rank_for_expected(expected_paths, probe_k) if expected_paths else None
    gate_rank = _rank_for_expected(gate_paths, expected_paths)
    gate_selected_rank = _rank_for_expected(gate_selected_paths, expected_paths)
    gate_rejected_rank = _rank_for_expected(gate_rejected_paths, expected_paths)
    expected_gate_entry = _gate_entry_for_expected(trace, expected_paths)
    gate_reason_category = _gate_reason_category(
        expected_gate_entry,
        map_rank=map_rank,
        missing_expected_paths=missing_expected_paths,
    )
    evidence_rank = _rank_for_expected(evidence_paths, expected_paths)
    recommendation_candidate_rank = _rank_for_expected(recommendation_paths, expected_paths)
    recommendation_evidence_rank = _rank_for_expected(recommendation_evidence_paths, expected_paths)
    expected_rank_raw = case.get("expected_rank")
    expected_rank = int(expected_rank_raw) if expected_rank_raw is not None else None

    answer_text = _answer_text(result)
    must_contain = [str(term) for term in case.get("must_contain", [])]
    must_not_contain = [str(term) for term in case.get("must_not_contain", [])]
    expected_warning = _as_string_list(case.get("expected_warning"))
    expected_trace_flags = _as_string_list(case.get("expected_trace_flags"))
    expected_validity_markers = _as_string_list(case.get("expected_validity_markers"))
    warning_text = "\n".join(str(item) for item in result.get("warnings") or [])
    primary_scope = _primary_scope_from_trace(trace)
    expected_primary_scope = str(case.get("expected_primary_scope") or "") or None
    expected_partition = str(case.get("expected_partition") or "") or None

    status_ok = case.get("expected_status") is None or result.get("status") == case.get("expected_status")
    evidence_hit = True if not expected_paths else evidence_rank is not None
    lexical_hit = True if not expected_paths else lexical_rank is not None
    hybrid_hit = True if not expected_paths else hybrid_rank is not None
    anchor_hit = True if not expected_paths else anchor_rank is not None
    gate_hit = True if not expected_paths else gate_rank is not None
    raw_retrieval_hit = lexical_hit or hybrid_hit
    map_candidate_hit = True if not expected_paths else map_rank is not None
    map_hit_10 = True if not expected_paths else map_rank is not None and map_rank <= 10
    map_hit_30 = True if not expected_paths else map_rank is not None and map_rank <= 30
    map_hit_80 = True if not expected_paths else map_rank is not None and map_rank <= 80
    not_in_map = False if not expected_paths else map_rank is None
    recommendation_candidate_hit_5 = (
        True if not expected_paths else recommendation_candidate_rank is not None and recommendation_candidate_rank <= 5
    )
    recommendation_evidence_hit = True if not expected_paths else recommendation_evidence_rank is not None
    planner_llm_hit = bool(_planner_llm_used(trace) and evidence_hit)
    planner_compensated_hit = evidence_hit and not raw_retrieval_hit
    map_compensated_hit = evidence_hit and not raw_retrieval_hit and map_candidate_hit
    rank_ok = True if expected_rank is None or hybrid_rank is None else hybrid_rank <= expected_rank
    forbidden_ok = not any(path in set(evidence_paths) for path in forbidden_paths)
    must_contain_hit = all(term in answer_text for term in must_contain)
    must_not_contain_hit = all(term not in answer_text for term in must_not_contain)
    warning_hit = True if not expected_warning else all(term in warning_text for term in expected_warning)
    trace_flags_hit = True if not expected_trace_flags else all(_trace_has_flag(trace, flag) for flag in expected_trace_flags)
    validity_hit = _trace_has_expected_validity(trace, expected_validity_markers)
    primary_scope_ok = True if not expected_primary_scope else primary_scope == expected_primary_scope
    outside_partition_paths: list[str] = []
    if expected_partition:
        outside_partition_paths = [
            path
            for path in evidence_paths
            if path.startswith("wiki/") and not path.startswith(f"wiki/{expected_partition}/")
        ]
    partition_ok = not outside_partition_paths

    failure_reasons: list[str] = []
    checks = {
        "status_ok": status_ok,
        "evidence_hit": evidence_hit,
        "rank_ok": rank_ok,
        "forbidden_ok": forbidden_ok,
        "must_contain_hit": must_contain_hit,
        "must_not_contain_hit": must_not_contain_hit,
        "warning_hit": warning_hit,
        "trace_flags_hit": trace_flags_hit,
        "validity_hit": validity_hit,
        "primary_scope_ok": primary_scope_ok,
        "partition_ok": partition_ok,
    }
    for key, ok in checks.items():
        if not ok:
            failure_reasons.append(key)

    if not failure_reasons:
        failure_layer = "ok"
    elif missing_expected_paths:
        failure_layer = "missing_knowledge"
    elif not anchor_hit:
        failure_layer = "knowledge_not_indexed"
    elif not primary_scope_ok:
        failure_layer = "scope_partition_routing"
    elif anchor_hit and not lexical_hit and not hybrid_hit:
        failure_layer = "natural_query_recall_miss"
    elif not lexical_hit and hybrid_hit:
        failure_layer = "lexical_recall_miss"
    elif not hybrid_hit and not gate_hit and not evidence_hit:
        failure_layer = "hybrid_recall_miss"
    elif not rank_ok:
        failure_layer = "ranking_topk_miss"
    elif gate_hit and not evidence_hit:
        failure_layer = "evidence_selection_miss"
    elif not validity_hit:
        failure_layer = "freshness_stale_guard_miss"
    elif not partition_ok or not forbidden_ok:
        failure_layer = "boundary_violation"
    elif case.get("runtime_dependency") and not warning_hit:
        failure_layer = "runtime_grounding_gap"
    elif case.get("actionability_expectation") and (not must_contain_hit or not must_not_contain_hit):
        failure_layer = "actionability_gap"
    else:
        failure_layer = "answer_synthesis_miss"
    prompt_budget_truncated = bool(trace.get("prompt_budget_truncated"))
    map_rank_band = _map_rank_band(map_rank)
    map_hit_but_evidence_miss = bool(expected_paths and map_rank is not None and evidence_rank is None)
    map_bridge_bucket = _map_bridge_bucket(
        expected_paths=expected_paths,
        missing_expected_paths=missing_expected_paths,
        trace_present=trace_present,
        map_rank=map_rank,
        gate_rank=gate_rank,
        gate_selected_rank=gate_selected_rank,
        gate_rejected_rank=gate_rejected_rank,
        evidence_rank=evidence_rank,
        passed=failure_layer == "ok",
        prompt_budget_truncated=prompt_budget_truncated,
    )

    return {
        "id": case["id"],
        "query": query,
        "passed": failure_layer == "ok",
        "failure_layer": failure_layer,
        "failure_reasons": failure_reasons,
        "status": result.get("status"),
        "expected_status": case.get("expected_status"),
        "diagnosis_category": case.get("diagnosis_category"),
        "eval_dimension": case.get("eval_dimension"),
        "freshness_mode": case.get("freshness_mode"),
        "case_source": case.get("case_source"),
        "case_source_ref": case.get("case_source_ref"),
        "runtime_dependency": bool(case.get("runtime_dependency")),
        "actionability_expectation": case.get("actionability_expectation"),
        "query_intent_bucket": query_intent_bucket,
        "expected_evidence_paths": expected_paths,
        "forbidden_evidence_paths": forbidden_paths,
        "missing_expected_paths": missing_expected_paths,
        "outside_partition_paths": outside_partition_paths,
        "lexical_rank": lexical_rank,
        "hybrid_rank": hybrid_rank,
        "anchor_rank": anchor_rank,
        "evidence_gate_rank": gate_rank,
        "evidence_gate_selected_rank": gate_selected_rank,
        "evidence_gate_rejected_rank": gate_rejected_rank,
        "evidence_gate_keep": expected_gate_entry.get("keep") if expected_gate_entry else None,
        "evidence_gate_selected": expected_gate_entry.get("selected") if expected_gate_entry else None,
        "evidence_gate_reason_category": gate_reason_category,
        "evidence_gate_validity": expected_gate_entry.get("validity") if expected_gate_entry else None,
        "evidence_gate_bridge_source": expected_gate_entry.get("bridge_source") if expected_gate_entry else None,
        "evidence_gate_authority": expected_gate_entry.get("authority") if expected_gate_entry else None,
        "evidence_gate_relevance": expected_gate_entry.get("relevance") if expected_gate_entry else None,
        "answer_evidence_rank": evidence_rank,
        "raw_retrieval_hit": raw_retrieval_hit,
        "map_candidate_hit": map_candidate_hit,
        "map_hit@10": map_hit_10,
        "map_hit@30": map_hit_30,
        "map_hit@80": map_hit_80,
        "map_rank": map_rank,
        "map_rank_band": map_rank_band,
        "map_bridge_bucket": map_bridge_bucket,
        "map_hit_but_evidence_miss": map_hit_but_evidence_miss,
        "map_error": map_error,
        "map_compensated_hit": map_compensated_hit,
        "recommendation_candidate_rank": recommendation_candidate_rank,
        "recommendation_candidate_hit@5": recommendation_candidate_hit_5,
        "recommendation_evidence_hit": recommendation_evidence_hit,
        "prompt_budget_truncated": prompt_budget_truncated,
        "not_in_map": not_in_map,
        "planner_llm_hit": planner_llm_hit,
        "planner_compensated_hit": planner_compensated_hit,
        "expected_rank": expected_rank,
        "primary_scope": primary_scope,
        "expected_primary_scope": expected_primary_scope,
        "trace_present": trace_present,
        "checks": checks,
        "trace_id": result.get("trace_id"),
        "run_id": result.get("run_id"),
    }


def _count_by_field(results: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        label = str(item.get(field) or "__unset__")
        counts[label] = counts.get(label, 0) + 1
    return counts


def summarize_diagnosis(results: list[dict[str, Any]]) -> dict[str, Any]:
    case_count = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    expected_path_cases = [item for item in results if item.get("expected_evidence_paths")]
    case_count_by_query_intent_bucket: dict[str, int] = {}
    expected_path_case_count_by_bucket: dict[str, int] = {}
    answer_evidence_hit_by_bucket: dict[str, int] = {}
    for item in results:
        bucket = _query_intent_bucket(item)
        case_count_by_query_intent_bucket[bucket] = case_count_by_query_intent_bucket.get(bucket, 0) + 1
    for item in expected_path_cases:
        bucket = _query_intent_bucket(item)
        expected_path_case_count_by_bucket[bucket] = (
            expected_path_case_count_by_bucket.get(bucket, 0) + 1
        )
        answer_evidence_hit_by_bucket.setdefault(bucket, 0)
        if item.get("answer_evidence_rank") is not None:
            answer_evidence_hit_by_bucket[bucket] += 1
    lexical_hits = sum(1 for item in expected_path_cases if item.get("lexical_rank") is not None)
    hybrid_hits = sum(1 for item in expected_path_cases if item.get("hybrid_rank") is not None)
    anchor_hits = sum(1 for item in expected_path_cases if item.get("anchor_rank") is not None)
    gate_hits = sum(1 for item in expected_path_cases if item.get("evidence_gate_rank") is not None)
    answer_evidence_hits = sum(1 for item in expected_path_cases if item.get("answer_evidence_rank") is not None)
    raw_retrieval_hits = sum(1 for item in expected_path_cases if item.get("raw_retrieval_hit"))
    map_candidate_hits = sum(1 for item in expected_path_cases if item.get("map_candidate_hit"))
    map_hit_10 = sum(1 for item in expected_path_cases if item.get("map_hit@10"))
    map_hit_30 = sum(1 for item in expected_path_cases if item.get("map_hit@30"))
    map_hit_80 = sum(1 for item in expected_path_cases if item.get("map_hit@80"))
    map_compensated_hits = sum(1 for item in expected_path_cases if item.get("map_compensated_hit"))
    map_hit_but_evidence_miss = sum(1 for item in expected_path_cases if item.get("map_hit_but_evidence_miss"))
    map_leak_cases = [item for item in expected_path_cases if item.get("map_hit_but_evidence_miss")]
    recommendation_candidate_hits_5 = sum(1 for item in expected_path_cases if item.get("recommendation_candidate_hit@5"))
    recommendation_evidence_hits = sum(1 for item in expected_path_cases if item.get("recommendation_evidence_hit"))
    not_in_map = sum(1 for item in expected_path_cases if item.get("not_in_map"))
    planner_llm_hits = sum(1 for item in expected_path_cases if item.get("planner_llm_hit"))
    planner_compensated_hits = sum(1 for item in expected_path_cases if item.get("planner_compensated_hit"))
    map_ranks = [
        int(item["map_rank"])
        for item in expected_path_cases
        if isinstance(item.get("map_rank"), int) and int(item["map_rank"]) > 0
    ]
    return {
        "case_count": case_count,
        "passed": passed,
        "pass_rate": passed / case_count if case_count else 0.0,
        "expected_path_case_count": len(expected_path_cases),
        "case_count_by_query_intent_bucket": case_count_by_query_intent_bucket,
        "expected_path_case_count_by_bucket": expected_path_case_count_by_bucket,
        "answer_evidence_hit_by_bucket": answer_evidence_hit_by_bucket,
        "lexical_hit": lexical_hits,
        "lexical_hit_rate": lexical_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "hybrid_hit": hybrid_hits,
        "hybrid_hit_rate": hybrid_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "anchor_hit": anchor_hits,
        "anchor_hit_rate": anchor_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "evidence_gate_hit": gate_hits,
        "evidence_gate_hit_rate": gate_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "answer_evidence_hit": answer_evidence_hits,
        "answer_evidence_hit_rate": answer_evidence_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "raw_retrieval_hit": raw_retrieval_hits,
        "raw_retrieval_hit_rate": raw_retrieval_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "map_candidate_hit": map_candidate_hits,
        "map_candidate_hit_rate": map_candidate_hits / len(expected_path_cases) if expected_path_cases else 1.0,
        "map_hit@10": map_hit_10,
        "map_hit@10_rate": map_hit_10 / len(expected_path_cases) if expected_path_cases else 1.0,
        "map_hit@30": map_hit_30,
        "map_hit@30_rate": map_hit_30 / len(expected_path_cases) if expected_path_cases else 1.0,
        "map_hit@80": map_hit_80,
        "map_hit@80_rate": map_hit_80 / len(expected_path_cases) if expected_path_cases else 1.0,
        "map_compensated_hit": map_compensated_hits,
        "map_compensated_hit_rate": map_compensated_hits / len(expected_path_cases) if expected_path_cases else 0.0,
        "map_hit_but_evidence_miss": map_hit_but_evidence_miss,
        "map_hit_but_evidence_miss_rate": (
            map_hit_but_evidence_miss / len(expected_path_cases) if expected_path_cases else 0.0
        ),
        "map_bridge_bucket_counts": _count_by_field(results, "map_bridge_bucket"),
        "map_leak_bucket_counts": _count_by_field(map_leak_cases, "map_bridge_bucket"),
        "map_leak_reason_category_counts": _count_by_field(map_leak_cases, "evidence_gate_reason_category"),
        "evidence_gate_reason_category_counts": _count_by_field(
            [item for item in expected_path_cases if item.get("evidence_gate_rank") is not None],
            "evidence_gate_reason_category",
        ),
        "map_rank_band_counts": _count_by_field(results, "map_rank_band"),
        "prompt_budget_truncated_count": sum(1 for item in results if item.get("prompt_budget_truncated")),
        "recommendation_candidate_hit@5": recommendation_candidate_hits_5,
        "recommendation_candidate_hit@5_rate": (
            recommendation_candidate_hits_5 / len(expected_path_cases) if expected_path_cases else 1.0
        ),
        "recommendation_evidence_hit": recommendation_evidence_hits,
        "recommendation_evidence_hit_rate": (
            recommendation_evidence_hits / len(expected_path_cases) if expected_path_cases else 1.0
        ),
        "not_in_map": not_in_map,
        "not_in_map_rate": not_in_map / len(expected_path_cases) if expected_path_cases else 0.0,
        "median_rank": statistics.median(map_ranks) if map_ranks else None,
        "mrr": sum(1 / rank for rank in map_ranks) / len(expected_path_cases) if expected_path_cases else 1.0,
        "planner_llm_hit": planner_llm_hits,
        "planner_llm_hit_rate": planner_llm_hits / len(expected_path_cases) if expected_path_cases else 0.0,
        "planner_compensated_hit": planner_compensated_hits,
        "planner_compensated_hit_rate": planner_compensated_hits / len(expected_path_cases) if expected_path_cases else 0.0,
        "failure_layer_counts": _count_by_field(results, "failure_layer"),
        "case_count_by_dimension": _count_by_field(results, "eval_dimension"),
        "case_count_by_category": _count_by_field(results, "diagnosis_category"),
        "action_seed_count": sum(1 for item in results if item.get("eval_dimension") == "action_grounding_seed"),
        "runtime_dependency_count": sum(1 for item in results if item.get("runtime_dependency")),
    }


def cmd_eval(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases, allow_paper_seed_tmp=args.allow_paper_seed_tmp)
    run_id = tm_answer.normalize_run_id(args.run_id) or default_run_id()
    results = [
        eval_case(case, run_id=run_id, write_trace=not getattr(args, "no_write_trace", False))
        for case in cases
    ]
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


def cmd_diagnose(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases, allow_paper_seed_tmp=args.allow_paper_seed_tmp)
    if args.limit is not None:
        cases = cases[: max(0, int(args.limit))]
    run_id = tm_answer.normalize_run_id(args.run_id) or default_run_id().replace("answer-eval", "answer-diagnosis")
    results = [
        diagnose_case(
            case,
            run_id=run_id,
            write_trace=bool(args.write_trace),
            top_k_probe=args.top_k_probe,
            map_probe_k=getattr(args, "map_probe_k", 80),
        )
        for case in cases
    ]
    summary = summarize_diagnosis(results)
    failures = [item for item in results if not item.get("passed")]
    report = {
        **summary,
        "run_id": run_id,
        "failures": [_compact_diagnosis_row(item) for item in failures] if args.compact else failures,
    }
    if not args.compact:
        report["results"] = results
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        print(
            f"cases={report['case_count']} passed={report['passed']}/{report['case_count']} "
            f"lexical={report['lexical_hit']}/{report['expected_path_case_count']} "
            f"hybrid={report['hybrid_hit']}/{report['expected_path_case_count']} "
            f"map80={report['map_hit@80']}/{report['expected_path_case_count']} "
            f"map_leak={report['map_hit_but_evidence_miss']}/{report['expected_path_case_count']} "
            f"rec5={report['recommendation_candidate_hit@5']}/{report['expected_path_case_count']} "
            f"rec_evidence={report['recommendation_evidence_hit']}/{report['expected_path_case_count']} "
            f"anchor={report['anchor_hit']}/{report['expected_path_case_count']} "
            f"evidence={report['answer_evidence_hit']}/{report['expected_path_case_count']}"
        )
        for layer, count in report["failure_layer_counts"].items():
            if layer != "ok":
                print(f"- {layer}: {count}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_answer_eval.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    eval_p = sub.add_parser("eval", help="run memory_answer cases")
    eval_p.add_argument("--cases", default="tests/fixtures/memory_answer_cases.jsonl")
    eval_p.add_argument("--json", action="store_true")
    eval_p.add_argument("--compact", action="store_true", help="omit per-case successes; keep summary and failures")
    eval_p.add_argument("--run-id", default=None, help="optional run id shared by all memory_answer trace rows")
    eval_p.add_argument("--no-write-trace", action="store_true", help="do not append eval rows to .tmp/memory-answer-trace.jsonl")
    eval_p.add_argument(
        "--allow-paper-seed-tmp",
        action="store_true",
        help="allow experimental paper_seed_tmp cases from .tmp fixtures",
    )
    eval_p.set_defaults(func=cmd_eval)
    diag_p = sub.add_parser("diagnose", help="run answer cases and attribute failures by layer")
    diag_p.add_argument("--cases", default="tests/fixtures/memory_answer_diagnosis_100.jsonl")
    diag_p.add_argument("--json", action="store_true")
    diag_p.add_argument("--compact", action="store_true", help="omit raw queries and success rows")
    diag_p.add_argument("--run-id", default=None, help="optional run id shared by all memory_answer calls")
    diag_p.add_argument("--write-trace", action="store_true", help="append diagnosis rows to .tmp/memory-answer-trace.jsonl")
    diag_p.add_argument("--top-k-probe", type=int, default=10, help="lexical/hybrid diagnostic probe depth")
    diag_p.add_argument("--map-probe-k", type=int, default=80, help="LLM Wiki map diagnostic probe depth")
    diag_p.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    diag_p.add_argument(
        "--allow-paper-seed-tmp",
        action="store_true",
        help="allow experimental paper_seed_tmp cases from .tmp fixtures",
    )
    diag_p.set_defaults(func=cmd_diagnose)
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
