from __future__ import annotations

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
        assert row["backend_origin"] == "openmemory"
        assert row["vector_status"] == "not_migrated"
        metadata = json.loads(row["metadata_json"])
        assert metadata["source"] == "codex"
        assert metadata["topic"] == "systems"
        assert metadata["route_score"] == 42
    finally:
        conn.close()


def test_compare_detects_lexical_and_semantic_regressions(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(
        source,
        [
            {"id": "m1", "content": "alpha beta", "metadata": {"source": "codex", "topic": "systems"}},
        ],
    )
    db = tmp_path / "mem.sqlite"
    tm_local_memory.main(["import", "--input", str(source), "--db", str(db)])

    rc = tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)])
    assert rc == 0

    _write_jsonl(
        source,
        [
            {"id": "m1", "content": "alpha beta changed", "metadata": {"source": "codex", "topic": "systems"}},
        ],
    )
    assert tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)]) == 1

    _write_jsonl(
        source,
        [
            {"id": "m1", "content": "alpha beta", "metadata": {"source": "codex", "topic": "systems", "vector_status": "migrated"}},
        ],
    )
    assert tm_local_memory.main(["compare", "--input", str(source), "--db", str(db)]) == 1


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
