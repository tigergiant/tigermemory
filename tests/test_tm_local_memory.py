from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import sys

from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_local_memory as tm_local_memory


def _write_jsonl(path: pathlib.Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_export_openmemory_writes_full_file_on_success(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MEM0_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    monkeypatch.setenv("MEM0_USER_ID", "tiger")

    call_pages: list[int] = []

    def fake_http_json(url: str, timeout: int = 15, bearer: str | None = None) -> dict[str, object]:
        call_pages.append(int(parse_qs(urlparse(url).query)["page"][0]))
        if call_pages[-1] == 1:
            return {
                "count": 2,
                "pages": 2,
                "items": [
                    {"id": "a", "content": "first", "metadata": {"source": "codex", "topic": "systems"}},
                    {"id": "b", "content": "second", "metadata": {"source": "codex", "topic": "systems"}},
                ],
            }
        return {"count": 2, "pages": 2, "items": []}

    monkeypatch.setattr(tm_local_memory, "_http_json", fake_http_json)
    out = tmp_path / "dump.jsonl"
    rc = tm_local_memory.main(["export-openmemory", "--out", str(out)])
    cap = capsys.readouterr()
    assert rc == 0
    assert out.exists()
    payload = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(payload) == 2
    assert call_pages == [1, 2]
    assert "count" in cap.out


def test_export_openmemory_does_not_leave_half_file_on_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEM0_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("MEM0_API_KEY", "test-key")
    monkeypatch.setenv("MEM0_USER_ID", "tiger")
    monkeypatch.setattr(
        tm_local_memory,
        "_http_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Mem0 unreachable")),
    )
    out = tmp_path / "dump.jsonl"
    rc = tm_local_memory.main(["export-openmemory", "--out", str(out)])
    assert rc == 2
    assert not out.exists()
    assert not out.with_suffix(".tmp").exists()


def test_import_dry_run_stats_only_and_actual_import_with_schema(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "m1",
                "content": "session-handoff sample",
                "metadata": {
                    "source": "codex",
                    "topic": "systems",
                    "route_score": 42,
                },
                "created_at": 1717500000,
            },
            {
                "id": "m2",
                "content": "vector fallback sample",
                "metadata": {"source": "codex", "topic": "systems", "route_score": 50},
                "created_at": 1717600000,
            },
        ],
    )
    db = tmp_path / "mem.sqlite"
    rc = tm_local_memory.main(["import", "--input", str(source), "--db", str(db), "--dry-run"])
    assert rc == 0
    assert not db.exists()

    rc = tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])
    assert rc == 0
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(1) AS c FROM memories").fetchone()["c"]
        assert count == 2
        row = conn.execute("SELECT source_agent, route_score, backend_origin, vector_status, metadata_json FROM memories WHERE id='m1'").fetchone()
        assert row["source_agent"] == "codex"
        assert row["route_score"] == 42
        assert row["backend_origin"] == "openmemory-import"
        assert row["vector_status"] == "not_migrated"
        metadata = json.loads(row["metadata_json"])
        assert metadata["source"] == "codex"
        assert metadata["topic"] == "systems"
        assert metadata["route_score"] == 42
    finally:
        conn.close()


def test_import_creates_migration_audit_and_outbox_tables(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "m1",
                "content": "schema support sample",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717500000,
            },
        ],
    )
    db = tmp_path / "mem.sqlite"

    assert tm_local_memory.main(["import", "--input", str(source), "--db", str(db)]) == 0

    conn = sqlite3.connect(db)
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
        assert conn.execute("SELECT COUNT(1) FROM outbox").fetchone()[0] == 0
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "3"
    finally:
        conn.close()


def test_import_records_migration_audit_rows(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "legacy-1",
                "content": "audit row sample",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717500000,
            },
        ],
    )
    db = tmp_path / "mem.sqlite"

    assert tm_local_memory.main(["import", "--input", str(source), "--db", str(db)]) == 0

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """
            SELECT legacy_mem0_id, new_id, content_sha256, disposition, verified
            FROM migration_audit
            WHERE legacy_mem0_id='legacy-1'
            """
        ).fetchone()
        assert row is not None
        assert row[0] == "legacy-1"
        assert row[1] == "legacy-1"
        assert row[2] == hashlib.sha256("audit row sample".encode("utf-8")).hexdigest()
        assert row[3] == "imported"
        assert row[4] == 1
    finally:
        conn.close()


