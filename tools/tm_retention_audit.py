#!/usr/bin/env python3
"""Read-only retention dry-run audit for tigermemory Mem0 records.

This module never deletes or updates memories. It scores records for human
review using local metadata, age, provenance, duplicate signals, recent search
visibility, promotion markers, and local sensitive-pattern checks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse
from typing import Any

import tm_core
import tm_memory_ops

REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_ITEMS = 200
DEFAULT_DOGFOOD_LOG = REPO_ROOT / ".tmp" / "search-tigermemory.jsonl"


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _age_days(value: Any, *, now: dt.datetime) -> int | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds() // 86400))


def _item_text(item: dict[str, Any]) -> str:
    return str(item.get("content") or item.get("memory") or item.get("text") or "")


def _item_meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata_") or item.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pinned", "keep"}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _memory_id(item: dict[str, Any], rank: int) -> str:
    return str(item.get("id") or item.get("memory_id") or f"rank-{rank}")


def fetch_mem0_page(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
    params = urllib.parse.urlencode({
        "user_id": "tiger",
        "page": page,
        "size": page_size,
    })
    raw = tm_core.mem0_request(
        f"{tm_core.mem0_base().rstrip('/')}/api/v1/memories/?{params}",
        timeout=tm_core.MEM0_READ_TIMEOUT,
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Mem0 list returned a non-object response")
    return data


def fetch_mem0_items(max_items: int = DEFAULT_MAX_ITEMS, page_size: int = DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < max_items:
        data = fetch_mem0_page(page=page, page_size=page_size)
        items = data.get("items") or data.get("results") or []
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if isinstance(item, dict):
                out.append(item)
                if len(out) >= max_items:
                    break
        if not data.get("next") and len(items) < page_size:
            break
        page += 1
    return out


def _normalized_fingerprint(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16] if compact else ""


def _recent_mem0_hits(log_path: pathlib.Path = DEFAULT_DOGFOOD_LOG, *, days: int = 30, now: dt.datetime | None = None) -> set[str]:
    now = now or _now_utc()
    cutoff = now - dt.timedelta(days=days)
    hits: set[str] = set()
    if not log_path.exists():
        return hits
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return hits
    for raw in lines[-2000:]:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts = _parse_dt(row.get("ts"))
        if ts is not None and ts < cutoff:
            continue
        for key in ("primary_top_path", "path"):
            value = str(row.get(key) or "")
            if value.startswith("mem0:"):
                hits.add(value.split(":", 1)[1])
    return hits


def _promotion_marker_ids(root: pathlib.Path = REPO_ROOT) -> set[str]:
    markers: set[str] = set()
    for rel_root in ("wiki", "sources"):
        base = root / rel_root
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in tm_core.MEM0_UUID_RE.finditer(text):
                markers.add(match.group(0))
    return markers


def _duplicate_groups(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        fp = _normalized_fingerprint(_item_text(item))
        if fp:
            counts[fp] = counts.get(fp, 0) + 1
    return counts


def score_item(
    item: dict[str, Any],
    *,
    rank: int,
    now: dt.datetime,
    duplicate_counts: dict[str, int],
    recent_hits: set[str],
    promotion_ids: set[str],
) -> dict[str, Any]:
    mem_id = _memory_id(item, rank)
    text = _item_text(item)
    meta = _item_meta(item)
    created_days = _age_days(item.get("created_at"), now=now)
    updated_days = _age_days(item.get("updated_at"), now=now)
    route_score = _number(meta.get("route_score"))
    review_score = _number(meta.get("llm_review_score"))
    source_agent = str(meta.get("source") or meta.get("source_agent") or "")
    topic = str(meta.get("topic") or "")
    pinned = any(_boolish(meta.get(key)) for key in ("is_pinned", "pinned", "retention_pin"))
    promoted = bool(
        mem_id in promotion_ids
        or any(meta.get(key) for key in ("promoted_to", "promoted_path", "wiki_path", "source_path"))
    )
    recent_hit = mem_id in recent_hits
    fingerprint = _normalized_fingerprint(text)
    duplicate_count = duplicate_counts.get(fingerprint, 0) if fingerprint else 0
    sensitive_hits = tm_memory_ops._light_sensitive_hits(text)

    score = 45
    reasons: list[str] = []
    risks: list[str] = []
    keep_signals: list[str] = []

    if pinned:
        score -= 80
        keep_signals.append("pinned")
    if promoted:
        score -= 35
        keep_signals.append("promoted_to_wiki_or_sources")
    if recent_hit:
        score -= 25
        keep_signals.append("recent_search_hit")
    if route_score is not None:
        if route_score >= 85:
            score -= 12
            keep_signals.append(f"high_route_score:{route_score:g}")
        elif route_score < 30:
            score += 25
            risks.append(f"low_route_score:{route_score:g}")
        elif route_score < 70:
            score += 8
            risks.append(f"medium_route_score:{route_score:g}")
    else:
        score += 8
        risks.append("missing_route_score")
    if review_score is not None:
        if review_score >= 70:
            score -= 8
            keep_signals.append(f"high_l2_review_score:{review_score:g}")
        elif review_score < 30:
            score += 18
            risks.append(f"low_l2_review_score:{review_score:g}")
    if created_days is None:
        score += 8
        risks.append("missing_created_at")
    elif created_days > 365:
        score += 20
        reasons.append(f"old_created_at:{created_days}d")
    elif created_days > 180:
        score += 10
        reasons.append(f"aged_created_at:{created_days}d")
    elif created_days <= 30:
        score -= 8
        keep_signals.append(f"recent_created_at:{created_days}d")
    if updated_days is not None and updated_days <= 30:
        score -= 8
        keep_signals.append(f"recent_updated_at:{updated_days}d")
    if not source_agent:
        score += 8
        risks.append("missing_source_agent")
    if not topic:
        score += 6
        risks.append("missing_topic")
    if duplicate_count > 1:
        score += min(25, 10 + duplicate_count * 3)
        risks.append(f"duplicate_fingerprint_count:{duplicate_count}")
    if sensitive_hits:
        score += 40
        risks.extend(f"sensitive:{hit['kind']}" for hit in sensitive_hits)
    if len(text.strip()) < 30:
        score += 10
        risks.append("very_short_text")

    score = max(0, min(100, int(round(score))))
    if pinned:
        action = "keep_pinned"
    elif sensitive_hits:
        action = "review_sensitive"
    elif score >= 70:
        action = "review_for_archive"
    elif score >= 50:
        action = "review"
    else:
        action = "keep"

    if not reasons and not risks:
        reasons.append("no_archive_pressure")

    return {
        "id": mem_id,
        "retention_score": score,
        "recommended_action": action,
        "topic": topic or None,
        "source_agent": source_agent or None,
        "state": item.get("state"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "created_age_days": created_days,
        "updated_age_days": updated_days,
        "route_score": route_score,
        "llm_review_score": review_score,
        "pinned": pinned,
        "promoted": promoted,
        "recent_search_hit": recent_hit,
        "duplicate_count": duplicate_count,
        "text_sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "text_preview": text[:180],
        "keep_signals": keep_signals,
        "reasons": reasons,
        "risks": risks,
    }


def run_retention_audit(
    *,
    max_items: int = DEFAULT_MAX_ITEMS,
    page_size: int = DEFAULT_PAGE_SIZE,
    now: dt.datetime | None = None,
    dogfood_log: pathlib.Path = DEFAULT_DOGFOOD_LOG,
) -> dict[str, Any]:
    now = now or _now_utc()
    try:
        items = fetch_mem0_items(max_items=max_items, page_size=page_size)
    except Exception as exc:
        return {
            "schema_version": "tm-retention-audit-v1",
            "dry_run": True,
            "ok": False,
            "status": "fail",
            "generated_at": now.astimezone(tm_core.TZ_CN).isoformat(timespec="seconds"),
            "item_count": 0,
            "action_counts": {},
            "signals": {},
            "candidates": [],
            "error": str(exc)[:500],
            "warnings": [
                "dry-run only: no Mem0 records were deleted or updated",
                "Mem0 records could not be listed; retention candidates are not available",
            ],
        }
    duplicate_counts = _duplicate_groups(items)
    recent_hits = _recent_mem0_hits(dogfood_log, now=now)
    promotion_ids = _promotion_marker_ids(REPO_ROOT)
    scored = [
        score_item(
            item,
            rank=index,
            now=now,
            duplicate_counts=duplicate_counts,
            recent_hits=recent_hits,
            promotion_ids=promotion_ids,
        )
        for index, item in enumerate(items, 1)
    ]
    scored.sort(key=lambda row: (-row["retention_score"], row["id"]))
    action_counts: dict[str, int] = {}
    for row in scored:
        action = str(row["recommended_action"])
        action_counts[action] = action_counts.get(action, 0) + 1
    return {
        "schema_version": "tm-retention-audit-v1",
        "dry_run": True,
        "ok": True,
        "status": "ok",
        "generated_at": now.astimezone(tm_core.TZ_CN).isoformat(timespec="seconds"),
        "item_count": len(items),
        "action_counts": action_counts,
        "signals": {
            "recent_search_hit_window_days": 30,
            "recent_search_hit_count": len(recent_hits),
            "promotion_marker_count": len(promotion_ids),
        },
        "candidates": scored,
        "warnings": [
            "dry-run only: no Mem0 records were deleted or updated",
            "route and review scores are advisory signals, not deletion authority",
        ],
    }


def render_markdown(report: dict[str, Any], *, limit: int = 30) -> str:
    lines = [
        "# Tigermemory Retention Dry-Run Audit",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- dry_run: `{report['dry_run']}`",
        f"- item_count: `{report['item_count']}`",
        f"- action_counts: `{json.dumps(report['action_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "No records were deleted or updated.",
        "",
        "| score | action | id | topic | source | reasons | risks | preview |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in report.get("candidates", [])[:limit]:
        reasons = "; ".join(row.get("reasons") or row.get("keep_signals") or [])
        risks = "; ".join(row.get("risks") or [])
        preview = str(row.get("text_preview") or "").replace("|", "\\|").replace("\n", " ")
        reasons_cell = reasons.replace("|", "\\|")
        risks_cell = risks.replace("|", "\\|")
        lines.append(
            f"| {row['retention_score']} | {row['recommended_action']} | `{row['id']}` | "
            f"{row.get('topic') or ''} | {row.get('source_agent') or ''} | "
            f"{reasons_cell} | {risks_cell} | {preview[:120]} |"
        )
    lines.append("")
    lines.append("## Warnings")
    for warning in report.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines) + "\n"


def cmd_audit(args: argparse.Namespace) -> int:
    report = run_retention_audit(max_items=args.max_items, page_size=args.page_size)
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_markdown(report, limit=args.limit))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_retention_audit.py", description=__doc__)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--limit", type=int, default=30, help="markdown rows to print")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args()
    sys.exit(cmd_audit(args))


if __name__ == "__main__":
    main()
