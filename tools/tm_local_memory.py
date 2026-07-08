#!/usr/bin/env python3
"""Utilities for OpenMemory (Mem0) -> local SQLite memory migration and sanity checks.

Commands:
  - export-openmemory --out <jsonl>
  - import --input <jsonl> --db <sqlite> [--dry-run]
  - compare --input <jsonl> --db <sqlite>
  - backup --db <sqlite> --out <path>
  - restore --backup <path> --db <sqlite> [--force]
  - verify --db <sqlite> --id <id> [--terms ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import _bootstrap_paths  # noqa: F401  -- ensures packages/*/src imports work when run as a script
from tigermemory_core import runtime_events as tm_runtime_events


REPO_ROOT = Path(__file__).resolve().parents[1]
MEM0_ENV_PATH = REPO_ROOT / "runtime" / "openmemory" / ".env"
DEFAULT_MEM0_URL = "http://localhost:8765"
DEFAULT_MEM0_USER = "tiger"
DEFAULT_EXPORT_PAGE_SIZE = 100
DEFAULT_TOPICS_SAMPLE = 5
SQLITE_SCHEMA_VERSION = 4
FTS_QUERY_TOKEN_RE = re.compile(r"\s+")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
LATIN_TERM_RE = re.compile(r"[a-z0-9][a-z0-9._:/\\-]*", re.IGNORECASE)
CJK_STOP_TERMS = {
    "是谁",
    "是什么",
    "什么",
    "怎么",
    "如何",
    "一下",
    "帮我",
    "请问",
    "查询",
    "搜索",
    "看看",
    "关于",
    "是否",
    "需要",
}

# Phase 0 shadow search (read-only bypass comparison; never changes online return values)
TZ_CN = dt.timezone(dt.timedelta(hours=8))
SHADOW_SEARCH_ENV = "TM_SHADOW_SEARCH_ENABLED"
SHADOW_SEARCH_DEFAULT_LOG_DIR = REPO_ROOT / ".tmp" / "search-shadow"
SHADOW_LOCAL_DB_ENV = "TIGERMEMORY_LOCAL_DB"
SHADOW_LOCAL_DB_DEFAULT = REPO_ROOT / "data" / "tigermemory" / "memory.sqlite"
SHADOW_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _read_runtime_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if MEM0_ENV_PATH.exists():
        for raw_line in MEM0_ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def _env_value(key: str, default: str | None = None) -> str:
    override = os.environ.get(key)
    if override is not None and override != "":
        return override
    runtime_env = _read_runtime_env()
    if key in runtime_env and runtime_env[key]:
        return runtime_env[key]
    if default is not None:
        return default
    raise RuntimeError(f"missing env key: {key} (or set {key} in {MEM0_ENV_PATH})")


def _record_local_memory_event(
    command: str,
    *,
    ok: bool,
    start: float,
    args: argparse.Namespace,
    error: str | None = None,
) -> None:
    try:
        target_ref = {
            "db": getattr(args, "db", None),
            "input": getattr(args, "input", None),
            "out": getattr(args, "out", None),
            "backup": getattr(args, "backup", None),
            "id": getattr(args, "id", None),
        }
        tm_runtime_events.record_event(
            event_type=f"local_memory_{command.replace('-', '_')}",
            service="tm-local-memory",
            component="migration",
            ok=ok,
            severity=None if ok else "error",
            duration_ms=(time.monotonic() - start) * 1000,
            route="local_sqlite",
            outcome=command,
            target_ref={k: v for k, v in target_ref.items() if v is not None},
            source_log="tm-local-memory",
            error=error,
            extra={
                "dry_run": getattr(args, "dry_run", None),
                "force": getattr(args, "force", None),
                "terms_count": len(getattr(args, "terms", []) or []),
            },
        )
    except Exception:
        pass


def _http_json(url: str, timeout: int = 15, bearer: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        raise RuntimeError(f"Mem0 unreachable: {reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if items is None:
        items = payload.get("results") if isinstance(payload, dict) else None
    if items is None:
        items = payload.get("memories") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("openmemory response missing list field items/results/memories")
    return items


def _as_int_timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("empty timestamp")
        if raw.isdigit():
            return int(raw)
        try:
            return int(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except Exception:
            pass
    raise ValueError(f"invalid timestamp: {value!r}")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_vector_status(value: Any) -> tuple[str, bool]:
    """Return normalized vector_status and whether caller explicitly supplied an upgrade.

    Explicitly-upgraded values are kept; otherwise fallback to not_migrated.
    """
    if value is None:
        return "not_migrated", False
    raw = str(value).strip().lower()
    if not raw or raw in {"none", "null", "not_migrated", "fts5_only", "fts", "no_vector"}:
        return "not_migrated", False
    explicit = raw not in {"migrating", "unknown", "unset", "pending"}
    return raw, explicit


def _has_vector_capability(status: str) -> bool:
    return str(status).strip().lower() not in {"", "not_migrated", "fts5_only"}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
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
        CREATE TABLE IF NOT EXISTS migration_audit (
            legacy_mem0_id TEXT PRIMARY KEY,
            new_id TEXT,
            content_sha256 TEXT NOT NULL,
            disposition TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            memory_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            done_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_outbox_pending
            ON outbox(status, next_attempt_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            id UNINDEXED,
            content
        );
        CREATE TRIGGER IF NOT EXISTS memories_fts_ai
        AFTER INSERT ON memories
        BEGIN
            INSERT INTO memories_fts(id, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS memories_fts_ad
        AFTER DELETE ON memories
        BEGIN
            DELETE FROM memories_fts WHERE id = old.id;
        END;
        CREATE TRIGGER IF NOT EXISTS memories_fts_au
        AFTER UPDATE ON memories
        BEGIN
            DELETE FROM memories_fts WHERE id = old.id;
            INSERT INTO memories_fts(id, content) VALUES (new.id, new.content);
        END;
        """
    )
    existing_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    for column_name, column_ddl in (
        ("content_sha256", "ALTER TABLE memories ADD COLUMN content_sha256 TEXT"),
        ("legacy_mem0_id", "ALTER TABLE memories ADD COLUMN legacy_mem0_id TEXT"),
        ("shadow_state", "ALTER TABLE memories ADD COLUMN shadow_state TEXT"),
        ("verified_at", "ALTER TABLE memories ADD COLUMN verified_at INTEGER"),
    ):
        if column_name not in existing_columns:
            conn.execute(column_ddl)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_content_sha_topic
        ON memories(content_sha256, topic)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_legacy_mem0_id
        ON memories(legacy_mem0_id) WHERE legacy_mem0_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_audit (
            legacy_mem0_id TEXT PRIMARY KEY,
            new_id TEXT,
            content_sha256 TEXT NOT NULL,
            disposition TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            memory_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            done_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbox_pending
        ON outbox(status, next_attempt_at)
        """
    )
    fts_tokenizer = _ensure_fts_trigram(conn)
    _demote_duplicate_active_rows(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_active_sha_topic
        ON memories(content_sha256, topic)
        WHERE state = 'active' AND content_sha256 IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_meta (key, value, updated_at)
        VALUES ('fts_tokenizer', ?, ?)
        """,
        (fts_tokenizer, dt.datetime.now(dt.UTC).isoformat()),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_meta (key, value, updated_at)
        VALUES ('schema_version', ?, ?)
        """,
        (str(SQLITE_SCHEMA_VERSION), dt.datetime.now(dt.UTC).isoformat()),
    )
    conn.commit()


def _ensure_fts_trigram(conn: sqlite3.Connection) -> str:
    """Schema v4: rebuild memories_fts with the trigram tokenizer.

    Mirrors tigermemory_core._ensure_local_fts_trigram; keep both in sync.
    Returns 'trigram' or 'default' (SQLite build without trigram support).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    existing_sql = str(row["sql"] or "") if row is not None else ""
    if "trigram" in existing_sql:
        return "trigram"
    try:
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                id UNINDEXED,
                content,
                tokenize='trigram'
            )
            """
        )
        conn.execute(
            "INSERT INTO memories_fts(id, content) SELECT id, content FROM memories"
        )
        return "trigram"
    except sqlite3.OperationalError:
        conn.execute("DROP TABLE IF EXISTS memories_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                id UNINDEXED,
                content
            )
            """
        )
        conn.execute(
            "INSERT INTO memories_fts(id, content) SELECT id, content FROM memories"
        )
        return "default"


