"""Tests for the tm_embed_memories backfill CLI (direction-1, WSL-side)."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]
import tm_embed_memories  # type: ignore[import-not-found]


@pytest.fixture()
def local_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "embed.sqlite"))
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)


def _seed(n: int) -> None:
    for i in range(n):
        tm_core.mem0_write("codex", "systems", f"memory number {i}")


def test_status_reports_pending(local_db, capsys):
    _seed(3)
    rc = tm_embed_memories.main(["status"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pending_embedding"] == 3
    assert out["vectored_rows"] == 0


def test_backfill_happy_path_with_mock(local_db, monkeypatch, capsys):
    _seed(5)
    monkeypatch.setattr(tm_core, "embed_one", lambda t: [1.0, 2.0, 3.0])
    rc = tm_embed_memories.main(["backfill", "--batch", "2"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["embedded"] == 5
    assert summary["failed"] == 0
    assert tm_core.local_memory_stats()["vectored_rows"] == 5


def test_backfill_stops_when_backend_down(local_db, monkeypatch, capsys):
    _seed(3)

    def _down(_t):
        raise RuntimeError("embedding backend unreachable")

    monkeypatch.setattr(tm_core, "embed_one", _down)
    rc = tm_embed_memories.main(["backfill", "--batch", "5"])
    assert rc == 1  # failures reported
    summary = json.loads(capsys.readouterr().out)
    assert summary["embedded"] == 0
    assert summary["failed"] == 3
    # No progress -> exactly one round, then stop (no infinite spin).
    assert summary["rounds"] == 1
