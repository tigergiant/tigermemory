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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize unified TigerMemory runtime events")
    parser.add_argument("--days", type=int, default=1, help="days to include, counting today")
    parser.add_argument("--limit", type=int, default=50, help="max recent events to include")
    parser.add_argument("--events", action="store_true", help="include recent event rows")
    args = parser.parse_args(argv)

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
