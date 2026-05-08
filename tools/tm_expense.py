#!/usr/bin/env python3
"""
tools/tm_expense.py — private expense tracker data layer (v2).

Not part of Wiki / Mem0 / digest / embedding index.
The ledger file is git-ignored and never committed.

P0 v2 adds:
- expense_write / expense_read with action/mode dispatch
- Soft delete, categories table, payment-method whitelist
- SQL readonly mode with validation
- Backward-compatible expense_record / expense_query aliases
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
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
VALID_CURRENCIES = {"CNY", "USD", "HKD", "JPY", "EUR"}

_PAYMENT_ALIASES = {
    "cash": {"cash", "现金"},
    "wechat": {"wechat", "微信", "wx", "weixin"},
    "alipay": {"alipay", "支付宝", "zhifubao"},
    "credit_card": {"credit_card", "信用卡", "刷卡", "贷记卡"},
    "debit_card": {"debit_card", "借记卡", "储蓄卡", "银行卡"},
    "bank_transfer": {"bank_transfer", "转账", "汇款", "网银"},
    "other": {"other", "其他"},
}

# Build reverse lookup: alias -> canonical
_PAYMENT_CANONICAL: dict[str, str] = {}
for canonical, aliases in _PAYMENT_ALIASES.items():
    for alias in aliases:
        _PAYMENT_CANONICAL[alias.lower()] = canonical

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(_TZ_CN).isoformat(timespec="seconds")


def _normalize_occurred_at(occurred_at: str | None) -> str:
    if occurred_at is None:
        return _now_iso()
    try:
        dt = datetime.datetime.fromisoformat(occurred_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ_CN)
        return dt.isoformat(timespec="seconds")
    except Exception:
        raise ValueError(f"invalid occurred_at datetime: {occurred_at!r}")


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema bootstrap (v1 + v2 columns if migration hasn't run)."""
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
    # v2 columns (ignored if migration already ran)
    for col, dtype in (
        ("category_id", "INTEGER"),
        ("merchant_id", "INTEGER"),
        ("tags", "TEXT"),
        ("deleted_at", "TEXT"),
        ("amount_cents", "INTEGER"),
    ):
        try:
            conn.execute(f"ALTER TABLE expense_entries ADD COLUMN {col} {dtype}")
        except sqlite3.OperationalError:
            pass
    # v2 tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('expense','income','both')),
            parent_id INTEGER REFERENCES categories(id),
            aliases TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name, kind)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            aliases TEXT,
            default_category_id INTEGER REFERENCES categories(id),
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL CHECK(period IN ('month','year')),
            period_key TEXT NOT NULL,
            category_id INTEGER REFERENCES categories(id),
            amount REAL NOT NULL CHECK(amount > 0),
            currency TEXT NOT NULL DEFAULT 'CNY',
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(period, period_key, category_id)
        )
    """)
    # indexes
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
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entries_deleted
        ON expense_entries(deleted_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entries_kind_cat_t
        ON expense_entries(kind, category, occurred_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_entries_tags
        ON expense_entries(tags)
    """)
    # views
    conn.execute("""
        CREATE VIEW IF NOT EXISTS v_active_entries AS
        SELECT * FROM expense_entries WHERE deleted_at IS NULL
    """)
    conn.execute("""
        CREATE VIEW IF NOT EXISTS v_monthly_by_category AS
        SELECT strftime('%Y-%m', occurred_at) AS month,
               kind, category,
               COUNT(*) AS n,
               SUM(amount) AS total
        FROM v_active_entries
        GROUP BY 1, 2, 3
    """)
    conn.execute("""
        CREATE VIEW IF NOT EXISTS v_yearly_by_category AS
        SELECT strftime('%Y', occurred_at) AS year,
               kind, category,
               COUNT(*) AS n,
               SUM(amount) AS total
        FROM v_active_entries
        GROUP BY 1, 2, 3
    """)


def _seed_categories(conn: sqlite3.Connection) -> None:
    """Idempotent seed of canonical categories."""
    now = _now_iso()
    seeds = [
        ("餐饮", "expense", '["吃饭","饭","食物","外卖","点餐","聚餐"]'),
        ("交通", "expense", '["打车","出行","油费","地铁","高铁","机票","火车"]'),
        ("购物", "expense", '["买东西","网购","超市","百货"]'),
        ("居家", "expense", '["房租","水电","物业","家居","清洁"]'),
        ("娱乐", "expense", '["电影","游戏","KTV","旅游","休闲"]'),
        ("医疗", "expense", '["看病","药","体检","就医"]'),
        ("教育", "expense", '["书","课","培训","学习"]'),
        ("通讯", "expense", '["话费","流量","宽带","订阅"]'),
        ("投资", "expense", '["买股票","买基金","定投"]'),
        ("其他", "expense", '["杂项","未分类"]'),
        ("工资", "income", '["薪水","薪资","收入"]'),
        ("投资收益", "income", '["分红","卖出收益","利息","理财"]'),
        ("红包", "income", '["奖金","压岁钱"]'),
        ("退款", "income", '["返现","退货","报销"]'),
        ("其他收入", "income", "[]"),
    ]
    for name, kind, aliases in seeds:
        conn.execute(
            """INSERT OR IGNORE INTO categories
               (name, kind, aliases, archived, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, 0, 0, ?, ?)""",
            (name, kind, aliases, now, now),
        )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_payment_method(pm: str | None) -> str | None:
    if pm is None:
        return None
    canonical = _PAYMENT_CANONICAL.get(pm.strip().lower())
    if canonical:
        return canonical
    # exact canonical match
    if pm.strip().lower() in {k.lower() for k in _PAYMENT_ALIASES}:
        return pm.strip().lower()
    raise ValueError(f"unknown payment_method: {pm!r}. Valid: {sorted(_PAYMENT_ALIASES.keys())}")


