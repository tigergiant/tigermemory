from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import _bootstrap_paths  # noqa: F401
import tm_route
import tm_route_events
from tigermemory_core import TZ_CN


def _decision() -> tm_route.RouteDecision:
    return tm_route.RouteDecision(
        route="mem0",
        score=90,
        topic_inferred="systems",
        issues=[],
        reasons="stable operational memory",
        is_transient=False,
        is_sensitive=False,
        needs_human_review=False,
        knowledge_target="mem0",
        target_confidence=95,
    )


def test_route_events_concurrent_writes_remain_valid_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_ROUTE_EVENTS_LOCK_TIMEOUT_SEC", "5")
    root = tmp_path / "route-events"
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN)

    def write_one(index: int) -> None:
        tm_route_events.record_route_event(
            agent="codex",
            requested_topic="systems",
            storage_topic="systems",
            text=f"2026-06-10 route event concurrent probe {index}",
            decision=_decision(),
            result={"route": "mem0", "id": f"m-{index}"},
            outcome="mem0",
            event_root=root,
            now=now + dt.timedelta(microseconds=index),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_one, range(40)))

    path = root / "2026-06-10" / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]

    assert len(rows) == 40
    assert {row["target_ref"]["id"] for row in rows} == {f"m-{index}" for index in range(40)}
    assert all(row["flow_target"] == "mem0" for row in rows)
    assert not path.with_name(path.name + ".lock").exists()


def test_route_events_do_not_leak_from_pytest_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TM_ROUTE_EVENTS_ROOT", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_tm_route_events.py::case")
    monkeypatch.setattr(tm_route_events, "DEFAULT_EVENT_ROOT", tmp_path / "repo-events")
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN)

    result = tm_route_events.record_route_event(
        agent="codex",
        requested_topic="systems",
        storage_topic="systems",
        text="synthetic fallback route event from test",
        decision=_decision(),
        result={"route": "mem0", "id": "m-1"},
        outcome="mem0",
        now=now,
    )

    assert result["skipped"] == "disabled"
    assert not (tmp_path / "repo-events").exists()


def test_route_events_pytest_can_write_to_explicit_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_tm_route_events.py::case")
    root = tmp_path / "explicit-events"
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN)

    result = tm_route_events.record_route_event(
        agent="codex",
        requested_topic="systems",
        storage_topic="systems",
        text="explicit route event test root",
        decision=_decision(),
        result={"route": "mem0", "id": "m-2"},
        outcome="mem0",
        event_root=root,
        now=now,
    )

    assert result["ok"] is True
    assert (root / "2026-06-10" / "events.jsonl").exists()
