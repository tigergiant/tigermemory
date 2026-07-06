"""Schema v4 tests: trigram FTS rebuild + exact dedup unique index.

Covers both schema owners (tigermemory_core and tools/tm_local_memory.py) with
temp DBs only; production data/tigermemory/memory.sqlite is never touched.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import time
import uuid

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]
import tm_local_memory  # type: ignore[import-not-found]

TRIGRAM_SUPPORTED = sqlite3.sqlite_version_info >= (3, 34, 0)


@pytest.fixture()
def local_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    return db_path


def _fts_sql(db_path: pathlib.Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        ).fetchone()
        return str(row[0] or "") if row else ""
    finally:
        conn.close()


def _meta(db_path: pathlib.Path, key: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key=?", (key,)
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def _insert_v3_row(
    conn: sqlite3.Connection,
    *,
    content: str,
    topic: str = "systems",
    created_at: int,
    state: str = "active",
    memory_id: str | None = None,
) -> str:
    import hashlib

    mid = memory_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO memories(
            id, content, topic, source_agent, route_decision, route_score,
            metadata_json, content_sha256, created_at, updated_at, state,
            backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
        ) VALUES (?, ?, ?, 'codex', 'mem0', 80, '{}', ?, ?, ?, ?, 'local', 'fts5_only', NULL, NULL, ?)
        """,
        (
            mid,
            content,
            topic,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            created_at,
            created_at,
            state,
            created_at,
        ),
    )
    return mid


