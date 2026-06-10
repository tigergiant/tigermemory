#!/usr/bin/env python3
"""Inspect TigerMemory unified runtime events."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

import _bootstrap_paths  # noqa: F401  -- must precede tigermemory_core imports

import tigermemory_core as tm_core
from tigermemory_core import runtime_events as tm_runtime_events


def _date_range(days: int) -> list[str]:
    today = dt.datetime.now(tm_core.TZ_CN).date()
    return [(today - dt.timedelta(days=offset)).isoformat() for offset in range(max(1, days))]


def _json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _pairs(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"expected KEY=VALUE, got: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"empty key in: {raw}")
        out[key] = value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize unified TigerMemory runtime events")
    sub = parser.add_subparsers(dest="cmd")

    record = sub.add_parser("record", help="append one runtime event")
    record.add_argument("--event-type", required=True)
    record.add_argument("--service", required=True)
    record.add_argument("--component")
    ok_group = record.add_mutually_exclusive_group()
    ok_group.add_argument("--ok", action="store_true", default=True)
    ok_group.add_argument("--failed", action="store_true")
    record.add_argument("--severity")
    record.add_argument("--trace-id")
    record.add_argument("--duration-ms", type=float)
    record.add_argument("--actor")
    record.add_argument("--agent")
    record.add_argument("--route")
    record.add_argument("--outcome")
    record.add_argument("--source-log")
    record.add_argument("--error")
    record.add_argument("--target-ref", action="append", default=[], help="KEY=VALUE; repeatable")
    record.add_argument("--extra", action="append", default=[], help="KEY=VALUE; repeatable")

    parser.add_argument("--days", type=int, default=1, help="days to include, counting today")
    parser.add_argument("--limit", type=int, default=50, help="max recent events to include")
    parser.add_argument("--events", action="store_true", help="include recent event rows")
    args = parser.parse_args(argv)

    if args.cmd == "record":
        result = tm_runtime_events.record_event(
            event_type=args.event_type,
            service=args.service,
            component=args.component,
            ok=not bool(args.failed),
            severity=args.severity,
            trace_id=args.trace_id,
            duration_ms=args.duration_ms,
            actor=args.actor,
            agent=args.agent,
            route=args.route,
            outcome=args.outcome,
            target_ref=_pairs(args.target_ref),
            source_log=args.source_log,
            error=args.error,
            extra=_pairs(args.extra),
        )
        _json(result)
        return 0 if result.get("ok") else 1

    dates = _date_range(args.days)
    events = tm_runtime_events.load_events(dates=dates, limit=args.limit)
    payload = {
        "ok": True,
        "summary": tm_runtime_events.summarize_events(events, dates=dates),
    }
    if args.events:
        payload["events"] = events
    _json(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