def _resolve_category(conn: sqlite3.Connection, category: str, kind: str | None) -> tuple[int | None, str]:
    """Return (category_id, canonical_name) or (None, input) if no match."""
    raw = category.strip()
    # 1. exact name match
    row = conn.execute(
        "SELECT id, name FROM categories WHERE name = ? AND archived = 0",
        (raw,),
    ).fetchone()
    if row:
        return row["id"], row["name"]
    # 2. alias match (JSON contains exact alias)
    # Use a simple LIKE for JSON array membership
    pattern = f'%"{raw}"%'
    row = conn.execute(
        "SELECT id, name FROM categories WHERE aliases LIKE ? AND archived = 0",
        (pattern,),
    ).fetchone()
    if row:
        return row["id"], row["name"]
    # 3. fallback: if the raw string exactly equals a canonical name regardless of kind
    row = conn.execute(
        "SELECT id, name FROM categories WHERE name = ? AND archived = 0",
        (raw,),
    ).fetchone()
    if row:
        return row["id"], row["name"]
    return None, raw


def _category_candidates(conn: sqlite3.Connection, category: str) -> list[dict[str, Any]]:
    """Return top 3 candidate categories for unknown input."""
    raw = category.strip().lower()
    rows = conn.execute(
        "SELECT name, aliases FROM categories WHERE archived = 0"
    ).fetchall()
    scored: list[tuple[float, str, str]] = []
    for r in rows:
        name = r["name"]
        aliases = json.loads(r["aliases"] or "[]")
        score = 0.0
        reason = ""
        if raw == name.lower():
            score = 1.0
            reason = "exact match"
        elif raw in [a.lower() for a in aliases]:
            score = 0.9
            reason = f"alias match"
        elif any(raw in a.lower() or a.lower() in raw for a in aliases):
            score = 0.6
            reason = "alias contains"
        elif raw and name.lower().startswith(raw[0]):
            score = 0.2
            reason = "first char match"
        if score > 0:
            scored.append((score, name, reason))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [
        {"name": name, "score": round(score, 2), "reason": reason}
        for score, name, reason in scored[:3]
    ]


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------


def expense_write(
    action: str = "record",
    id: int | None = None,
    kind: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    occurred_at: str | None = None,
    currency: str | None = None,
    merchant: str | None = None,
    note: str | None = None,
    payment_method: str | None = None,
    tags: list[str] | None = None,
    source_agent: str = "openclaw",
    source_text: str | None = None,
    entries: list[dict] | None = None,
    confirm_new_category: bool = False,
    # ---- action=manage_category ----
    manage_category_action: str = "add",
    manage_category_name: str | None = None,
    manage_category_new_name: str | None = None,
    manage_category_target_name: str | None = None,
    manage_category_alias: str | None = None,
    manage_category_kind: str = "expense",
    # ---- action=manage_merchant ----
    manage_merchant_action: str = "add",
    manage_merchant_name: str | None = None,
    manage_merchant_new_name: str | None = None,
    manage_merchant_target_name: str | None = None,
    manage_merchant_alias: str | None = None,
    manage_merchant_default_category_id: int | None = None,
    # ---- action=set_budget ----
    budget_period: str = "month",
    budget_period_key: str | None = None,
    budget_category_id: int | None = None,
    budget_amount: float | None = None,
    budget_note: str | None = None,
    # ---- action=delete_budget ----
    budget_id: int | None = None,
) -> dict[str, Any]:
    """Unified write endpoint for expense tracker v2.

    Actions: record, update, delete, restore, batch_record,
             manage_category, manage_merchant, set_budget, delete_budget.
    """
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        _seed_categories(conn)

        if action == "record":
            return _action_record(
                conn, kind, amount, category, occurred_at, currency,
                merchant, note, payment_method, tags, source_agent, source_text,
                confirm_new_category,
            )
        if action == "update":
            return _action_update(
                conn, id, kind, amount, category, occurred_at, currency,
                merchant, note, payment_method, tags, source_agent, source_text,
                confirm_new_category,
            )
        if action == "delete":
            return _action_delete(conn, id)
        if action == "restore":
            return _action_restore(conn, id)
        if action == "batch_record":
            return _action_batch_record(conn, entries or [], confirm_new_category)
        if action == "manage_category":
            return _action_manage_category(
                conn, manage_category_action, manage_category_name,
                manage_category_new_name, manage_category_target_name,
                manage_category_alias, manage_category_kind,
            )
        if action == "manage_merchant":
            return _action_manage_merchant(
                conn, manage_merchant_action, manage_merchant_name,
                manage_merchant_new_name, manage_merchant_target_name,
                manage_merchant_alias, manage_merchant_default_category_id,
            )
        if action == "set_budget":
            return _action_set_budget(
                conn, budget_period, budget_period_key, budget_category_id,
                budget_amount, budget_note,
            )
        if action == "delete_budget":
            return _action_delete_budget(conn, budget_id)
        raise ValueError(f"unknown action: {action!r}")
    finally:
        conn.close()