def _make_v3_db(db_path: pathlib.Path) -> None:
    """Create a legacy-shape DB: default-tokenizer FTS, no v4 index."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT NOT NULL, topic TEXT NOT NULL,
                source_agent TEXT NOT NULL, route_decision TEXT NOT NULL,
                route_score INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}', content_sha256 TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                backend_origin TEXT NOT NULL DEFAULT 'local',
                vector_status TEXT NOT NULL DEFAULT 'fts5_only',
                legacy_mem0_id TEXT, shadow_state TEXT, verified_at INTEGER
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(id UNINDEXED, content);
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.skipif(not TRIGRAM_SUPPORTED, reason="sqlite < 3.34 lacks trigram")
def test_new_db_gets_trigram_and_unique_index(local_db):
    tm_core.mem0_write("codex", "systems", "schema v4 trigram smoke content")
    assert "trigram" in _fts_sql(local_db)
    assert _meta(local_db, "fts_tokenizer") == "trigram"
    assert _meta(local_db, "schema_version") == "4"

    conn = sqlite3.connect(str(local_db))
    try:
        idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_memories_active_sha_topic'"
        ).fetchone()
        assert idx is not None
        assert "state = 'active'" in idx[0]
    finally:
        conn.close()


@pytest.mark.skipif(not TRIGRAM_SUPPORTED, reason="sqlite < 3.34 lacks trigram")
def test_legacy_db_fts_rebuilt_with_rows_preserved(local_db):
    _make_v3_db(local_db)
    conn = sqlite3.connect(str(local_db))
    try:
        _insert_v3_row(conn, content="历史遗留的量化投资研究记录", created_at=1000)
        conn.execute(
            "INSERT INTO memories_fts(id, content) SELECT id, content FROM memories"
        )
        conn.commit()
    finally:
        conn.close()
    assert "trigram" not in _fts_sql(local_db)

    # Any write triggers ensure-schema, which must rebuild FTS in place.
    tm_core.mem0_write("codex", "systems", "触发迁移的新记录内容")
    assert "trigram" in _fts_sql(local_db)

    conn = sqlite3.connect(str(local_db))
    try:
        fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert fts_count == mem_count == 2
    finally:
        conn.close()


def test_legacy_duplicate_actives_demoted_keeping_earliest(local_db):
    _make_v3_db(local_db)
    conn = sqlite3.connect(str(local_db))
    try:
        keeper = _insert_v3_row(conn, content="重复的收工总结", created_at=1000)
        dup1 = _insert_v3_row(conn, content="重复的收工总结", created_at=2000)
        dup2 = _insert_v3_row(conn, content="重复的收工总结", created_at=3000)
        other = _insert_v3_row(
            conn, content="重复的收工总结", topic="operations", created_at=1500
        )
        conn.commit()
    finally:
        conn.close()

    tm_core.mem0_write("codex", "systems", "触发迁移")

    conn = sqlite3.connect(str(local_db))
    conn.row_factory = sqlite3.Row
    try:
        states = {
            str(row["id"]): str(row["state"])
            for row in conn.execute("SELECT id, state FROM memories").fetchall()
        }
    finally:
        conn.close()
    assert states[keeper] == "active"
    assert states[dup1] == "superseded_dup"
    assert states[dup2] == "superseded_dup"
    # Same content in a different topic is not a duplicate.
    assert states[other] == "active"


def test_live_write_dedup_marks_superseded_dup(local_db):
    first = json.loads(tm_core.mem0_write("codex", "systems", "完全相同的记忆内容"))
    second = json.loads(tm_core.mem0_write("codex", "systems", "完全相同的记忆内容"))
    assert first["route_info"].get("dedup") is None
    assert second["route_info"]["dedup"] == "superseded_dup"
    assert second["route_info"]["dup_of"] == first["id"]

    # Different topic: not a duplicate.
    third = json.loads(tm_core.mem0_write("codex", "operations", "完全相同的记忆内容"))
    assert third["route_info"].get("dedup") is None

    # Search must surface only the active keeper.
    payload = json.loads(tm_core.mem0_search("完全相同的记忆内容", size=10))
    hit_ids = [item["id"] for item in payload["results"]]
    assert first["id"] in hit_ids
    assert second["id"] not in hit_ids


@pytest.mark.skipif(not TRIGRAM_SUPPORTED, reason="sqlite < 3.34 lacks trigram")
def test_trigram_matches_mid_string_cjk(local_db):
    written = json.loads(
        tm_core.mem0_write("codex", "systems", "家庭量化投资研究系统已经完成部署")
    )
    payload = json.loads(tm_core.mem0_search("投资研究", size=5))
    assert written["id"] in [item["id"] for item in payload["results"]]


def test_short_cjk_query_still_hits_via_fallback(local_db):
    written = json.loads(tm_core.mem0_write("codex", "systems", "虎哥偏好简洁中文回复"))
    payload = json.loads(tm_core.mem0_search("虎哥", size=5))
    assert written["id"] in [item["id"] for item in payload["results"]]


def test_deleted_rows_hidden_from_fts_search(local_db):
    written = json.loads(
        tm_core.mem0_write("codex", "systems", "这条记忆稍后会被标记删除状态")
    )
    conn = sqlite3.connect(str(local_db))
    try:
        conn.execute(
            "UPDATE memories SET state='deleted' WHERE id=?", (written["id"],)
        )
        conn.commit()
    finally:
        conn.close()
    payload = json.loads(tm_core.mem0_search("标记删除状态", size=5))
    assert written["id"] not in [item["id"] for item in payload["results"]]


def test_import_duplicate_content_becomes_superseded_dup(tmp_path):
    import subprocess

    db_path = tmp_path / "import.sqlite"
    jsonl = tmp_path / "export.jsonl"
    base = {
        "content": "openmemory 导出的重复记忆",
        "metadata": {"topic": "systems", "source": "codex"},
        "created_at": 1700000000,
        "updated_at": 1700000000,
    }
    rows = [
        base | {"id": str(uuid.uuid4())},
        base | {"id": str(uuid.uuid4())},
    ]
    jsonl.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "tm_local_memory.py"),
            "import",
            "--db",
            str(db_path),
            "--input",
            str(jsonl),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    conn = sqlite3.connect(str(db_path))
    try:
        counts = dict(
            conn.execute(
                "SELECT state, COUNT(*) FROM memories GROUP BY state"
            ).fetchall()
        )
    finally:
        conn.close()
    assert counts.get("active") == 1
    assert counts.get("superseded_dup") == 1
