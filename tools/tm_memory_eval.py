"""Run deterministic retrieval evaluation cases for tigermemory.

Phase 1 is intentionally read-only: it calls existing retrieval paths and
reports whether expected sources are found. It does not change production
ranking, write Mem0, write Wiki, or add a new search API.

Usage:
    py -3 tools/tm_memory_eval.py eval --cases tests/fixtures/memory_eval_cases.jsonl
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import tm_core
import tm_lessons
import tm_persona

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALID_SCOPES = {"wiki", "lessons", "onboarding", "mem0", "all"}


@dataclass(frozen=True)
class EvalCase:
    id: str
    query: str
    scope: str
    expected_paths: list[str]
    must_contain: list[str]
    notes: str


@dataclass
class SearchHit:
    path: str
    title: str
    snippet: str
    score: float
    source: str


def _ensure_list(value: Any, field: str, line_no: int, case_id: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"line {line_no} case {case_id}: {field} must be a list[str]")
    return value


def load_cases(path: pathlib.Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_no}: invalid JSON: {exc}") from exc

            missing = [k for k in ("id", "query", "scope", "expected_paths", "must_contain", "notes") if k not in data]
            case_id = str(data.get("id", f"line-{line_no}"))
            if missing:
                raise ValueError(f"line {line_no} case {case_id}: missing fields {missing}")
            if case_id in seen:
                raise ValueError(f"line {line_no} case {case_id}: duplicate id")
            seen.add(case_id)

            scope = str(data["scope"])
            if scope not in VALID_SCOPES:
                raise ValueError(f"line {line_no} case {case_id}: invalid scope {scope!r}; expected {sorted(VALID_SCOPES)}")
            cases.append(EvalCase(
                id=case_id,
                query=str(data["query"]),
                scope=scope,
                expected_paths=_ensure_list(data["expected_paths"], "expected_paths", line_no, case_id),
                must_contain=_ensure_list(data["must_contain"], "must_contain", line_no, case_id),
                notes=str(data["notes"]),
            ))
    if not cases:
        raise ValueError(f"no cases found in {path}")
    return cases


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip().lower()) if t]


def _path_matches(actual: str, expected: str) -> bool:
    if expected.endswith("*"):
        return actual.startswith(expected[:-1])
    return actual == expected or actual.startswith(expected + "#")


def _best_snippet(text: str, tokens: list[str], width: int = 220) -> str:
    lower = text.lower()
    positions = [lower.find(t) for t in tokens if lower.find(t) >= 0]
    if not positions:
        return text[:width].replace("\n", " ").strip()
    start = max(0, min(positions) - 50)
    end = min(len(text), start + width)
    return text[start:end].replace("\n", " ").strip()


def search_wiki_case(query: str, top_k: int, *, include_sources: bool = False) -> list[SearchHit]:
    hits = tm_core.search_wiki(query, size=top_k, include_sources=include_sources, include_inbox=False)
    return [
        SearchHit(
            path=str(h.get("path", "")),
            title=str(h.get("title", "")),
            snippet=str(h.get("snippet", "")),
            score=float(h.get("score", 0)),
            source="wiki",
        )
        for h in hits
    ]


def search_lessons_case(query: str, top_k: int) -> list[SearchHit]:
    tokens = _tokens(query)
    scored: list[tuple[int, pathlib.Path, str, str]] = []
    for path in sorted(tm_lessons.LESSONS_DIR.glob("*.md")):
        if path.name == "index.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        score, title, _aliases = tm_lessons._score_lesson(text, tokens)
        if score > 0:
            scored.append((score, path, title, tm_lessons._excerpt(text, tokens, width=120)))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [
        SearchHit(
            path=path.relative_to(REPO_ROOT).as_posix(),
            title=title,
            snippet=excerpt,
            score=float(score),
            source="lessons",
        )
        for score, path, title, excerpt in scored[:top_k]
    ]


def search_onboarding_case(query: str, top_k: int) -> list[SearchHit]:
    tokens = _tokens(query)
    hits: list[SearchHit] = []
    for depth in ("30s", "5min", "full"):
        text = tm_persona.compile_snapshot(depth)
        lower = text.lower()
        score = 0
        for token in tokens:
            count = lower.count(token)
            if count == 0:
                score = 0
                break
            score += count
        if score > 0:
            hits.append(SearchHit(
                path=tm_persona.SNAPSHOT_PAGE,
                title=f"Agent Onboarding Snapshot ({depth})",
                snippet=_best_snippet(text, tokens),
                score=float(score),
                source="onboarding",
            ))
    hits.sort(key=lambda hit: (-hit.score, hit.title))
    return hits[:top_k]


def search_mem0_case(query: str, top_k: int) -> tuple[list[SearchHit], str | None]:
    try:
        raw = tm_core.mem0_search(query, size=top_k)
        data = json.loads(raw)
    except Exception as exc:  # Mem0 may be down; eval should still report baseline.
        return [], f"mem0 unavailable: {exc}"

    items = data.get("items") or data.get("results") or []
    hits: list[SearchHit] = []
    for index, item in enumerate(items[:top_k], 1):
        meta = item.get("metadata_") or item.get("metadata") or {}
        text = str(item.get("content") or item.get("memory") or item.get("text") or "")
        mem_id = str(item.get("id") or f"rank-{index}")
        raw_score = item.get("score")
        score = float(raw_score) if isinstance(raw_score, (int, float)) else float(top_k - index + 1)
        hits.append(SearchHit(
            path=f"mem0:{mem_id}",
            title=f"{meta.get('topic', 'unknown')} / {meta.get('source', 'unknown')}",
            snippet=text[:300],
            score=score,
            source="mem0",
        ))
    return hits, None


def run_search(scope: str, query: str, top_k: int) -> tuple[list[SearchHit], list[str]]:
    errors: list[str] = []
    hits: list[SearchHit] = []

    if scope in ("wiki", "all"):
        hits.extend(search_wiki_case(query, top_k, include_sources=(scope == "all")))
    if scope in ("lessons", "all"):
        hits.extend(search_lessons_case(query, top_k))
    if scope in ("onboarding", "all"):
        hits.extend(search_onboarding_case(query, top_k))
    if scope in ("mem0", "all"):
        mem_hits, mem_error = search_mem0_case(query, top_k)
        hits.extend(mem_hits)
        if mem_error:
            errors.append(mem_error)

    deduped: dict[tuple[str, str], SearchHit] = {}
    for hit in hits:
        key = (hit.source, hit.path)
        if key not in deduped or hit.score > deduped[key].score:
            deduped[key] = hit
    ordered = sorted(deduped.values(), key=lambda hit: (-hit.score, hit.source, hit.path))
    return ordered[:top_k], errors


def _contains_all(hits: list[SearchHit], needles: list[str], k: int) -> bool:
    if not needles:
        return False
    haystack = "\n".join(f"{h.path}\n{h.title}\n{h.snippet}" for h in hits[:k]).lower()
    return all(needle.lower() in haystack for needle in needles)


def score_case(case: EvalCase, hits: list[SearchHit], k: int) -> bool:
    if case.expected_paths:
        for expected in case.expected_paths:
            if any(_path_matches(hit.path, expected) for hit in hits[:k]):
                return True
        return False
    return _contains_all(hits, case.must_contain, k)


def evaluate(cases: list[EvalCase], top_k: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    hit1 = 0
    hit3 = 0
    for case in cases:
        start = time.perf_counter()
        hits, errors = run_search(case.scope, case.query, top_k)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        case_hit1 = score_case(case, hits, 1)
        case_hit3 = score_case(case, hits, min(3, top_k))
        hit1 += int(case_hit1)
        hit3 += int(case_hit3)
        rows.append({
            "id": case.id,
            "query": case.query,
            "scope": case.scope,
            "hit1": case_hit1,
            "hit3": case_hit3,
            "latency_ms": latency_ms,
            "expected_paths": case.expected_paths,
            "must_contain": case.must_contain,
            "errors": errors,
            "top_results": [
                {
                    "rank": i,
                    "source": h.source,
                    "path": h.path,
                    "score": h.score,
                    "title": h.title,
                    "snippet": h.snippet,
                }
                for i, h in enumerate(hits, 1)
            ],
        })
    total = len(cases)
    return {
        "case_count": total,
        "hit1": hit1,
        "hit3": hit3,
        "hit1_rate": round(hit1 / total, 4),
        "hit3_rate": round(hit3 / total, 4),
        "top_k": top_k,
        "results": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    print("# tm_memory_eval baseline")
    print(f"cases: {report['case_count']}")
    print(f"hit@1: {report['hit1']}/{report['case_count']} ({report['hit1_rate']:.0%})")
    print(f"hit@3: {report['hit3']}/{report['case_count']} ({report['hit3_rate']:.0%})")
    print()
    for row in report["results"]:
        status = "OK" if row["hit3"] else "MISS"
        print(f"{status} {row['id']} [{row['scope']}] {row['query']} ({row['latency_ms']} ms)")
        if row["errors"]:
            for error in row["errors"]:
                print(f"  error: {error}")
        for result in row["top_results"][:3]:
            print(f"  {result['rank']}. {result['source']} {result['path']} score={result['score']}")
        if not row["hit3"]:
            print(f"  expected_paths: {row['expected_paths']}")
            print(f"  must_contain: {row['must_contain']}")
    print()
    print("Note: first baseline records current behavior; thresholds should be set after reviewing misses.")


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        cases = load_cases(REPO_ROOT / args.cases)
        report = evaluate(cases, top_k=args.top_k)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_memory_eval.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    eval_p = sub.add_parser("eval", help="run retrieval eval cases")
    eval_p.add_argument("--cases", default="tests/fixtures/memory_eval_cases.jsonl")
    eval_p.add_argument("--top-k", type=int, default=5)
    eval_p.add_argument("--json", action="store_true", help="emit full JSON report")
    eval_p.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
