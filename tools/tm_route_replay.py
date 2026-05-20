#!/usr/bin/env python3
"""Seven-day route replay support for memory reflection proposals.

This tool collects historical write_memory inputs from Mem0, inbox, and discard
quarantine, then compares old route decisions with a supplied/new judgment.
It intentionally does not call DeepSeek. In cron usage, GPT-5.5 can provide a
judgment file; tests inject a judge function.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Callable

import tm_core
import tm_route_audit

try:
    import tm_retention_audit
except Exception:  # pragma: no cover - only for degraded runtime imports
    tm_retention_audit = None  # type: ignore[assignment]

REPO_ROOT = tm_core.REPO_ROOT
PROPOSAL_ROOT = REPO_ROOT / ".tmp" / "cron-proposals"
ROUTES = ("mem0", "inbox", "discard")
SEVERE_FLIPS = {("mem0", "discard"), ("discard", "mem0")}
DEFAULT_MAX_CASES = 300
SAMPLE_EDGE_COUNT = 50


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    source: str
    content: str
    topic: str
    agent: str
    original_route: str
    original_score: int | None
    original_reason: str
    created_at: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class ReplayDecision:
    route: str
    score: int | None = None
    reason: str = ""


def _now_local() -> dt.datetime:
    return dt.datetime.now(tm_core.TZ_CN)


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def date_window(date: str, *, days: int = 7) -> list[str]:
    end = _parse_date(date)
    start = end - dt.timedelta(days=days - 1)
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]


def _parse_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return dt.datetime.fromtimestamp(int(text), tz=dt.timezone.utc).astimezone(tm_core.TZ_CN)
        except (OverflowError, ValueError):
            return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tm_core.TZ_CN)
    return parsed.astimezone(tm_core.TZ_CN)


def _date_of(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def _item_text(item: dict[str, Any]) -> str:
    return str(item.get("content") or item.get("memory") or item.get("text") or "")


def _item_meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata_") or item.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _safe_route(value: Any, fallback: str = "discard") -> str:
    text = str(value or fallback)
    return text if text in ROUTES else fallback


def _safe_score(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _relpath(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4:].lstrip("\r\n")
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"')
        if value.lower() in {"true", "false"}:
            data[key.strip()] = value.lower() == "true"
        else:
            data[key.strip()] = value
    return data, body


def collect_mem0_cases(*, dates: set[str], max_items: int = 500) -> list[ReplayCase]:
    if tm_retention_audit is None:
        return []
    try:
        items = tm_retention_audit.fetch_mem0_items(max_items=max_items)
    except Exception:
        return []
    cases: list[ReplayCase] = []
    for index, item in enumerate(items):
        created = _date_of(item.get("created_at") or item.get("created_at_local"))
        if created not in dates:
            continue
        meta = _item_meta(item)
        text = tm_route_audit._redact(_item_text(item))  # noqa: SLF001 - shared redaction helper
        case_id = str(item.get("id") or f"mem0-{index}")
        cases.append(ReplayCase(
            case_id=case_id,
            source="mem0",
            content=text,
            topic=str(meta.get("route_requested_topic") or meta.get("topic") or "cross"),
            agent=str(meta.get("source") or "mem0"),
            original_route="mem0",
            original_score=_safe_score(meta.get("route_score")),
            original_reason=str(meta.get("route_reason") or meta.get("route_decision") or "mem0 formal write"),
            created_at=str(item.get("created_at") or ""),
        ))
    return cases


def collect_inbox_cases(*, dates: set[str], inbox_dir: pathlib.Path | None = None) -> list[ReplayCase]:
    root = inbox_dir or (REPO_ROOT / "inbox")
    cases: list[ReplayCase] = []
    if not root.exists():
        return cases
    for path in sorted(root.glob("*.md")):
        match = re.match(r"(\d{4}-\d{2}-\d{2})-", path.name)
        if not match or match.group(1) not in dates:
            continue
        text = path.read_text(encoding="utf-8")
        fm, body = _frontmatter(text)
        topic = str(fm.get("route_requested_topic") or fm.get("stored_topic") or "cross")
        agent = str(fm.get("source") or fm.get("owner") or "unknown")
        cases.append(ReplayCase(
            case_id=path.name,
            source="inbox",
            content=tm_route_audit._redact(body),  # noqa: SLF001
            topic=topic,
            agent=agent,
            original_route="inbox",
            original_score=_safe_score(fm.get("route_score")),
            original_reason=str(fm.get("route_decision_reason") or "inbox formal write"),
            created_at=match.group(1),
            path=_relpath(path),
        ))
    return cases


def collect_discard_cases(
    *,
    dates: set[str],
    audit_root: pathlib.Path | None = None,
) -> list[ReplayCase]:
    cases: list[ReplayCase] = []
    for date in sorted(dates):
        for row in tm_route_audit.load_discard_events(date=date, audit_root=audit_root):
            cases.append(ReplayCase(
                case_id=str(row.get("event_id") or f"discard-{date}-{len(cases)}"),
                source="discard",
                content=str(row.get("text_excerpt") or ""),
                topic=str(row.get("requested_topic") or "cross"),
                agent=str(row.get("agent") or "unknown"),
                original_route="discard",
                original_score=_safe_score(row.get("score")),
                original_reason=str(row.get("reasons") or ""),
                created_at=str(row.get("ts") or date),
            ))
    return cases


def collect_cases(
    *,
    date: str,
    days: int = 7,
    max_mem0_items: int = 500,
    audit_root: pathlib.Path | None = None,
    inbox_dir: pathlib.Path | None = None,
) -> list[ReplayCase]:
    dates = set(date_window(date, days=days))
    out: list[ReplayCase] = []
    out.extend(collect_mem0_cases(dates=dates, max_items=max_mem0_items))
    out.extend(collect_inbox_cases(dates=dates, inbox_dir=inbox_dir))
    out.extend(collect_discard_cases(dates=dates, audit_root=audit_root))
    return out


def _sample_cases(cases: list[ReplayCase], max_cases: int) -> tuple[list[ReplayCase], str]:
    if len(cases) <= max_cases:
        return cases, "full"
    scored = sorted(cases, key=lambda c: (-1 if c.original_score is None else c.original_score))
    low = [case for case in scored if case.original_score is not None][:SAMPLE_EDGE_COUNT]
    high = [case for case in scored if case.original_score is not None][-SAMPLE_EDGE_COUNT:]
    selected: dict[str, ReplayCase] = {case.case_id: case for case in low + high}
    for case in cases:
        if len(selected) >= max_cases:
            break
        selected.setdefault(case.case_id, case)
    return list(selected.values()), "edge-sample"


def load_judgment_file(path: pathlib.Path) -> dict[str, ReplayDecision]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("decisions"), dict):
        data = data["decisions"]
    if not isinstance(data, dict):
        raise ValueError("judgment file must be an object or {decisions:{...}}")
    out: dict[str, ReplayDecision] = {}
    for case_id, raw in data.items():
        if not isinstance(raw, dict):
            continue
        out[str(case_id)] = ReplayDecision(
            route=_safe_route(raw.get("route")),
            score=_safe_score(raw.get("score")),
            reason=str(raw.get("reason") or ""),
        )
    return out


def no_llm_judge(case: ReplayCase, _new_prompt: str) -> ReplayDecision:
    return ReplayDecision(route=case.original_route, score=case.original_score, reason="no GPT-5.5 judgment supplied")


def replay_cases(
    cases: list[ReplayCase],
    *,
    date: str,
    new_prompt: str,
    proposal_id: str,
    judge: Callable[[ReplayCase, str], ReplayDecision] | None = None,
    max_cases: int = DEFAULT_MAX_CASES,
) -> dict[str, Any]:
    judge = judge or no_llm_judge
    selected, token_budget_mode = _sample_cases(cases, max_cases)
    matrix = {old: {new: 0 for new in ROUTES} for old in ROUTES}
    severe_cases: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for case in selected:
        decision = judge(case, new_prompt)
        old_route = _safe_route(case.original_route)
        new_route = _safe_route(decision.route)
        matrix[old_route][new_route] += 1
        row = {
            "case_id": case.case_id,
            "source": case.source,
            "topic": case.topic,
            "agent": case.agent,
            "old_decision": {
                "route": old_route,
                "score": case.original_score,
                "reason": case.original_reason,
            },
            "new_decision": {
                "route": new_route,
                "score": decision.score,
                "reason": decision.reason,
            },
            "content_excerpt": case.content[:800],
        }
        rows.append(row)
        if (old_route, new_route) in SEVERE_FLIPS:
            severe_cases.append(row)
    severe_count = len(severe_cases)
    if severe_count <= 2:
        recommendation = "apply"
    elif severe_count <= 5:
        recommendation = "review-warning"
    else:
        recommendation = "reject-by-default"
    return {
        "date": date,
        "proposal_id": proposal_id,
        "date_window": date_window(date),
        "total_cases": len(cases),
        "replayed_cases": len(selected),
        "token_budget_mode": token_budget_mode,
        "matrix": matrix,
        "severe_count": severe_count,
        "recommendation": recommendation,
        "severe_cases": severe_cases[:5],
        "cases": rows,
    }


def write_result(result: dict[str, Any], *, date: str, proposal_id: str) -> pathlib.Path:
    out = PROPOSAL_ROOT / date / proposal_id / "replay-result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def run_replay(
    *,
    date: str,
    proposal_id: str,
    new_prompt: str,
    judgment_file: pathlib.Path | None = None,
    days: int = 7,
    max_cases: int = DEFAULT_MAX_CASES,
    audit_root: pathlib.Path | None = None,
    inbox_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    cases = collect_cases(date=date, days=days, audit_root=audit_root, inbox_dir=inbox_dir)
    judgments = load_judgment_file(judgment_file) if judgment_file else {}

    def judge(case: ReplayCase, prompt: str) -> ReplayDecision:
        return judgments.get(case.case_id) or no_llm_judge(case, prompt)

    result = replay_cases(
        cases,
        date=date,
        new_prompt=new_prompt,
        proposal_id=proposal_id,
        judge=judge,
        max_cases=max_cases,
    )
    path = write_result(result, date=date, proposal_id=proposal_id)
    result["output_path"] = _relpath(path)
    return result


def cmd_replay(args: argparse.Namespace) -> int:
    if args.prompt_file:
        new_prompt = pathlib.Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        new_prompt = args.prompt or ""
    result = run_replay(
        date=args.date,
        proposal_id=args.proposal,
        new_prompt=new_prompt,
        judgment_file=pathlib.Path(args.judgments) if args.judgments else None,
        days=args.days,
        max_cases=args.max_cases,
        audit_root=pathlib.Path(args.audit_root) if args.audit_root else None,
        inbox_dir=pathlib.Path(args.inbox_dir) if args.inbox_dir else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay memory route decisions for prompt proposals")
    parser.add_argument("--date", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--prompt-file")
    parser.add_argument("--prompt")
    parser.add_argument("--judgments", help="optional GPT-5.5 judgment JSON keyed by case_id")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-cases", type=int, default=DEFAULT_MAX_CASES)
    parser.add_argument("--audit-root")
    parser.add_argument("--inbox-dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return cmd_replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
