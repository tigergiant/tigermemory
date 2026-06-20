#!/usr/bin/env python3
"""Build a raw-query-free miss ledger from memory_answer diagnose output."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

import _bootstrap_paths  # noqa: F401


def _read_json_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _hash_query(value: Any) -> str:
    text = str(value or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _target_family(path: str) -> str:
    normalized = str(path or "").replace("\\", "/")
    if not normalized:
        return "unknown"
    if normalized == "AGENTS.md":
        return "root_policy"
    if normalized.startswith("wiki/"):
        parts = normalized.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else "wiki"
    if normalized.startswith("sources/"):
        parts = normalized.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else "sources"
    if _is_runtime_only_path(normalized):
        return "external_runtime_or_codex_config"
    return normalized.split("/", 1)[0]


def _is_runtime_only_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/")
    return (
        normalized.startswith(".tmp/")
        or normalized.startswith(".codex/")
        or "/.codex/" in normalized
    )


def _primary_expected_path(row: dict[str, Any]) -> str:
    paths = row.get("expected_evidence_paths")
    if isinstance(paths, list) and paths:
        return str(paths[0])
    missing = row.get("missing_expected_paths")
    if isinstance(missing, list) and missing:
        return str(missing[0])
    return ""


def _decision_bucket(row: dict[str, Any]) -> str:
    bucket = str(row.get("map_bridge_bucket") or "")
    reason = str(row.get("evidence_gate_reason_category") or "")
    expected = _primary_expected_path(row)
    if _is_runtime_only_path(expected):
        return "source_policy_or_surrogate_needed"
    if bucket == "missing_knowledge" or reason == "missing_knowledge":
        return "missing_knowledge"
    if row.get("map_hit@80") and row.get("evidence_gate_rank") is None:
        return "candidate_handoff_not_in_gate"
    if bucket in {"map_miss", "map_deep_rank_not_in_gate"}:
        return "map_recall_or_index_gap"
    if bucket in {"map_top10_not_in_gate", "map_top30_not_in_gate"} or reason == "not_in_gate":
        return "candidate_handoff_not_in_gate"
    if bucket == "evidence_selection_topk_miss":
        return "gate_kept_but_not_selected"
    if bucket == "evidence_gate_rejected":
        return "gate_rejected"
    if not bucket and expected.startswith(("wiki/", "sources/")):
        return "compact_eval_needs_diagnose"
    return bucket or "unclassified"


def build_ledger(report: dict[str, Any]) -> dict[str, Any]:
    rows = report.get("results") or report.get("failures") or []
    entries: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        is_expected_case = bool(row.get("expected_evidence_paths") or row.get("missing_expected_paths"))
        if checks:
            evidence_hit = bool(checks.get("evidence_hit"))
        else:
            evidence_hit = bool(row.get("expected_evidence_hit"))
        if not is_expected_case or evidence_hit:
            continue
        expected_path = _primary_expected_path(row)
        entries.append({
            "case_id": row.get("id"),
            "query_hash": _hash_query(row.get("query")),
            "case_source": row.get("case_source"),
            "case_source_ref": row.get("case_source_ref"),
            "eval_dimension": row.get("eval_dimension"),
            "query_intent_bucket": row.get("query_intent_bucket"),
            "freshness_mode": row.get("freshness_mode"),
            "expected_path": expected_path,
            "expected_target_family": _target_family(expected_path),
            "failure_layer": row.get("failure_layer"),
            "decision_bucket": _decision_bucket(row),
            "map_bridge_bucket": row.get("map_bridge_bucket"),
            "map_rank": row.get("map_rank"),
            "map_rank_band": row.get("map_rank_band"),
            "map_hit@10": row.get("map_hit@10"),
            "map_hit@30": row.get("map_hit@30"),
            "map_hit@80": row.get("map_hit@80"),
            "lexical_rank": row.get("lexical_rank"),
            "hybrid_rank": row.get("hybrid_rank"),
            "anchor_rank": row.get("anchor_rank"),
            "raw_retrieval_hit": row.get("raw_retrieval_hit"),
            "evidence_gate_rank": row.get("evidence_gate_rank"),
            "evidence_gate_selected_rank": row.get("evidence_gate_selected_rank"),
            "evidence_gate_rejected_rank": row.get("evidence_gate_rejected_rank"),
            "evidence_gate_reason_category": row.get("evidence_gate_reason_category"),
            "evidence_gate_authority": row.get("evidence_gate_authority"),
            "evidence_gate_relevance": row.get("evidence_gate_relevance"),
            "prompt_budget_truncated": row.get("prompt_budget_truncated"),
            "status": row.get("status"),
        })

    decision_counts = collections.Counter(str(item["decision_bucket"]) for item in entries)
    target_counts = collections.Counter(str(item["expected_path"]) for item in entries)
    family_counts = collections.Counter(str(item["expected_target_family"]) for item in entries)
    trunc_by_decision: dict[str, dict[str, int]] = {}
    for item in entries:
        bucket = str(item["decision_bucket"])
        slot = trunc_by_decision.setdefault(bucket, {"total": 0, "prompt_budget_truncated": 0})
        slot["total"] += 1
        if item.get("prompt_budget_truncated"):
            slot["prompt_budget_truncated"] += 1

    return {
        "schema_version": "p315-miss-ledger-v1",
        "run_id": report.get("run_id"),
        "case_count": report.get("case_count"),
        "expected_path_case_count": report.get("expected_path_case_count") or report.get("expected_evidence_case_count"),
        "answer_evidence_hit": report.get("answer_evidence_hit") or report.get("expected_evidence_hit"),
        "miss_count": len(entries),
        "decision_bucket_counts": dict(decision_counts),
        "expected_target_family_counts": dict(family_counts),
        "top_expected_paths": dict(target_counts.most_common(20)),
        "prompt_budget_truncated_by_decision_bucket": trunc_by_decision,
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to memory_answer diagnose JSON output")
    parser.add_argument("--output", help="Write ledger JSON to this path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    report = json.loads(_read_json_text(Path(args.input)))
    ledger = build_ledger(report)
    text = json.dumps(ledger, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
