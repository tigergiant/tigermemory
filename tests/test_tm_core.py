from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import sqlite3
import sys
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return b'{"ok": true}'


class _FakeOpener:
    def __init__(self):
        self.open_calls = []

    def open(self, request, *, timeout):
        self.open_calls.append((request, timeout))
        return _FakeResponse()


class _TimeoutOpener:
    def open(self, request, *, timeout):
        raise TimeoutError("timed out")


def _use_hybrid_profile(monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_HYBRID)


def _today_cn_date_str() -> str:
    return datetime.datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d")


def test_env_positive_int_uses_valid_override(monkeypatch):
    monkeypatch.setenv("TM_TEST_POSITIVE_INT", "17")
    assert tm_core._env_positive_int("TM_TEST_POSITIVE_INT", 5) == 17


def test_env_positive_int_falls_back_for_invalid_values(monkeypatch):
    monkeypatch.setenv("TM_TEST_POSITIVE_INT", "0")
    assert tm_core._env_positive_int("TM_TEST_POSITIVE_INT", 5) == 5
    monkeypatch.setenv("TM_TEST_POSITIVE_INT", "not-an-int")
    assert tm_core._env_positive_int("TM_TEST_POSITIVE_INT", 5) == 5


def test_embed_batch_once_wraps_raw_timeout_as_transient(monkeypatch):
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_args, **_kwargs: _TimeoutOpener())
    cfg = {
        "base": "http://127.0.0.1:19190/v1",
        "model": "test-embedding",
        "api_key": "test-key",
        "dim": None,
    }

    with pytest.raises(tm_core.EmbeddingError) as exc:
        tm_core._embed_batch_once(["hello"], cfg, 1)

    assert exc.value.kind == "transient"
    assert "timeout" in str(exc.value).lower()


def test_render_inbox_body_adds_summary_cn_from_agent_chinese_line():
    body = "这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。\nOriginal English details."

    rendered = tm_core.render_inbox_body("codex", "Routed memory 50", body, date="2026-05-21")

    assert "title_cn: 这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。" in rendered
    assert "preview_cn: 这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。" in rendered
    assert "review_cn_source: body_chinese_lines" in rendered
    assert "summary_cn: 这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。" in rendered
    assert "summary_cn_source: title_cn" in rendered


def test_render_inbox_body_marks_missing_chinese_summary():
    rendered = tm_core.render_inbox_body("codex", "Routed memory 50", "English only details.", date="2026-05-21")

    assert "title_cn: 未提供中文摘要" in rendered
    assert "preview_cn: 未提供中文摘要" in rendered
    assert "review_cn_source: missing" in rendered
    assert "summary_cn: 未提供中文摘要" in rendered
    assert "summary_cn_source: missing" in rendered


def test_render_inbox_body_skips_generic_routed_memory_headings():
    body = "\n".join([
        "# Routed memory 35",
        "",
        "# 标题",
        "中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入",
        "",
        "# 元数据",
        "- 文档时间：未标注（API配置教程）",
        "",
        "# 摘要",
        "该文档是中转 API 配置说明，主打 Claude Opus 4.5 保真满血版。",
    ])

    rendered = tm_core.render_inbox_body("codex", "Routed memory 35", body, date="2026-05-21")

    assert "title_cn: 中转API配置说明：Claude Opus 4.5 保真满血版" in rendered
    assert "title_cn: 标题" not in rendered
    assert "preview_cn: 该文档是中转 API 配置说明" in rendered


def test_render_inbox_body_overrides_bad_frontmatter_title():
    body = "# 标题\n中转API配置说明：Claude Opus 4.5 保真满血版\n\n# 摘要\n该文档是中转 API 配置说明。"

    rendered = tm_core.render_inbox_body(
        "codex",
        "Routed memory 35",
        body,
        date="2026-05-21",
        frontmatter_extra={"title_cn": "标题", "preview_cn": "标题", "summary_cn": "标题"},
    )

    assert "title_cn: 中转API配置说明：Claude Opus 4.5 保真满血版" in rendered
    assert "title_cn: 标题" not in rendered
    assert "preview_cn: 该文档是中转 API 配置说明。" in rendered


def test_mem0_request_bypasses_default_proxy_opener(monkeypatch):
    _use_hybrid_profile(monkeypatch)
    fake_opener = _FakeOpener()

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("mem0_request must not use default urlopen")

    monkeypatch.setattr(tm_core, "mem0_key", lambda: "test-key")
    monkeypatch.setattr(tm_core.urllib.request, "urlopen", fail_urlopen)
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_handlers: fake_opener)

    raw = tm_core.mem0_request("http://localhost:8765/api/v1/memories/?user_id=tiger")

    assert raw == '{"ok": true}'
    assert len(fake_opener.open_calls) == 1
    request, timeout = fake_opener.open_calls[0]
    assert request.get_header("Authorization") == "Bearer test-key"
    assert timeout == tm_core.MEM0_READ_TIMEOUT


def test_mem0_search_uses_openmemory_search_query_param(monkeypatch):
    _use_hybrid_profile(monkeypatch)
    captured = {}

    def fake_request(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return '{"items": []}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    raw = tm_core.mem0_search("Inbox routing", size=7)

    assert raw == '{"items": []}'
    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["search_query"] == ["Inbox routing"]
    assert "query" not in qs
    assert qs["size"] == ["7"]
    assert qs["match_mode"] == ["id_first"]
    assert captured["timeout"] == tm_core.MEM0_READ_TIMEOUT


def test_mem0_search_allows_explicit_substring_match_mode(monkeypatch):
    _use_hybrid_profile(monkeypatch)
    captured = {}

    def fake_request(url, *, timeout):
        captured["url"] = url
        return '{"items": []}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    tm_core.mem0_search("legacy contiguous text", size=3, match_mode="substring")

    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["match_mode"] == ["substring"]


def test_mem0_search_writes_shadow_log_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    local_id = json.loads(tm_core.mem0_write("codex", "systems", "shadow center alpha"))["id"]

    _use_hybrid_profile(monkeypatch)
    monkeypatch.setenv("TM_SHADOW_SEARCH_ENABLED", "1")
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda url, *, timeout: json.dumps({"items": [{"id": local_id}, {"id": "old-only"}]}),
    )

    raw = tm_core.mem0_search("shadow center alpha", size=5)

    assert json.loads(raw)["items"][0]["id"] == local_id
    log_path = tmp_path / ".tmp" / "search-shadow" / f"{_today_cn_date_str()}.jsonl"
    assert log_path.exists()
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["query"] == "shadow center alpha"
    assert record["old_ids"] == [local_id, "old-only"]
    assert local_id in record["local_ids"]
    assert record["intersection_count"] == 1


