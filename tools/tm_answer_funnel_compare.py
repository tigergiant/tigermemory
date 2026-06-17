#!/usr/bin/env python3
"""Run memory_answer diagnose across opt-in retrieval arms.

The command writes local-only artifacts under `.tmp/` by default. It keeps the
release gate honest by comparing production defaults against optional map arms
without flipping those arms on globally.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import _bootstrap_paths  # noqa: F401

import tigermemory_core as tm_core


MATRICES: dict[str, dict[str, str]] = {
    "summary_off": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
    "summary_on": {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
    "summary_on_map_arm": {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
    "production": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
    "production_packer": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "1",
    },
    "map_arm": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
    "map_arm_packer": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "1",
    },
    "bridge": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "1",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
    # Safe combined experiment: legacy planner wiki-map stays off because it
    # previously regressed the 25-case answer gate.
    "safe_combined_opt_in": {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "1",
        "TM_ANSWER_WIKI_MAP": "0",
        "TM_ANSWER_EVIDENCE_PACK_V2": "0",
    },
}

SUMMARY_KEYS = [
    "case_count",
    "passed",
    "pass_rate",
    "expected_path_case_count",
    "filtered_case_count",
    "shard_count",
    "shard_index",
    "answer_evidence_hit",
    "surrogate_path_case_count",
    "surrogate_evidence_hit",
    "evidence_gate_hit",
    "map_hit@10",
    "map_hit@30",
    "map_hit@80",
    "map_hit_but_evidence_miss",
    "map_bridge_bucket_counts",
    "map_leak_bucket_counts",
    "map_leak_reason_category_counts",
    "evidence_gate_reason_category_counts",
    "case_count_by_query_intent_bucket",
    "expected_path_case_count_by_bucket",
    "answer_evidence_hit_by_bucket",
    "prompt_budget_truncated_count",
    "failure_layer_counts",
]


def _default_output_dir() -> Path:
    stamp = dt.datetime.now(tm_core.TZ_CN).strftime("%Y%m%d-%H%M%S")
    return tm_core.REPO_ROOT / ".tmp" / f"p310-funnel-{stamp}"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compact_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report.get(key) for key in SUMMARY_KEYS if key in report}


def _case_outcomes(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("results")
    if not isinstance(rows, list):
        rows = report.get("failures")
    if not isinstance(rows, list):
        return {}
    outcomes: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        evidence_hit = checks.get("evidence_hit")
        if evidence_hit is None:
            evidence_hit = row.get("expected_evidence_hit")
        outcomes[str(row["id"])] = {
            "passed": bool(row.get("passed")),
            "failure_layer": row.get("failure_layer"),
            "failure_reasons": row.get("failure_reasons") if isinstance(row.get("failure_reasons"), list) else [],
            "answer_evidence_hit": bool(evidence_hit),
            "surrogate_evidence_hit": bool(row.get("surrogate_evidence_hit")),
            "map_bridge_bucket": row.get("map_bridge_bucket"),
            "prompt_budget_truncated": bool(row.get("prompt_budget_truncated")),
        }
    return outcomes


def _run_matrix(
    *,
    matrix: str,
    cases: str,
    output_dir: Path,
    run_id_prefix: str,
    limit: int | None,
    shard_count: int,
    shard_index: int,
    top_k_probe: int,
    map_probe_k: int,
) -> dict[str, Any]:
    report_path = output_dir / f"{matrix}.json"
    env = os.environ.copy()
    env.update(MATRICES[matrix])
    env.setdefault("PYTHONIOENCODING", "utf-8")
    run_id = f"{run_id_prefix}-{matrix}"
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        str(tm_core.REPO_ROOT / "tools" / "tm_answer_eval.py"),
        "diagnose",
        "--cases",
        cases,
        "--json",
        "--run-id",
        run_id,
        "--top-k-probe",
        str(top_k_probe),
        "--map-probe-k",
        str(map_probe_k),
        "--shard-count",
        str(shard_count),
        "--shard-index",
        str(shard_index),
    ]
    if limit is not None:
        cmd.extend(["--limit", str(max(0, int(limit)))])
    completed = subprocess.run(
        cmd,
        cwd=tm_core.REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        error_path = output_dir / f"{matrix}.stderr.txt"
        error_path.write_text(completed.stderr or "", encoding="utf-8")
        raise RuntimeError(f"{matrix} diagnose failed with exit {completed.returncode}; stderr={error_path}")
    stdout = completed.stdout or ""
    report_path.write_text(stdout, encoding="utf-8")
    report = json.loads(stdout)
    return {
        "matrix": matrix,
        "run_id": run_id,
        "artifact": str(report_path),
        "env": MATRICES[matrix],
        "case_outcomes": _case_outcomes(report),
        **_compact_summary(report),
    }


def _delta(base: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "passed",
        "pass_rate",
        "answer_evidence_hit",
        "evidence_gate_hit",
        "map_hit_but_evidence_miss",
        "prompt_budget_truncated_count",
        "map_hit@10",
        "map_hit@30",
        "map_hit@80",
    ]
    out: dict[str, Any] = {"matrix": item["matrix"], "baseline": base["matrix"]}
    for field in fields:
        base_value = base.get(field)
        value = item.get(field)
        if isinstance(base_value, (int, float)) and isinstance(value, (int, float)):
            out[field] = value - base_value
    base_cases = base.get("case_outcomes") if isinstance(base.get("case_outcomes"), dict) else {}
    item_cases = item.get("case_outcomes") if isinstance(item.get("case_outcomes"), dict) else {}
    improved: list[str] = []
    regressed: list[str] = []
    changed_layers: dict[str, dict[str, str | None]] = {}
    regressed_layers: dict[str, int] = {}
    for case_id in sorted(set(base_cases) & set(item_cases)):
        base_row = base_cases[case_id]
        item_row = item_cases[case_id]
        base_passed = bool(base_row.get("passed"))
        item_passed = bool(item_row.get("passed"))
        if not base_passed and item_passed:
            improved.append(case_id)
        elif base_passed and not item_passed:
            regressed.append(case_id)
            layer = str(item_row.get("failure_layer") or "unknown")
            regressed_layers[layer] = regressed_layers.get(layer, 0) + 1
        if base_row.get("failure_layer") != item_row.get("failure_layer"):
            changed_layers[case_id] = {
                "from": base_row.get("failure_layer"),
                "to": item_row.get("failure_layer"),
            }
    out["improved_case_ids"] = improved
    out["regressed_case_ids"] = regressed
    out["changed_failure_layer_count"] = len(changed_layers)
    out["changed_failure_layers"] = changed_layers
    out["regressed_failure_layer_counts"] = dict(sorted(regressed_layers.items()))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="tests/fixtures/memory_answer_diagnosis_100.jsonl")
    parser.add_argument("--matrix", action="append", choices=sorted(MATRICES), help="matrix to run; repeatable")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-id-prefix", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--top-k-probe", type=int, default=10)
    parser.add_argument("--map-probe-k", type=int, default=80)
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    matrices = args.matrix or ["summary_off", "summary_on", "summary_on_map_arm"]
    run_id_prefix = args.run_id_prefix or output_dir.name

    summaries = [
        _run_matrix(
            matrix=matrix,
            cases=args.cases,
            output_dir=output_dir,
            run_id_prefix=run_id_prefix,
            limit=args.limit,
            shard_count=args.shard_count,
            shard_index=args.shard_index,
            top_k_probe=args.top_k_probe,
            map_probe_k=args.map_probe_k,
        )
        for matrix in matrices
    ]
    baseline = summaries[0]
    comparison = {
        "cases": args.cases,
        "output_dir": str(output_dir),
        "matrices": summaries,
        "deltas_vs_first": [_delta(baseline, item) for item in summaries[1:]],
    }
    comparison_path = output_dir / "comparison.json"
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
