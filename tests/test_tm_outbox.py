"""Tests for the durable outbox: tm_core row lifecycle + tm_memory_ops worker.

All tests use a temp DB via TIGERMEMORY_LOCAL_DB and never touch the
production data/tigermemory/memory.sqlite.
"""
from __future__ import annotations

import datetime
import pathlib
import sqlite3
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]
import tm_memory_ops  # type: ignore[import-not-found]


@pytest.fixture()
def outbox_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setenv("TM_OUTBOX_WORKER", "1")
    return db_path


def _row(db_path: pathlib.Path, kind: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM outbox WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
    finally:
        conn.close()


def _force_due(db_path: pathlib.Path, kind: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE outbox SET next_attempt_at='2000-01-01T00:00:00+0800' WHERE kind=?",
            (kind,),
        )
        conn.commit()
    finally:
        conn.close()


def test_upsert_debounces_to_single_pending_row(outbox_db):
    first = tm_core.outbox_upsert("digest_refresh", {}, delay_seconds=60)
    assert first["action"] == "inserted"
    second = tm_core.outbox_upsert("digest_refresh", {}, delay_seconds=120)
    assert second["action"] == "updated"

    conn = sqlite3.connect(str(outbox_db))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE kind='digest_refresh' AND status='pending'"
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()
    assert second["next_attempt_at"] > first["next_attempt_at"]


def test_claim_due_skips_future_rows(outbox_db):
    tm_core.outbox_upsert("digest_refresh", {}, delay_seconds=3600)
    assert tm_core.outbox_claim_due() == []

    _force_due(outbox_db, "digest_refresh")
    claimed = tm_core.outbox_claim_due()
    assert len(claimed) == 1
    assert claimed[0]["kind"] == "digest_refresh"
    assert claimed[0]["status"] == "running"
    # Claimed rows are not claimable again until stale deadline passes.
    assert tm_core.outbox_claim_due() == []


def test_complete_marks_done(outbox_db):
    tm_core.outbox_upsert("digest_refresh", {}, delay_seconds=0)
    claimed = tm_core.outbox_claim_due()
    tm_core.outbox_complete(claimed[0]["id"])
    row = _row(outbox_db, "digest_refresh")
    assert row["status"] == "done"
    assert row["done_at"] is not None
    assert tm_core.outbox_counts()["done"] == 1


def test_fail_backoff_then_dead(outbox_db):
    tm_core.outbox_upsert("digest_refresh", {}, delay_seconds=0)
    claimed = tm_core.outbox_claim_due()
    outbox_id = claimed[0]["id"]

    result = tm_core.outbox_fail(outbox_id, "boom-1")
    assert result["status"] == "pending"
    assert result["attempts"] == 1
    row = _row(outbox_db, "digest_refresh")
    assert row["last_error"] == "boom-1"

    for attempt in range(2, tm_core.OUTBOX_MAX_ATTEMPTS):
        result = tm_core.outbox_fail(outbox_id, f"boom-{attempt}")
        assert result["status"] == "pending"
    result = tm_core.outbox_fail(outbox_id, "boom-final")
    assert result["status"] == "dead"
    assert _row(outbox_db, "digest_refresh")["status"] == "dead"


def test_stale_running_row_is_reclaimed(outbox_db):
    tm_core.outbox_upsert("digest_refresh", {}, delay_seconds=0)
    claimed = tm_core.outbox_claim_due()
    assert len(claimed) == 1
    # Simulate a worker that died: push the stale deadline into the past.
    _force_due(outbox_db, "digest_refresh")
    reclaimed = tm_core.outbox_claim_due()
    assert len(reclaimed) == 1
    assert reclaimed[0]["id"] == claimed[0]["id"]


def test_schedule_digest_refresh_uses_outbox_when_enabled(outbox_db):
    tm_memory_ops.schedule_digest_refresh()
    row = _row(outbox_db, "digest_refresh")
    assert row is not None
    assert row["status"] == "pending"


def test_schedule_embed_refresh_uses_outbox_when_enabled(outbox_db):
    result = tm_memory_ops.schedule_embed_refresh(scope="wiki", reason="test")
    assert result["embed_refresh_via"] == "outbox"
    row = _row(outbox_db, "embed_refresh:wiki")
    assert row is not None
    assert row["status"] == "pending"


def test_schedule_falls_back_to_timer_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "memory.sqlite"))
    monkeypatch.setenv("TM_OUTBOX_WORKER", "0")
    result = tm_memory_ops.schedule_embed_refresh(scope="wiki", reason="test")
    assert "embed_refresh_via" not in result
    assert not (tmp_path / "memory.sqlite").exists() or _row(
        tmp_path / "memory.sqlite", "embed_refresh:wiki"
    ) is None
    # Cancel the timer we just started so the test process exits cleanly.
    with tm_memory_ops._embed_lock:
        timer = tm_memory_ops._embed_timers.pop("wiki", None)
    if timer is not None:
        timer.cancel()


def test_poll_once_dispatches_and_completes(outbox_db, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        tm_memory_ops, "_refresh_digest_today", lambda: calls.append("digest")
    )
    tm_memory_ops.schedule_digest_refresh()
    _force_due(outbox_db, "digest_refresh")

    stats = tm_memory_ops.outbox_poll_once()
    assert stats == {"claimed": 1, "done": 1, "retried": 0, "dead": 0}
    assert calls == ["digest"]
    assert _row(outbox_db, "digest_refresh")["status"] == "done"


def test_poll_once_retries_failing_handler(outbox_db, monkeypatch):
    def _boom() -> None:
        raise RuntimeError("digest exploded")

    monkeypatch.setattr(tm_memory_ops, "_refresh_digest_today", _boom)
    tm_memory_ops.schedule_digest_refresh()
    _force_due(outbox_db, "digest_refresh")

    stats = tm_memory_ops.outbox_poll_once()
    assert stats == {"claimed": 1, "done": 0, "retried": 1, "dead": 0}
    row = _row(outbox_db, "digest_refresh")
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert "digest exploded" in row["last_error"]


def test_poll_once_unknown_kind_goes_to_retry(outbox_db):
    tm_core.outbox_upsert("no_such_kind", {}, delay_seconds=0)
    stats = tm_memory_ops.outbox_poll_once()
    assert stats["claimed"] == 1
    assert stats["retried"] == 1


def test_start_outbox_worker_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "memory.sqlite"))
    monkeypatch.setenv("TM_OUTBOX_WORKER", "0")
    assert tm_memory_ops.start_outbox_worker() is False


def test_embed_payload_round_trip(outbox_db):
    tm_memory_ops.schedule_embed_refresh(
        scope="sources_only", reason="ingest", paths=["sources/a.md"]
    )
    _force_due(outbox_db, "embed_refresh:sources_only")
    claimed = tm_core.outbox_claim_due()
    assert claimed[0]["payload"] == {
        "scope": "sources_only",
        "reason": "ingest",
        "paths": ["sources/a.md"],
    }
