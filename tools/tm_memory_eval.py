"""Run deterministic retrieval evaluation cases for tigermemory.

Phase 1 is intentionally read-only: it calls existing retrieval paths and
reports whether expected sources are found. It does not change production
ranking, write Mem0, write Wiki, or add a new search API.

Usage:
    py -3 tools/tm_memory_eval.py eval --cases tests/fixtures/memory_eval_cases.jsonl
    py -3 tools/tm_memory_eval.py eval --cases tests/fixtures/memory_eval_cases.jsonl --grouped
    py -3 tools/tm_memory_eval.py eval --cases tests/fixtures/memory_eval_cases.jsonl --fuse
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import tm_core
import tm_embed_index
import tm_hier_index
import tm_lessons
import tm_persona

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALID_SCOPES = {"wiki", "lessons", "onboarding", "mem0", "all"}

# `kind` splits fixture cases into two evaluation layers:
#   - "retrieval" (default): counts toward main hit@k quality baseline.
#   - "runtime_probe": measured but reported separately; does NOT enter
#     retrieval denominator. Used for Mem0 health probes whose outcome
#     depends on Mem0 data state, not Wiki/sources retrieval quality.
# Cases without an explicit `kind` field default to "retrieval" for
# back-compat with the existing fixture schema.
VALID_KINDS = {"retrieval", "runtime_probe"}


@dataclass(frozen=True)
class EvalCase:
    id: str
    query: str
    scope: str
    expected_paths: list[str]
    must_contain: list[str]
    notes: str
    kind: str = "retrieval"


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
            kind = str(data.get("kind", "retrieval"))
            if kind not in VALID_KINDS:
                raise ValueError(f"line {line_no} case {case_id}: invalid kind {kind!r}; expected {sorted(VALID_KINDS)}")
            cases.append(EvalCase(
                id=case_id,
                query=str(data["query"]),
                scope=scope,
                expected_paths=_ensure_list(data["expected_paths"], "expected_paths", line_no, case_id),
                must_contain=_ensure_list(data["must_contain"], "must_contain", line_no, case_id),
                notes=str(data["notes"]),
                kind=kind,
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


def search_wiki_case_embedding(query: str, top_k: int, *, include_sources: bool = False) -> list[SearchHit]:
    """Embedding-based recall over the wiki/+sources/ index built by tm_embed_index.

    `include_sources=False` filters the index hits down to wiki/ paths only;
    the index itself always covers both roots so we only build / refresh once.
    Snippet is left empty — `_best_snippet` rebuilds one from the file body if
    the eval reporter wants it; the eval scorer only needs path + title.
    """
    raw = tm_embed_index.search(query, scope="wiki", k=max(top_k * 4, 12))
    out: list[SearchHit] = []
    for h in raw:
        path = h["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        out.append(SearchHit(
            path=path,
            title=h.get("title", ""),
            snippet="",
            score=float(h["score"]),
            source="wiki",
        ))
        if len(out) >= top_k:
            break
    return out


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


def run_search_grouped(
    scope: str,
    query: str,
    top_k: int,
    *,
    recall: str = "lexical",
) -> tuple[list[SearchHit], list[str]]:
    """Evaluate the grouped search_tigermemory shape using primary results only.

    For explicit case scopes, the primary group is that scope. For `all`, this
    mirrors `search_tigermemory(scope="auto")`: gather every group, then score
    the deterministic primary group instead of a fused single leaderboard.
    """
    errors: list[str] = []
    primary_scope = tm_core.primary_search_scope(query) if scope == "all" else scope
    requested_scopes = ["wiki", "lessons", "onboarding", "mem0"] if scope == "all" else [scope]
    groups: dict[str, list[SearchHit]] = {}

    if "wiki" in requested_scopes:
        groups["wiki"] = _wiki_recall(query, top_k, include_sources=(scope == "all"), recall=recall)
    if "lessons" in requested_scopes:
        groups["lessons"] = search_lessons_case(query, top_k)
    if "onboarding" in requested_scopes:
        groups["onboarding"] = search_onboarding_case(query, top_k)
    if "mem0" in requested_scopes:
        mem_hits, mem_error = search_mem0_case(query, top_k)
        groups["mem0"] = mem_hits
        if mem_error:
            errors.append(mem_error)

    return groups.get(primary_scope, [])[:top_k], errors


RRF_K = 60  # standard constant from the RRF paper; small k=10..100 all behave similarly.
HYBRID_LEXICAL_ANCHOR_COUNT = 2
HYBRID_LEXICAL_ANCHOR_MIN_SCORE = 100.0


def search_wiki_case_hybrid(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Reciprocal Rank Fusion of lexical + embedding wiki recall.

    Both branches return ranked path lists. RRF score per path =
    sum(1 / (RRF_K + rank_in_branch)), so ranks (not raw scores) drive fusion;
    scores are no longer comparable across branches anyway. This preserves the
    lexical strengths (exact-token English canonical hits) without sacrificing
    embedding's semantic / cross-language recall.
    """
    pool_k = max(top_k * 4, 12)  # wider pool than top_k to give RRF room
    lex = search_wiki_case(query, pool_k, include_sources=include_sources)
    emb = search_wiki_case_embedding(query, pool_k, include_sources=include_sources)

    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(lex, 1):
        fused.setdefault(hit.path, {"hit": hit, "score": 0.0, "lex_rank": None, "emb_rank": None})
        fused[hit.path]["score"] += 1.0 / (RRF_K + rank)
        fused[hit.path]["lex_rank"] = rank
    for rank, hit in enumerate(emb, 1):
        if hit.path not in fused:
            fused[hit.path] = {"hit": hit, "score": 0.0, "lex_rank": None, "emb_rank": None}
        fused[hit.path]["score"] += 1.0 / (RRF_K + rank)
        fused[hit.path]["emb_rank"] = rank
        # Prefer lex hit object if both branches found it (snippet richer).

    ordered = sorted(fused.values(), key=lambda v: -v["score"])
    anchor_paths = [
        hit.path for hit in lex[:HYBRID_LEXICAL_ANCHOR_COUNT]
        if hit.score >= HYBRID_LEXICAL_ANCHOR_MIN_SCORE
    ]
    out: list[SearchHit] = []
    seen: set[str] = set()
    limit = max(1, top_k)

    def add_entry(entry: dict[str, Any]) -> None:
        if len(out) >= limit:
            return
        h = entry["hit"]
        if h.path in seen:
            return
        seen.add(h.path)
        # Re-stamp score with the fused RRF score so downstream sort is stable.
        out.append(SearchHit(
            path=h.path,
            title=h.title,
            snippet=h.snippet,
            score=round(entry["score"], 6),
            source="wiki",
        ))

    if ordered:
        add_entry(ordered[0])
    for path in anchor_paths:
        entry = fused.get(path)
        if entry:
            add_entry(entry)
    for entry in ordered[1:]:
        if len(out) >= limit:
            break
        add_entry(entry)
    return out