def test_mem0_search_shadow_log_missing_db_does_not_create_db(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    missing_db = tmp_path / "missing.sqlite"
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(missing_db))
    monkeypatch.setenv("TM_SHADOW_SEARCH_ENABLED", "1")
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda url, *, timeout: json.dumps({"items": [{"id": "old-id"}]}),
    )

    raw = tm_core.mem0_search("shadow missing db", size=5)

    assert json.loads(raw)["items"][0]["id"] == "old-id"
    assert missing_db.exists() is False
    log_path = tmp_path / ".tmp" / "search-shadow" / f"{_today_cn_date_str()}.jsonl"
    assert log_path.exists()
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["local_ids"] == []
    assert any("local_db_missing" in warning for warning in record["warnings"])


def test_verify_memory_id_active_hit_with_digest(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    created = int(datetime.datetime(2026, 5, 16, 3, 23, 5, tzinfo=tm_core.TZ_CN).timestamp())
    text = "2026-05-16 T-X3.5 000001.DAT 242 rows"
    digest = tmp_path / "inbox" / "daily" / "2026-05-16.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(f"memory_ids:\n  - {mem_id}\n", encoding="utf-8")

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: json.dumps({
        "id": mem_id,
        "text": text,
        "created_at": created,
        "state": "active",
        "metadata_": {"source": "codex", "topic": "systems"},
    }))

    def fake_search(query, size=5, match_mode="id_first"):
        assert match_mode == "id_first"
        return json.dumps({"items": [{"id": mem_id}]})

    monkeypatch.setattr(tm_core, "mem0_search", fake_search)

    result = tm_core.verify_memory_id(mem_id, key_terms="T-X3.5 000001.DAT 242 rows")

    assert result["status"] == "exists_active"
    assert result["direct_readback_ok"] is True
    assert result["search_by_id_self_hit"] is True
    assert result["search_by_terms_self_hit"] is True
    assert result["digest_date"] == "2026-05-16"
    assert result["digest_contains"] is True
    assert result["metadata"]["source"] == "codex"
    assert result["text_len"] == len(text)
    assert result["text_sha256_12"]


def test_verify_memory_id_explains_outside_digest_window(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    created = int(datetime.datetime(2026, 5, 16, 3, 23, 5, tzinfo=tm_core.TZ_CN).timestamp())

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: json.dumps({
        "id": mem_id,
        "text": "T-X3.5",
        "created_at": created,
        "state": "active",
    }))
    monkeypatch.setattr(tm_core, "mem0_search", lambda *_args, **_kwargs: json.dumps({"items": []}))

    result = tm_core.verify_memory_id(mem_id, digest_date="2026-05-15")

    assert result["status"] == "exists_active"
    assert result["digest_contains"] is False
    assert "outside digest window 2026-05-15" in result["digest_inclusion_reason"]


def test_verify_memory_id_distinguishes_not_found_and_unreachable(monkeypatch):
    _use_hybrid_profile(monkeypatch)
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 HTTP 404: nope")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "not_found"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 unreachable: refused")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: "{not-json")
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"


def test_local_verify_memory_id_resolves_legacy_mem0_id(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "memory.sqlite"))
    legacy_id = "22222222-2222-4222-8222-222222222222"
    raw = tm_core.mem0_write(
        "codex",
        "systems",
        "legacy id compatibility local sqlite readback",
        metadata_extra={"legacy_mem0_id": legacy_id, "shadow_state": "pending"},
    )
    local_id = json.loads(raw)["id"]

    result = tm_core.verify_memory_id(legacy_id, key_terms="legacy id compatibility")

    assert result["queried_id"] == legacy_id
    assert result["id"] == local_id
    assert result["resolved_id"] == local_id
    assert result["legacy_mem0_id"] == legacy_id
    assert result["direct_readback_ok"] is True
    assert result["search_by_id_self_hit"] is True
    assert result["search_by_terms_self_hit"] is True


def test_local_schema_v2_has_migration_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))

    tm_core.mem0_write("codex", "systems", "schema v2 migration field smoke")

    conn = sqlite3.connect(str(db_path))
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        assert {"content_sha256", "legacy_mem0_id", "shadow_state", "verified_at"} <= columns
        row = conn.execute(
            "SELECT content_sha256, verified_at FROM memories LIMIT 1"
        ).fetchone()
        assert isinstance(row[0], str) and len(row[0]) == 64
        assert isinstance(row[1], int)
    finally:
        conn.close()


