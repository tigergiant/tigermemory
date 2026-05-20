#!/usr/bin/env python3
"""Shared grouped search helpers for tigermemory tools."""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

import tm_core
import tm_lessons
import tm_persona

SEARCH_SCOPES = {"auto", "all", "wiki", "lessons", "onboarding", "mem0"}
DEFAULT_DOGFOOD_LOG = tm_core.REPO_ROOT / ".tmp" / "search-tigermemory.jsonl"


def format_search_hit(
    source: str,
    path: str,
    title: str,
    snippet: str,
    score: float,
    extra: dict[str, Any] | None = None,
    *,
    score_breakdown: dict[str, Any] | None = None,
    injection_eligible: bool | None = None,
    injection_reason: str | None = None,
) -> dict[str, Any]:
    hit = {
        "source": source,
        "path": path,
        "title": title,
        "snippet": snippet,
        "score": score,
    }
    if score_breakdown is not None:
        hit["score_breakdown"] = score_breakdown
    if injection_eligible is not None:
        hit["injection_eligible"] = injection_eligible
    if injection_reason is not None:
        hit["injection_reason"] = injection_reason
    if extra:
        hit.update(extra)
    return hit


def _search_lessons_group(query: str, top_k: int) -> list[dict[str, Any]]:
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    scored: list[tuple[int, Any, str, str, dict[str, Any] | None]] = []
    if not tokens or not tm_lessons.LESSONS_DIR.exists():
        return []
    for path in sorted(tm_lessons.LESSONS_DIR.glob("*.md")):
        if path.name == "index.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        score, title, _aliases, breakdown = tm_lessons._score_lesson(text, tokens, explain=True)
        if score > 0:
            scored.append((score, path, title, tm_lessons._excerpt(text, tokens, width=120), breakdown))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [
        format_search_hit(
            "lessons",
            path.relative_to(tm_core.REPO_ROOT).as_posix(),
            title,
            excerpt,
            float(score),
            score_breakdown=breakdown,
        )
        for score, path, title, excerpt, breakdown in scored[:top_k]
    ]


def _search_onboarding_group(query: str, top_k: int) -> list[dict[str, Any]]:
    tokens = [t.lower() for t in re.split(r"\s+", query.strip()) if t]
    if not tokens:
        return []
    hits: list[dict[str, Any]] = []
    for depth in ("30s", "5min", "full"):
        content = tm_persona.compile_snapshot(depth)
        lower = content.lower()
        score = 0
        for token in tokens:
            count = lower.count(token)
            if count == 0:
                score = 0
                break
            score += count
        if score > 0:
            hits.append(format_search_hit(
                "onboarding",
                tm_persona.SNAPSHOT_PAGE,
                f"Agent Onboarding Snapshot ({depth})",
                content[:300].replace("\n", " ").strip(),
                float(score),
                score_breakdown={
                    "depth": depth,
                    "token_hits": score,
                    "matched_terms": [token for token in tokens if token in lower],
                    "final_score": score,
                },
            ))
    hits.sort(key=lambda hit: (-hit["score"], hit["title"]))
    return hits[:top_k]


def _search_mem0_group(query: str, top_k: int) -> tuple[list[dict[str, Any]], str | None]:
    try:
        data = json.loads(tm_core.mem0_search(query, size=top_k))
    except Exception as exc:
        return [], f"mem0 unavailable: {exc}"
    items = data.get("items") or data.get("results") or []
    hits: list[dict[str, Any]] = []
    for index, item in enumerate(items[:top_k], 1):
        meta = item.get("metadata_") or item.get("metadata") or {}
        text = str(item.get("content") or item.get("memory") or item.get("text") or "")
        mem_id = str(item.get("id") or f"rank-{index}")
        raw_score = item.get("score")
        score = float(raw_score) if isinstance(raw_score, (int, float)) else float(top_k - index + 1)
        age_days = None
        created_at = item.get("created_at")
        if created_at:
            try:
                created_dt = datetime.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                age_days = (datetime.datetime.now(datetime.timezone.utc) - created_dt.astimezone(datetime.timezone.utc)).days
            except ValueError:
                age_days = None
        hits.append(format_search_hit(
            "mem0",
            f"mem0:{mem_id}",
            f"{meta.get('topic', 'unknown')} / {meta.get('source', 'unknown')}",
            text[:300],
            score,
            extra={
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            },
            score_breakdown={
                "native_score": raw_score if isinstance(raw_score, (int, float)) else None,
                "rank": index,
                "rank_fallback": not isinstance(raw_score, (int, float)),
                "age_days": age_days,
                "route_decision": meta.get("route_decision"),
                "topic": meta.get("topic"),
                "final_score": score,
            },
        ))
    return hits, None


def _log_search_tigermemory(payload: dict[str, Any], log_path: Path | None) -> None:
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


def search_tigermemory(
    query: str,
    scope: str = "auto",
    top_k: int = 5,
    *,
    role: str = "writer",
    dogfood_log: Path | None = DEFAULT_DOGFOOD_LOG,
) -> dict[str, Any]:
    """Grouped search across wiki, lessons, onboarding, and Mem0."""
    q = (query or "").strip()
    selected_scope = (scope or "auto").strip().lower()
    if not q:
        raise ValueError("query must be non-empty")
    if selected_scope not in SEARCH_SCOPES:
        raise ValueError(f"invalid scope {scope!r}; expected one of {sorted(SEARCH_SCOPES)}")
    limit = min(max(int(top_k), 1), 20)

    primary_scope = tm_core.primary_search_scope(q) if selected_scope in ("auto", "all") else selected_scope
    scopes = ["wiki", "lessons", "onboarding", "mem0"] if selected_scope in ("auto", "all") else [selected_scope]
    groups: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []

    if "wiki" in scopes:
        include_sources = selected_scope in ("auto", "all")
        groups["wiki"] = [
            format_search_hit(
                "wiki",
                str(hit.get("path", "")),
                str(hit.get("title", "")),
                str(hit.get("snippet", "")),
                float(hit.get("score", 0.0)),
                score_breakdown=hit.get("score_breakdown") if isinstance(hit.get("score_breakdown"), dict) else None,
            )
            for hit in tm_core.search_wiki_hybrid(q, size=limit, include_sources=include_sources, include_inbox=False, explain=True)
        ]
    if "lessons" in scopes:
        groups["lessons"] = _search_lessons_group(q, limit)
    if "onboarding" in scopes:
        groups["onboarding"] = _search_onboarding_group(q, limit)
    if "mem0" in scopes:
        mem_hits, mem_warning = _search_mem0_group(q, limit)
        groups["mem0"] = mem_hits
        if mem_warning:
            warnings.append(mem_warning)

    result = {
        "query": q,
        "scope": selected_scope,
        "strategy": "grouped-intent-budget-v1",
        "primary_scope": primary_scope,
        "primary_results": groups.get(primary_scope, []),
        "groups": groups,
        "warnings": warnings,
    }
    primary_results = result["primary_results"]
    _log_search_tigermemory({
        "ts": datetime.datetime.now(tm_core.TZ_CN).isoformat(),
        "role": role,
        "query": q,
        "scope": selected_scope,
        "top_k": limit,
        "strategy": result["strategy"],
        "primary_scope": primary_scope,
        "primary_top_path": primary_results[0].get("path") if primary_results else None,
        "primary_count": len(primary_results),
        "group_counts": {name: len(items) for name, items in groups.items()},
        "warnings": warnings,
    }, dogfood_log)
    return result