def search_wiki_case_hierarchical(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Hierarchical L0/L1/L2 recall with full per-page layer aggregation.

    Uses `tm_hier_index.search_pages` which scores EVERY layer entry in
    the index and aggregates by path with the fixed formula:
      page_score = 0.45 * max_L0 + 0.35 * max_L1 + 0.20 * max_L2
    Missing layers score 0.

    Implementation note (2026-05-09 bugfix): the first version of this
    function called `tm_hier_index.search(k=20)` which returned only the
    top-20 *layer* entries — a page whose L2 ranked top-5 might still be
    missing its L0/L1 from the truncated pool, causing those layers to
    contribute 0 in the aggregate even when their actual cosine was
    decent. That collapsed scores to ~0.20 * L2 for many pages and broke
    eval (-24/-25 vs hybrid baseline). The fix scores all 1164 entries
    so every candidate page gets all three layer signals.
    """
    # Pool wider than top_k so source-filter doesn't starve final ranking.
    pool_k = max(top_k * 4, 20)
    page_results = tm_hier_index.search_pages(query, k=max(pool_k * 2, 50))

    out: list[SearchHit] = []
    for entry in page_results:
        path = entry["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        out.append(SearchHit(
            path=path,
            title=entry["title"],
            snippet="",
            score=round(entry["score"], 6),
            source="wiki",
        ))
        if len(out) >= top_k:
            break
    return out


def search_wiki_case_hier_hybrid(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
    weights: tuple[float, float, float] = (0.2, 0.6, 0.2),
) -> list[SearchHit]:
    """Phase 4b: RRF fusion of lexical + hierarchical (LLM L0/L1/L2) embedding.

    Unlike Phase 4's `hierarchical` mode (which replaced hybrid entirely),
    this preserves the lexical branch and only replaces the embedding branch
    with hierarchical page scoring. The two branches are then RRF-fused.
    """
    pool_k = max(top_k * 4, 12)
    lex = search_wiki_case(query, pool_k, include_sources=include_sources)

    # Hierarchical embedding branch: use search_pages with given weights
    page_results = tm_hier_index.search_pages(query, k=pool_k, weights=weights)

    # Build ranked list from hierarchical results
    hier_hits: list[SearchHit] = []
    for entry in page_results:
        path = entry["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        hier_hits.append(SearchHit(
            path=path,
            title=entry["title"],
            snippet="",
            score=round(entry["score"], 6),
            source="wiki",
        ))

    # RRF fusion: lexical + hierarchical embedding
    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(lex, 1):
        fused.setdefault(hit.path, {"hit": hit, "score": 0.0, "lex_rank": None, "emb_rank": None})
        fused[hit.path]["score"] += 1.0 / (RRF_K + rank)
        fused[hit.path]["lex_rank"] = rank
    for rank, hit in enumerate(hier_hits, 1):
        if hit.path not in fused:
            fused[hit.path] = {"hit": hit, "score": 0.0, "lex_rank": None, "emb_rank": None}
        fused[hit.path]["score"] += 1.0 / (RRF_K + rank)
        fused[hit.path]["emb_rank"] = rank

    ordered = sorted(fused.values(), key=lambda v: -v["score"])
    out: list[SearchHit] = []
    for entry in ordered[:top_k]:
        h = entry["hit"]
        out.append(SearchHit(
            path=h.path,
            title=h.title,
            snippet=h.snippet,
            score=round(entry["score"], 6),
            source="wiki",
        ))
    return out


def search_wiki_case_doubao_hybrid(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 5d eval-only Doubao multimodal dense+sparse hybrid recall.

    Uses `tm_doubao_hybrid_index.search` (separate index file
    `runtime/embed_index/wiki_doubao_hybrid.jsonl`, never overlaps with the
    Qwen v5 production index). Score = 0.55 * cosine_dense + 0.45 *
    cosine_sparse — the fixed initial formula from the Phase 5d brief.
    """
    import tm_doubao_hybrid_index  # type: ignore[import-not-found]
    pool_k = max(top_k * 4, 12)
    raw = tm_doubao_hybrid_index.search(query, k=pool_k)
    out: list[SearchHit] = []
    for h in raw:
        path = h["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        out.append(SearchHit(
            path=path,
            title=h.get("title", ""),
            snippet="",
            score=float(h["score"]),
            source="wiki",
        ))
        if len(out) >= top_k:
            break
    return out