def _action_record(
    conn, kind, amount, category, occurred_at, currency,
    merchant, note, payment_method, tags, source_agent, source_text,
    confirm_new_category,
):
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError(f"amount must be > 0, got {amount!r}")
    if not category or not category.strip():
        raise ValueError("category is required (non-empty)")

    category_id, canonical_name = _resolve_category(conn, category, kind)
    if category_id is None:
        if not confirm_new_category:
            candidates = _category_candidates(conn, category)
            return {
                "ok": False,
                "needs_confirmation": True,
                "reason": "unknown category",
                "input": category.strip(),
                "candidates": candidates or [{"name": "其他", "score": 0.1, "reason": "fallback"}],
                "hint": "Re-call with category='<canonical>' to use existing, or confirm_new_category=True to create.",
            }
        # create new category on the fly
        now = _now_iso()
        conn.execute(
            """INSERT INTO categories (name, kind, aliases, archived, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, 0, 0, ?, ?)""",
            (category.strip(), kind, "[]", now, now),
        )
        category_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        canonical_name = category.strip()

    occurred_at = _normalize_occurred_at(occurred_at)
    now = _now_iso()
    currency = (currency or DEFAULT_CURRENCY).upper()
    if currency not in VALID_CURRENCIES:
        raise ValueError(f"currency must be one of {sorted(VALID_CURRENCIES)}, got {currency!r}")
    pm = _normalize_payment_method(payment_method)
    tags_str = "," + ",".join(t.strip() for t in (tags or []) if t.strip()) + "," if tags else None

    cur = conn.execute(
        """INSERT INTO expense_entries
           (kind, amount, currency, occurred_at, category, category_id, merchant,
            note, payment_method, tags, source_agent, source_text,
            created_at, updated_at, amount_cents)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kind, float(amount), currency, occurred_at,
            canonical_name, category_id, merchant,
            note, pm, tags_str, source_agent, source_text,
            now, now, round(float(amount) * 100),
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "action": "record",
        "id": cur.lastrowid,
        "normalized": {
            "category": canonical_name,
            "category_id": category_id,
            "payment_method": pm,
            "occurred_at": occurred_at,
        },
    }


def _action_update(
    conn, row_id, kind, amount, category, occurred_at, currency,
    merchant, note, payment_method, tags, source_agent, source_text,
    confirm_new_category,
):
    if row_id is None:
        raise ValueError("id is required for update")
    existing = conn.execute(
        "SELECT * FROM expense_entries WHERE id = ?", (row_id,)
    ).fetchone()
    if not existing:
        return {"ok": False, "error": f"entry id={row_id} not found"}

    updates: dict[str, Any] = {}
    if kind is not None:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
        updates["kind"] = kind
    if amount is not None:
        if not isinstance(amount, (int, float)) or amount <= 0:
            raise ValueError("amount must be > 0")
        updates["amount"] = float(amount)
        updates["amount_cents"] = round(float(amount) * 100)
    if category is not None:
        category_id, canonical_name = _resolve_category(conn, category, updates.get("kind", existing["kind"]))
        if category_id is None:
            candidates = _category_candidates(conn, category)
            return {
                "ok": False,
                "needs_confirmation": True,
                "reason": "unknown category",
                "input": category.strip(),
                "candidates": candidates or [{"name": "其他", "score": 0.1, "reason": "fallback"}],
                "hint": "Re-call with category='<canonical>' to use existing, or confirm_new_category=True to create.",
            }
        updates["category"] = canonical_name
        updates["category_id"] = category_id
    if occurred_at is not None:
        updates["occurred_at"] = _normalize_occurred_at(occurred_at)
    if currency is not None:
        currency = currency.upper()
        if currency not in VALID_CURRENCIES:
            raise ValueError(f"invalid currency: {currency}")
        updates["currency"] = currency
    if merchant is not None:
        updates["merchant"] = merchant
    if note is not None:
        updates["note"] = note
    if payment_method is not None:
        updates["payment_method"] = _normalize_payment_method(payment_method)
    if tags is not None:
        updates["tags"] = "," + ",".join(t.strip() for t in tags if t.strip()) + "," if tags else None
    if source_agent is not None:
        updates["source_agent"] = source_agent
    if source_text is not None:
        updates["source_text"] = source_text

    if not updates:
        return {"ok": False, "error": "no fields to update"}

    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [row_id]
    conn.execute(
        f"UPDATE expense_entries SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    return {"ok": True, "action": "update", "id": row_id, "updated_fields": list(updates.keys())}


def _action_delete(conn, row_id):
    if row_id is None:
        raise ValueError("id is required for delete")
    now = _now_iso()
    cur = conn.execute(
        "UPDATE expense_entries SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        (now, now, row_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": f"entry id={row_id} not found or already deleted"}
    return {"ok": True, "action": "delete", "id": row_id}


def _action_restore(conn, row_id):
    if row_id is None:
        raise ValueError("id is required for restore")
    cur = conn.execute(
        "UPDATE expense_entries SET deleted_at = NULL, updated_at = ? WHERE id = ? AND deleted_at IS NOT NULL",
        (_now_iso(), row_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": f"entry id={row_id} not found or not deleted"}
    return {"ok": True, "action": "restore", "id": row_id}


def _action_batch_record(conn, entries, confirm_new_category):
    if not entries:
        raise ValueError("entries list is required for batch_record")
    now = _now_iso()
    results = []
    try:
        for i, entry in enumerate(entries):
            kind = entry.get("kind")
            amount = entry.get("amount")
            category = entry.get("category")
            if kind not in VALID_KINDS:
                raise ValueError(f"entry[{i}] kind must be one of {sorted(VALID_KINDS)}")
            if not isinstance(amount, (int, float)) or amount <= 0:
                raise ValueError(f"entry[{i}] amount must be > 0")
            if not category or not category.strip():
                raise ValueError(f"entry[{i}] category is required")

            category_id, canonical_name = _resolve_category(conn, category, kind)
            if category_id is None and not confirm_new_category:
                raise ValueError(
                    f"entry[{i}] unknown category '{category}'. Use confirm_new_category=True to create."
                )
            if category_id is None and confirm_new_category:
                # create new category on the fly
                conn.execute(
                    """INSERT INTO categories (name, kind, aliases, archived, sort_order, created_at, updated_at)
                       VALUES (?, ?, ?, 0, 0, ?, ?)""",
                    (category.strip(), kind, "[]", now, now),
                )
                category_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                canonical_name = category.strip()

            occurred_at = _normalize_occurred_at(entry.get("occurred_at"))
            currency = (entry.get("currency") or DEFAULT_CURRENCY).upper()
            if currency not in VALID_CURRENCIES:
                raise ValueError(f"entry[{i}] invalid currency: {currency}")
            pm = _normalize_payment_method(entry.get("payment_method"))
            tags = entry.get("tags")
            tags_str = "," + ",".join(t.strip() for t in tags if t.strip()) + "," if tags else None

            cur = conn.execute(
                """INSERT INTO expense_entries
                   (kind, amount, currency, occurred_at, category, category_id, merchant,
                    note, payment_method, tags, source_agent, source_text,
                    created_at, updated_at, amount_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind, float(amount), currency, occurred_at,
                    canonical_name, category_id, entry.get("merchant"),
                    entry.get("note"), pm, tags_str,
                    entry.get("source_agent", "openclaw"), entry.get("source_text"),
                    now, now, round(float(amount) * 100),
                ),
            )
            results.append({"id": cur.lastrowid, "category": canonical_name})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "action": "batch_record", "count": len(results), "results": results}