def _demote_duplicate_active_rows(conn: sqlite3.Connection) -> int:
    """Schema v4 exact dedup: keep earliest active row per (content_sha256, topic).

    Later duplicates become state='superseded_dup'; rows are never deleted.
    Must run before creating the partial unique index. Mirrors
    tigermemory_core._demote_duplicate_active_memories.
    """
    cur = conn.execute(
        """
        UPDATE memories SET state='superseded_dup', updated_at=?
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY content_sha256, topic
                    ORDER BY created_at ASC, id ASC
                ) AS rn
                FROM memories
                WHERE state='active' AND content_sha256 IS NOT NULL
            ) WHERE rn > 1
        )
        """,
        (int(time.time()),),
    )
    return int(cur.rowcount or 0)


def _conn(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, raw in enumerate(f, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{path} line {line_no}: invalid JSONL") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"{path} line {line_no}: expected object payload")
            yield payload


def _metadata_from_item(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    if metadata is None:
        metadata = item.get("metadata_")
    if metadata is None:
        metadata = item.get("metadata_json")
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            if isinstance(parsed, dict):
                metadata = parsed
            else:
                metadata = {"_raw_metadata": metadata}
        except json.JSONDecodeError:
            metadata = {"_raw_metadata": metadata}
    if not isinstance(metadata, dict):
        metadata = {}
    return dict(metadata)


@dataclass
class NormalizedMemory:
    id: str
    content: str
    topic: str
    source_agent: str
    route_decision: str
    route_score: int
    metadata_json: str
    created_at: int
    updated_at: int
    backend_origin: str = "openmemory-import"
    vector_status: str = "not_migrated"
    vector_explicit: bool = False
    content_sha256: str = ""
    legacy_mem0_id: str | None = None
    shadow_state: str | None = None


def _normalize_item(item: dict[str, Any]) -> NormalizedMemory:
    memory_id = str(item.get("id", "")).strip()
    if not memory_id:
        raise RuntimeError("memory item missing id")
    content = str(item.get("content", item.get("text", ""))).strip()
    if content == "":
        raise RuntimeError(f"memory {memory_id} missing content/text")
    metadata = _metadata_from_item(item)
    source = str(metadata.get("source", "openmemory")).strip() or "openmemory"
    topic = str(metadata.get("topic", item.get("topic", "cross"))).strip() or item.get("topic", "cross") or "cross"
    route_decision = str(item.get("route_decision") or metadata.get("route_decision") or "mem0").strip() or "mem0"
    route_score_raw = item.get("route_score", metadata.get("route_score"))
    route_score = _to_int(route_score_raw, 0)
    created_at_raw = item.get("created_at")
    if created_at_raw is None:
        created_at_raw = item.get("created_at_ts", int(time.time()))
    created_at = _as_int_timestamp(created_at_raw) if not isinstance(created_at_raw, int) else int(created_at_raw)
    updated_at_raw = item.get("updated_at", created_at_raw)
    updated_at = _as_int_timestamp(updated_at_raw) if not isinstance(updated_at_raw, int) else int(updated_at_raw)
    vector_status = "not_migrated"
    vector_explicit = False
    for key in ("vector_status", "vectorCapability", "metadata_json.vector_status"):
        if key in item:
            vector_status, vector_explicit = _normalize_vector_status(item.get(key))
            break
    if not vector_explicit:
        vector_status, vector_explicit = _normalize_vector_status(metadata.get("vector_status"))
    metadata.setdefault("source", source)
    metadata.setdefault("topic", topic)
    legacy_mem0_id = str(item.get("legacy_mem0_id") or item.get("legacy_id") or memory_id).strip() or None
    if legacy_mem0_id:
        metadata.setdefault("legacy_mem0_id", legacy_mem0_id)
    if route_score is not None:
        metadata["route_score"] = route_score
    metadata.setdefault("route_decision", route_decision)
    metadata_json = json.dumps(metadata, ensure_ascii=False)
    return NormalizedMemory(
        id=memory_id,
        content=content,
        topic=topic,
        source_agent=source,
        route_decision=route_decision,
        route_score=route_score,
        metadata_json=metadata_json,
        created_at=created_at,
        updated_at=updated_at,
        vector_status=vector_status,
        vector_explicit=vector_explicit,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        legacy_mem0_id=legacy_mem0_id,
        shadow_state="pending",
    )


def _fetch_openmemory_records() -> list[dict[str, Any]]:
    base_url = _env_value("MEM0_URL", DEFAULT_MEM0_URL).rstrip("/")
    api_key = _env_value("MEM0_API_KEY")
    user_id = _env_value("MEM0_USER_ID", DEFAULT_MEM0_USER)

    page = 1
    size = DEFAULT_EXPORT_PAGE_SIZE
    items: list[dict[str, Any]] = []
    while True:
        qs = urllib.parse.urlencode({
            "user_id": user_id,
            "page": page,
            "size": size,
            "match_mode": "id_first",
        })
        payload = _http_json(f"{base_url}/api/v1/memories/?{qs}", bearer=api_key)
        batch = _extract_items(payload)
        if not isinstance(batch, list):
            raise RuntimeError("openmemory response items not list")
        for entry in batch:
            if isinstance(entry, dict):
                items.append(entry)
        pages = payload.get("pages")
        total = payload.get("count") or payload.get("total")
        if pages:
            if page >= int(pages):
                break
        elif total is not None and isinstance(total, int):
            if page * size >= int(total):
                break
        elif len(batch) < size:
            break
        page += 1
    if not items and page == 1:
        return []
    return items


def _backup_path(out: Path) -> Path:
    if out.suffix:
        return out
    timestamp = f"memory-{int(time.time())}.sqlite"
    if out.exists() and out.is_dir():
        return out / timestamp
    if out.exists() and out.is_file():
        return out.with_suffix(".sqlite")
    if out.name:
        return out / timestamp
    return out / timestamp


def _chunked(values: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _build_fts_query(query: str) -> str:
    terms = [t.strip().replace('"', '""') for t in FTS_QUERY_TOKEN_RE.split(query) if t.strip()]
    if not terms:
        return ""
    return " AND ".join(f'"{t}"' for t in terms)


def _cjk_query_terms(query: str, *, max_terms: int = 48) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        normalized = term.strip().lower()
        if not normalized or normalized in seen or normalized in CJK_STOP_TERMS:
            return
        if CJK_RUN_RE.fullmatch(normalized):
            if len(normalized) < 2:
                return
        elif len(normalized) < 2:
            return
        seen.add(normalized)
        terms.append(normalized)

    q = (query or "").strip()
    if not q:
        return []
    for item in LATIN_TERM_RE.findall(q):
        add(item)
    for run in CJK_RUN_RE.findall(q):
        cleaned = run
        for stop in sorted(CJK_STOP_TERMS, key=len, reverse=True):
            cleaned = cleaned.replace(stop, "")
        add(cleaned)
        if len(cleaned) >= 3:
            for width in (4, 3, 2):
                if len(cleaned) < width:
                    continue
                for idx in range(0, len(cleaned) - width + 1):
                    add(cleaned[idx : idx + width])
    return terms[:max_terms]


def _fallback_ids_by_terms(conn: sqlite3.Connection, query: str, *, limit: int = 50) -> list[str]:
    terms = _cjk_query_terms(query)
    if not terms:
        return []
    rows = conn.execute(
        """
        SELECT id, content, topic, source_agent, metadata_json, created_at
        FROM memories
        WHERE state = 'active'
        ORDER BY created_at DESC
        LIMIT 500
        """
    ).fetchall()
    q_lower = (query or "").strip().lower()
    query_has_cjk = bool(CJK_RUN_RE.search(query or ""))
    scored: list[tuple[int, int, str]] = []
    for row in rows:
        text = "\n".join(
            str(row[key] or "")
            for key in ("content", "topic", "source_agent", "metadata_json")
        ).lower()
        score = 0
        if q_lower and q_lower in text:
            score += 20
        matched_terms = 0
        for term in terms:
            if term in text:
                matched_terms += 1
                score += 4 if CJK_RUN_RE.search(term) and len(term) >= 3 else 2
        if not query_has_cjk and len(terms) >= 2 and matched_terms < 2 and q_lower not in text:
            continue
        if score > 0:
            scored.append((score, int(row["created_at"]), str(row["id"])))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [memory_id for _score, _created_at, memory_id in scored[:limit]]


def _read_memory_by_id(conn: sqlite3.Connection, memory_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, content, topic, source_agent, route_decision, route_score,
               metadata_json, content_sha256, created_at, updated_at, state,
               backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
        FROM memories
        WHERE id = ?
        """,
        (memory_id,),
    ).fetchone()
    return dict(row) if row else None


def _read_memory_by_legacy_id(conn: sqlite3.Connection, legacy_mem0_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, content, topic, source_agent, route_decision, route_score,
               metadata_json, content_sha256, created_at, updated_at, state,
               backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
        FROM memories
        WHERE legacy_mem0_id = ?
        """,
        (legacy_mem0_id,),
    ).fetchone()
    return dict(row) if row else None


def _read_memory_count_by_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    id_list = [i for i in ids if i]
    if not id_list:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for chunk in _chunked(id_list, 400):
        placeholders = ",".join(["?"] * len(chunk))
        sql = f"""
            SELECT id, content, topic, metadata_json, content_sha256, created_at,
                   state, backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
            FROM memories
            WHERE id IN ({placeholders}) OR legacy_mem0_id IN ({placeholders})
        """
        for row in conn.execute(sql, chunk + chunk).fetchall():
            out[row["id"]] = dict(row)
            if row["legacy_mem0_id"]:
                out[row["legacy_mem0_id"]] = dict(row)
    return out


def _record_migration_audit(
    conn: sqlite3.Connection,
    item: NormalizedMemory,
    *,
    new_id: str | None = None,
    disposition: str = "imported",
    verified: int = 1,
) -> None:
    legacy_id = item.legacy_mem0_id or item.id
    conn.execute(
        """
        INSERT INTO migration_audit (
            legacy_mem0_id, new_id, content_sha256, disposition, imported_at, verified
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(legacy_mem0_id) DO UPDATE SET
            new_id=excluded.new_id,
            content_sha256=excluded.content_sha256,
            disposition=excluded.disposition,
            imported_at=excluded.imported_at,
            verified=excluded.verified
        """,
        (
            legacy_id,
            new_id or item.id,
            item.content_sha256,
            disposition,
            dt.datetime.now(TZ_CN).isoformat(),
            verified,
        ),
    )


def cmd_export_openmemory(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    try:
        records = _fetch_openmemory_records()
        with tmp.open("w", encoding="utf-8") as f:
            for item in records:
                f.write(json.dumps(item, ensure_ascii=False))
                f.write("\n")
        tmp.replace(out)
        print(json.dumps({
            "ok": True,
            "out": str(out),
            "count": len(records),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        print(f"export-openmemory failed: {exc}", file=sys.stderr)
        return 2


def cmd_import(args: argparse.Namespace) -> int:
    dry_run = bool(args.dry_run)
    in_path = Path(args.input).resolve()
    records = list(_iter_jsonl(in_path))
    summary = {
        "input_count": len(records),
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    normalized = []
    seen = set[str]()
    for raw in records:
        normalized.append(_normalize_item(raw))
        if normalized[-1].id in seen:
            summary["skipped"] += 1
            normalized.pop()
        else:
            seen.add(normalized[-1].id)
    if dry_run:
        print(json.dumps(summary | {"vector_status_default": "not_migrated"}, ensure_ascii=False))
        return 0 if summary["errors"] == 0 else 2

    db_path = Path(args.db).resolve()
    try:
        conn = _conn(db_path)
    except sqlite3.Error as exc:
        print(f"import failed: cannot open db {db_path}: {exc}", file=sys.stderr)
        return 2
    try:
        for item in normalized:
            existing = _read_memory_by_id(conn, item.id)
            existing_by_legacy = (
                _read_memory_by_legacy_id(conn, item.legacy_mem0_id)
                if item.legacy_mem0_id
                else None
            )
            if existing is None and existing_by_legacy is None:
                summary["inserted"] += 1
                target_id = item.id
            elif existing_by_legacy is not None:
                summary["updated"] += 1
                target_id = str(existing_by_legacy["id"])
            else:
                summary["updated"] += 1
                target_id = item.id
            if existing_by_legacy is not None and target_id != item.id:

                def _update_row(state: str) -> None:
                    conn.execute(
                        """
                        UPDATE memories SET
                            content=?,
                            topic=?,
                            source_agent=?,
                            route_decision=?,
                            route_score=?,
                            metadata_json=?,
                            content_sha256=?,
                            created_at=?,
                            updated_at=?,
                            state=?,
                            backend_origin=?,
                            vector_status=?,
                            legacy_mem0_id=?,
                            shadow_state=?,
                            verified_at=?
                        WHERE id=?
                        """,
                        (
                            item.content,
                            item.topic,
                            item.source_agent,
                            item.route_decision,
                            item.route_score,
                            item.metadata_json,
                            item.content_sha256,
                            item.created_at,
                            item.updated_at,
                            state,
                            item.backend_origin,
                            item.vector_status,
                            item.legacy_mem0_id,
                            item.shadow_state,
                            int(time.time()),
                            target_id,
                        ),
                    )

                try:
                    _update_row("active")
                except sqlite3.IntegrityError:
                    # Another active row already holds this (content_sha256,
                    # topic); keep this row for audit as an exact duplicate.
                    _update_row("superseded_dup")
                _record_migration_audit(conn, item, new_id=target_id)
                continue

            def _insert_row(state: str) -> None:
                conn.execute(
                    """
                    INSERT INTO memories (
                        id, content, topic, source_agent, route_decision, route_score,
                        metadata_json, content_sha256, created_at, updated_at, state,
                        backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content=excluded.content,
                        topic=excluded.topic,
                        source_agent=excluded.source_agent,
                        route_decision=excluded.route_decision,
                        route_score=excluded.route_score,
                        metadata_json=excluded.metadata_json,
                        content_sha256=excluded.content_sha256,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at,
                        state=excluded.state,
                        backend_origin=excluded.backend_origin,
                        vector_status=excluded.vector_status,
                        legacy_mem0_id=excluded.legacy_mem0_id,
                        shadow_state=excluded.shadow_state,
                        verified_at=excluded.verified_at
                    """,
                    (
                        item.id,
                        item.content,
                        item.topic,
                        item.source_agent,
                        item.route_decision,
                        item.route_score,
                        item.metadata_json,
                        item.content_sha256,
                        item.created_at,
                        item.updated_at,
                        state,
                        item.backend_origin,
                        item.vector_status,
                        item.legacy_mem0_id,
                        item.shadow_state,
                        int(time.time()),
                    ),
                )

            try:
                _insert_row("active")
            except sqlite3.IntegrityError:
                # Exact duplicate of another active row (schema v4 unique
                # index): keep the row, mark it superseded_dup, never delete.
                _insert_row("superseded_dup")
            _record_migration_audit(conn, item, new_id=target_id)
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        summary["errors"] += 1
        print(f"import failed: db write error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    in_path = Path(args.input).resolve()
    db_path = Path(args.db).resolve()
    source_records = [_normalize_item(item) for item in _iter_jsonl(in_path)]
    if not source_records:
        print(json.dumps({"ok": False, "error": "input is empty"}, ensure_ascii=False))
        return 2

    source_count = len(source_records)
    source_topics: dict[str, int] = {}
    source_vector_expected = 0
    source_ids: list[str] = []
    source_rows: list[tuple[str, int, str, str]] = []
    source_vector_expectation: dict[str, bool] = {}
    source_sha_keys: set[str] = set()
    for item in source_records:
        source_topics[item.topic] = source_topics.get(item.topic, 0) + 1
        source_ids.append(item.id)
        source_sha_keys.add(f"{item.content_sha256}:{item.topic}")
        should_have_vector = item.vector_explicit and _has_vector_capability(item.vector_status)
        if should_have_vector:
            source_vector_expected += 1
        source_vector_expectation[item.id] = should_have_vector
        source_rows.append((item.id, item.created_at, item.content, item.vector_status))

    if not db_path.exists():
        print(json.dumps({"ok": False, "error": f"db not found: {db_path}"}, ensure_ascii=False))
        return 2
    try:
        conn = _conn(db_path)
    except sqlite3.Error as exc:
        print(f"compare failed: cannot open db {db_path}: {exc}", file=sys.stderr)
        return 2
    try:
        db_count_row = conn.execute("SELECT COUNT(1) AS c FROM memories").fetchone()
        db_count = int(db_count_row["c"]) if db_count_row else 0
        rows = conn.execute("SELECT topic, COUNT(1) AS c FROM memories GROUP BY topic").fetchall()
        db_topics = {row["topic"]: int(row["c"]) for row in rows}
        db_ids_map = _read_memory_count_by_ids(conn, source_ids)
    finally:
        conn.close()

    missing_ids = [memory_id for memory_id in source_ids if memory_id not in db_ids_map]
    matched_rows = [
        db_ids_map[memory_id]
        for memory_id in source_ids
        if memory_id in db_ids_map
    ]
    matched_db_topics: dict[str, int] = {}
    for row in matched_rows:
        topic = str(row.get("topic") or "")
        matched_db_topics[topic] = matched_db_topics.get(topic, 0) + 1
    active_imported_count = sum(1 for row in matched_rows if str(row.get("state") or "") == "active")
    superseded_dup_count = sum(1 for row in matched_rows if str(row.get("state") or "") == "superseded_dup")
    invalid_count = sum(1 for row in matched_rows if str(row.get("state") or "").startswith("invalid"))
    conservation_right_count = active_imported_count + superseded_dup_count + invalid_count
    db_sha_keys = {
        f"{row.get('content_sha256') or hashlib.sha256(str(row.get('content') or '').encode('utf-8')).hexdigest()}:{row.get('topic')}"
        for row in matched_rows
    }
    source_minus_db_sha = sorted(source_sha_keys - db_sha_keys)
    db_minus_source_sha = sorted(db_sha_keys - source_sha_keys)
    vector_mismatch_ids = []
    lexical_mismatch_ids = []
    recent_samples = sorted(source_rows, key=lambda item: item[1], reverse=True)[:DEFAULT_TOPICS_SAMPLE]
    sample_checks = []
    for memory_id, _, content, _ in recent_samples:
        db_row = db_ids_map.get(memory_id)
        exists = db_row is not None
        hash_match = False
        if exists:
            db_hash = hashlib.sha256(db_row["content"].encode("utf-8")).hexdigest()[:12]
            source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
            hash_match = db_hash == source_hash
            if not hash_match:
                lexical_mismatch_ids.append(memory_id)
            vector_status = str(db_row.get("vector_status", ""))
            if source_vector_expectation.get(memory_id, False) and not _has_vector_capability(vector_status):
                vector_mismatch_ids.append(memory_id)
        sample_checks.append({"id": memory_id, "direct_readback": exists, "text_hash_match": hash_match})

    if source_vector_expected:
        for source_id, db_row in db_ids_map.items():
            if not source_vector_expectation.get(source_id, False):
                continue
            if db_row is None or not _has_vector_capability(str(db_row.get("vector_status", ""))):
                if source_id not in vector_mismatch_ids:
                    vector_mismatch_ids.append(source_id)

    result = {
        "ok": True,
        "counts": {
            "source": source_count,
            "db": db_count,
            "db_matched_source_ids": len(matched_rows),
            "source_topics": source_topics,
            "db_topics": matched_db_topics,
            "db_all_topics": db_topics,
            "topic_match": source_topics == matched_db_topics,
        },
        "direct_readback": {
            "checked": source_count,
            "missing": len(missing_ids),
            "missing_ids": missing_ids[:10],
        },
        "conservation": {
            "left_source_count": source_count,
            "right_active_imported_count": active_imported_count,
            "right_superseded_dup_count": superseded_dup_count,
            "right_invalid_count": invalid_count,
            "right_total": conservation_right_count,
            "balanced": source_count == conservation_right_count,
            "missing_ids": missing_ids[:10],
        },
        "sha_diff": {
            "source_unique_sha_topic_count": len(source_sha_keys),
            "db_unique_sha_topic_count": len(db_sha_keys),
            "source_minus_db_count": len(source_minus_db_sha),
            "db_minus_source_count": len(db_minus_source_sha),
            "symmetric_diff_count": len(source_minus_db_sha) + len(db_minus_source_sha),
            "source_minus_db": source_minus_db_sha[:10],
            "db_minus_source": db_minus_source_sha[:10],
        },
        "lexical": {
            "sample_checked": len(sample_checks),
            "mismatch_ids": lexical_mismatch_ids,
            "status": "match" if not lexical_mismatch_ids else "mismatch",
        },
        "semantic": {
            "source_explicit_vector": source_vector_expected,
            "downgraded_ids": vector_mismatch_ids,
            "status": "match" if not vector_mismatch_ids else "mismatch",
        },
        "samples": sample_checks,
    }

    if source_topics != matched_db_topics:
        result["ok"] = False
        result["counts"]["status"] = "topic_mismatch"
    if missing_ids:
        result["ok"] = False
        result["counts"]["status"] = "readback_mismatch"
    if not result["conservation"]["balanced"]:
        result["ok"] = False
        result["counts"]["status"] = "conservation_mismatch"
    if result["sha_diff"]["symmetric_diff_count"]:
        result["ok"] = False
        result["counts"]["status"] = "sha_mismatch"
    if lexical_mismatch_ids:
        result["ok"] = False
        result["counts"]["status"] = "lexical_mismatch"
    if source_vector_expected and vector_mismatch_ids:
        result["ok"] = False
        result["semantic"]["status"] = "downgraded"
        result["counts"]["status"] = "semantic_downgrade"
    elif vector_mismatch_ids and source_vector_expected:
        result["ok"] = False
        result["counts"]["status"] = "semantic_downgrade"
    if getattr(args, "out", None):
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_backup(args: argparse.Namespace) -> int:
    db_path = Path(args.db).resolve()
    out = Path(args.out).resolve()
    if not db_path.exists():
        print(f"backup failed: db missing {db_path}", file=sys.stderr)
        return 2
    target = _backup_path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if target.exists() and not args.force:
            raise FileExistsError(f"{target} exists")
        source = sqlite3.connect(str(db_path))
        try:
            source.execute("PRAGMA wal_checkpoint(FULL)")
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
    except Exception as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "db": str(db_path), "backup": str(target)}, ensure_ascii=False))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    db_path = Path(args.db).resolve()
    backup_path = Path(args.backup).resolve()
    if not backup_path.exists():
        print(f"restore failed: backup missing {backup_path}", file=sys.stderr)
        return 2
    if db_path.exists() and not args.force:
        print(
            f"restore failed: db exists {db_path} (use --force to overwrite)",
            file=sys.stderr,
        )
        return 2
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(db_path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copy2(backup_path, db_path)
    except Exception as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "db": str(db_path), "backup": str(backup_path)}, ensure_ascii=False))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"verify failed: db missing {db_path}", file=sys.stderr)
        return 2
    try:
        conn = _conn(db_path)
    except sqlite3.Error as exc:
        print(f"verify failed: cannot open db {db_path}: {exc}", file=sys.stderr)
        return 2
    try:
        row = _read_memory_by_id(conn, args.id)
        if not row:
            row = _read_memory_by_legacy_id(conn, args.id)
        if not row:
            result = {
                "id": args.id,
                "queried_id": args.id,
                "resolved_id": None,
                "legacy_mem0_id": None,
                "exists": False,
                "direct_readback_ok": False,
                "search_by_id_self_hit": False,
                "search_by_terms_self_hit": None if not args.terms else False,
            }
            print(json.dumps(result, ensure_ascii=False))
            return 2
        resolved_id = str(row["id"])
        legacy_mem0_id = row.get("legacy_mem0_id") if isinstance(row, dict) else None
        search_by_id_ids = [resolved_id]
        result = {
            "id": resolved_id,
            "queried_id": args.id,
            "resolved_id": resolved_id,
            "legacy_mem0_id": legacy_mem0_id,
            "exists": True,
            "direct_readback_ok": True,
            "state": row["state"],
            "backend_origin": row["backend_origin"],
            "vector_status": row["vector_status"],
            "created_at": row["created_at"],
            "search_by_id_self_hit": resolved_id in search_by_id_ids,
            "search_by_id_ids": search_by_id_ids,
            "search_by_id_count": len(search_by_id_ids),
            "search_by_terms_self_hit": None,
            "search_by_terms_ids": [],
            "search_by_terms_count": 0,
        }
        if args.terms:
            joined_terms = " ".join(args.terms)
            fts_query = _build_fts_query(joined_terms)
            found: list[str] = []
            if fts_query:
                rows = conn.execute(
                    """
                    SELECT m.id FROM memories AS m
                    WHERE m.id IN (SELECT id FROM memories_fts WHERE memories_fts MATCH ?)
                    """,
                    (fts_query,),
                ).fetchall()
                found = [r["id"] for r in rows]
            if resolved_id not in found:
                for memory_id in _fallback_ids_by_terms(conn, joined_terms):
                    if memory_id not in found:
                        found.append(memory_id)
            result["search_by_terms_ids"] = found
            result["search_by_terms_count"] = len(found)
            result["search_by_terms_self_hit"] = resolved_id in found
        print(json.dumps(result, ensure_ascii=False))
        if args.terms and result["search_by_terms_self_hit"] is False:
            return 2
        return 0
    finally:
        conn.close()


def shadow_search_enabled() -> bool:
    """Return True only when TM_SHADOW_SEARCH_ENABLED is explicitly truthy.

    Phase 0 shadow comparison is disabled by default; the automatic hook
    (maybe_log_shadow_search) is a no-op unless this returns True.
    """
    raw = os.environ.get(SHADOW_SEARCH_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _shadow_log_path(log_dir: Path | None = None) -> Path:
    today = dt.datetime.now(TZ_CN).strftime("%Y-%m-%d")
    root = Path(log_dir) if log_dir is not None else SHADOW_SEARCH_DEFAULT_LOG_DIR
    return root / f"{today}.jsonl"


def _shadow_local_db_path() -> Path:
    override = os.environ.get(SHADOW_LOCAL_DB_ENV)
    if override:
        return Path(override)
    return SHADOW_LOCAL_DB_DEFAULT


def _extract_ids_from_openmemory_body(body: str | dict[str, Any] | None) -> list[str]:
    """Extract memory ids from an OpenMemory search response body (str or parsed dict)."""
    if body is None:
        return []
    if isinstance(body, str):
        if not body.strip():
            return []
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []
    else:
        data = body
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if items is None:
        items = data.get("results")
    if items is None:
        items = data.get("memories")
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


def _memory_item_text(item: dict[str, Any]) -> str:
    return str(item.get("content") or item.get("text") or item.get("memory") or "")


def _memory_item_topic(item: dict[str, Any]) -> str:
    meta = item.get("metadata") or item.get("metadata_") or {}
    if not isinstance(meta, dict):
        meta = {}
    return str(meta.get("topic") or item.get("topic") or "cross")


def _memory_item_sha(item: dict[str, Any]) -> str | None:
    text = _memory_item_text(item)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _readonly_conn(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def summarize_cutover_coverage(openmemory_items: list[dict[str, Any]], db_path: Path) -> dict[str, Any]:
    """Summarize read-cutover Gate A without mutating the local DB."""
    db = Path(db_path)
    if not db.exists():
        return {"status": "blocked", "reason": "local_db_missing", "db_path": str(db)}

    conn = _readonly_conn(db)
    try:
        local_active = int(
            conn.execute("SELECT COUNT(*) AS n FROM memories WHERE state='active'").fetchone()["n"]
        )
        local_total = int(conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"])
        state_counts = {
            str(row["state"]): int(row["n"])
            for row in conn.execute("SELECT state, COUNT(*) AS n FROM memories GROUP BY state").fetchall()
        }
        shadow_counts = {
            str(row["shadow_state"]): int(row["n"])
            for row in conn.execute(
                """
                SELECT shadow_state, COUNT(*) AS n
                FROM memories
                WHERE backend_origin='local-shadow'
                GROUP BY shadow_state
                """
            ).fetchall()
        }

        missing = 0
        non_active = 0
        non_active_with_equiv = 0
        non_active_without_equiv = 0
        for item in openmemory_items:
            memory_id = str(item.get("id") or "")
            if not memory_id:
                continue
            row = conn.execute(
                """
                SELECT id, state, topic, content_sha256
                FROM memories
                WHERE id=? OR legacy_mem0_id=?
                LIMIT 1
                """,
                (memory_id, memory_id),
            ).fetchone()
            if row is None:
                missing += 1
                continue
            if str(row["state"]) == "active":
                continue
            non_active += 1
            item_sha = _memory_item_sha(item)
            stored_sha = str(row["content_sha256"] or "")
            topic = str(row["topic"] or _memory_item_topic(item))
            equiv = None
            for candidate_sha in {sha for sha in (item_sha, stored_sha) if sha}:
                equiv = conn.execute(
                    """
                    SELECT 1
                    FROM memories
                    WHERE state='active' AND topic=? AND content_sha256=?
                    LIMIT 1
                    """,
                    (topic, candidate_sha),
                ).fetchone()
                if equiv:
                    break
            if equiv:
                non_active_with_equiv += 1
            else:
                non_active_without_equiv += 1
    finally:
        conn.close()

    openmemory_count = len([item for item in openmemory_items if isinstance(item, dict) and item.get("id")])
    strict_active_count_pass = local_active >= openmemory_count
    content_coverage_pass = missing == 0 and non_active_without_equiv == 0
    id_contract_pass = missing == 0 and non_active == 0
    return {
        "status": "pass" if strict_active_count_pass and content_coverage_pass and id_contract_pass else "blocked",
        "openmemory_count": openmemory_count,
        "local_active": local_active,
        "local_total": local_total,
        "active_minus_openmemory": local_active - openmemory_count,
        "strict_active_count_pass": strict_active_count_pass,
        "content_coverage_pass": content_coverage_pass,
        "id_contract_pass": id_contract_pass,
        "openmemory_ids_missing_local_any_state": missing,
        "openmemory_ids_non_active_local": non_active,
        "non_active_with_active_same_content_topic": non_active_with_equiv,
        "non_active_without_active_same_content_topic": non_active_without_equiv,
        "state_counts": state_counts,
        "shadow_counts": shadow_counts,
        "db_path": str(db),
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * q))
    idx = max(0, min(idx, len(ordered) - 1))
    return round(ordered[idx], 2)


def summarize_cutover_shadow_probe(
    rows: list[dict[str, Any]],
    *,
    min_queries: int = 30,
    max_local_p95_ms: float = 500.0,
) -> dict[str, Any]:
    local_empty = sum(
        1 for row in rows if int(row.get("old_count") or 0) > 0 and int(row.get("local_count") or 0) == 0
    )
    id_zero_overlap = sum(
        1
        for row in rows
        if int(row.get("old_count") or 0) > 0
        and int(row.get("local_count") or 0) > 0
        and int(row.get("id_intersection_count") or 0) == 0
    )
    content_zero_overlap = sum(
        1
        for row in rows
        if int(row.get("old_count") or 0) > 0
        and int(row.get("local_count") or 0) > 0
        and int(row.get("content_sha_intersection_count") or 0) == 0
    )
    warning_count = sum(1 for row in rows if row.get("warnings"))
    latencies = [
        float(row["local_latency_ms"])
        for row in rows
        if isinstance(row.get("local_latency_ms"), (int, float))
    ]
    p95 = _percentile(latencies, 0.95)
    reasons: list[str] = []
    if len(rows) < min_queries:
        reasons.append("not_enough_queries")
    if local_empty:
        reasons.append("local_empty_but_old_had")
    if id_zero_overlap:
        reasons.append("id_zero_overlap")
    if content_zero_overlap:
        reasons.append("content_zero_overlap")
    if warning_count:
        reasons.append("warnings_present")
    if p95 is not None and p95 > max_local_p95_ms:
        reasons.append("local_latency_p95_too_high")
    return {
        "status": "pass" if not reasons else "blocked",
        "reasons": reasons,
        "query_count": len(rows),
        "old_nonempty_count": sum(1 for row in rows if int(row.get("old_count") or 0) > 0),
        "local_empty_but_old_had": local_empty,
        "id_zero_overlap_when_both_nonempty": id_zero_overlap,
        "content_zero_overlap_when_both_nonempty": content_zero_overlap,
        "warning_count": warning_count,
        "local_latency_p95_ms": p95,
        "max_local_p95_ms": max_local_p95_ms,
    }


def _shadow_openmemory_search_items(
    query: str,
    *,
    size: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        url = _env_value("MEM0_URL", DEFAULT_MEM0_URL).rstrip("/")
        api_key = _env_value("MEM0_API_KEY", "")
        uid = _env_value("MEM0_USER_ID", DEFAULT_MEM0_USER)
        qs = urllib.parse.urlencode(
            {
                "user_id": uid,
                "search_query": query,
                "page": 1,
                "size": size,
                "match_mode": "id_first",
            }
        )
        payload = _http_json(f"{url}/api/v1/memories/?{qs}", bearer=api_key or None)
        return _extract_items(payload), warnings
    except Exception as exc:
        warnings.append(f"openmemory_search_error: {exc}")
        return [], warnings


def _local_hybrid_search_items(
    query: str,
    *,
    size: int = 5,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    import tigermemory_core as tm_core

    old_db_env = os.environ.get(SHADOW_LOCAL_DB_ENV)
    if db_path is not None:
        os.environ[SHADOW_LOCAL_DB_ENV] = str(db_path)
    try:
        payload = tm_core.local_search_hybrid(query, size=size)
    finally:
        if db_path is not None:
            if old_db_env is None:
                os.environ.pop(SHADOW_LOCAL_DB_ENV, None)
            else:
                os.environ[SHADOW_LOCAL_DB_ENV] = old_db_env
    rows = payload.get("results") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def run_cutover_shadow_probe(
    queries: list[str],
    *,
    db_path: Path,
    size: int = 5,
    openmemory_fetch=None,
    local_search=None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fetch_old = openmemory_fetch or _shadow_openmemory_search_items
    fetch_local = local_search or _local_hybrid_search_items
    for query in queries:
        q = str(query or "").strip()
        if not q:
            continue
        warnings: list[str] = []
        old_t0 = time.monotonic()
        try:
            old_items, old_warnings = fetch_old(q, size=size)
            if isinstance(old_warnings, list):
                warnings.extend(old_warnings)
        except Exception as exc:
            old_items = []
            warnings.append(f"openmemory_fetch_exception: {exc}")
        old_latency_ms = (time.monotonic() - old_t0) * 1000.0

        local_t0 = time.monotonic()
        try:
            local_items = fetch_local(q, size=size, db_path=Path(db_path))
        except Exception as exc:
            local_items = []
            warnings.append(f"local_hybrid_exception: {exc}")
        local_latency_ms = (time.monotonic() - local_t0) * 1000.0

        old_ids = [str(item.get("id")) for item in old_items if isinstance(item, dict) and item.get("id")]
        local_ids = [str(item.get("id")) for item in local_items if isinstance(item, dict) and item.get("id")]
        old_shas = {_memory_item_sha(item) for item in old_items if isinstance(item, dict)}
        local_shas = {_memory_item_sha(item) for item in local_items if isinstance(item, dict)}
        old_shas.discard(None)
        local_shas.discard(None)
        rows.append(
            {
                "timestamp": dt.datetime.now(TZ_CN).isoformat(),
                "query": q,
                "size": size,
                "old_count": len(old_ids),
                "local_count": len(local_ids),
                "id_intersection_count": len(set(old_ids) & set(local_ids)),
                "content_sha_intersection_count": len(old_shas & local_shas),
                "old_latency_ms": round(old_latency_ms, 2),
                "local_latency_ms": round(local_latency_ms, 2),
                "warnings": warnings,
            }
        )
    return rows


def _shadow_local_search_ids(conn: sqlite3.Connection, query: str, *, size: int = 5) -> list[str]:
    """Run a local SQLite lexical search and return ordered ids.

    Mirrors _local_search_local_memory in tigermemory_core: UUID direct lookup,
    then FTS5, then CJK/latin term fallback. Read-only.
    """
    limit = max(1, int(size))
    q = (query or "").strip()
    if not q:
        return []
    if SHADOW_UUID_RE.fullmatch(q):
        row = _read_memory_by_id(conn, q)
        if row is None:
            row = _read_memory_by_legacy_id(conn, q)
        return [str(row["id"])] if row else []
    fts_query = _build_fts_query(q)
    rows: list[sqlite3.Row] = []
    if fts_query:
        try:
            rows = conn.execute(
                """
                SELECT id FROM memories
                WHERE id IN (
                    SELECT id FROM memories_fts WHERE memories_fts MATCH ?
                )
                  AND state = 'active'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.Error:
            rows = []
    seen = {str(row["id"]) for row in rows}
    out = [str(row["id"]) for row in rows]
    if len(out) < limit:
        for memory_id in _fallback_ids_by_terms(conn, q, limit=limit):
            if memory_id in seen:
                continue
            out.append(memory_id)
            seen.add(memory_id)
            if len(out) >= limit:
                break
    return out


def _shadow_openmemory_search_ids(
    query: str,
    *,
    size: int = 5,
    base_url: str | None = None,
    bearer: str | None = None,
    user_id: str | None = None,
    timeout: int = 15,
) -> tuple[list[str], list[str]]:
    """Call OpenMemory search and return (ids, warnings). Does not raise."""
    warnings: list[str] = []
    try:
        url = (base_url or _env_value("MEM0_URL", DEFAULT_MEM0_URL)).rstrip("/")
        api_key = bearer or _env_value("MEM0_API_KEY", "")
        uid = user_id or _env_value("MEM0_USER_ID", DEFAULT_MEM0_USER)
        qs = urllib.parse.urlencode(
            {
                "user_id": uid,
                "search_query": query,
                "page": 1,
                "size": size,
                "match_mode": "id_first",
            }
        )
        payload = _http_json(
            f"{url}/api/v1/memories/?{qs}",
            timeout=timeout,
            bearer=api_key or None,
        )
        items = _extract_items(payload)
        ids = [
            str(item.get("id"))
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
        return ids, warnings
    except Exception as exc:
        warnings.append(f"openmemory_search_error: {exc}")
        return [], warnings


def run_shadow_search(
    query: str,
    *,
    db_path: Path,
    size: int = 5,
    openmemory_fetch=None,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Run one shadow comparison and append a JSONL log entry.

    This is the explicit entry point (CLI or direct call). It calls OpenMemory
    search (or the openmemory_fetch override) for old_ids, runs local SQLite
    search for local_ids, and writes a JSONL record to
    .tmp/search-shadow/YYYY-MM-DD.jsonl. Never raises; warnings capture errors.
    Does NOT change any online return value.
    """
    warnings: list[str] = []

    if openmemory_fetch is not None:
        old_t0 = time.monotonic()
        try:
            old_ids, om_warnings = openmemory_fetch(query, size=size)
            if not isinstance(old_ids, list):
                old_ids = []
            if isinstance(om_warnings, list):
                warnings.extend(om_warnings)
        except Exception as exc:
            old_ids = []
            warnings.append(f"openmemory_fetch_exception: {exc}")
        old_latency_ms = (time.monotonic() - old_t0) * 1000.0
    else:
        old_t0 = time.monotonic()
        old_ids, om_warnings = _shadow_openmemory_search_ids(query, size=size)
        old_latency_ms = (time.monotonic() - old_t0) * 1000.0
        warnings.extend(om_warnings)

    local_t0 = time.monotonic()
    local_ids: list[str] = []
    local_db = Path(db_path)
    try:
        if not local_db.exists():
            warnings.append(f"local_db_missing: {local_db}")
        else:
            conn = _conn(local_db)
            try:
                local_ids = _shadow_local_search_ids(conn, query, size=size)
            finally:
                conn.close()
    except sqlite3.Error as exc:
        warnings.append(f"local_db_error: {exc}")
    except Exception as exc:
        warnings.append(f"local_search_exception: {exc}")
    local_latency_ms = (time.monotonic() - local_t0) * 1000.0

    intersection = set(old_ids) & set(local_ids)
    record: dict[str, Any] = {
        "timestamp": dt.datetime.now(TZ_CN).isoformat(),
        "query": query,
        "size": size,
        "old_ids": old_ids,
        "local_ids": local_ids,
        "intersection_count": len(intersection),
        "old_count": len(old_ids),
        "local_count": len(local_ids),
        "old_latency_ms": round(old_latency_ms, 2),
        "local_latency_ms": round(local_latency_ms, 2),
        "warnings": warnings,
    }

    try:
        log_path = _shadow_log_path(log_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception as exc:
        record["warnings"] = record.get("warnings", []) + [f"log_write_error: {exc}"]

    return record


def maybe_log_shadow_search(
    query: str,
    old_response_body: str,
    *,
    db_path: Path | None = None,
    size: int = 5,
    log_dir: Path | None = None,
) -> None:
    """Best-effort shadow comparison hook for the main search path.

    - No-op when TM_SHADOW_SEARCH_ENABLED is not set (default off).
    - Accepts the already-fetched OpenMemory response body so we never double-call.
    - Never raises; swallows all exceptions internally.
    - Does NOT modify old_response_body or return anything.
    - Reads local DB (TIGERMEMORY_LOCAL_DB or data/tigermemory/memory.sqlite) for
      comparison; never creates the production DB.
    """
    if not shadow_search_enabled():
        return
    try:
        old_ids = _extract_ids_from_openmemory_body(old_response_body)
        resolved_db = Path(db_path) if db_path is not None else _shadow_local_db_path()
        warnings: list[str] = []
        local_ids: list[str] = []
        local_t0 = time.monotonic()
        if not resolved_db.exists():
            warnings.append(f"local_db_missing: {resolved_db}")
            local_latency_ms = 0.0
        else:
            try:
                conn = _conn(resolved_db)
                try:
                    local_ids = _shadow_local_search_ids(conn, query, size=size)
                finally:
                    conn.close()
            except Exception as exc:
                warnings.append(f"local_search_exception: {exc}")
            local_latency_ms = (time.monotonic() - local_t0) * 1000.0

        intersection = set(old_ids) & set(local_ids)
        record = {
            "timestamp": dt.datetime.now(TZ_CN).isoformat(),
            "query": query,
            "size": size,
            "old_ids": old_ids,
            "local_ids": local_ids,
            "intersection_count": len(intersection),
            "old_count": len(old_ids),
            "local_count": len(local_ids),
            "old_latency_ms": 0.0,  # caller already paid the fetch cost; not measured here
            "local_latency_ms": round(local_latency_ms, 2),
            "warnings": warnings,
        }
        log_path = _shadow_log_path(log_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception:
        pass  # shadow hook must never raise into the main search path


def cmd_shadow_search(args: argparse.Namespace) -> int:
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"shadow-search failed: db missing {db_path}", file=sys.stderr)
        return 2
    record = run_shadow_search(
        args.query,
        db_path=db_path,
        size=args.size,
        log_dir=Path(args.log_dir) if args.log_dir else None,
    )
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_cutover_gates(args: argparse.Namespace) -> int:
    db_path = Path(args.db).resolve()
    if args.openmemory_dump:
        openmemory_items = list(_iter_jsonl(Path(args.openmemory_dump).resolve()))
        openmemory_source = str(Path(args.openmemory_dump).resolve())
    else:
        openmemory_items = _fetch_openmemory_records()
        openmemory_source = "openmemory-list-api"

    coverage = summarize_cutover_coverage(openmemory_items, db_path)

    if args.queries_file:
        queries_path = Path(args.queries_file).resolve()
        queries = [
            line.strip()
            for line in queries_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        shadow_rows = run_cutover_shadow_probe(queries, db_path=db_path, size=args.size)
        shadow_source = str(queries_path)
        shadow_probe = summarize_cutover_shadow_probe(
            shadow_rows,
            min_queries=args.min_queries,
            max_local_p95_ms=args.max_local_p95_ms,
        )
    elif args.shadow_probe_log:
        shadow_rows = list(_iter_jsonl(Path(args.shadow_probe_log).resolve()))
        shadow_source = str(Path(args.shadow_probe_log).resolve())
        shadow_probe = summarize_cutover_shadow_probe(
            shadow_rows,
            min_queries=args.min_queries,
            max_local_p95_ms=args.max_local_p95_ms,
        )
    else:
        shadow_source = None
        shadow_probe = {
            "status": "pending",
            "reasons": ["missing_shadow_probe_log"],
            "query_count": 0,
        }

    status = "pass" if coverage.get("status") == "pass" and shadow_probe.get("status") == "pass" else "blocked"
    result = {
        "schema": "tm-read-cutover-gates-v1",
        "generated_at": dt.datetime.now(TZ_CN).isoformat(),
        "status": status,
        "coverage": coverage,
        "shadow_probe": shadow_probe,
        "inputs": {
            "db": str(db_path),
            "openmemory_source": openmemory_source,
            "shadow_probe_source": shadow_source,
        },
    }

    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if status == "pass" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="OpenMemory/local memory migration helpers")
    sub = ap.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("export-openmemory", help="Export all OpenMemory memories to JSONL")
    ex.add_argument("--out", required=True)

    im = sub.add_parser("import", help="Import JSONL memories into local sqlite")
    im.add_argument("--input", required=True)
    im.add_argument("--db", required=True)
    im.add_argument("--dry-run", action="store_true")

    co = sub.add_parser("compare", help="Compare JSONL dump against local sqlite")
    co.add_argument("--input", required=True)
    co.add_argument("--db", required=True)
    co.add_argument("--out", default=None)

    rec = sub.add_parser("reconcile", help="Write a compare/reconcile report for OpenMemory dump vs local sqlite")
    rec.add_argument("--input", required=True)
    rec.add_argument("--db", required=True)
    rec.add_argument("--out", default=None)

    ba = sub.add_parser("backup", help="Backup local sqlite")
    ba.add_argument("--db", required=True)
    ba.add_argument("--out", required=True)
    ba.add_argument("--force", action="store_true")

    re = sub.add_parser("restore", help="Restore sqlite from backup")
    re.add_argument("--backup", required=True)
    re.add_argument("--db", required=True)
    re.add_argument("--force", action="store_true")

    ve = sub.add_parser("verify", help="Verify local sqlite memory id and optional terms")
    ve.add_argument("--db", required=True)
    ve.add_argument("--id", required=True)
    ve.add_argument("--terms", nargs="*", default=[])

    ss = sub.add_parser(
        "shadow-search",
        help="Phase 0 read-only shadow comparison: OpenMemory vs local SQLite ids",
    )
    ss.add_argument("--query", required=True)
    ss.add_argument("--db", required=True)
    ss.add_argument("--size", type=int, default=5)
    ss.add_argument("--log-dir", default=None)

    cg = sub.add_parser("cutover-gates", help="Summarize read-cutover Gate A/B readiness")
    cg.add_argument("--db", required=True)
    cg.add_argument("--openmemory-dump", default=None, help="optional OpenMemory JSONL dump; default fetches list API")
    cg.add_argument("--queries-file", default=None, help="optional newline-separated real queries for live hybrid probe")
    cg.add_argument("--shadow-probe-log", default=None, help="JSONL rows from a cutover shadow probe")
    cg.add_argument("--size", type=int, default=5)
    cg.add_argument("--min-queries", type=int, default=30)
    cg.add_argument("--max-local-p95-ms", type=float, default=500.0)
    cg.add_argument("--out", default=None)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "export-openmemory": cmd_export_openmemory,
        "import": cmd_import,
        "compare": cmd_compare,
        "reconcile": cmd_compare,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "verify": cmd_verify,
        "shadow-search": cmd_shadow_search,
        "cutover-gates": cmd_cutover_gates,
    }
    start = time.monotonic()
    try:
        rc = dispatch[args.command](args)
    except Exception as exc:
        _record_local_memory_event(args.command, ok=False, start=start, args=args, error=str(exc))
        raise
    _record_local_memory_event(
        args.command,
        ok=rc == 0,
        start=start,
        args=args,
        error=None if rc == 0 else f"command exited {rc}",
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