def test_compare_detects_lexical_and_semantic_regressions(tmp_path, capsys) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {"id": "m1", "content": "alpha beta", "metadata": {"source": "codex", "topic": "systems"}},
        ],
    )
    db = tmp_path / "mem.sqlite"
    tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])
    capsys.readouterr()

    rc = tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["conservation"]["balanced"] is True
    assert payload["sha_diff"]["symmetric_diff_count"] == 0

    _write_jsonl(
        source,
        [
            {"id": "m1", "content": "alpha beta changed", "metadata": {"source": "codex", "topic": "systems"}},
        ],
    )
    assert tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["sha_diff"]["symmetric_diff_count"] == 2

    _write_jsonl(
        source,
        [
            {"id": "m1", "content": "alpha beta", "metadata": {"source": "codex", "topic": "systems", "vector_status": "migrated"}},
        ],
    )
    assert tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)]) == 1


def test_reconcile_writes_report_file(tmp_path, capsys) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "m1",
                "content": "reconcile report sample",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717500000,
            },
        ],
    )
    db = tmp_path / "mem.sqlite"
    report = tmp_path / "reconcile.json"
    assert tm_local_memory.main(["import", "--input", str(source), "--db", str(db)]) == 0
    capsys.readouterr()

    rc = tm_local_memory.main([
        "reconcile",
        "--input", str(source),
        "--db", str(db),
        "--out", str(report),
    ])
    stdout_payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert stdout_payload["ok"] is True
    assert report.exists()
    file_payload = json.loads(report.read_text(encoding="utf-8"))
    assert file_payload["conservation"]["balanced"] is True
    assert file_payload["sha_diff"]["symmetric_diff_count"] == 0


def test_compare_uses_legacy_mem0_id_mapping(tmp_path, capsys) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "legacy-1",
                "content": "legacy mapped content",
                "metadata": {"source": "codex", "topic": "systems"},
            },
        ],
    )
    db = tmp_path / "mem.sqlite"
    tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])
    capsys.readouterr()
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE memories SET id='local-1' WHERE id='legacy-1'")
        conn.commit()
    finally:
        conn.close()

    rc = tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["direct_readback"]["missing"] == 0
    assert payload["conservation"]["balanced"] is True
    assert payload["sha_diff"]["symmetric_diff_count"] == 0


