#!/usr/bin/env python3
"""Discard quarantine for write_memory route decisions.

Mem0 and inbox writes already have durable storage and should not be duplicated
into temp logs. This module records only entries that would otherwise be
discarded, so nightly Codex reflection can inspect misses without polluting
same-day memory search.
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
from typing import Any

import tigermemory_core as tm_core
import tm_route

REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_AUDIT_ROOT = REPO_ROOT / ".tmp" / "memory-discard-quarantine"
MAX_EXCERPT_CHARS = 2400

CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer_token", re.compile(r"(?i)\b(?:Authorization\s*:\s*)?Bearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    (
        "credential",
        re.compile(
            r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|pwd|access[_-]?token|"
            r"refresh[_-]?token|private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s]{8,}"
        ),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    ),
    ("cn_phone", re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")),
    (
        "cn_id",
        re.compile(
            r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9Xx])"
        ),
    ),
]


def _now_local() -> dt.datetime:
    return dt.datetime.now(tm_core.TZ_CN)


def _event_date(now: dt.datetime | None = None) -> str:
    return (now or _now_local()).astimezone(tm_core.TZ_CN).strftime("%Y-%m-%d")


def _redact(text: str) -> str:
    redacted = text
    for kind, pattern in CREDENTIAL_PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
    return redacted


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _write_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _relpath(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def record_discard_event(
    *,
    agent: str,
    requested_topic: str,
    text: str,
    decision: tm_route.RouteDecision,
    source: str = "write_memory",
    audit_root: pathlib.Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Append one discard event and return lightweight file metadata."""
    now = (now or _now_local()).astimezone(tm_core.TZ_CN)
    date = _event_date(now)
    root = pathlib.Path(audit_root or DEFAULT_AUDIT_ROOT)
    text_clean = _redact(text)
    text_excerpt = text_clean[:MAX_EXCERPT_CHARS]
    event_id = _sha12(f"{now.isoformat()}|{agent}|{requested_topic}|discard|{text}")
    row = {
        "event_id": event_id,
        "ts": now.isoformat(),
        "date": date,
        "source": source,
        "agent": agent,
        "requested_topic": requested_topic,
        "topic_inferred": decision.topic_inferred,
        "route": "discard",
        "score": decision.score,
        "is_transient": decision.is_transient,
        "is_sensitive": decision.is_sensitive,
        "needs_human_review": decision.needs_human_review,
        "unreviewed": decision.unreviewed,
        "issues": decision.issues,
        "reasons": decision.reasons,
        "text_len": len(text),
        "text_sha256_12": _sha12(text),
        "text_excerpt": text_excerpt,
    }
    path = root / date / "discard" / "events.jsonl"
    _write_jsonl(path, row)
    return {
        "ok": True,
        "event_id": event_id,
        "date": date,
        "route": "discard",
        "path": _relpath(path),
    }


def load_discard_events(
    *,
    date: str | None = None,
    audit_root: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    root = pathlib.Path(audit_root or DEFAULT_AUDIT_ROOT)
    date = date or _event_date()
    path = root / date / "discard" / "events.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for raw in lines:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            events.append(row)
    return events


def build_summary(events: list[dict[str, Any]], *, date: str) -> dict[str, Any]:
    topic_counts: dict[str, int] = {}
    agent_counts: dict[str, int] = {}
    review_flags: list[dict[str, Any]] = []
    for row in events:
        topic = str(row.get("requested_topic") or "unknown")
        agent = str(row.get("agent") or "unknown")
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        agent_counts[agent] = agent_counts.get(agent, 0) + 1
        score = row.get("score")
        if isinstance(score, int) and score >= 70:
            review_flags.append({
                "event_id": row.get("event_id"),
                "reason": "high_score_discard",
                "score": score,
                "excerpt": str(row.get("text_excerpt") or "")[:240],
            })
        if not bool(row.get("is_transient")) and not bool(row.get("is_sensitive")):
            review_flags.append({
                "event_id": row.get("event_id"),
                "reason": "non_transient_discard",
                "score": score,
                "excerpt": str(row.get("text_excerpt") or "")[:240],
            })
    return {
        "date": date,
        "event_count": len(events),
        "route_counts": {"discard": len(events)},
        "topic_counts": topic_counts,
        "agent_counts": agent_counts,
        "review_flags": review_flags,
        "events": events,
    }


def render_markdown(summary: dict[str, Any], *, limit: int = 80) -> str:
    lines = [
        f"# Memory Discard Quarantine Summary {summary['date']}",
        "",
        "This is a local review feed for entries that write_memory would have discarded.",
        "Mem0 and inbox entries are not duplicated here.",
        "",
        "## Counts",
        "",
        f"- discarded events: {summary['event_count']}",
        f"- topics: {json.dumps(summary['topic_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- agents: {json.dumps(summary['agent_counts'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## Review Flags",
        "",
    ]
    flags = summary.get("review_flags") or []
    if flags:
        for flag in flags[:limit]:
            excerpt = str(flag.get("excerpt") or "").replace("|", "\\|")
            lines.append(
                f"- `{flag.get('reason')}` score={flag.get('score')} "
                f"id={flag.get('event_id')} :: {excerpt}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Discard Events", ""])
    for row in summary.get("events", [])[:limit]:
        excerpt = str(row.get("text_excerpt") or "").replace("\n", " ")[:220].replace("|", "\\|")
        lines.append(
            f"- score={row.get('score')} topic={row.get('requested_topic')} "
            f"agent={row.get('agent')} id={row.get('event_id')} :: {excerpt}"
        )
    return "\n".join(lines) + "\n"


def run_summary(
    *,
    date: str | None = None,
    audit_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    date = date or _event_date()
    events = load_discard_events(date=date, audit_root=audit_root)
    return build_summary(events, date=date)


def cmd_summary(args: argparse.Namespace) -> int:
    summary = run_summary(date=args.date, audit_root=pathlib.Path(args.root) if args.root else None)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(summary, limit=args.limit), end="")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize local write_memory discard quarantine events")
    parser.add_argument("--date")
    parser.add_argument("--root")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    raise SystemExit(cmd_summary(args))


if __name__ == "__main__":
    main()
