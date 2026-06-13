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
    },
    "summary_on": {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    },
    "summary_on_map_arm": {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    },
    "production": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    },
    "map_arm": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    },
    "bridge": {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "1",
        "TM_ANSWER_WIKI_MAP": "0",
    },
    # Safe combined experiment: legacy planner wiki-map stays off because it
    # previously regressed the 25-case answer gate.
    "safe_combined_opt_in": {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "1",
        "TM_ANSWER_WIKI_MAP": "0",
    },
}

SUMMARY_KEYS = [
    "case_count",
    "passed",
    "expected_path_case_count",
    "answer_evidence_hit",
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


def _run_matrix(
    *,
    matrix: str,
    cases: str,
    output_dir: Path,
    run_id_prefix: str,
    limit: int | None,
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
        **_compact_summary(report),
    }


def _delta(base: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "passed",
        "answer_evidence_hit",
        "evidence_gate_hit",
        "map_hit_but_evidence_miss",
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
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="tests/fixtures/memory_answer_diagnosis_100.jsonl")
    parser.add_argument("--matrix", action="append", choices=sorted(MATRICES), help="matrix to run; repeatable")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-id-prefix", default=None)
    parser.add_argument("--limit", type=int, default=None)
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
