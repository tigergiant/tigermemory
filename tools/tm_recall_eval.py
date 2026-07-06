#!/usr/bin/env python3
"""Local event-memory retrieval eval — the measuring ruler for direction-1.

Inputs: a controlled fixture (corpus of memories + queries with the corpus key
        each must recall), tagged by dimension.
Outputs: recall@k overall + per dimension, plus the miss list. JSON or text.
Depends-on: tigermemory_core local profile (temp DB only). No network, no LLM,
            never touches the production data/tigermemory/memory.sqlite.

Two arms:
  - current (default): today's local search (trigram FTS + substring fallback).
  - vector: FTS + in-process cosine rerank over local embeddings. Requires
            direction-1 (local vectors) to be built; errors clearly until then.

The point: run --arm current NOW to record the baseline (cross_language and
paraphrase cases will largely MISS — that is the honest gap). After direction-1
lands, run --arm vector and diff: cross_language recall must go up, and no other
dimension may regress.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-core" / "src"))

import tigermemory_core as tm_core  # noqa: E402

DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "memory_recall_eval.json"


def _seed_corpus(corpus: list[dict]) -> dict[str, str]:
    """Write each corpus memory into the local temp DB; return key -> memory id."""
    key_to_id: dict[str, str] = {}
    for item in corpus:
        resp = json.loads(
            tm_core.mem0_write(
                agent="codex", topic=str(item["topic"]), text=str(item["text"])
            )
        )
        key_to_id[str(item["key"])] = str(resp["id"])
    return key_to_id


def _search_ids(query: str, size: int, arm: str) -> list[str]:
    if arm == "vector":
        # Direction-1 hook: a hybrid FTS+cosine local search. Until it exists,
        # fail loudly rather than silently scoring the FTS baseline as "vector".
        search = getattr(tm_core, "local_search_hybrid", None)
        if search is None:
            raise SystemExit(
                "arm=vector requires tigermemory_core.local_search_hybrid "
                "(direction-1 local vectors) which is not built yet."
            )
        payload = search(query, size=size)
    else:
        payload = json.loads(tm_core.mem0_search(query, size=size))
    return [str(r.get("id")) for r in payload.get("results", []) if r.get("id")]


def run_eval(fixture: dict, *, k: int = 5, arm: str = "current") -> dict:
    key_to_id = _seed_corpus(fixture["corpus"])
    cases = fixture["cases"]
    by_dim: dict[str, dict[str, int]] = {}
    misses: list[dict] = []
    hit_total = 0
    for case in cases:
        dim = str(case.get("dimension", "plain"))
        by_dim.setdefault(dim, {"hit": 0, "total": 0})
        by_dim[dim]["total"] += 1
        expect_id = key_to_id.get(str(case["expect"]))
        ids = _search_ids(str(case["query"]), k, arm)
        hit = expect_id in ids
        # distractor: the wrong memory must not outrank the right one.
        if hit and case.get("not_outranked_by"):
            wrong_id = key_to_id.get(str(case["not_outranked_by"]))
            if wrong_id in ids and ids.index(wrong_id) < ids.index(expect_id):
                hit = False
        if hit:
            by_dim[dim]["hit"] += 1
            hit_total += 1
        else:
            misses.append(
                {"id": case["id"], "dimension": dim, "query": case["query"], "expect": case["expect"]}
            )
    dim_recall = {
        d: {"hit": v["hit"], "total": v["total"], "recall": round(v["hit"] / v["total"], 3)}
        for d, v in sorted(by_dim.items())
    }
    return {
        "schema": "tm-recall-eval-v1",
        "arm": arm,
        "k": k,
        "total_cases": len(cases),
        "hit_total": hit_total,
        "overall_recall": round(hit_total / len(cases), 3) if cases else 0.0,
        "by_dimension": dim_recall,
        "misses": misses,
    }


def render_text(result: dict) -> str:
    lines = [
        f"Local recall eval  arm={result['arm']}  k={result['k']}",
        f"overall recall@{result['k']}: {result['hit_total']}/{result['total_cases']} "
        f"= {result['overall_recall']}",
        "",
        "per dimension:",
    ]
    for dim, v in result["by_dimension"].items():
        lines.append(f"  {dim:<15} {v['hit']}/{v['total']}  recall={v['recall']}")
    if result["misses"]:
        lines.append("")
        lines.append("misses:")
        for m in result["misses"]:
            lines.append(f"  [{m['dimension']}] {m['id']}: '{m['query']}' -> expected {m['expect']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--arm", choices=["current", "vector"], default="current")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=None, help="temp DB path (default: a throwaway temp file)")
    args = ap.parse_args(argv)

    fixture = json.loads(pathlib.Path(args.fixture).read_text(encoding="utf-8"))

    # Force local profile against a throwaway DB so we never touch production.
    os.environ["TIGERMEMORY_PROFILE"] = tm_core.TIGERMEMORY_PROFILE_LOCAL
    if args.db:
        os.environ["TIGERMEMORY_LOCAL_DB"] = args.db
        result = run_eval(fixture, k=args.k, arm=args.arm)
    else:
        with tempfile.TemporaryDirectory() as td:
            os.environ["TIGERMEMORY_LOCAL_DB"] = str(pathlib.Path(td) / f"recall-{uuid.uuid4().hex}.sqlite")
            result = run_eval(fixture, k=args.k, arm=args.arm)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
