#!/usr/bin/env python3
"""Read-only retention dry-run audit for tigermemory Mem0 records.

This module never deletes or updates memories. It scores records for human
review using local metadata, age, provenance, duplicate signals, recent search
visibility, promotion markers, and local sensitive-pattern checks.
Inputs: CLI/API payloads, inbox or digest markdown, route decisions, proposal metadata, or Mem0 write requests.
Outputs: Rendered markdown, JSON status payloads, routed memory writes, proposal decisions, or review actions.
Depends-on (must-have): tm_core, tm_route/tm_memory_ops helpers, local git-managed files, and configured Mem0/OpenMemory endpoints.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

import tm_core
import tm_memory_ops

REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_MAX_ITEMS = 200
DEFAULT_DOGFOOD_LOG = REPO_ROOT / ".tmp" / "search-tigermemory.jsonl"

# Diagnostic offline sample data testing all scoring combinations
SAMPLE_DATA = [
    {
        "id": "mem-pinned-1",
        "text": "Core security protocols and tiger-mainmachine configuration parameters.",
        "metadata": {
            "is_pinned": True,
            "topic": "systems",
            "source": "human",
            "created_at": "2026-01-01T00:00:00Z",
            "last_accessed_at": "2026-05-20T00:00:00Z"
        }
    },
    {
        "id": "mem-person-1",
        "text": "Personal profile information regarding user preferences, dietary restrictions, and emergency contact numbers.",
        "metadata": {
            "topic": "person",
            "source": "claude-code",
            "created_at": "2025-01-01T00:00:00Z",
            "last_accessed_at": "2025-02-01T00:00:00Z"
        }
    },
    {
        "id": "mem-invest-1",
        "text": "Conservative investment analysis and portfolio scanning logic for 600887.SH milk industry analysis.",
        "metadata": {
            "topic": "investment",
            "source": "deerflow",
            "created_at": "2025-01-01T00:00:00Z",
            "last_accessed_at": "2025-02-01T00:00:00Z"
        }
    },
    {
        "id": "mem-human-1",
        "text": "🐯 Tiger explicitly instructed to keep local Obsidian environment in sync with remote worktree F4.",
        "metadata": {
            "topic": "operations",
            "source": "human",
            "created_at": "2025-05-01T00:00:00Z",
            "last_accessed_at": "2025-06-01T00:00:00Z"
        }
    },
    {
        "id": "mem-missing-topic",
        "text": "A temporary deployment record for server settings.",
        "metadata": {
            "source": "codex",
            "created_at": "2026-01-01T00:00:00Z",
            "last_accessed_at": "2026-05-01T00:00:00Z"
        }
    },
    {
        "id": "mem-missing-source",
        "text": "Systems integration notes with no clear author agent defined.",
        "metadata": {
            "topic": "systems",
            "created_at": "2026-01-01T00:00:00Z",
            "last_accessed_at": "2026-05-01T00:00:00Z"
        }
    },
    {
        "id": "mem-missing-accessed",
        "text": "Highly detailed guide for IPFB copywriting standards and white shirt features.",
        "metadata": {
            "topic": "brand",
            "source": "cascade",
            "created_at": "2026-01-01T00:00:00Z"
        }
    },
    {
        "id": "mem-placeholder-1",
        "text": "test",
        "metadata": {
            "topic": "brand",
            "source": "chatgpt",
            "created_at": "2026-04-01T00:00:00Z",
            "last_accessed_at": "2026-05-01T00:00:00Z"
        }
    },
    {
        "id": "mem-stale-1",
        "text": "Legacy deployment steps for old staging server at IP address 192.168.1.100. Superceded by production cloud setup.",
        "metadata": {
            "topic": "systems",
            "source": "codex",
            "created_at": "2025-01-01T00:00:00Z",
            "last_accessed_at": "2025-02-01T00:00:00Z"
        }
    },
    {
        "id": "mem-recent-1",
        "text": "Newly added database connection pool configuration, utilizing redis cluster endpoints.",
        "metadata": {
            "topic": "systems",
            "source": "gemini",
            "created_at": "2026-05-20T00:00:00Z",
            "last_accessed_at": "2026-05-22T00:00:00Z"
        }
    }
]


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
        except (OverflowError, ValueError, OSError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return dt.datetime.fromtimestamp(int(text), tz=dt.timezone.utc)
        except (OverflowError, ValueError, OSError):
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
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pinned", "keep", "protected"}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _memory_id(item: dict[str, Any], rank: int) -> str:
    return str(item.get("id") or item.get("memory_id") or f"rank-{rank}")


def load_mem0_json(path: str) -> list[dict[str, Any]]:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "candidates", "memories"):
            if isinstance(data.get(key), list):
                return data[key]
        if "content" in data or "text" in data or "memory" in data:
            return [data]
    raise ValueError("JSON must be a list of records or a dictionary containing an 'items', 'results' or 'candidates' list.")


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
    created_days = _age_days(item.get("created_at") or meta.get("created_at"), now=now)
    updated_days = _age_days(item.get("updated_at") or meta.get("updated_at"), now=now)

    last_accessed = meta.get("last_accessed_at") or meta.get("last_accessed")
    accessed_days = _age_days(last_accessed, now=now)

    route_score = _number(meta.get("route_score"))
    review_score = _number(meta.get("llm_review_score"))
    source_agent = str(meta.get("source") or meta.get("source_agent") or "").strip()
    topic = str(meta.get("topic") or "").strip()

    pinned = any(
        _boolish(meta.get(key))
        for key in ("is_pinned", "pinned", "retention_pin", "is_protected", "protected")
    ) or _boolish(item.get("pinned")) or _boolish(item.get("protected"))

    promoted = bool(
        mem_id in promotion_ids
        or any(meta.get(key) for key in ("promoted_to", "promoted_path", "wiki_path", "source_path"))
    )
    recent_hit = mem_id in recent_hits
    fingerprint = _normalized_fingerprint(text)
    duplicate_count = duplicate_counts.get(fingerprint, 0) if fingerprint else 0
    sensitive_hits = tm_memory_ops._light_sensitive_hits(text)

    score = 50
    reasons: list[str] = []
    risks: list[str] = []
    keep_signals: list[str] = []

    if pinned:
        score = 0
        keep_signals.append("pinned_or_protected")
    else:
        if promoted:
            score -= 20
            keep_signals.append("promoted_to_wiki_or_sources")
        if recent_hit:
            score -= 15
            keep_signals.append("recent_search_hit")

        if created_days is None:
            score += 10
            risks.append("missing_created_at")
        elif created_days > 365:
            score += 20
            reasons.append(f"old_created_at:{created_days}d")
        elif created_days > 180:
            score += 10
            reasons.append(f"aged_created_at:{created_days}d")
        elif created_days <= 30:
            score -= 10
            keep_signals.append(f"recent_created_at:{created_days}d")

        if last_accessed is None:
            score += 15
            risks.append("missing_last_accessed_at")
        else:
            if accessed_days is not None:
                if accessed_days > 180:
                    score += 20
                    reasons.append(f"stale_last_accessed:{accessed_days}d")
                elif accessed_days <= 30:
                    score -= 20
                    keep_signals.append(f"recent_last_accessed:{accessed_days}d")
            else:
                score += 10
                risks.append("invalid_last_accessed_at")

        if not topic:
            score += 15
            risks.append("missing_topic")
        if not source_agent:
            score += 15
            risks.append("missing_source_agent")

        text_len = len(text.strip())
        if text_len < 20:
            score += 20
            risks.append("very_short_text")
        elif text_len < 50:
            score += 10
            risks.append("short_text")

        if re.search(r"(?i)\b(?:placeholder|test|temp|asdf|foo|bar)\b", text):
            score += 15
            risks.append("low_quality_placeholder")

        if duplicate_count > 1:
            score += min(25, 10 + duplicate_count * 3)
            risks.append(f"duplicate_fingerprint_count:{duplicate_count}")

        if sensitive_hits:
            score += 30
            risks.extend(f"sensitive:{hit['kind']}" for hit in sensitive_hits)

        is_conservative = topic in ("person", "investment") or source_agent == "human"
        if is_conservative:
            if topic == "person":
                score -= 20
                keep_signals.append("topic:person")
            if topic == "investment":
                score -= 20
                keep_signals.append("topic:investment")
            if source_agent == "human":
                score -= 25
                keep_signals.append("source:human")

    score = max(0, min(100, int(round(score))))

    if pinned:
        action = "keep"
    elif not topic or not source_agent:
        action = "protect_metadata_missing"
    else:
        is_conservative = topic in ("person", "investment") or source_agent == "human"
        if is_conservative:
            if score >= 60:
                action = "review"
            else:
                action = "keep"
        else:
            if last_accessed is None:
                action = "review"
            elif score >= 75:
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
        "created_at": item.get("created_at") or meta.get("created_at"),
        "updated_at": item.get("updated_at") or meta.get("updated_at"),
        "last_accessed_at": last_accessed,
        "created_age_days": created_days,
        "updated_age_days": updated_days,
        "last_accessed_age_days": accessed_days,
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
    source: str = "sample",
    input_path: str | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    now: dt.datetime | None = None,
    dogfood_log: pathlib.Path = DEFAULT_DOGFOOD_LOG,
) -> dict[str, Any]:
    now = now or _now_utc()
    items: list[dict[str, Any]] = []
    warnings: list[str] = [
        "dry-run only: no Mem0 records were deleted or updated",
    ]

    try:
        if source == "sample":
            items = SAMPLE_DATA
            warnings.append("using local SAMPLE_DATA for offline review simulation")
        elif source == "mem0-json":
            if not input_path:
                raise ValueError("source='mem0-json' requires --input parameter")
            items = load_mem0_json(input_path)
            warnings.append(f"loaded records from local file: {input_path}")
        else:
            raise ValueError(f"unknown source: {source}")
        if max_items > 0:
            items = items[:max_items]
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
                "retention candidates could not be loaded",
            ],
        }

    duplicate_counts = _duplicate_groups(items)

    recent_hits = set()
    try:
        recent_hits = _recent_mem0_hits(dogfood_log, now=now)
    except Exception:
        pass

    promotion_ids = set()
    try:
        promotion_ids = _promotion_marker_ids(REPO_ROOT)
    except Exception:
        pass

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
        "warnings": warnings,
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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    report = run_retention_audit(
        source=args.source,
        input_path=args.input,
        max_items=args.max_items,
    )
    if not report.get("ok"):
        sys.stderr.write(f"Error: {report.get('error')}\n")
        return 1

    if args.json:
        output_content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    else:
        output_content = render_markdown(report, limit=args.limit)

    if args.output:
        out_path = pathlib.Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_content, encoding="utf-8")
        sys.stdout.write(f"Retention report written to: {args.output}\n")
    else:
        sys.stdout.write(output_content)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_retention_audit.py", description=__doc__)
    parser.add_argument(
        "--source",
        choices=["sample", "mem0-json"],
        default="sample",
        help="Data source: 'sample' (offline built-in) or 'mem0-json' (local file)"
    )
    parser.add_argument(
        "--input",
        type=str,
        help="Path to input JSON file (required when --source is 'mem0-json')"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Path to output markdown or json file instead of writing to stdout"
    )
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--limit", type=int, default=30, help="markdown rows to print")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args()

    if args.source == "mem0-json" and not args.input:
        parser.error("--input is required when --source is 'mem0-json'")

    sys.exit(cmd_audit(args))


if __name__ == "__main__":
    main()