def search_wiki_case_qwen_v4_dense(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6 eval-only: Aliyun DashScope text-embedding-v4 dense-only recall.

    Uses `tools/tm_qwen_v4_index.search_dense` against the separate
    `runtime/embed_index/wiki_qwen_v4_dense.jsonl` index. Query side uses
    text_type=query + instruct (DEFAULT_INSTRUCT) per Qwen3-Embedding family
    asymmetric retrieval guidance. Cosine over 1024-dim dense vectors.
    """
    import tm_qwen_v4_index  # type: ignore[import-not-found]
    pool_k = max(top_k * 4, 12)
    raw = tm_qwen_v4_index.search_dense(query, k=pool_k)
    out: list[SearchHit] = []
    for h in raw:
        path = h["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        out.append(SearchHit(
            path=path,
            title=h.get("title", ""),
            snippet="",
            score=float(h["score"]),
            source="wiki",
        ))
        if len(out) >= top_k:
            break
    return out


def search_wiki_case_qwen_v4_hybrid(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6 eval-only: text-embedding-v4 dense+sparse hybrid recall.

    Score = w_d * cosine_dense + w_s * cosine_sparse, where w_d defaults to
    0.5 and is overridable via env TM_QWENV4_DENSE_W (for grid search:
    0.3 / 0.5 / 0.7). w_s = 1 - w_d.
    """
    import tm_qwen_v4_index  # type: ignore[import-not-found]
    try:
        w_d = float(os.environ.get("TM_QWENV4_DENSE_W", "0.5"))
    except ValueError:
        w_d = 0.5
    w_d = max(0.0, min(1.0, w_d))
    pool_k = max(top_k * 4, 12)
    raw = tm_qwen_v4_index.search_hybrid(query, k=pool_k, dense_weight=w_d)
    out: list[SearchHit] = []
    for h in raw:
        path = h["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        out.append(SearchHit(
            path=path,
            title=h.get("title", ""),
            snippet="",
            score=float(h["score"]),
            source="wiki",
        ))
        if len(out) >= top_k:
            break
    return out


