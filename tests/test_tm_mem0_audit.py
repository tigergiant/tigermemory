from __future__ import annotations

import datetime
import json

import pytest

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_mem0_audit  # type: ignore[import-not-found]


def _item(memory_id: str, text: str, *, topic: str = "systems", agent: str = "cascade", created_at: str = "2026-05-21T08:00:00+08:00") -> dict:
    return {
        "id": memory_id,
        "content": text,
        "created_at": created_at,
        "metadata": {"topic": topic, "source": agent},
    }


def _closeout(detail: str) -> str:
    return (
        "Windsurf Cascade post-response closeout summary. "
        "Sanitized Cascade response: *Rules used for this response:* "
        "- (Always On) Triggered Rule: keep response grounded. "
        f"{detail}"
    )


def test_strip_boilerplate_removes_cascade_closeout_markers():
    cleaned = tm_mem0_audit.strip_boilerplate(_closeout("Actual useful detail remains."))

    assert "Windsurf Cascade" not in cleaned
    assert "Triggered Rule" not in cleaned
    assert "Actual useful detail remains" in cleaned


def test_hamming_distance_counts_bit_differences():
    assert tm_mem0_audit.hamming_distance(0b1010, 0b0011) == 2
    assert tm_mem0_audit.hamming_distance(0, 0) == 0


def test_simhash_is_stable_for_identical_text():
    left = tm_mem0_audit.simhash64("same normalized memory text")
    right = tm_mem0_audit.simhash64("same normalized memory text")

    assert left == right


def test_singleton_cluster_has_no_candidate():
    rows = tm_mem0_audit.dedup_candidates([_item("m1", _closeout("only one"))])

    assert rows == []


def test_two_duplicate_entries_keep_newest_as_canonical():
    rows = tm_mem0_audit.dedup_candidates([
        _item("old", _closeout("same durable result"), created_at="2026-05-21T08:00:00+08:00"),
        _item("new", _closeout("same durable result"), created_at="2026-05-21T09:00:00+08:00"),
    ])

    assert len(rows) == 1
    assert rows[0]["candidate_id"] == "old"
    assert rows[0]["canonical_id"] == "new"
    assert rows[0]["signature_distance"] <= tm_mem0_audit.DEDUP_DISTANCE_THRESHOLD


def test_three_duplicate_entries_create_two_candidates():
    rows = tm_mem0_audit.dedup_candidates([
        _item("m1", _closeout("same commit and push")),
        _item("m2", _closeout("same commit and push"), created_at="2026-05-21T09:00:00+08:00"),
        _item("m3", _closeout("same commit and push"), created_at="2026-05-21T10:00:00+08:00"),
    ])

    assert len(rows) == 2
    assert {row["canonical_id"] for row in rows} == {"m3"}


def test_same_content_different_topic_does_not_cluster():
    rows = tm_mem0_audit.dedup_candidates([
        _item("systems-1", _closeout("same text"), topic="systems"),
        _item("ops-1", _closeout("same text"), topic="operations"),
    ])

    assert rows == []


def test_candidates_are_capped_to_top_twenty():
    items = [
        _item(f"m{idx:02d}", _closeout("same repeated closeout"), created_at=f"2026-05-21T{idx % 24:02d}:00:00+08:00")
        for idx in range(25)
    ]

    rows = tm_mem0_audit.dedup_candidates(items)

    assert len(rows) == tm_mem0_audit.MAX_DEDUP_CANDIDATES


def test_audit_dedup_writes_json(tmp_path):
    report = tm_mem0_audit.audit_dedup(
        "2026-05-21",
        items=[
            _item("old", _closeout("same written item"), created_at="2026-05-21T08:00:00+08:00"),
            _item("new", _closeout("same written item"), created_at="2026-05-21T09:00:00+08:00"),
        ],
        audit_root=tmp_path,
    )

    path = pathlib.Path(report["path"])
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["candidate_id"] == "old"
    status = tm_mem0_audit.load_audit_status("2026-05-21", audit_root=tmp_path)
    assert status["ok"] is True
    assert status["status"] == "ok"
    assert status["candidate_count"] == 1


def test_audit_dedup_failure_writes_status_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tm_mem0_audit,
        "fetch_items_for_date",
        lambda _date: (_ for _ in ()).throw(RuntimeError("local profile: mem0_request blocked")),
    )

    report = tm_mem0_audit.audit_dedup("2026-05-21", audit_root=tmp_path)

    assert report["ok"] is False
    assert report["status"] == "warning"
    assert report["candidate_count"] == 0
    assert "local profile" in report["warnings"][0]
    candidates_path = tmp_path / "2026-05-21" / "dedup_candidates.json"
    status_path = tmp_path / "2026-05-21" / "status.json"
    assert json.loads(candidates_path.read_text(encoding="utf-8")) == []
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["status"] == "warning"
    assert "candidates" not in status


def test_audit_dedup_local_profile_reads_sqlite(tmp_path, monkeypatch):
    core = tm_mem0_audit.tm_memory_ops.tm_core
    monkeypatch.setattr(core, "tigermemory_profile", lambda: core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "local.sqlite"))
    monkeypatch.setattr(
        core,
        "mem0_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP must not be used")),
    )

    core.mem0_write(
        "cascade",
        "systems",
        _closeout("same local profile closeout persisted for audit"),
        metadata_extra={"source": "cascade", "topic": "systems"},
    )
    core.mem0_write(
        "cascade",
        "systems",
        _closeout("same local profile closeout persisted for audit"),
        metadata_extra={"source": "cascade", "topic": "systems"},
    )

    today = datetime.datetime.now(core.TZ_CN).strftime("%Y-%m-%d")
    report = tm_mem0_audit.audit_dedup(today, audit_root=tmp_path / "audit")

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["warnings"] == []
    assert report["candidate_count"] == 1
    assert report["candidates"][0]["canonical_id"]


def test_load_audit_status_missing_is_explicit_warning(tmp_path):
    status = tm_mem0_audit.load_audit_status("2026-05-21", audit_root=tmp_path)

    assert status["status"] == "missing"
    assert status["warnings"]
    assert "status.json missing" in status["warnings"][0]


def test_audit_window_covers_date_and_previous_day():
    start, end = tm_mem0_audit.audit_window("2026-05-21")

    assert start.isoformat().startswith("2026-05-20T00:00:00")
    assert end.isoformat().startswith("2026-05-22T00:00:00")