def test_local_schema_migrates_old_table_before_index_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                topic TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                route_decision TEXT NOT NULL,
                route_score INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                backend_origin TEXT NOT NULL DEFAULT 'local',
                vector_status TEXT NOT NULL DEFAULT 'fts5_only'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO memories (
                id, content, topic, source_agent, route_decision,
                route_score, metadata_json, created_at, updated_at,
                state, backend_origin, vector_status
            ) VALUES (
                'old-1', 'old local schema migration smoke', 'systems',
                'codex', 'mem0', 90, '{}', 1717500000, 1717500000,
                'active', 'local', 'fts5_only'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    payload = json.loads(tm_core.mem0_search("old local schema", size=3))

    assert payload["count"] >= 1
    conn = sqlite3.connect(str(db_path))
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        assert {"content_sha256", "legacy_mem0_id", "shadow_state", "verified_at"} <= columns
        index_names = {
            row[1]
            for row in conn.execute("PRAGMA index_list(memories)").fetchall()
        }
        assert "idx_memories_content_sha_topic" in index_names
        assert "idx_memories_legacy_mem0_id" in index_names
    finally:
        conn.close()


def test_local_schema_has_migration_audit_and_outbox(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))

    tm_core.mem0_write("codex", "systems", "schema migration support smoke")

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"migration_audit", "outbox"} <= tables

        audit_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(migration_audit)").fetchall()
        }
        assert {
            "legacy_mem0_id",
            "new_id",
            "content_sha256",
            "disposition",
            "imported_at",
            "verified",
        } <= audit_columns

        outbox_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(outbox)").fetchall()
        }
        assert {
            "id",
            "kind",
            "memory_id",
            "payload_json",
            "status",
            "attempts",
            "next_attempt_at",
            "last_error",
            "created_at",
            "done_at",
        } <= outbox_columns

        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(outbox)").fetchall()
        }
        assert "idx_outbox_pending" in indexes

        schema_version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert schema_version == "4"

        outbox_count = conn.execute("SELECT COUNT(1) FROM outbox").fetchone()[0]
        assert outbox_count == 0
    finally:
        conn.close()


def test_local_schema_migrates_existing_db_without_dropping_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)
    db_path = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                topic TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                route_decision TEXT NOT NULL,
                route_score INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                content_sha256 TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                backend_origin TEXT NOT NULL DEFAULT 'local',
                vector_status TEXT NOT NULL DEFAULT 'fts5_only',
                legacy_mem0_id TEXT,
                shadow_state TEXT,
                verified_at INTEGER
            );
            INSERT INTO schema_meta (key, value, updated_at)
            VALUES ('schema_version', '2', '2026-07-04T00:00:00+08:00');
            INSERT INTO memories (
                id, content, topic, source_agent, route_decision, route_score,
                metadata_json, content_sha256, created_at, updated_at, state,
                backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
            )
            VALUES (
                'old-local-id', 'existing local memory row', 'systems', 'codex',
                'mem0', 80, '{}',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                1717500000, 1717500000, 'active', 'local', 'fts5_only',
                NULL, NULL, 1717500000
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    raw = tm_core.mem0_search("existing local memory row", size=5)
    payload = json.loads(raw)

    assert [item["id"] for item in payload["items"]] == ["old-local-id"]
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"migration_audit", "outbox"} <= tables
        assert conn.execute("SELECT COUNT(1) FROM memories").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(1) FROM outbox").fetchone()[0] == 0
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "4"
    finally:
        conn.close()


def test_mem0_write_dual_write_default_off_does_not_create_local_db(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    db_path = tmp_path / "local-shadow.sqlite"
    env_path = tmp_path / "openmemory.env"
    env_path.write_text("MEM0_URL=http://localhost:8765\nMEM0_API_KEY=test\n", encoding="utf-8")
    remote_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.delenv("TM_LOCAL_DUAL_WRITE", raising=False)
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(env_path))
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )

    raw = tm_core.mem0_write("codex", "systems", "default off dual write")

    assert json.loads(raw)["id"] == remote_id
    assert db_path.exists() is False