def _action_manage_category(
    conn, sub_action, name, new_name, target_name, alias, kind,
):
    now = _now_iso()
    if sub_action == "add":
        if not name or not name.strip():
            raise ValueError("name is required for add")
        try:
            conn.execute(
                """INSERT INTO categories (name, kind, aliases, archived, sort_order, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 0, ?, ?)""",
                (name.strip(), kind, json.dumps([alias] if alias else []), now, now),
            )
            conn.commit()
            return {"ok": True, "action": "manage_category", "sub_action": "add", "name": name.strip()}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": f"category '{name}' already exists"}

    if sub_action == "rename":
        if not name or not new_name:
            raise ValueError("name and new_name are required for rename")
        row = conn.execute("SELECT id FROM categories WHERE name = ? AND archived = 0", (name,)).fetchone()
        if not row:
            return {"ok": False, "error": f"category '{name}' not found"}
        cat_id = row["id"]
        conn.execute("UPDATE categories SET name = ?, updated_at = ? WHERE id = ?", (new_name.strip(), now, cat_id))
        conn.execute("UPDATE expense_entries SET category = ?, updated_at = ? WHERE category_id = ?", (new_name.strip(), now, cat_id))
        conn.commit()
        return {"ok": True, "action": "manage_category", "sub_action": "rename", "old": name, "new": new_name.strip()}

    if sub_action == "merge":
        if not name or not target_name:
            raise ValueError("name and target_name are required for merge")
        src = conn.execute("SELECT id FROM categories WHERE name = ? AND archived = 0", (name,)).fetchone()
        dst = conn.execute("SELECT id, name FROM categories WHERE name = ? AND archived = 0", (target_name,)).fetchone()
        if not src:
            return {"ok": False, "error": f"source category '{name}' not found"}
        if not dst:
            return {"ok": False, "error": f"target category '{target_name}' not found"}
        src_id, dst_id, dst_name = src["id"], dst["id"], dst["name"]
        conn.execute("UPDATE expense_entries SET category_id = ?, category = ?, updated_at = ? WHERE category_id = ?", (dst_id, dst_name, now, src_id))
        conn.execute("UPDATE categories SET archived = 1, updated_at = ? WHERE id = ?", (now, src_id))
        conn.commit()
        return {"ok": True, "action": "manage_category", "sub_action": "merge", "from": name, "to": target_name}

    if sub_action == "archive":
        if not name:
            raise ValueError("name is required for archive")
        cur = conn.execute("UPDATE categories SET archived = 1, updated_at = ? WHERE name = ? AND archived = 0", (now, name))
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"category '{name}' not found or already archived"}
        return {"ok": True, "action": "manage_category", "sub_action": "archive", "name": name}

    if sub_action == "alias_add":
        if not name or not alias:
            raise ValueError("name and alias are required for alias_add")
        row = conn.execute("SELECT id, aliases FROM categories WHERE name = ? AND archived = 0", (name,)).fetchone()
        if not row:
            return {"ok": False, "error": f"category '{name}' not found"}
        aliases = json.loads(row["aliases"] or "[]")
        if alias.strip() not in aliases:
            aliases.append(alias.strip())
        conn.execute("UPDATE categories SET aliases = ?, updated_at = ? WHERE id = ?", (json.dumps(aliases), now, row["id"]))
        conn.commit()
        return {"ok": True, "action": "manage_category", "sub_action": "alias_add", "name": name, "aliases": aliases}

    raise ValueError(f"unknown manage_category sub_action: {sub_action!r}")