def test_import_updates_existing_shadow_row_by_legacy_id(tmp_path, capsys) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "legacy-shadow-1",
                "content": "historical content replaces shadow",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717500000,
            },
        ],
    )
    db = tmp_path / "mem.sqlite"
    conn = tm_local_memory._conn(db)
    try:
        conn.execute(
            """
            INSERT INTO memories(
                id, content, topic, source_agent, route_decision, route_score,
                metadata_json, content_sha256, created_at, updated_at, state,
                backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "local-shadow-id",
                "temporary shadow content",
                "systems",
                "codex",
                "mem0",
                0,
                json.dumps({"source": "codex", "topic": "systems"}, ensure_ascii=False),
                hashlib.sha256("temporary shadow content".encode("utf-8")).hexdigest(),
                1717500000,
                1717500000,
                "active",
                "local-shadow",
                "fts5_only",
                "legacy-shadow-1",
                "pending",
                1717500000,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    rc = tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["updated"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM memories").fetchall()
        audit = conn.execute("SELECT * FROM migration_audit WHERE legacy_mem0_id='legacy-shadow-1'").fetchone()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["id"] == "local-shadow-id"
    assert rows[0]["content"] == "historical content replaces shadow"
    assert rows[0]["backend_origin"] == "openmemory-import"
    assert audit["new_id"] == "local-shadow-id"


def test_verify_reads_back_memory_and_term_search(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [{"id": "m1", "content": "verify search terms", "metadata": {"source": "codex", "topic": "systems"}}],
    )
    db = tmp_path / "mem.sqlite"
    tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])
    rc = tm_local_memory.main(["verify", "--db", str(db), "--id", "m1", "--terms", "search", "terms"])
    assert rc == 0
    rc_miss = tm_local_memory.main(["verify", "--db", str(db), "--id", "m1", "--terms", "missing_terms"])
    assert rc_miss == 2


def test_verify_uses_chinese_term_fallback(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [{"id": "m1", "content": "虎哥的偏好是先看已验证事实，再看推断。", "metadata": {"source": "codex", "topic": "systems"}}],
    )
    db = tmp_path / "mem.sqlite"
    tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])

    rc = tm_local_memory.main(["verify", "--db", str(db), "--id", "m1", "--terms", "虎哥偏好"])

    assert rc == 0


def test_backup_and_restore_cycle(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [{"id": "m1", "content": "to backup", "metadata": {"source": "codex", "topic": "systems"}}])
    db = tmp_path / "mem.sqlite"
    tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])
    backup_dir = tmp_path / "bk"
    rc = tm_local_memory.main(["backup", "--db", str(db), "--out", str(backup_dir)])
    assert rc == 0
    backup_files = sorted(backup_dir.glob("*.sqlite"))
    assert len(backup_files) == 1
    backup_file = backup_files[0]
    restored = tmp_path / "restored.sqlite"
    rc_restore = tm_local_memory.main(["restore", "--backup", str(backup_file), "--db", str(restored)])
    assert rc_restore == 0
    assert restored.exists()
    conn = sqlite3.connect(restored)
    try:
        row = conn.execute("SELECT id, content FROM memories LIMIT 1").fetchone()
        assert row[0] == "m1"
        assert row[1] == "to backup"
    finally:
        conn.close()


def test_local_memory_main_records_runtime_event(tmp_path, monkeypatch) -> None:
    event_root = tmp_path / "events"
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(event_root))
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [{"id": "m1", "content": "event sample", "metadata": {"source": "codex", "topic": "systems"}}])
    db = tmp_path / "mem.sqlite"

    rc = tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])

    assert rc == 0
    events = tm_local_memory.tm_runtime_events.load_events(
        dates=[tm_local_memory.tm_runtime_events._date_key()],
        event_root=event_root,
    )
    assert events[-1]["event_type"] == "local_memory_import"
    assert events[-1]["target_ref"]["db"] == str(db)
    assert events[-1]["target_ref"]["input"] == str(source)


# --- Phase 0 shadow search tests ---


def _today_cn_date_str() -> str:
    tz_cn = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.now(tz_cn).strftime("%Y-%m-%d")


def _seed_shadow_db(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a temp SQLite db with three memories for shadow comparison."""
    source = tmp_path / "shadow_source.jsonl"
    _write_jsonl(
        source,
        [
            {
                "id": "s1",
                "content": "shadow search alpha beta",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717500000,
            },
            {
                "id": "s2",
                "content": "shadow search gamma delta",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717600000,
            },
            {
                "id": "s3",
                "content": "unrelated memory item",
                "metadata": {"source": "codex", "topic": "systems"},
                "created_at": 1717700000,
            },
        ],
    )
    db = tmp_path / "shadow.sqlite"
    assert tm_local_memory.main(["import", "--input", str(source), "--db", str(db)]) == 0
    return db


def test_shadow_search_enabled_respects_env(monkeypatch) -> None:
    monkeypatch.delenv(tm_local_memory.SHADOW_SEARCH_ENV, raising=False)
    assert tm_local_memory.shadow_search_enabled() is False

    monkeypatch.setenv(tm_local_memory.SHADOW_SEARCH_ENV, "1")
    assert tm_local_memory.shadow_search_enabled() is True

    monkeypatch.setenv(tm_local_memory.SHADOW_SEARCH_ENV, "false")
    assert tm_local_memory.shadow_search_enabled() is False

    monkeypatch.setenv(tm_local_memory.SHADOW_SEARCH_ENV, "yes")
    assert tm_local_memory.shadow_search_enabled() is True


def test_run_shadow_search_writes_log_with_required_fields(tmp_path) -> None:
    db = _seed_shadow_db(tmp_path)
    log_dir = tmp_path / "search-shadow"

    def fake_openmemory_fetch(query: str, *, size: int = 5):
        # Pretend OpenMemory returned s1 and an id not in local db
        return (["s1", "zzz-not-in-local"], [])

    record = tm_local_memory.run_shadow_search(
        "shadow search alpha",
        db_path=db,
        size=5,
        openmemory_fetch=fake_openmemory_fetch,
        log_dir=log_dir,
    )

    # Required fields per acceptance criteria
    for field in (
        "query",
        "old_ids",
        "local_ids",
        "intersection_count",
        "old_latency_ms",
        "local_latency_ms",
        "warnings",
    ):
        assert field in record, f"missing field: {field}"

    assert record["query"] == "shadow search alpha"
    assert record["old_ids"] == ["s1", "zzz-not-in-local"]
    assert "s1" in record["local_ids"]
    assert record["intersection_count"] == 1
    assert record["old_latency_ms"] >= 0
    assert record["local_latency_ms"] >= 0
    assert isinstance(record["warnings"], list)

    # Log file written to <log_dir>/<YYYY-MM-DD>.jsonl
    expected_log = log_dir / f"{_today_cn_date_str()}.jsonl"
    assert expected_log.exists()
    lines = expected_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["query"] == "shadow search alpha"
    assert logged["intersection_count"] == 1
    assert logged["old_ids"] == ["s1", "zzz-not-in-local"]


def test_run_shadow_search_captures_openmemory_fetch_exception(tmp_path) -> None:
    db = _seed_shadow_db(tmp_path)
    log_dir = tmp_path / "search-shadow"

    def broken_fetch(query: str, *, size: int = 5):
        raise RuntimeError("openmemory down")

    record = tm_local_memory.run_shadow_search(
        "shadow search",
        db_path=db,
        openmemory_fetch=broken_fetch,
        log_dir=log_dir,
    )
    assert record["old_ids"] == []
    assert any("openmemory_fetch_exception" in w for w in record["warnings"])


def test_run_shadow_search_does_not_create_missing_db(tmp_path) -> None:
    missing_db = tmp_path / "missing.sqlite"
    log_dir = tmp_path / "search-shadow"

    record = tm_local_memory.run_shadow_search(
        "shadow search",
        db_path=missing_db,
        openmemory_fetch=lambda query, *, size=5: (["old-id"], []),
        log_dir=log_dir,
    )

    assert missing_db.exists() is False
    assert record["old_ids"] == ["old-id"]
    assert record["local_ids"] == []
    assert any("local_db_missing" in w for w in record["warnings"])


def test_maybe_log_shadow_search_noop_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(tm_local_memory.SHADOW_SEARCH_ENV, raising=False)
    log_dir = tmp_path / "search-shadow"
    db = _seed_shadow_db(tmp_path)

    tm_local_memory.maybe_log_shadow_search(
        "shadow search alpha",
        json.dumps({"items": [{"id": "s1"}]}),
        db_path=db,
        log_dir=log_dir,
    )
    # No log file should be created when disabled
    assert not log_dir.exists()


def test_maybe_log_shadow_search_logs_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(tm_local_memory.SHADOW_SEARCH_ENV, "1")
    log_dir = tmp_path / "search-shadow"
    db = _seed_shadow_db(tmp_path)

    old_body = json.dumps({"items": [{"id": "s1"}, {"id": "missing-from-local"}]})

    tm_local_memory.maybe_log_shadow_search(
        "shadow search alpha",
        old_body,
        db_path=db,
        log_dir=log_dir,
    )

    expected_log = log_dir / f"{_today_cn_date_str()}.jsonl"
    assert expected_log.exists()
    logged = json.loads(expected_log.read_text(encoding="utf-8").splitlines()[-1])
    assert logged["old_ids"] == ["s1", "missing-from-local"]
    assert "s1" in logged["local_ids"]
    assert logged["intersection_count"] == 1
    assert logged["old_latency_ms"] == 0.0  # caller already paid fetch cost
    assert logged["local_latency_ms"] >= 0


def test_maybe_log_shadow_search_does_not_create_missing_db(tmp_path, monkeypatch) -> None:
    """The hook must never create the production DB; it warns instead."""
    monkeypatch.setenv(tm_local_memory.SHADOW_SEARCH_ENV, "1")
    log_dir = tmp_path / "search-shadow"
    missing_db = tmp_path / "never-exists.sqlite"
    assert not missing_db.exists()

    tm_local_memory.maybe_log_shadow_search(
        "anything",
        json.dumps({"items": [{"id": "x"}]}),
        db_path=missing_db,
        log_dir=log_dir,
    )
    # DB file must not have been created
    assert not missing_db.exists()

    expected_log = log_dir / f"{_today_cn_date_str()}.jsonl"
    assert expected_log.exists()
    logged = json.loads(expected_log.read_text(encoding="utf-8").splitlines()[-1])
    assert logged["local_ids"] == []
    assert any("local_db_missing" in w for w in logged["warnings"])


def test_maybe_log_shadow_search_never_raises_on_bad_input(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(tm_local_memory.SHADOW_SEARCH_ENV, "1")
    log_dir = tmp_path / "search-shadow"
    db = _seed_shadow_db(tmp_path)

    # Garbage body + valid db: must not raise
    tm_local_memory.maybe_log_shadow_search(
        "query",
        "not valid json {{{",
        db_path=db,
        log_dir=log_dir,
    )
    expected_log = log_dir / f"{_today_cn_date_str()}.jsonl"
    assert expected_log.exists()
    logged = json.loads(expected_log.read_text(encoding="utf-8").splitlines()[-1])
    assert logged["old_ids"] == []


def test_shadow_search_cli_command(tmp_path, monkeypatch) -> None:
    db = _seed_shadow_db(tmp_path)
    log_dir = tmp_path / "search-shadow"

    # Stub the OpenMemory HTTP path so the CLI doesn't need a live server
    def fake_openmemory_search_ids(query, *, size=5, **kwargs):
        return (["s1", "s2"], [])

    monkeypatch.setattr(
        tm_local_memory,
        "_shadow_openmemory_search_ids",
        fake_openmemory_search_ids,
    )

    rc = tm_local_memory.main([
        "shadow-search",
        "--query", "shadow search",
        "--db", str(db),
        "--size", "5",
        "--log-dir", str(log_dir),
    ])
    assert rc == 0
    expected_log = log_dir / f"{_today_cn_date_str()}.jsonl"
    assert expected_log.exists()


def test_shadow_search_cli_missing_db_returns_2(tmp_path, capsys) -> None:
    missing = tmp_path / "nope.sqlite"
    rc = tm_local_memory.main([
        "shadow-search",
        "--query", "x",
        "--db", str(missing),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "db missing" in err