def test_mem0_write_dual_write_reads_runtime_env_file(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    db_path = tmp_path / "local-shadow.sqlite"
    env_path = tmp_path / "openmemory.env"
    env_path.write_text(
        "MEM0_URL=http://localhost:8765\nMEM0_API_KEY=test\nTM_LOCAL_DUAL_WRITE=1\n",
        encoding="utf-8",
    )
    remote_id = "44444444-4444-4444-8444-444444444444"
    monkeypatch.delenv("TM_LOCAL_DUAL_WRITE", raising=False)
    monkeypatch.setenv("TIGERMEMORY_OPENMEMORY_ENV", str(env_path))
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )

    raw = tm_core.mem0_write("codex", "systems", "runtime env file dual write")

    assert json.loads(raw)["id"] == remote_id
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute(
            "SELECT COUNT(1) FROM memories WHERE legacy_mem0_id=?",
            (remote_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_mem0_write_dual_write_persists_local_shadow(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    db_path = tmp_path / "local-shadow.sqlite"
    remote_id = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setenv("TM_LOCAL_DUAL_WRITE", "1")
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )

    raw = tm_core.mem0_write(
        "codex",
        "systems",
        "dual write local shadow",
        metadata_extra={"route_decision": "mem0", "route_score": 90},
    )

    assert json.loads(raw)["id"] == remote_id
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, content, backend_origin, legacy_mem0_id, shadow_state, route_score
            FROM memories
            WHERE legacy_mem0_id=?
            """,
            (remote_id,),
        ).fetchone()
        assert row is not None
        assert row["id"] != remote_id
        assert row["content"] == "dual write local shadow"
        assert row["backend_origin"] == "local-shadow"
        assert row["legacy_mem0_id"] == remote_id
        assert row["shadow_state"] == "pending"
        assert row["route_score"] == 90
        assert conn.execute("SELECT COUNT(1) FROM outbox").fetchone()[0] == 0
    finally:
        conn.close()


def test_mem0_write_dual_write_failure_does_not_block_remote(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    remote_id = "33333333-3333-4333-8333-333333333333"
    monkeypatch.setenv("TM_LOCAL_DUAL_WRITE", "1")
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path / "runtime-events"))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )
    monkeypatch.setattr(
        tm_core,
        "_local_write_memory_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("local db readonly")),
    )

    raw = tm_core.mem0_write("codex", "systems", "dual write failure remains remote ok")

    assert json.loads(raw)["id"] == remote_id
    event_files = list((tmp_path / "runtime-events").glob("*/events.jsonl"))
    assert len(event_files) == 1
    event = json.loads(event_files[0].read_text(encoding="utf-8").splitlines()[-1])
    assert event["event_type"] == "memory_local_dual_write"
    assert event["ok"] is False
    assert event["outcome"] == "shadow_write_failed"
    assert event["target_ref"]["legacy_mem0_id"] == remote_id
    assert "dual write failure remains remote ok" not in event_files[0].read_text(encoding="utf-8")


def test_mem0_delete_dual_write_marks_local_shadow_deleted(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    db_path = tmp_path / "local-shadow.sqlite"
    remote_id = "55555555-5555-4555-8555-555555555555"
    monkeypatch.setenv("TM_LOCAL_DUAL_WRITE", "1")
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )

    tm_core.mem0_write("codex", "systems", "delete mirror source")
    tm_core.mem0_delete([remote_id])

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT state, shadow_state FROM memories WHERE legacy_mem0_id=?",
            (remote_id,),
        ).fetchone()
        assert row is not None
        assert row["state"] == "deleted"
        assert row["shadow_state"] == "mem0_deleted"
    finally:
        conn.close()


def test_mem0_update_content_dual_write_updates_local_shadow(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    db_path = tmp_path / "local-shadow.sqlite"
    remote_id = "66666666-6666-4666-8666-666666666666"
    monkeypatch.setenv("TM_LOCAL_DUAL_WRITE", "1")
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )

    tm_core.mem0_write("codex", "systems", "update mirror source")
    tm_core.mem0_update_content(remote_id, "replacement mirrored content")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content, shadow_state, content_sha256 FROM memories WHERE legacy_mem0_id=?",
            (remote_id,),
        ).fetchone()
        assert row is not None
        assert row["content"] == "replacement mirrored content"
        assert row["shadow_state"] == "mem0_updated"
        assert row["content_sha256"] == hashlib.sha256(
            "replacement mirrored content".encode("utf-8")
        ).hexdigest()
    finally:
        conn.close()


def test_mem0_update_content_reactivates_deleted_local_shadow(monkeypatch, tmp_path):
    _use_hybrid_profile(monkeypatch)
    db_path = tmp_path / "local-shadow.sqlite"
    remote_id = "77777777-7777-4777-8777-777777777777"
    monkeypatch.setenv("TM_LOCAL_DUAL_WRITE", "1")
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(
        tm_core,
        "mem0_request",
        lambda *_args, **_kwargs: json.dumps({"id": remote_id}),
    )

    tm_core.mem0_write("codex", "systems", "update after delete source")
    tm_core.mem0_delete([remote_id])
    tm_core.mem0_update_content(remote_id, "reactivated content")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content, state, shadow_state FROM memories WHERE legacy_mem0_id=?",
            (remote_id,),
        ).fetchone()
        assert row is not None
        assert row["content"] == "reactivated content"
        assert row["state"] == "active"
        assert row["shadow_state"] == "mem0_updated"
    finally:
        conn.close()


def test_mem0_update_content_puts_content_only(monkeypatch):
    _use_hybrid_profile(monkeypatch)
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fake_request(url, data=None, *, timeout, method=None):
        captured.update({"url": url, "data": data, "timeout": timeout, "method": method})
        return '{"id": "fd65b298-05bd-493c-83ce-e37d84447362"}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    raw = tm_core.mem0_update_content(mem_id, "replacement content")

    assert raw.startswith('{"id"')
    assert captured["url"].endswith(f"/api/v1/memories/{mem_id}")
    assert captured["timeout"] == tm_core.MEM0_WRITE_TIMEOUT
    assert captured["method"] == "PUT"
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload == {"user_id": "tiger", "memory_content": "replacement content"}
    assert "metadata" not in payload


def test_mem0_update_content_rejects_invalid_uuid_and_empty_content():
    with pytest.raises(ValueError):
        tm_core.mem0_update_content("fd65", "replacement content")
    with pytest.raises(ValueError):
        tm_core.mem0_update_content("fd65b298-05bd-493c-83ce-e37d84447362", "   ")


def test_search_wiki_ranks_alias_match_above_repeated_body_terms(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "investment").mkdir(parents=True)
    (wiki / "systems").mkdir(parents=True)
    (wiki / "investment" / "portfolio-overview.md").write_text(
        """---
owner: codex
status: active
updated: 2026-05-16
aliases: ["portfolio holdings", "family investment"]
title: "投资组合总览"
---
# 投资组合总览

## 摘要

组合入口页。

## 来源

- local
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "investment-ai-hub-upgrade-plan.md").write_text(
        """---
owner: codex
status: active
updated: 2026-05-16
aliases: ["投资 AI 中枢升级计划"]
title: "投资 AI 中枢升级计划"
---
# 投资 AI 中枢升级计划

## 摘要

investment portfolio family holdings investment portfolio family holdings investment portfolio family holdings

## 来源

- local
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("portfolio holdings family investment", size=2, include_sources=False)

    assert results[0]["path"] == "wiki/investment/portfolio-overview.md"


def test_search_wiki_uses_block_aliases_tags_summary_and_key_facts(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "admin-answer.md").write_text(
        """---
owner: human
status: active
title: "Public Answer"
summary: "公开版自然语言问答使用证据优先回答。"
aliases:
  - "public answer mode"
tags:
  - "问答"
  - "LLM Admin"
key_facts:
  - "tm ask 在线模式必须返回带来源回答。"
---

# Public Answer

Body intentionally sparse.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    alias_results = tm_core.search_wiki("public answer mode", size=1, include_sources=False, explain=True)
    tag_results = tm_core.search_wiki("LLM Admin 问答", size=1, include_sources=False, explain=True)
    fact_results = tm_core.search_wiki("带来源回答", size=1, include_sources=False, explain=True)

    assert alias_results[0]["path"] == "wiki/systems/admin-answer.md"
    assert alias_results[0]["aliases"] == ["public answer mode"]
    assert tag_results[0]["tags"] == ["问答", "LLM Admin"]
    assert tag_results[0]["score_breakdown"]["tag_match"] is True
    assert fact_results[0]["key_facts"] == ["tm ask 在线模式必须返回带来源回答。"]
    assert fact_results[0]["score_breakdown"]["key_fact_match"] is True


def test_search_wiki_handles_unsegmented_chinese_question(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "public-core-contract.md").write_text(
        """---
owner: human
status: active
title: "TigerMemory Public Core Contract"
summary: "本文定义 TigerMemory 首次公开快照可以承诺的公共核心边界。"
aliases: ["public core contract", "公共核心契约"]
---

# TigerMemory Public Core Contract

## 摘要

公开产品定位改为 LLM-first，同时保留 local fallback。
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "product-vision.md").write_text(
        """---
owner: human
status: active
title: "TigerMemory Product Vision"
summary: "TigerMemory 公开版会复用私有版核心能力，并持续明确产品边界。"
aliases: ["tigermemory 产品愿景", "公开版核心能力"]
---

# TigerMemory Product Vision
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("TigerMemory公开版的核心边界是什么？", size=1, include_sources=False)

    assert results[0]["path"] == "wiki/systems/public-core-contract.md"


def test_search_wiki_ranks_exact_alias_phrase_above_related_spec(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "api-notes.md").write_text(
        """---
owner: codex
status: active
updated: 2026-06-09
aliases: ["OpenClaw CE API notes plugin"]
title: "OpenClaw CE API notes"
---
# OpenClaw CE API notes

API baseline.
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "plugin-spec.md").write_text(
        """---
owner: codex
status: active
updated: 2026-06-09
aliases: ["OpenClaw CE plugin spec"]
title: "OpenClaw CE plugin spec"
---
# OpenClaw CE plugin spec

OpenClaw CE API notes plugin OpenClaw CE plugin OpenClaw context engine plugin.
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("OpenClaw CE API notes plugin", size=2, include_sources=False)

    assert results[0]["path"] == "wiki/systems/api-notes.md"


def test_search_wiki_ranks_exact_short_title_above_repeated_body_terms(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "operations").mkdir(parents=True)
    (wiki / "systems").mkdir(parents=True)
    (wiki / "operations" / "project-canvas.md").write_text(
        """---
owner: codex
status: active
---

# Project Canvas

The project state map.
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "mermaid-task-canvas-protocol.md").write_text(
        """---
owner: codex
status: active
---

# Mermaid Task Canvas Protocol

Project canvas project canvas project canvas project canvas project canvas.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("Project Canvas", size=2, include_sources=False)

    assert results[0]["path"] == "wiki/operations/project-canvas.md"


def test_search_wiki_explain_includes_lexical_breakdown(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "example.md").write_text(
        """---
owner: codex
status: active
updated: 2026-05-20
aliases: ["route memory"]
title: "Route Memory"
---

# Route Memory

Body mentions routing.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("route memory", size=1, include_sources=False, explain=True)

    breakdown = results[0]["score_breakdown"]
    assert breakdown["lexical_score"] == results[0]["score"]
    assert breakdown["lexical_rank"] == 1
    assert breakdown["alias_match"] is True
    assert breakdown["vector_score"] is None
    assert breakdown["rrf_score"] is None


def test_answer_from_public_evidence_filters_invalid_citations(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_ADMIN_MODEL=answer-model\nDEEPSEEK_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    def fake_call(_system_prompt, user_msg, **kwargs):
        assert kwargs["model"] == "answer-model"
        assert "[W1]" in user_msg
        return True, {
            "answer": "公开版可以基于本地证据回答问题 [W1]。",
            "claims": [
                {"text": "公开版可以回答。", "citation_ids": ["W1", "BAD"]},
            ],
            "citations": [
                {"id": "W1", "reason": "测试证据"},
                {"id": "BAD", "reason": "无效证据"},
            ],
            "confidence": 88,
            "insufficient_evidence": False,
        }

    monkeypatch.setattr(tm_core, "_call_deepseek_json", fake_call)

    result = tm_core.answer_from_public_evidence(
        "公开版能回答吗",
        [{"source": "wiki", "path": "wiki/systems/public.md", "title": "Public", "snippet": "公开版可以回答。"}],
    )

    assert result["schema"] == "tigermemory-public-answer-v1"
    assert result["model"] == "answer-model"
    assert result["confidence"] == 88
    assert result["citations"] == [{"id": "W1", "source": "wiki", "path": "wiki/systems/public.md", "title": "Public", "reason": "测试证据"}]
    assert result["claims"] == [{"text": "公开版可以回答。", "citation_ids": ["W1"]}]


def test_answer_from_public_evidence_does_not_call_llm_without_evidence(monkeypatch):
    called = False

    def fake_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return True, {}

    monkeypatch.setattr(tm_core, "_call_deepseek_json", fake_call)

    result = tm_core.answer_from_public_evidence("unknown", [])

    assert result["insufficient_evidence"] is True
    assert result["model"] is None
    assert called is False


def test_search_wiki_hybrid_promotes_dominant_lexical_anchor(monkeypatch):
    import types

    lex_hits = [
        {"path": "wiki/systems/exact-a.md", "score": 200.0, "title": "exact a", "snippet": ""},
        {"path": "wiki/systems/exact-b.md", "score": 150.0, "title": "exact b", "snippet": ""},
        {"path": "wiki/systems/filler.md", "score": 20.0, "title": "filler", "snippet": ""},
        {"path": "wiki/systems/semantic-top.md", "score": 10.0, "title": "semantic", "snippet": ""},
    ]
    emb_hits = [
        {"path": "wiki/systems/semantic-top.md", "score": 0.9, "title": "semantic"},
        {"path": "wiki/systems/semantic-two.md", "score": 0.8, "title": "semantic two"},
        {"path": "wiki/systems/semantic-three.md", "score": 0.7, "title": "semantic three"},
    ]

    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: lex_hits)
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: emb_hits))

    results = tm_core.search_wiki_hybrid("exact semantic query", size=3, include_sources=False)

    assert [item["path"] for item in results[:2]] == [
        "wiki/systems/exact-a.md",
        "wiki/systems/exact-b.md",
    ]


def test_search_wiki_hybrid_explain_includes_branch_scores(monkeypatch):
    import types

    lex_hits = [
        {"path": "wiki/systems/exact-a.md", "score": 200.0, "title": "exact a", "snippet": ""},
        {"path": "wiki/systems/semantic-top.md", "score": 10.0, "title": "semantic", "snippet": ""},
    ]
    emb_hits = [
        {"path": "wiki/systems/semantic-top.md", "score": 0.9, "title": "semantic"},
        {"path": "wiki/systems/exact-a.md", "score": 0.5, "title": "exact a"},
    ]

    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: lex_hits)
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: emb_hits))

    results = tm_core.search_wiki_hybrid("exact semantic query", size=2, include_sources=False, explain=True)

    by_path = {item["path"]: item["score_breakdown"] for item in results}
    assert by_path["wiki/systems/exact-a.md"]["lexical_score"] == 200.0
    assert by_path["wiki/systems/exact-a.md"]["vector_score"] == 0.5
    assert by_path["wiki/systems/exact-a.md"]["rrf_score"] == results[0]["score"]
    assert by_path["wiki/systems/exact-a.md"]["degraded"] is False


def test_search_wiki_hybrid_map_arm_is_off_by_default(monkeypatch):
    import types

    monkeypatch.delenv("TM_HYBRID_MAP_ARM", raising=False)
    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: [])
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: []))
    monkeypatch.setitem(
        sys.modules,
        "tm_llm_wiki_map",
        types.SimpleNamespace(
            load_map=lambda: (_ for _ in ()).throw(AssertionError("map arm should be disabled")),
            map_recall=lambda *_args, **_kwargs: [],
        ),
    )

    assert tm_core.search_wiki_hybrid("natural language query", size=3, include_sources=False) == []


def test_search_wiki_hybrid_map_arm_fuses_map_only_hits(monkeypatch, tmp_path):
    import types

    target = tmp_path / "wiki" / "systems" / "map-target.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Map Target\nnatural language answer", encoding="utf-8")
    monkeypatch.setenv("TM_HYBRID_MAP_ARM", "1")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "_HYBRID_MAP_RECORDS_CACHE", None)
    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: [])
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: []))
    monkeypatch.setitem(
        sys.modules,
        "tm_llm_wiki_map",
        types.SimpleNamespace(
            load_map=lambda: [{"path": "wiki/systems/map-target.md"}],
            map_recall=lambda *_args, **_kwargs: [{
                "path": "wiki/systems/map-target.md",
                "title": "Map Target",
                "source_surface": "wiki",
                "score": 42.0,
            }],
        ),
    )

    results = tm_core.search_wiki_hybrid("natural language query", size=3, include_sources=False, explain=True)

    assert results[0]["path"] == "wiki/systems/map-target.md"
    assert "natural language answer" in results[0]["snippet"]
    breakdown = results[0]["score_breakdown"]
    assert breakdown["map_score"] == 42.0
    assert breakdown["map_rank"] == 1
    assert breakdown["vector_rank"] is None
    assert breakdown["degraded"] is False


def test_search_wiki_hybrid_does_not_promote_retrieval_eval_report(monkeypatch):
    import types

    lex_hits = [
        {"path": "wiki/systems/memory-retrieval-eval.md", "score": 600.0, "title": "report", "snippet": ""},
        {"path": "wiki/systems/exact-target.md", "score": 400.0, "title": "exact", "snippet": ""},
    ]
    emb_hits = [
        {"path": "wiki/systems/semantic-top.md", "score": 0.9, "title": "semantic"},
        {"path": "wiki/systems/exact-target.md", "score": 0.8, "title": "exact"},
    ]

    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: lex_hits)
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: emb_hits))

    results = tm_core.search_wiki_hybrid("exact semantic query", size=3, include_sources=False)

    assert results[0]["path"] == "wiki/systems/exact-target.md"
    assert "wiki/systems/memory-retrieval-eval.md" in [item["path"] for item in results]


def test_search_wiki_lexical_demotes_retrieval_eval_report():
    results = tm_core.search_wiki("OpenMemory CE search limits", size=3, include_sources=False)

    assert results[0]["path"] == "wiki/systems/openmemory-ce-limits.md"


def test_search_wiki_lexical_expands_cjk_domain_terms():
    assert tm_core.search_wiki("记忆库备份策略", size=1, include_sources=False)[0]["path"] == "wiki/operations/mem0-backup.md"
    assert tm_core.search_wiki("虎哥个人资料", size=1, include_sources=False)[0]["path"] == "wiki/person/tiger.md"
    assert tm_core.search_wiki("变基出现冲突怎么办", size=1, include_sources=True)[0]["path"] == "AGENTS.md"


def test_primary_scope_routes_chinese_commit_push_to_lessons():
    assert tm_core.primary_search_scope("提交后忘记推送") == "lessons"


# ---------------------------------------------------------------------------
# git_session_status — phantom detection (added 2026-05-16)
# Background: stat cache drift on cross-fs (WSL 9P, Windows mount, CRLF/LF)
# can make `git status --porcelain=v1` report ' M' entries whose actual content
# matches HEAD byte-for-byte. close_session must not block on these phantoms.
# See lessons/2026-05-16-close-session-stat-cache-phantom.md.
# ---------------------------------------------------------------------------

import types  # noqa: E402  -- kept local to phantom tests for clarity


def _make_fake_run(
    status_lines: list[str],
    real_dirty_paths: set[str],
    eol_only_paths: set[str] | None = None,
):
    """Build a fake `tm_core.run` for phantom tests.

    `status_lines` is what `git status --porcelain=v1` returns (one line per
    entry, including the XY prefix and space).

    `real_dirty_paths` is the set of paths that are TRULY dirty: both passes
    (`git diff --quiet HEAD --` and `git diff --quiet --ignore-cr-at-eol HEAD --`)
    return rc=1 for these.

    `eol_only_paths` is the set of paths whose only diff is CRLF↔LF: pass 2
    (byte-equality) returns rc=1, but pass 3 (--ignore-cr-at-eol) returns rc=0.

    Anything else (` M` / `M ` / `MM` entry not in either set) is a stat-cache
    phantom: pass 2 returns rc=0 immediately.
    """
    eol_only_paths = eol_only_paths or set()
    calls: list[list[str]] = []

    def _proc(rc: int, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    def fake_run(cmd: list[str], check: bool = True) -> types.SimpleNamespace:
        calls.append(cmd)
        head = cmd[:2]
        if head == ["git", "update-index"]:
            return _proc(0)
        if head == ["git", "status"]:
            return _proc(0, "\n".join(status_lines) + ("\n" if status_lines else ""))
        if head == ["git", "diff"] and "--quiet" in cmd:
            path = cmd[-1]
            if "--ignore-cr-at-eol" in cmd:
                # Pass 3: only TRULY dirty paths still differ here.
                return _proc(1 if path in real_dirty_paths else 0)
            # Pass 2: bytes differ for both real-dirty and eol-only.
            return _proc(1 if (path in real_dirty_paths or path in eol_only_paths) else 0)
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _proc(0)
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return _proc(0, "master\n")
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return _proc(0, "abc1234\n")
        if cmd[:2] == ["git", "rev-parse"]:
            return _proc(0, "origin/master\n")
        if head == ["git", "rev-list"]:
            return _proc(0, "0\t0\n")
        if head == ["git", "config"]:
            return _proc(0, ".githooks\n")
        return _proc(0)

    return fake_run, calls


def _install_hooks(tmp_path: pathlib.Path) -> None:
    githooks = tmp_path / ".githooks"
    githooks.mkdir()
    for hook in ("pre-commit", "commit-msg", "post-commit"):
        (githooks / hook).write_text("#!/bin/sh\n", encoding="utf-8")


def test_git_session_status_excludes_pure_phantom_dirty(monkeypatch, tmp_path):
    """ ' M' entries with no real content diff should be reclassified as phantom."""
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(
        status_lines=[" M .gitignore", " M deploy/openmemory/scripts/install-backup-task.ps1"],
        real_dirty_paths=set(),  # both are phantom (no real diff)
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 0
    assert result["paths"] == []
    assert result["phantom_count"] == 2
    assert sorted(result["phantom_paths"]) == [
        " M .gitignore",
        " M deploy/openmemory/scripts/install-backup-task.ps1",
    ]
    # No dirty-worktree blocker should remain when only phantoms exist.
    assert not any(b.startswith("dirty worktree:") for b in result["blockers"])
    assert result["ok"] is True


def test_git_session_status_keeps_real_dirty_when_mixed_with_phantom(monkeypatch, tmp_path):
    """Mixed phantom + real should yield dirty_count=1+untracked=1, phantom_count=1.

    Self-scope discipline (2026-05-24): dirty_count is reported but is NOT a
    default blocker. The session is OK to proceed even with real dirty paths;
    use strict_clean=True to restore the legacy behaviour.
    """
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=[" M phantom.md", " M real.md", "?? new.md"],
        real_dirty_paths={"real.md"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 2  # 1 real-modified + 1 untracked
    assert result["paths"] == [" M real.md", "?? new.md"]
    assert result["phantom_count"] == 1
    assert result["phantom_paths"] == [" M phantom.md"]
    assert result["unstaged_count"] == 1
    assert result["untracked_count"] == 1
    # New default: foreign dirty is informational only, not a blocker.
    assert not any(b.startswith("dirty worktree:") for b in result["blockers"]), result["blockers"]
    assert result["ok"] is True


def test_git_session_status_strict_clean_blocks_on_real_dirty(monkeypatch, tmp_path):
    """strict_clean=True restores the legacy 'dirty worktree' blocker for sweep
    tasks (archive moves, release verification) that genuinely need a clean tree."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=[" M phantom.md", " M real.md", "?? new.md"],
        real_dirty_paths={"real.md"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status(strict_clean=True)

    # Same accounting as the default-mode test, but blockers now include dirty.
    assert result["dirty_count"] == 2
    assert result["phantom_count"] == 1
    assert any(b == "dirty worktree: 2" for b in result["blockers"]), result["blockers"]
    assert result["ok"] is False


def test_git_session_status_strict_clean_does_not_block_on_pure_phantom(monkeypatch, tmp_path):
    """strict_clean should still respect phantom detection: a pure-phantom
    worktree is clean for legacy callers, not dirty."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=[" M .gitignore", " M deploy/script.ps1"],
        real_dirty_paths=set(),  # both phantom
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status(strict_clean=True)

    assert result["dirty_count"] == 0
    assert result["phantom_count"] == 2
    assert not any(b.startswith("dirty worktree:") for b in result["blockers"]), result["blockers"]
    assert result["ok"] is True


def test_git_session_status_real_only_baseline_unaffected(monkeypatch, tmp_path):
    """Pre-existing behaviour preserved when no entries are phantoms."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=["MM both.md", " M working.md", "M  staged.md"],
        real_dirty_paths={"both.md", "working.md", "staged.md"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 3
    assert result["phantom_count"] == 0
    assert result["phantom_paths"] == []
    # Sanity: staged + unstaged accounting unchanged.
    assert result["staged_count"] == 2  # MM and M_
    assert result["unstaged_count"] == 2  # MM and _M


def test_git_session_status_runs_update_index_refresh_first(monkeypatch, tmp_path):
    """The kernel must invoke `git update-index --refresh` before reading status,
    so git CLI itself can reset stat cache where possible (cheap fast path)."""
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(status_lines=[], real_dirty_paths=set())
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    tm_core.git_session_status()

    git_cmds = [c for c in calls if c[:1] == ["git"]]
    assert git_cmds[0][:3] == ["git", "update-index", "--refresh"]
    # And status comes after refresh.
    status_idx = next(i for i, c in enumerate(git_cmds) if c[:2] == ["git", "status"])
    refresh_idx = next(i for i, c in enumerate(git_cmds) if c[:2] == ["git", "update-index"])
    assert refresh_idx < status_idx


def test_git_session_status_excludes_eol_only_phantom(monkeypatch, tmp_path):
    """CRLF↔LF only diff (Windows editor saves CRLF; WSL git autocrlf=false sees
    diff vs LF index) should be reclassified as phantom via --ignore-cr-at-eol.

    Repro from 2026-05-16 V3.1C incident: D:\\tigermemory\\.gitignore and
    deploy/openmemory/scripts/install-backup-task.ps1 showed 65-line diff but
    every hunk was '-LF +CRLF' on identical content. close_session correctly
    flagged real codex audit_replay/* as dirty but should NOT have flagged the
    EOL-only entries.
    """
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(
        status_lines=[
            " M .gitignore",                                                # EOL-only phantom
            " M deploy/openmemory/scripts/install-backup-task.ps1",         # EOL-only phantom
            " M data/expense_import/reports/2026/audit_replay/alipay.jsonl",  # real dirty (codex)
        ],
        real_dirty_paths={"data/expense_import/reports/2026/audit_replay/alipay.jsonl"},
        eol_only_paths={".gitignore", "deploy/openmemory/scripts/install-backup-task.ps1"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 1, "only the codex real-dirty entry should remain"
    assert result["paths"] == [" M data/expense_import/reports/2026/audit_replay/alipay.jsonl"]
    assert result["phantom_count"] == 2
    assert sorted(result["phantom_paths"]) == [
        " M .gitignore",
        " M deploy/openmemory/scripts/install-backup-task.ps1",
    ]
    # Sanity: the kernel issued both passes for the EOL-only entries; for the
    # real-dirty entry it stops after pass 3 with rc=1.
    diff_quiet_calls = [c for c in calls if c[:2] == ["git", "diff"] and "--quiet" in c]
    pass2_calls = [c for c in diff_quiet_calls if "--ignore-cr-at-eol" not in c]
    pass3_calls = [c for c in diff_quiet_calls if "--ignore-cr-at-eol" in c]
    assert len(pass2_calls) == 3   # one per ' M' entry
    assert len(pass3_calls) == 3   # all three need pass 3 (pass 2 returned rc=1)


def test_git_session_status_does_not_phantom_check_untracked(monkeypatch, tmp_path):
    """ '??' entries are real (untracked, by definition); they must never be
    submitted to the phantom diff check (which would be both wrong and slow)."""
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(
        status_lines=["?? new1.md", "?? new2.md"],
        real_dirty_paths=set(),
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 2
    assert result["untracked_count"] == 2
    assert result["phantom_count"] == 0
    # Verify no `git diff --quiet` was issued for untracked paths.
    diff_quiet_calls = [c for c in calls if c[:2] == ["git", "diff"] and "--quiet" in c]
    assert diff_quiet_calls == []


def test_mem0_user_id_default_when_env_missing(monkeypatch, tmp_path):
    # Runtime config file omits MEM0_USER_ID; helper falls back to "tiger".
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("MEM0_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.mem0_user_id() == "tiger"


def test_mem0_user_id_reads_env_when_set(monkeypatch, tmp_path):
    # Runtime config file sets MEM0_USER_ID=alice; helper returns the override.
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("MEM0_USER_ID=alice\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.mem0_user_id() == "alice"


def test_deepseek_endpoint_default_when_env_missing(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_endpoint() == tm_core.DEFAULT_DEEPSEEK_ENDPOINT


def test_deepseek_endpoint_reads_env_when_set(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_BASE_URL=https://example.test/v1/chat/completions\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_endpoint() == "https://example.test/v1/chat/completions"


def test_deepseek_model_default_when_env_missing(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_model() == tm_core.DEFAULT_DEEPSEEK_MODEL


def test_deepseek_model_reads_env_when_set(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_MODEL=custom-chat\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_model() == "custom-chat"


def test_deepseek_model_prefers_shell_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("DEEPSEEK_MODEL", "shell-model")

    assert tm_core.deepseek_model() == "shell-model"


def test_deepseek_admin_model_defaults_to_pro(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_MODEL=custom-chat\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_admin_model() == tm_core.DEFAULT_DEEPSEEK_ADMIN_MODEL


def test_deepseek_admin_model_reads_env_when_set(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_ADMIN_MODEL=custom-admin\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_admin_model() == "custom-admin"


def test_deepseek_admin_model_prefers_shell_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("DEEPSEEK_ADMIN_MODEL", "shell-admin")

    assert tm_core.deepseek_admin_model() == "shell-admin"


_LINT_PAGE_BASE = (
    "---\n"
    "owner: cascade\n"
    "status: active\n"
    "updated: 2026-05-24\n"
    "title: t\n"
    "{extra}"
    "---\n"
    "\n## 摘要\n\nbody\n\n## 来源\n\n- none\n"
)


def test_lint_page_accepts_missing_public_field():
    text = _LINT_PAGE_BASE.format(extra="")
    assert tm_core.lint_page_errors(text) == []


def test_lint_page_accepts_utf8_bom_before_frontmatter():
    text = "\ufeff" + _LINT_PAGE_BASE.format(extra="")
    assert tm_core.lint_page_errors(text) == []


def test_lint_page_accepts_public_true_and_false():
    text_true = _LINT_PAGE_BASE.format(extra="public: true\n")
    text_false = _LINT_PAGE_BASE.format(extra="public: false\n")
    assert tm_core.lint_page_errors(text_true) == []
    assert tm_core.lint_page_errors(text_false) == []


def test_lint_page_rejects_non_bool_public_field():
    text_str = _LINT_PAGE_BASE.format(extra="public: yes\n")
    errors = tm_core.lint_page_errors(text_str)
    assert any("public" in e and "yes" in e for e in errors), errors