def _action_manage_merchant(
    conn, sub_action, name, new_name, target_name, alias, default_category_id,
):
    now = _now_iso()
    if sub_action == "add":
        if not name or not name.strip():
            raise ValueError("name is required for add")
        try:
            conn.execute(
                """INSERT INTO merchants (name, aliases, default_category_id, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name.strip(), json.dumps([alias] if alias else []), default_category_id, None, now, now),
            )
            conn.commit()
            return {"ok": True, "action": "manage_merchant", "sub_action": "add", "name": name.strip()}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": f"merchant '{name}' already exists"}

    if sub_action == "rename":
        if not name or not new_name:
            raise ValueError("name and new_name are required for rename")
        row = conn.execute("SELECT id FROM merchants WHERE name = ?", (name,)).fetchone()
        if not row:
            return {"ok": False, "error": f"merchant '{name}' not found"}
        m_id = row["id"]
        conn.execute("UPDATE merchants SET name = ?, updated_at = ? WHERE id = ?", (new_name.strip(), now, m_id))
        conn.execute("UPDATE expense_entries SET merchant = ?, updated_at = ? WHERE merchant = ?", (new_name.strip(), now, name))
        conn.commit()
        return {"ok": True, "action": "manage_merchant", "sub_action": "rename", "old": name, "new": new_name.strip()}

    if sub_action == "merge":
        if not name or not target_name:
            raise ValueError("name and target_name are required for merge")
        src = conn.execute("SELECT id FROM merchants WHERE name = ?", (name,)).fetchone()
        dst = conn.execute("SELECT id, name FROM merchants WHERE name = ?", (target_name,)).fetchone()
        if not src:
            return {"ok": False, "error": f"source merchant '{name}' not found"}
        if not dst:
            return {"ok": False, "error": f"target merchant '{target_name}' not found"}
        src_id, dst_id, dst_name = src["id"], dst["id"], dst["name"]
        conn.execute("UPDATE expense_entries SET merchant = ?, updated_at = ? WHERE merchant = ?", (dst_name, now, name))
        conn.execute("DELETE FROM merchants WHERE id = ?", (src_id,))
        conn.commit()
        return {"ok": True, "action": "manage_merchant", "sub_action": "merge", "from": name, "to": target_name}

    if sub_action == "archive":
        # merchants has no archived column; just delete
        if not name:
            raise ValueError("name is required for archive")
        cur = conn.execute("DELETE FROM merchants WHERE name = ?", (name,))
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"merchant '{name}' not found"}
        return {"ok": True, "action": "manage_merchant", "sub_action": "archive", "name": name}

    if sub_action == "alias_add":
        if not name or not alias:
            raise ValueError("name and alias are required for alias_add")
        row = conn.execute("SELECT id, aliases FROM merchants WHERE name = ?", (name,)).fetchone()
        if not row:
            return {"ok": False, "error": f"merchant '{name}' not found"}
        aliases = json.loads(row["aliases"] or "[]")
        if alias.strip() not in aliases:
            aliases.append(alias.strip())
        conn.execute("UPDATE merchants SET aliases = ?, updated_at = ? WHERE id = ?", (json.dumps(aliases), now, row["id"]))
        conn.commit()
        return {"ok": True, "action": "manage_merchant", "sub_action": "alias_add", "name": name, "aliases": aliases}

    raise ValueError(f"unknown manage_merchant sub_action: {sub_action!r}")


def _action_set_budget(
    conn, period, period_key, category_id, amount, note,
):
    if period not in {"month", "year"}:
        raise ValueError(f"budget_period must be month/year, got {period!r}")
    if period_key is None:
        raise ValueError("budget_period_key is required")
    if amount is None or amount <= 0:
        raise ValueError("budget_amount must be > 0")
    now = _now_iso()
    try:
        conn.execute(
            """INSERT INTO budgets (period, period_key, category_id, amount, currency, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(period, period_key, category_id) DO UPDATE SET
                   amount=excluded.amount, note=excluded.note, updated_at=excluded.updated_at""",
            (period, period_key, category_id, float(amount), "CNY", note, now, now),
        )
        conn.commit()
        return {"ok": True, "action": "set_budget", "period": period, "period_key": period_key, "category_id": category_id, "amount": float(amount)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _action_delete_budget(conn, budget_id):
    if budget_id is None:
        raise ValueError("budget_id is required for delete_budget")
    cur = conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
    conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": f"budget id={budget_id} not found"}
    return {"ok": True, "action": "delete_budget", "id": budget_id}


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def expense_read(
    mode: str = "aggregate",
    start_date: str | None = None,
    end_date: str | None = None,
    kind: str | None = None,
    category: str | list[str] | None = None,
    merchant: str | list[str] | None = None,
    payment_method: str | None = None,
    tags: list[str] | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
    order_by: str = "occurred_at desc",
    group_by: list[str] | None = None,
    metric: str = "sum",
    bucket: str = "month",
    sql: str | None = None,
    sql_params: dict | None = None,
    # ---- mode=compare ----
    compare: str = "yoy",
    compare_group_by: list[str] | None = None,
    # ---- mode=anomaly ----
    anomaly_window_days: int = 90,
    anomaly_sigma: float = 2.0,
    # ---- mode=export ----
    export_format: str = "markdown",
) -> dict[str, Any]:
    """Unified read endpoint for expense tracker v2.

    Modes: list, aggregate, trend, compare, anomaly, budget_status,
           categories, merchants, export, sql.
    """
    if kind is not None and kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")

    if mode == "list":
        return _read_list(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, limit, offset, order_by,
        )
    if mode == "aggregate":
        return _read_aggregate(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, group_by, metric, limit,
        )
    if mode == "trend":
        return _read_trend(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, bucket, group_by, metric,
        )
    if mode == "compare":
        return _read_compare(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, compare, compare_group_by, metric,
        )
    if mode == "anomaly":
        return _read_anomaly(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, anomaly_window_days, anomaly_sigma,
        )
    if mode == "budget_status":
        return _read_budget_status(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount,
        )
    if mode == "categories":
        return _read_categories()
    if mode == "merchants":
        return _read_merchants()
    if mode == "export":
        return _read_export(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, export_format, limit,
        )
    if mode == "sql":
        return _read_sql(sql, sql_params)
    raise ValueError(f"unknown mode: {mode!r}")


def _build_where(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if not include_deleted:
        where.append("deleted_at IS NULL")

    if start_date:
        where.append("occurred_at >= ?")
        if "T" not in start_date:
            start_date = start_date + "T00:00:00+08:00"
        params.append(start_date)
    if end_date:
        where.append("occurred_at <= ?")
        if "T" not in end_date:
            end_date = end_date + "T23:59:59+08:00"
        params.append(end_date)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if category:
        if isinstance(category, list):
            placeholders = ", ".join("?" for _ in category)
            where.append(f"category IN ({placeholders})")
            params.extend(category)
        else:
            where.append("category = ?")
            params.append(category)
    if merchant:
        if isinstance(merchant, list):
            placeholders = ", ".join("?" for _ in merchant)
            where.append(f"merchant IN ({placeholders})")
            params.extend(merchant)
        else:
            where.append("merchant = ?")
            params.append(merchant)
    if payment_method:
        where.append("payment_method = ?")
        params.append(payment_method)
    if tags:
        for tag in tags:
            where.append("tags LIKE ?")
            params.append(f"%,{tag.strip()},%")
    if min_amount is not None:
        where.append("amount >= ?")
        params.append(float(min_amount))
    if max_amount is not None:
        where.append("amount <= ?")
        params.append(float(max_amount))

    where_clause = " AND ".join(where) if where else "1=1"
    return where_clause, params


def _read_list(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, limit, offset, order_by,
):
    _VALID_ORDER = {
        "occurred_at desc", "occurred_at asc",
        "amount desc", "amount asc",
        "id desc", "id asc",
        "created_at desc", "created_at asc",
    }
    if order_by not in _VALID_ORDER:
        raise ValueError(f"order_by must be one of {sorted(_VALID_ORDER)}, got {order_by!r}")
    limit = min(max(int(limit), 1), 1000)
    offset = max(int(offset), 0)

    where_clause, params = _build_where(
        start_date, end_date, kind, category, merchant, payment_method,
        tags, min_amount, max_amount, include_deleted,
    )

    conn = _get_conn()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT * FROM expense_entries WHERE {where_clause} ORDER BY {order_by} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM expense_entries WHERE {where_clause}",
            params,
        ).fetchone()[0]
        return {
            "ok": True,
            "mode": "list",
            "total_count": total,
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _read_aggregate(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, group_by, metric, limit,
):
    if metric not in {"sum", "count", "avg", "min", "max"}:
        raise ValueError(f"metric must be sum/count/avg/min/max, got {metric!r}")
    _VALID_GROUPS = {"kind", "category", "merchant", "month", "year", "payment_method", "tag"}
    if group_by is None:
        group_by = ["category"]
    invalid = set(group_by) - _VALID_GROUPS
    if invalid:
        raise ValueError(f"invalid group_by fields: {sorted(invalid)}. Valid: {sorted(_VALID_GROUPS)}")
    limit = min(max(int(limit), 1), 1000)

    where_clause, params = _build_where(
        start_date, end_date, kind, category, merchant, payment_method,
        tags, min_amount, max_amount, include_deleted,
    )

    # Build SELECT / GROUP BY
    select_cols: list[str] = []
    group_cols: list[str] = []
    for g in group_by:
        if g == "month":
            select_cols.append("strftime('%Y-%m', occurred_at) AS month")
            group_cols.append("strftime('%Y-%m', occurred_at)")
        elif g == "year":
            select_cols.append("strftime('%Y', occurred_at) AS year")
            group_cols.append("strftime('%Y', occurred_at)")
        elif g == "tag":
            # For tag grouping, we denormalize by splitting tags in Python after fetching
            select_cols.append("tags")
            group_cols.append("tags")
        else:
            select_cols.append(g)
            group_cols.append(g)

    metric_expr = {
        "sum": "SUM(amount)",
        "count": "COUNT(*)",
        "avg": "AVG(amount)",
        "min": "MIN(amount)",
        "max": "MAX(amount)",
    }[metric]
    select_cols.append(f"{metric_expr} AS metric_value")
    select_cols.append("COUNT(*) AS n")

    group_by_sql = ", ".join(group_cols) if group_cols else "1"
    sql = f"SELECT {', '.join(select_cols)} FROM expense_entries WHERE {where_clause} GROUP BY {group_by_sql} ORDER BY metric_value DESC LIMIT ?"
    params_with_limit = params + [limit]

    conn = _get_conn()
    try:
        _ensure_schema(conn)
        rows = conn.execute(sql, params_with_limit).fetchall()
        grand = conn.execute(
            f"SELECT {metric_expr} AS total FROM expense_entries WHERE {where_clause}",
            params,
        ).fetchone()
        return {
            "ok": True,
            "mode": "aggregate",
            "group_by": group_by,
            "metric": metric,
            "groups": [dict(r) for r in rows],
            "grand_total": {"metric_value": grand["total"] if grand else 0, "metric": metric},
        }
    finally:
        conn.close()


def _read_trend(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, bucket, group_by, metric,
):
    if bucket not in {"day", "week", "month", "quarter", "year"}:
        raise ValueError(f"bucket must be day/week/month/quarter/year, got {bucket!r}")
    if metric not in {"sum", "count", "avg", "min", "max"}:
        raise ValueError(f"metric must be sum/count/avg/min/max, got {metric!r}")
    _VALID_GROUPS = {"kind", "category", "merchant", "payment_method"}
    if group_by is None:
        group_by = []
    invalid = set(group_by) - _VALID_GROUPS
    if invalid:
        raise ValueError(f"invalid group_by for trend: {sorted(invalid)}. Valid: {sorted(_VALID_GROUPS)}")

    where_clause, params = _build_where(
        start_date, end_date, kind, category, merchant, payment_method,
        tags, min_amount, max_amount, include_deleted,
    )

    bucket_fmt = {
        "day": "%Y-%m-%d",
        "week": "%Y-W%W",
        "month": "%Y-%m",
        "quarter": "%Y-Q" + "((CAST(strftime('%m', occurred_at) AS INTEGER) + 2) / 3)",  # simplified below
        "year": "%Y",
    }[bucket]

    if bucket == "quarter":
        period_expr = "strftime('%Y', occurred_at) || '-Q' || ((CAST(strftime('%m', occurred_at) AS INTEGER) + 2) / 3)"
    else:
        period_expr = f"strftime('{bucket_fmt}', occurred_at)"

    select_cols = [f"{period_expr} AS period"]
    group_cols = [period_expr]
    for g in group_by:
        select_cols.append(g)
        group_cols.append(g)

    metric_expr = {
        "sum": "SUM(amount)",
        "count": "COUNT(*)",
        "avg": "AVG(amount)",
        "min": "MIN(amount)",
        "max": "MAX(amount)",
    }[metric]
    select_cols.append(f"{metric_expr} AS metric_value")
    select_cols.append("COUNT(*) AS n")

    group_by_sql = ", ".join(group_cols)
    sql = f"SELECT {', '.join(select_cols)} FROM expense_entries WHERE {where_clause} GROUP BY {group_by_sql} ORDER BY period DESC"

    conn = _get_conn()
    try:
        _ensure_schema(conn)
        rows = conn.execute(sql, params).fetchall()
        return {
            "ok": True,
            "mode": "trend",
            "bucket": bucket,
            "group_by": group_by,
            "metric": metric,
            "buckets": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _read_compare(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, compare, compare_group_by, metric,
):
    if compare not in {"mom", "yoy", "qoq"}:
        raise ValueError(f"compare must be mom/yoy/qoq, got {compare!r}")
    if metric not in {"sum", "count", "avg", "min", "max"}:
        raise ValueError(f"metric must be sum/count/avg/min/max, got {metric!r}")
    if compare_group_by is None:
        compare_group_by = ["category"]

    where_clause, params = _build_where(
        start_date, end_date, kind, category, merchant, payment_method,
        tags, min_amount, max_amount, include_deleted,
    )

    metric_expr = {
        "sum": "SUM(amount)",
        "count": "COUNT(*)",
        "avg": "AVG(amount)",
        "min": "MIN(amount)",
        "max": "MAX(amount)",
    }[metric]

    group_cols = []
    select_cols = []
    for g in compare_group_by:
        if g == "month":
            select_cols.append("strftime('%Y-%m', occurred_at) AS month")
            group_cols.append("strftime('%Y-%m', occurred_at)")
        elif g == "year":
            select_cols.append("strftime('%Y', occurred_at) AS year")
            group_cols.append("strftime('%Y', occurred_at)")
        else:
            select_cols.append(g)
            group_cols.append(g)

    select_cols.append(f"{metric_expr} AS metric_value")
    select_cols.append("COUNT(*) AS n")

    group_by_sql = ", ".join(group_cols) if group_cols else "1"
    base_sql = f"SELECT {', '.join(select_cols)} FROM expense_entries WHERE {where_clause}"
    period_sql = f"{base_sql} GROUP BY {group_by_sql}"

    conn = _get_conn()
    try:
        _ensure_schema(conn)
        rows = conn.execute(period_sql, params).fetchall()
        # Determine current/previous periods from start_date/end_date
        current_period = None
        previous_period = None
        if start_date and end_date:
            if compare == "yoy":
                current_period = (start_date[:4], end_date[:4])
                prev_start = str(int(start_date[:4]) - 1) + start_date[4:]
                prev_end = str(int(end_date[:4]) - 1) + end_date[4:]
            elif compare == "mom":
                # simplistic: previous month
                current_period = (start_date[:7], end_date[:7])
                # compute previous month
                dt = datetime.datetime.strptime(start_date[:7], "%Y-%m")
                prev = dt - datetime.timedelta(days=1)
                prev_start = prev.strftime("%Y-%m") + "-01"
                prev_end = prev.strftime("%Y-%m") + "-31"
            else:  # qoq
                current_period = (start_date[:4], end_date[:4])
                prev_start = None
                prev_end = None
        else:
            prev_start = prev_end = None

        prev_where, prev_params = _build_where(
            prev_start, prev_end, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
        )
        prev_sql = f"SELECT {', '.join(select_cols)} FROM expense_entries WHERE {prev_where} GROUP BY {group_by_sql}"
        prev_rows = conn.execute(prev_sql, prev_params).fetchall() if prev_start else []

        # Build comparison map
        prev_map: dict[str, float] = {}
        key_cols = compare_group_by
        def _row_key(row, cols):
            return "|".join(str(row[c]) if c in row.keys() else "" for c in cols)

        for r in prev_rows:
            key = _row_key(r, key_cols)
            prev_map[key] = float(r["metric_value"] or 0)

        results = []
        for r in rows:
            key = _row_key(r, key_cols)
            cur_val = float(r["metric_value"] or 0)
            prev_val = prev_map.get(key, 0)
            delta = cur_val - prev_val
            delta_pct = round(delta / prev_val * 100, 2) if prev_val else None
            results.append({
                **dict(r),
                "current": cur_val,
                "previous": prev_val,
                "delta": round(delta, 2),
                "delta_pct": delta_pct,
            })

        return {
            "ok": True,
            "mode": "compare",
            "compare": compare,
            "group_by": compare_group_by,
            "metric": metric,
            "groups": results,
        }
    finally:
        conn.close()


def _read_anomaly(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, anomaly_window_days, anomaly_sigma,
):
    # Compute historical mean/std from window before end_date
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        if end_date is None:
            end_date = datetime.datetime.now(_TZ_CN).strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (datetime.datetime.now(_TZ_CN) - datetime.timedelta(days=anomaly_window_days)).strftime("%Y-%m-%d")

        # historical stats from window
        hist_end = end_date
        hist_start = (datetime.datetime.strptime(hist_end, "%Y-%m-%d") - datetime.timedelta(days=anomaly_window_days)).strftime("%Y-%m-%d")
        hist_where, hist_params = _build_where(
            hist_start, hist_end, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
        )
        stats = conn.execute(
            f"SELECT AVG(amount) AS mean, (AVG(amount*amount) - AVG(amount)*AVG(amount)) AS var FROM expense_entries WHERE {hist_where}",
            hist_params,
        ).fetchone()
        mean = float(stats["mean"] or 0)
        var = float(stats["var"] or 0)
        std = var ** 0.5 if var > 0 else 0
        upper = mean + anomaly_sigma * std
        lower = mean - anomaly_sigma * std if mean - anomaly_sigma * std > 0 else 0

        # Find anomalies in current period
        curr_where, curr_params = _build_where(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
        )
        rows = conn.execute(
            f"SELECT * FROM expense_entries WHERE {curr_where} AND (amount > ? OR amount < ?) ORDER BY amount DESC",
            curr_params + [upper, lower],
        ).fetchall()

        anomalies = []
        for r in rows:
            amt = float(r["amount"])
            z = (amt - mean) / std if std > 0 else 0
            anomalies.append({
                "id": r["id"],
                "amount": amt,
                "z_score": round(z, 2),
                "category": r["category"],
                "occurred_at": r["occurred_at"],
            })

        return {
            "ok": True,
            "mode": "anomaly",
            "mean": round(mean, 2),
            "std": round(std, 2),
            "sigma": anomaly_sigma,
            "threshold_upper": round(upper, 2),
            "threshold_lower": round(lower, 2),
            "anomalies": anomalies,
        }
    finally:
        conn.close()


def _read_budget_status(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount,
):
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        # Default to current month
        if start_date is None or end_date is None:
            now = datetime.datetime.now(_TZ_CN)
            start_date = now.strftime("%Y-%m-01")
            end_date = now.strftime("%Y-%m-%d")
        period_key = start_date[:7]

        # Get all budgets for this period
        budgets = conn.execute(
            "SELECT * FROM budgets WHERE period_key = ?",
            (period_key,),
        ).fetchall()

        # Get spending per category in period
        where_clause, params = _build_where(
            start_date, end_date, "expense", category, merchant, payment_method,
            tags, min_amount, max_amount, False,
        )
        rows = conn.execute(
            f"SELECT category_id, category, SUM(amount) AS spent FROM expense_entries WHERE {where_clause} AND deleted_at IS NULL GROUP BY category_id, category",
            params,
        ).fetchall()
        spent_map: dict[int | None, float] = {}
        for r in rows:
            spent_map[r["category_id"]] = float(r["spent"] or 0)
            spent_map[None] = spent_map.get(None, 0) + float(r["spent"] or 0)

        results = []
        for b in budgets:
            cat_id = b["category_id"]
            spent = spent_map.get(cat_id, 0)
            budget_amt = float(b["amount"])
            remaining = budget_amt - spent
            pct_used = round(spent / budget_amt * 100, 1) if budget_amt else 0
            results.append({
                "category_id": cat_id,
                "budget": budget_amt,
                "spent": round(spent, 2),
                "remaining": round(remaining, 2),
                "pct_used": pct_used,
            })

        return {
            "ok": True,
            "mode": "budget_status",
            "period_key": period_key,
            "budgets": results,
        }
    finally:
        conn.close()


def _read_categories():
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        _seed_categories(conn)
        rows = conn.execute("SELECT * FROM categories ORDER BY sort_order, name").fetchall()
        return {
            "ok": True,
            "mode": "categories",
            "categories": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _read_merchants():
    conn = _get_conn()
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM merchants ORDER BY name").fetchall()
        return {
            "ok": True,
            "mode": "merchants",
            "merchants": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _read_export(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, export_format, limit,
):
    if export_format not in {"markdown", "csv", "json"}:
        raise ValueError(f"export_format must be markdown/csv/json, got {export_format!r}")

    where_clause, params = _build_where(
        start_date, end_date, kind, category, merchant, payment_method,
        tags, min_amount, max_amount, include_deleted,
    )
    limit = min(max(int(limit), 1), 5000)

    conn = _get_conn()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT * FROM expense_entries WHERE {where_clause} ORDER BY occurred_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        data = [dict(r) for r in rows]

        if export_format == "json":
            content = json.dumps(data, ensure_ascii=False, indent=2)
        elif export_format == "csv":
            import io, csv as csv_mod
            buf = io.StringIO()
            if data:
                writer = csv_mod.DictWriter(buf, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
            content = buf.getvalue()
        else:  # markdown
            lines = ["| id | kind | amount | currency | category | occurred_at | note |", "|---|---|---|---|---|---|---|"]
            for r in data:
                lines.append(f"| {r.get('id','')} | {r.get('kind','')} | {r.get('amount','')} | {r.get('currency','')} | {r.get('category','')} | {r.get('occurred_at','')} | {r.get('note','') or ''} |")
            content = "\n".join(lines)

        return {
            "ok": True,
            "mode": "export",
            "format": export_format,
            "row_count": len(data),
            "content": content,
        }
    finally:
        conn.close()


# SQL readonly validation
_SQL_FORBIDDEN_RE = re.compile(
    r"\b(ATTACH|DETACH|DROP|DELETE|UPDATE|INSERT|CREATE|ALTER|REINDEX|VACUUM|PRAGMA\s+writable_schema)\b",
    re.IGNORECASE,
)
_SQL_ALLOWED_START_RE = re.compile(
    r"^\s*(SELECT\s|WITH\s|PRAGMA\s+table_info\s*\(|PRAGMA\s+index_info\s*\()",
    re.IGNORECASE,
)


def _read_sql(sql: str | None, sql_params: dict | None):
    if sql is None:
        raise ValueError("sql is required for mode=sql")
    if sql.strip() == ":help":
        return {
            "ok": True,
            "mode": "sql",
            "help": {
                "tables": ["expense_entries", "categories", "merchants", "budgets"],
                "views": ["v_active_entries", "v_monthly_by_category", "v_yearly_by_category"],
                "note": "Use PRAGMA table_info(table_name) to inspect columns.",
            },
        }
    if len(sql) > 4096:
        return {"ok": False, "reason": "sql validation failed", "detail": "SQL exceeds 4096 bytes limit"}
    if ";" in sql:
        return {"ok": False, "reason": "sql validation failed", "detail": "semicolons not allowed (multi-statement guard)"}
    if not _SQL_ALLOWED_START_RE.match(sql):
        return {"ok": False, "reason": "sql validation failed", "detail": "SQL must start with SELECT, WITH, or PRAGMA table_info/index_info"}
    if _SQL_FORBIDDEN_RE.search(sql):
        return {"ok": False, "reason": "sql validation failed", "detail": "forbidden keyword detected"}

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        cur = conn.execute(sql, sql_params or {})
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(1001)
        truncated = len(rows) > 1000
        if truncated:
            rows = rows[:1000]
        return {
            "ok": True,
            "mode": "sql",
            "columns": cols,
            "rows": [list(r) for r in rows],
            "truncated": truncated,
            "row_count": len(rows),
        }
    except sqlite3.Error as e:
        return {"ok": False, "reason": "sql execution failed", "detail": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backward-compatible v1 aliases
# ---------------------------------------------------------------------------


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
    """v1 alias → expense_write(action='record', ...)."""
    return expense_write(
        action="record",
        kind=kind,
        amount=amount,
        category=category,
        occurred_at=occurred_at,
        currency=currency,
        merchant=merchant,
        note=note,
        payment_method=payment_method,
        source_agent=source_agent,
        source_text=source_text,
    )


def expense_query(
    start_date: str | None = None,
    end_date: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    group_by: str = "category",
    limit: int = 50,
) -> dict[str, Any]:
    """v1 alias → expense_read(mode='aggregate', group_by=[...]).

    Keeps v1 single-field group_by semantics.
    """
    if group_by == "none":
        return expense_read(
            mode="list",
            start_date=start_date,
            end_date=end_date,
            kind=kind,
            category=category,
            limit=limit,
        )
    # v1 group_by values: category, month, kind
    gb = [group_by] if group_by in {"category", "month", "kind"} else ["category"]
    return expense_read(
        mode="aggregate",
        start_date=start_date,
        end_date=end_date,
        kind=kind,
        category=category,
        group_by=gb,
        metric="sum",
        limit=limit,
    )
