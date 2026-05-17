#!/usr/bin/env python3
"""Run answer-level eval cases for tigermemory memory_answer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import tm_answer


def load_cases(path: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
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
            cases.append(item)
    return cases


def eval_case(case: dict[str, Any]) -> dict[str, Any]:
    result = tm_answer.memory_answer_core(
        str(case["query"]),
        scope=str(case.get("scope", "auto")),
        top_k=int(case.get("top_k", 5)),
        max_evidence=int(case.get("max_evidence", 6)),
        include_trace=False,
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
        "claim_count": len(claims),
        "supported_claim_count": len(supported_claims),
        "trace_id": result.get("trace_id"),
        "warnings": result.get("warnings") or [],
    }


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
    }


def cmd_eval(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    results = [eval_case(case) for case in cases]
    report = {
        **summarize(results),
        "results": results,
    }
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        print(
            f"cases={report['case_count']} "
            f"status={report['status_correct']}/{report['case_count']} "
            f"evidence={report['expected_evidence_hit']}/{report['expected_evidence_case_count']} "
            f"claim_support_rate={report['claim_support_rate']:.2f} "
            f"not_found_precision={report['not_found_precision']:.2f}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_answer_eval.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    eval_p = sub.add_parser("eval", help="run memory_answer cases")
    eval_p.add_argument("--cases", default="tests/fixtures/memory_answer_cases.jsonl")
    eval_p.add_argument("--json", action="store_true")
    eval_p.set_defaults(func=cmd_eval)
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