def _rrf_fuse_lex_emb(
    query: str,
    top_k: int,
    include_sources: bool,
    emb_branch: list[SearchHit],
) -> list[SearchHit]:
    """Helper: RRF fuse the lexical branch with a precomputed embedding branch."""
    pool_k = max(top_k * 4, 12)
    lex = search_wiki_case(query, pool_k, include_sources=include_sources)
    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(lex, 1):
        fused.setdefault(hit.path, {"hit": hit, "score": 0.0})
        fused[hit.path]["score"] += 1.0 / (RRF_K + rank)
    for rank, hit in enumerate(emb_branch, 1):
        if hit.path not in fused:
            fused[hit.path] = {"hit": hit, "score": 0.0}
        fused[hit.path]["score"] += 1.0 / (RRF_K + rank)
    ordered = sorted(fused.values(), key=lambda v: -v["score"])
    out: list[SearchHit] = []
    for entry in ordered[:top_k]:
        h = entry["hit"]
        out.append(SearchHit(
            path=h.path,
            title=h.title,
            snippet=h.snippet,
            score=round(entry["score"], 6),
            source="wiki",
        ))
    return out


def search_wiki_case_qwen_v4_dense_rrf(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6: lexical + qwen-v4 dense RRF fusion."""
    pool_k = max(top_k * 4, 12)
    emb = search_wiki_case_qwen_v4_dense(query, pool_k, include_sources=include_sources)
    return _rrf_fuse_lex_emb(query, top_k, include_sources, emb)


def search_wiki_case_qwen_v4_hybrid_rrf(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6: lexical + qwen-v4 (dense+sparse) RRF fusion."""
    pool_k = max(top_k * 4, 12)
    emb = search_wiki_case_qwen_v4_hybrid(query, pool_k, include_sources=include_sources)
    return _rrf_fuse_lex_emb(query, top_k, include_sources, emb)


def search_wiki_case_qwen_v4_dense_2048(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6b: text-embedding-v4 dense, dimension=2048."""
    import tm_qwen_v4_index  # type: ignore[import-not-found]
    pool_k = max(top_k * 4, 12)
    raw = tm_qwen_v4_index.search_dense(query, k=pool_k, dim=2048)
    out: list[SearchHit] = []
    for h in raw:
        path = h["path"]
        if not include_sources and path.startswith("sources/"):
            continue
        out.append(SearchHit(
            path=path,
            title=h.get("title", ""),
            snippet="",
            score=float(h["score"]),
            source="wiki",
        ))
        if len(out) >= top_k:
            break
    return out


def search_wiki_case_qwen_v4_dense_2048_rrf(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6b: lexical + qwen-v4 dense (2048) RRF."""
    pool_k = max(top_k * 4, 12)
    emb = search_wiki_case_qwen_v4_dense_2048(query, pool_k, include_sources=include_sources)
    return _rrf_fuse_lex_emb(query, top_k, include_sources, emb)


def search_wiki_case_qwen_v4_triple_rrf(
    query: str,
    top_k: int,
    *,
    include_sources: bool = False,
) -> list[SearchHit]:
    """Phase 6b: triple RRF over lexical + qwen-v4 dense + qwen-v4 sparse.

    Uses the hybrid index's branches separately ranked (NOT score-fused).
    Per-branch RRF weights overridable via env (defaults 1.0 each):
      TM_QWENV4_TRIPLE_W_LEX, TM_QWENV4_TRIPLE_W_DENSE, TM_QWENV4_TRIPLE_W_SPARSE.

    The 1024-dim hybrid index is used (Phase 6 build). Switch via env
    TM_QWENV4_TRIPLE_DIM if a 2048 hybrid is built later.
    """
    import tm_qwen_v4_index  # type: ignore[import-not-found]

    def _w(name: str) -> float:
        try:
            return float(os.environ.get(name, "1.0"))
        except ValueError:
            return 1.0

    w_lex = _w("TM_QWENV4_TRIPLE_W_LEX")
    w_dense = _w("TM_QWENV4_TRIPLE_W_DENSE")
    w_sparse = _w("TM_QWENV4_TRIPLE_W_SPARSE")
    try:
        dim = int(os.environ.get("TM_QWENV4_TRIPLE_DIM", "1024"))
    except ValueError:
        dim = 1024

    pool_k = max(top_k * 4, 12)
    lex = search_wiki_case(query, pool_k, include_sources=include_sources)
    branches = tm_qwen_v4_index.search_hybrid_branches(query, k=pool_k, dim=dim)

    def _filter(raw: list[dict[str, Any]]) -> list[SearchHit]:
        out: list[SearchHit] = []
        for h in raw:
            path = h["path"]
            if not include_sources and path.startswith("sources/"):
                continue
            out.append(SearchHit(
                path=path, title=h.get("title", ""), snippet="",
                score=float(h.get("score", 0.0)), source="wiki",
            ))
            if len(out) >= pool_k:
                break
        return out

    dense_hits = _filter(branches.get("dense", []))
    sparse_hits = _filter(branches.get("sparse", []))

    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(lex, 1):
        fused.setdefault(hit.path, {"hit": hit, "score": 0.0,
                                    "branches": []})
        fused[hit.path]["score"] += w_lex / (RRF_K + rank)
        fused[hit.path]["branches"].append(f"lex@{rank}")
    for rank, hit in enumerate(dense_hits, 1):
        if hit.path not in fused:
            fused[hit.path] = {"hit": hit, "score": 0.0, "branches": []}
        fused[hit.path]["score"] += w_dense / (RRF_K + rank)
        fused[hit.path]["branches"].append(f"dense@{rank}")
    for rank, hit in enumerate(sparse_hits, 1):
        if hit.path not in fused:
            fused[hit.path] = {"hit": hit, "score": 0.0, "branches": []}
        fused[hit.path]["score"] += w_sparse / (RRF_K + rank)
        fused[hit.path]["branches"].append(f"sparse@{rank}")

    ordered = sorted(fused.values(), key=lambda v: -v["score"])
    out: list[SearchHit] = []
    for entry in ordered[:top_k]:
        h = entry["hit"]
        out.append(SearchHit(
            path=h.path,
            title=h.title,
            snippet=";".join(entry["branches"][:3]),  # winning-branch trace
            score=round(entry["score"], 6),
            source="wiki",
        ))
    return out


def _wiki_recall(query: str, top_k: int, *, include_sources: bool, recall: str) -> list[SearchHit]:
    """Dispatch wiki recall to lexical / embedding / hybrid / hierarchical backend."""
    if recall == "embedding":
        return search_wiki_case_embedding(query, top_k, include_sources=include_sources)
    if recall == "hybrid":
        return search_wiki_case_hybrid(query, top_k, include_sources=include_sources)
    if recall == "hierarchical":
        return search_wiki_case_hierarchical(query, top_k, include_sources=include_sources)
    if recall == "hier-hybrid":
        return search_wiki_case_hier_hybrid(query, top_k, include_sources=include_sources)
    if recall == "doubao-hybrid":
        return search_wiki_case_doubao_hybrid(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-dense":
        return search_wiki_case_qwen_v4_dense(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-hybrid":
        return search_wiki_case_qwen_v4_hybrid(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-dense-rrf":
        return search_wiki_case_qwen_v4_dense_rrf(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-hybrid-rrf":
        return search_wiki_case_qwen_v4_hybrid_rrf(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-dense-2048":
        return search_wiki_case_qwen_v4_dense_2048(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-dense-2048-rrf":
        return search_wiki_case_qwen_v4_dense_2048_rrf(query, top_k, include_sources=include_sources)
    if recall == "qwen-v4-triple-rrf":
        return search_wiki_case_qwen_v4_triple_rrf(query, top_k, include_sources=include_sources)
    return search_wiki_case(query, top_k, include_sources=include_sources)


def run_search(
    scope: str,
    query: str,
    top_k: int,
    *,
    fuse: bool = False,
    grouped: bool = False,
    recall: str = "lexical",
) -> tuple[list[SearchHit], list[str]]:
    errors: list[str] = []

    # Fuse mode: force-merge ALL sources regardless of case.scope, then normalize.
    if fuse:
        return run_search_fused(query, top_k, recall=recall)
    if grouped:
        return run_search_grouped(scope, query, top_k, recall=recall)

    hits: list[SearchHit] = []
    if scope in ("wiki", "all"):
        hits.extend(_wiki_recall(query, top_k, include_sources=(scope == "all"), recall=recall))
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


def run_search_fused(
    query: str,
    top_k: int,
    *,
    recall: str = "lexical",
) -> tuple[list[SearchHit], list[str]]:
    """Query all 4 sources, normalize each source's scores to [0, 1], then merge.

    Per-source normalization (score / max_score_in_source) is the simplest fix
    for "raw scores across sources are incommensurable" — wiki returns token
    counts (10-30), mem0 returns embedding similarity (~0-5), lessons returns
    weighted sums. Without this, mem0 hits get pushed out by wiki's larger
    raw numbers (or vice versa depending on query).
    """
    errors: list[str] = []
    grouped: dict[str, list[SearchHit]] = {
        "wiki": _wiki_recall(query, top_k, include_sources=True, recall=recall),
        "lessons": search_lessons_case(query, top_k),
        "onboarding": search_onboarding_case(query, top_k),
    }
    mem_hits, mem_error = search_mem0_case(query, top_k)
    grouped["mem0"] = mem_hits
    if mem_error:
        errors.append(mem_error)

    normalized: list[SearchHit] = []
    for hits in grouped.values():
        if not hits:
            continue
        max_score = max((h.score for h in hits), default=0.0) or 1.0
        for h in hits:
            normalized.append(SearchHit(
                path=h.path,
                title=h.title,
                snippet=h.snippet,
                score=h.score / max_score,
                source=h.source,
            ))

    deduped: dict[tuple[str, str], SearchHit] = {}
    for hit in normalized:
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


def _mem0_unavailable(errors: list[str]) -> bool:
    return any(error.startswith("mem0 unavailable:") for error in errors)


def _runtime_unavailable_case(case: EvalCase, errors: list[str], *, grouped: bool) -> bool:
    if not _mem0_unavailable(errors):
        return False
    if case.scope == "mem0":
        return True
    return grouped and case.scope == "all" and tm_core.primary_search_scope(case.query) == "mem0"


def _hit_text_for_embed(hit: SearchHit) -> str:
    """Compose a compact representation of a hit for embedding rerank.

    Title + snippet (trimmed). Path slug adds recall signal for queries that
    match only via file naming (e.g. `cross-worktree-pull-omission`).
    """
    slug = re.sub(r"[^a-z0-9]+", " ", hit.path.lower())
    parts = [hit.title.strip(), slug, hit.snippet.strip()]
    return " | ".join(p for p in parts if p)[:1200]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rerank_by_embedding(
    query: str,
    hits: list[SearchHit],
    top_k: int,
) -> tuple[list[SearchHit], str | None]:
    """Embed query + hit texts, cosine-rerank, return top_k.

    Returns (reranked_hits, error). On embedding failure, returns the original
    hits truncated to top_k plus an error string so the caller can record it
    and fall back to lexical ranking (no silent substitution).
    """
    if not hits:
        return [], None
    texts = [_hit_text_for_embed(h) for h in hits]
    try:
        vectors = tm_core.embed_texts([query] + texts)
    except Exception as exc:  # noqa: BLE001
        return hits[:top_k], f"embedding unavailable: {exc}"
    q_vec, hit_vecs = vectors[0], vectors[1:]
    scored = sorted(
        (
            (idx, _cosine(q_vec, hv))
            for idx, hv in enumerate(hit_vecs)
        ),
        key=lambda kv: -kv[1],
    )
    reranked: list[SearchHit] = []
    for idx, sim in scored[:top_k]:
        h = hits[idx]
        reranked.append(SearchHit(
            path=h.path,
            title=h.title,
            snippet=h.snippet,
            score=float(sim),
            source=h.source,
        ))
    return reranked, None


def _run_case(
    case: EvalCase,
    *,
    top_k: int,
    pool_k: int,
    rerank_enabled: bool,
    fuse: bool,
    grouped: bool,
    recall: str,
) -> tuple[dict[str, Any], bool, bool, bool]:
    """Execute one case end-to-end and return (row, hit1, hit3, runtime_unavailable)."""
    start = time.perf_counter()
    hits, errors = run_search(
        case.scope, case.query, pool_k,
        fuse=fuse, grouped=grouped, recall=recall,
    )
    if rerank_enabled:
        reranked, rerank_err = rerank_by_embedding(case.query, hits, top_k)
        if rerank_err:
            errors = list(errors) + [rerank_err]
        hits = reranked
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    case_hit1 = score_case(case, hits, 1)
    case_hit3 = score_case(case, hits, min(3, top_k))
    runtime_unavailable = _runtime_unavailable_case(case, errors, grouped=grouped)
    row = {
        "id": case.id,
        "kind": case.kind,
        "query": case.query,
        "scope": case.scope,
        "hit1": case_hit1,
        "hit3": case_hit3,
        "runtime_unavailable": runtime_unavailable,
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
    }
    return row, case_hit1, case_hit3, runtime_unavailable


def _rate(num: int, denom: int) -> float:
    return round(num / denom, 4) if denom else 0.0


def evaluate(
    cases: list[EvalCase],
    top_k: int,
    *,
    fuse: bool = False,
    grouped: bool = False,
    rerank: str = "off",
    candidate_k: int | None = None,
    recall: str = "lexical",
) -> dict[str, Any]:
    """Run eval split into retrieval-quality layer and runtime-probe layer.

    The main `hit1 / hit3 / case_count` fields cover the retrieval-quality
    layer only. Probe cases (Mem0 health / similar data-state-dependent
    checks) are measured with the same `run_search` / `score_case` path but
    reported under `probe_*` fields and `probe_results`. Layered denominators
    prevent Mem0 data-state drift from showing up as retrieval regression.
    """
    retrieval_rows: list[dict[str, Any]] = []
    probe_rows: list[dict[str, Any]] = []
    hit1 = hit3 = 0
    quality_hit1 = quality_hit3 = 0
    runtime_unavailable_count = 0
    probe_hit1 = probe_hit3 = 0
    probe_runtime_unavailable_count = 0

    rerank_enabled = rerank == "embedding"
    pool_k = candidate_k if candidate_k is not None else max(top_k * 3, 10)
    if not rerank_enabled:
        pool_k = top_k  # no rerank: don't pay for extra lexical scanning

    for case in cases:
        row, case_hit1, case_hit3, runtime_unavailable = _run_case(
            case,
            top_k=top_k,
            pool_k=pool_k,
            rerank_enabled=rerank_enabled,
            fuse=fuse,
            grouped=grouped,
            recall=recall,
        )
        if case.kind == "runtime_probe":
            probe_rows.append(row)
            probe_hit1 += int(case_hit1)
            probe_hit3 += int(case_hit3)
            if runtime_unavailable:
                probe_runtime_unavailable_count += 1
            continue
        # retrieval quality layer
        retrieval_rows.append(row)
        hit1 += int(case_hit1)
        hit3 += int(case_hit3)
        if runtime_unavailable:
            runtime_unavailable_count += 1
        else:
            quality_hit1 += int(case_hit1)
            quality_hit3 += int(case_hit3)

    retrieval_total = len(retrieval_rows)
    quality_total = retrieval_total - runtime_unavailable_count
    probe_total = len(probe_rows)
    return {
        # Retrieval-quality baseline (primary): excludes runtime probes.
        "case_count": retrieval_total,
        "hit1": hit1,
        "hit3": hit3,
        "hit1_rate": _rate(hit1, retrieval_total),
        "hit3_rate": _rate(hit3, retrieval_total),
        "quality_case_count": quality_total,
        "quality_hit1": quality_hit1,
        "quality_hit3": quality_hit3,
        "quality_hit1_rate": _rate(quality_hit1, quality_total),
        "quality_hit3_rate": _rate(quality_hit3, quality_total),
        "runtime_unavailable_count": runtime_unavailable_count,
        # Runtime-probe layer (reported separately, does not enter retrieval denominator).
        "probe_case_count": probe_total,
        "probe_hit1": probe_hit1,
        "probe_hit3": probe_hit3,
        "probe_runtime_unavailable_count": probe_runtime_unavailable_count,
        # Config / shared metadata.
        "total_case_count": retrieval_total + probe_total,
        "top_k": top_k,
        "fuse": fuse,
        "grouped": grouped,
        "rerank": rerank,
        "candidate_k": pool_k,
        "recall": recall,
        "results": retrieval_rows,
        "probe_results": probe_rows,
    }


def print_report(report: dict[str, Any]) -> None:
    if report.get("fuse"):
        mode = "FUSED (all sources, normalized)"
    elif report.get("grouped"):
        mode = "GROUPED (primary group, no fused ranking)"
    else:
        mode = "baseline (per-case scope)"
    recall = report.get("recall", "lexical")
    if recall != "lexical":
        mode += f" + recall={recall}"
    rerank = report.get("rerank", "off")
    if rerank != "off":
        mode += f" + rerank={rerank} (candidate_k={report.get('candidate_k')})"
    print(f"# tm_memory_eval {mode}")
    print(f"retrieval cases: {report['case_count']} "
          f"(total including probes: {report.get('total_case_count', report['case_count'])})")
    print(f"retrieval hit@1: {report['hit1']}/{report['case_count']} ({report['hit1_rate']:.0%})")
    print(f"retrieval hit@3: {report['hit3']}/{report['case_count']} ({report['hit3_rate']:.0%})")
    if report["runtime_unavailable_count"]:
        print(
            "retrieval quality cases: "
            f"{report['quality_case_count']} "
            f"(runtime-unavailable excluded: {report['runtime_unavailable_count']})"
        )
        print(
            f"retrieval quality hit@1: {report['quality_hit1']}/{report['quality_case_count']} "
            f"({report['quality_hit1_rate']:.0%})"
        )
        print(
            f"retrieval quality hit@3: {report['quality_hit3']}/{report['quality_case_count']} "
            f"({report['quality_hit3_rate']:.0%})"
        )
    print()

    def _emit_rows(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            status = "RUNTIME" if row["runtime_unavailable"] else ("OK" if row["hit3"] else "MISS")
            print(f"{status} {row['id']} [{row['scope']}] {row['query']} ({row['latency_ms']} ms)")
            if row["errors"]:
                for error in row["errors"]:
                    print(f"  error: {error}")
            for result in row["top_results"][:3]:
                print(f"  {result['rank']}. {result['source']} {result['path']} score={result['score']}")
            if not row["hit3"]:
                print(f"  expected_paths: {row['expected_paths']}")
                print(f"  must_contain: {row['must_contain']}")

    _emit_rows(report["results"])

    probe_rows = report.get("probe_results") or []
    if probe_rows:
        print()
        print(f"# runtime probes: {report.get('probe_case_count', len(probe_rows))} "
              f"(hit@1={report.get('probe_hit1', 0)}, hit@3={report.get('probe_hit3', 0)}, "
              f"runtime-unavailable={report.get('probe_runtime_unavailable_count', 0)})")
        _emit_rows(probe_rows)

    print()
    print("Note: retrieval hit@k covers kind=retrieval cases only. Runtime probes are reported separately and do not enter the retrieval denominator; quality hit@k further excludes retrieval cases whose target runtime is unavailable.")


def load_eval_env(path: pathlib.Path) -> None:
    """Load embedding-related variables from a simple KEY=VALUE .env file."""
    if not path.exists():
        raise ValueError(f"env file not found: {path}")
    allowed_exact = {
        "OPENAI_API_KEY",
    }
    allowed_prefixes = ("EMBEDDING_",)
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if not name:
                continue
            if name not in allowed_exact and not any(name.startswith(prefix) for prefix in allowed_prefixes):
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[name] = value


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        if args.env_file:
            load_eval_env(REPO_ROOT / args.env_file)
        if args.embedding_base_url:
            os.environ["EMBEDDING_BASE_URL"] = args.embedding_base_url.rstrip("/")
        cases = load_cases(REPO_ROOT / args.cases)
        report = evaluate(
            cases,
            top_k=args.top_k,
            fuse=args.fuse,
            grouped=args.grouped,
            rerank=args.rerank,
            candidate_k=args.candidate_k,
            recall=args.recall,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        # Write UTF-8 directly to stdout buffer to avoid GBK encoding errors in PowerShell
        output = dict(report)
        if args.compact:
            output.pop("results", None)
            output.pop("probe_results", None)
        json_str = json.dumps(output, ensure_ascii=False, indent=2)
        sys.stdout.buffer.write(json_str.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
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
    eval_p.add_argument("--compact", action="store_true",
                        help="with --json, omit per-case rows that include raw queries")
    mode = eval_p.add_mutually_exclusive_group()
    mode.add_argument("--fuse", action="store_true",
                      help="force-merge all sources with per-source score normalization "
                           "(negative control: does fused ranking hurt hit@k?)")
    mode.add_argument("--grouped", action="store_true",
                      help="evaluate the grouped search_tigermemory shape using primary_results")
    eval_p.add_argument("--recall", choices=["lexical", "embedding", "hybrid", "hierarchical", "hier-hybrid", "doubao-hybrid", "qwen-v4-dense", "qwen-v4-hybrid", "qwen-v4-dense-rrf", "qwen-v4-hybrid-rrf", "qwen-v4-dense-2048", "qwen-v4-dense-2048-rrf", "qwen-v4-triple-rrf"], default="lexical",
                        help="wiki/sources recall backend. 'embedding' = cosine over "
                             "tm_embed_index; 'hybrid' = RRF fusion of lexical + embedding; "
                             "'hierarchical' = L0/L1/L2 multi-layer aggregation (experimental); "
                             "'lexical' = current token-AND search_wiki. Build the embed "
                             "index first via `python tools/tm_embed_index.py build` or "
                             "`python tools/tm_hier_index.py build` for hierarchical.")
    eval_p.add_argument("--rerank", choices=["off", "embedding"], default="off",
                        help="post-lexical rerank stage. 'embedding' = cosine over the query "
                             "and hit-text embeddings (EMBEDDING_BASE_URL/MODEL env). "
                             "Lexical ranking is kept if the embedding call fails.")
    eval_p.add_argument("--candidate-k", type=int, default=None,
                        help="candidate pool size fed into rerank. Defaults to max(top_k*3, 10). "
                             "Ignored when --rerank=off.")
    eval_p.add_argument("--env-file", default=None,
                        help="load embedding-related env vars from a local .env file before eval")
    eval_p.add_argument("--embedding-base-url", default=None,
                        help="override EMBEDDING_BASE_URL for local eval without editing .env")
    eval_p.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
