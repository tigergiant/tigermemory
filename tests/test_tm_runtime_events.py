from __future__ import annotations

import concurrent.futures
import datetime as dt
import importlib.util
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import _bootstrap_paths  # noqa: F401
from tigermemory_core import TZ_CN
from tigermemory_core import runtime_events as tm_runtime_events


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("tm_runtime_events_cli", REPO_ROOT / "tools" / "tm_runtime_events.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_record_event_redacts_raw_content_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path))
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN)

    result = tm_runtime_events.record_event(
        event_type="http_request",
        service="tm-http",
        component="/write_memory",
        ok=False,
        severity="error",
        trace_id="trace-1",
        duration_ms=12.3,
        error="failed",
        extra={
            "text": "raw memory text that must not be copied",
            "api_key": "secret-value",
            "safe_count": 3,
        },
        now=now,
    )

    assert result["ok"] is True
    path = tmp_path / "2026-06-10" / "events.jsonl"
    raw = path.read_text(encoding="utf-8")
    assert "raw memory text" not in raw
    assert "secret-value" not in raw
    row = json.loads(raw)
    assert row["extra"]["text"]["len"] == 39
    assert row["extra"]["api_key"] == "[redacted]"
    assert row["extra"]["safe_count"] == 3


def test_load_and_summarize_events(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path))
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN)

    tm_runtime_events.record_event(
        event_type="memory_route",
        service="write_memory",
        component="router",
        ok=True,
        route="mem0",
        outcome="mem0",
        now=now,
    )
    tm_runtime_events.record_event(
        event_type="daily_review_action",
        service="tm-dashboard",
        component="digest_review",
        ok=False,
        outcome="timeout",
        now=now,
    )

    events = tm_runtime_events.load_events(dates=["2026-06-10"], event_root=tmp_path)
    summary = tm_runtime_events.summarize_events(events, dates=["2026-06-10"], event_root=tmp_path)

    assert len(events) == 2
    assert summary["event_count"] == 2
    assert summary["service_counts"] == {"tm-dashboard": 1, "write_memory": 1}
    assert summary["type_counts"]["memory_route"] == 1
    assert summary["ok_counts"] == {"ok": 1, "failed": 1}


def test_record_event_concurrent_writes_remain_valid_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path))
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN)

    def write_one(index: int) -> None:
        tm_runtime_events.record_event(
            event_type="concurrent_probe",
            service="tm-test",
            component="jsonl_lock",
            ok=True,
            trace_id=f"trace-{index}",
            now=now + dt.timedelta(microseconds=index),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_one, range(40)))

    path = tmp_path / "2026-06-10" / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]

    assert len(rows) == 40
    assert {row["trace_id"] for row in rows} == {f"trace-{index}" for index in range(40)}
    assert not path.with_name(path.name + ".lock").exists()


def test_record_event_fails_explicitly_when_jsonl_lock_is_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path))
    monkeypatch.setenv("TM_RUNTIME_EVENTS_LOCK_TIMEOUT_SEC", "0")
    monkeypatch.setenv("TM_RUNTIME_EVENTS_LOCK_STALE_SEC", "3600")
    path = tmp_path / "2026-06-10" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.with_name(path.name + ".lock").write_text("busy\n", encoding="utf-8")

    with pytest.raises(TimeoutError, match="runtime event ledger lock busy"):
        tm_runtime_events.record_event(
            event_type="blocked_probe",
            service="tm-test",
            component="jsonl_lock",
            ok=True,
            now=dt.datetime(2026, 6, 10, 12, 0, tzinfo=TZ_CN),
        )


def test_runtime_events_cli_record_subcommand(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path))
    cli = _load_cli_module()

    rc = cli.main([
        "record",
        "--event-type", "service_auto_update",
        "--service", "tm-mcp",
        "--component", "auto_update",
        "--ok",
        "--outcome", "ff_only_checked",
        "--target-ref", "head=abc123",
        "--extra", "timeout_sec=5",
    ])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    events = tm_runtime_events.load_events(dates=[tm_runtime_events._date_key()], event_root=tmp_path)
    assert events[-1]["event_type"] == "service_auto_update"
    assert events[-1]["target_ref"]["head"] == "abc123"
