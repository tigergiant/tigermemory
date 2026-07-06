"""Tests for R2-B read-only ops observability.

Covers tm_core.local_memory_stats / local_shadow_stats against a temp DB, and
the tm_http /ops/* route helpers. No network, no writes, no shell.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


@pytest.fixture()
def seeded_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    # Two active local writes (second is an exact dup -> superseded_dup).
    tm_core.mem0_write("codex", "systems", "第一条本地记忆内容")
    tm_core.mem0_write("codex", "systems", "第一条本地记忆内容")  # dup
    tm_core.mem0_write("codex", "operations", "另一条不同的记忆")
    # A shadow row (as dual-write would create) inserted directly.
    conn = sqlite3.connect(str(db_path))
    try:
        import time
        import uuid

        conn.execute(
            """
            INSERT INTO memories(
                id, content, topic, source_agent, route_decision, route_score,
                metadata_json, content_sha256, created_at, updated_at, state,
                backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
            ) VALUES (?, 'shadow body', 'systems', 'codex', 'mem0', 80, '{}', 'sha', ?, ?,
                      'active', 'local-shadow', 'fts5_only', ?, 'pending', ?)
            """,
            (str(uuid.uuid4()), int(time.time()), int(time.time()),
             str(uuid.uuid4()), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_local_memory_stats_shape(seeded_db):
    stats = tm_core.local_memory_stats()
    assert stats["schema_version"] == "4"
    assert stats["fts_tokenizer"] in ("trigram", "default")
    assert stats["total"] >= 4
    assert stats["by_state"]["active"] >= 2
    assert stats["by_state"]["superseded_dup"] == 1  # the exact dup
    assert "local" in stats["by_backend_origin"]
    assert stats["by_backend_origin"].get("local-shadow") == 1
    assert stats["vectored_rows"] == 0  # no embeddings yet
    assert stats["db_path"].endswith("memory.sqlite")


def test_local_shadow_stats_shape(seeded_db):
    shadow = tm_core.local_shadow_stats()
    assert shadow["local_shadow_rows"] == 1
    assert shadow["rows_with_legacy_mem0_id"] == 1
    assert shadow["by_shadow_state"] == {"pending": 1}


def test_outbox_counts_empty_by_default(seeded_db):
    counts = tm_core.outbox_counts()
    assert counts == {"pending": 0, "running": 0, "done": 0, "dead": 0}


def test_stats_never_write(seeded_db):
    before = tm_core.local_memory_stats()["total"]
    tm_core.local_memory_stats()
    tm_core.local_shadow_stats()
    tm_core.outbox_counts()
    after = tm_core.local_memory_stats()["total"]
    assert before == after  # pure reads, no row growth


def test_ops_routes_via_helpers(seeded_db):
    # Exercise the tm_http helper layer (import-guarded: needs fastapi).
    pytest.importorskip("fastapi")
    import tm_http  # type: ignore[import-not-found]

    db = tm_http._ops_db_stats()
    assert db["schema_version"] == "4"
    ob = tm_http._ops_outbox_status()
    assert ob["counts"]["pending"] == 0
    sh = tm_http._ops_shadow_status()
    assert sh["local_shadow_rows"] == 1


def test_ops_status_aggregate_via_testclient(seeded_db, monkeypatch):
    pytest.importorskip("fastapi")
    starlette_testclient = pytest.importorskip("starlette.testclient")
    import tm_http  # type: ignore[import-not-found]

    # Disable Bearer auth for the test without embedding the real token
    # (the module reads TM_MCP_API_KEY from the live .env at import time).
    monkeypatch.setattr(tm_http, "_API_KEY", None)
    client = starlette_testclient.TestClient(tm_http.app)
    resp = client.get("/ops/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "tm-ops-status-v1"
    assert body["db"]["by_state"]["superseded_dup"] == 1
    assert body["shadow"]["local_shadow_rows"] == 1
    assert set(body["outbox"]["counts"]) == {"pending", "running", "done", "dead"}
