#!/usr/bin/env python3
"""
tools/tm_expense.py — private expense tracker data layer.

Not part of Wiki / Mem0 / digest / embedding index.
The ledger file is git-ignored and never committed.
"""
from __future__ import annotations

import datetime
import pathlib
import sqlite3
from typing import Any

try:
    from zoneinfo import ZoneInfo
    _TZ_CN = ZoneInfo("Asia/Shanghai")
except Exception:
    _TZ_CN = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "expense_tracker"
DB_PATH = DATA_DIR / "ledger.sqlite"

VALID_KINDS = {"expense", "income"}
DEFAULT_CURRENCY = "CNY"


def _now_iso() -> str:
    return datetime.datetime.now(_TZ_CN).isoformat(timespec="seconds")


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expense_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
            amount REAL NOT NULL CHECK(amount > 0),
            currency TEXT NOT NULL DEFAULT 'CNY',
            occurred_at TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            merchant TEXT,
            note TEXT,
            payment_method TEXT,
            source_agent TEXT NOT NULL DEFAULT 'openclaw',
            source_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_expense_occurred
        ON expense_entries(occurred_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_expense_kind
        ON expense_entries(kind)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_expense_category
        ON expense_entries(category)
    """)


def expense_record(
    kind: str,
    amount: float,
    category: str,
    occurred_at: str | None = None,
    currency: str = "CNY",
    merchant: str | None = None,
    note: str | None = None,
    payment_method: str | None = None,
    source_agent: str = "openclaw",
    source_text: str | None = None,
) -> dict[str, Any]:
    """Record an expense or income entry. Returns the new row id."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError(f"amount must be > 0, got {amount!r}")
    if not category or not category.strip():
        raise ValueError("category is required (non-empty)")

    if occurred_at is None:
        occurred_at = _now_iso()
    else:
        try:
            dt = datetime.datetime.fromisoformat(occurred_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_TZ_CN)
            occurred_at = dt.isoformat(timespec="seconds")
        except Exception:
            raise ValueError(f"invalid occurred_at datetime: {occurred_at!r}")

    now = _now_iso()
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            """INSERT INTO expense_entries
               (kind, amount, currency, occurred_at, category, merchant, note,
                payment_method, source_agent, source_text, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kind, float(amount), currency or DEFAULT_CURRENCY, occurred_at,
                category.strip(), merchant, note, payment_method,
                source_agent, source_text, now, now,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        return {"ok": True, "id": row_id, "kind": kind, "amount": amount, "category": category}
    finally:
        conn.close()


def expense_query(
    start_date: str | None = None,
    end_date: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    group_by: str = "category",
    limit: int = 50,
) -> dict[str, Any]:
    """Query expense entries with optional filters and grouping."""
    if kind is not None and kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if group_by not in {"category", "month", "kind", "none"}:
        raise ValueError(f"group_by must be category/month/kind/none, got {group_by!r}")
    limit = min(max(int(limit), 1), 200)

    conn = _get_conn()
    try:
        _ensure_schema(conn)

        where: list[str] = []
        params: list[Any] = []

        if start_date:
            where.append("occurred_at >= ?")
            params.append(start_date)
        if end_date:
            where.append("occurred_at <= ?")
            params.append(end_date + "T23:59:59+08:00")
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if category:
            where.append("category = ?")
            params.append(category)

        where_clause = " AND ".join(where) if where else "1=1"

        if group_by == "none":
            rows = conn.execute(
                f"SELECT * FROM expense_entries WHERE {where_clause} ORDER BY occurred_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM expense_entries WHERE {where_clause}",
                params,
            ).fetchone()[0]
            return {
                "ok": True,
                "total_count": total,
                "rows": [dict(r) for r in rows],
            }

        if group_by == "category":
            rows = conn.execute(
                f"""SELECT category, kind, COUNT(*) as cnt, SUM(amount) as total
                    FROM expense_entries WHERE {where_clause}
                    GROUP BY category, kind ORDER BY total DESC LIMIT ?""",
                params + [limit],
            ).fetchall()
        elif group_by == "month":
            rows = conn.execute(
                f"""SELECT substr(occurred_at, 1, 7) as month, kind,
                           COUNT(*) as cnt, SUM(amount) as total
                    FROM expense_entries WHERE {where_clause}
                    GROUP BY month, kind ORDER BY month DESC LIMIT ?""",
                params + [limit],
            ).fetchall()
        elif group_by == "kind":
            rows = conn.execute(
                f"""SELECT kind, COUNT(*) as cnt, SUM(amount) as total
                    FROM expense_entries WHERE {where_clause}
                    GROUP BY kind""",
                params,
            ).fetchall()

        grand_total = conn.execute(
            f"""SELECT kind, SUM(amount) as total
                FROM expense_entries WHERE {where_clause}
                GROUP BY kind""",
            params,
        ).fetchall()

        return {
            "ok": True,
            "group_by": group_by,
            "groups": [dict(r) for r in rows],
            "grand_total": {r["kind"]: r["total"] for r in grand_total},
        }
    finally:
        conn.close()
