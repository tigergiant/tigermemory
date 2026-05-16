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
import hashlib
import itertools
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

# P3: Import tm_llm at module level for testability
import tm_llm

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
            amount REAL NOT NULL CHECK(amount >= 0),
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
    # v4 columns (P4: dedup support)
    for col, dtype in (
        ("source_external_id", "TEXT"),
        ("dedup_hash", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE expense_entries ADD COLUMN {col} {dtype}")
        except sqlite3.OperationalError:
            pass
    # v4 unique indexes (partial: only active rows)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_external_id
        ON expense_entries(source_external_id)
        WHERE source_external_id IS NOT NULL AND deleted_at IS NULL
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_dedup_hash
        ON expense_entries(dedup_hash)
        WHERE source_external_id IS NULL AND dedup_hash IS NOT NULL AND deleted_at IS NULL
    """)
    # v5 column (P5: transaction status)
    try:
        conn.execute(
            """ALTER TABLE expense_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'success'
               CHECK(status IN ('success','refunded','closed','internal_transfer'))"""
        )
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
            amount REAL NOT NULL CHECK(amount >= 0),
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
    # v3 FTS5 full-text search table (if migration hasn't run)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS expense_entries_fts
        USING fts5(note, tags, content='expense_entries', content_rowid='id')
    """)
    # FTS5 triggers for auto-sync
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS fts_entries_insert AFTER INSERT ON expense_entries BEGIN
            INSERT INTO expense_entries_fts(rowid, note, tags)
            VALUES (NEW.id, NEW.note, NEW.tags);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS fts_entries_update AFTER UPDATE ON expense_entries BEGIN
            UPDATE expense_entries_fts SET note = NEW.note, tags = NEW.tags WHERE rowid = NEW.id;
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS fts_entries_delete AFTER DELETE ON expense_entries BEGIN
            DELETE FROM expense_entries_fts WHERE rowid = OLD.id;
        END
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
    conn.commit()


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Idempotent v4 migration: backfill dedup_hash for rows without it.

    dedup_hash = sha1(occurred_at[:19] | amount_cents | payment_method | merchant | status | note_md5[:8])[:16]
    Only backfills rows where dedup_hash IS NULL and deleted_at IS NULL.
    Existing dedup_hash values are never recomputed (backward compat).
    """
    # Try to include status and note if they exist (v5+), fall back for pre-v5 DBs
    try:
        rows = conn.execute(
            """SELECT id, occurred_at, amount_cents, payment_method, merchant, status, note
               FROM expense_entries
               WHERE dedup_hash IS NULL AND deleted_at IS NULL"""
        ).fetchall()
        has_status_note = True
    except sqlite3.OperationalError:
        rows = conn.execute(
            """SELECT id, occurred_at, amount_cents, payment_method, merchant
               FROM expense_entries
               WHERE dedup_hash IS NULL AND deleted_at IS NULL"""
        ).fetchall()
        has_status_note = False
    if not rows:
        return
    for r in rows:
        occurred_second = (r["occurred_at"] or "")[:19]
        status = (r["status"] if has_status_note else None) or "success"
        note = (r["note"] if has_status_note else None) or ""
        note_hash = hashlib.md5(note.encode()).hexdigest()[:8]
        fingerprint = "|".join([
            occurred_second,
            str(r["amount_cents"] or 0),
            r["payment_method"] or "",
            r["merchant"] or "",
            status,
            note_hash,
        ])
        h = hashlib.sha1(fingerprint.encode()).hexdigest()[:16]
        conn.execute(
            "UPDATE expense_entries SET dedup_hash = ? WHERE id = ?",
            (h, r["id"]),
        )
    conn.commit()


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """Idempotent v5 migration: ensure all existing rows have status='success'.

    Before v5, all rows were implicitly success (refunds/internal_transfers
    were skipped by importers). This migration is a no-op for most rows but
    guarantees the invariant.
    """
    conn.execute(
        "UPDATE expense_entries SET status = 'success' WHERE status IS NULL OR status = ''"
    )
    conn.commit()


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """Idempotent v6 migration: fix CHECK(amount > 0) → CHECK(amount >= 0).

    Older DBs created before the P4 fix have CHECK(amount > 0) which blocks
    amount=0 rows (closed/internal_transfer). This migration recreates the
    table with the corrected constraint.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='expense_entries'"
    ).fetchone()
    if row and "CHECK(amount >= 0)" in row["sql"]:
        return  # already fixed

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        # Drop dependent views first
        conn.execute("DROP VIEW IF EXISTS v_yearly_by_category")
        conn.execute("DROP VIEW IF EXISTS v_monthly_by_category")
        conn.execute("DROP VIEW IF EXISTS v_active_entries")
        conn.execute("""
            CREATE TABLE expense_entries_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
                amount REAL NOT NULL CHECK(amount >= 0),
                currency TEXT NOT NULL DEFAULT 'CNY',
                occurred_at TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                merchant TEXT,
                note TEXT,
                payment_method TEXT,
                source_agent TEXT NOT NULL DEFAULT 'openclaw',
                source_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                category_id INTEGER,
                merchant_id INTEGER,
                tags TEXT,
                deleted_at TEXT,
                amount_cents INTEGER,
                source_external_id TEXT,
                dedup_hash TEXT,
                status TEXT NOT NULL DEFAULT 'success'
                    CHECK(status IN ('success','refunded','closed','internal_transfer'))
            )
        """)
        conn.execute(
            "INSERT INTO expense_entries_new SELECT * FROM expense_entries"
        )
        conn.execute("DROP TABLE expense_entries")
        conn.execute("ALTER TABLE expense_entries_new RENAME TO expense_entries")
        # Recreate indexes
        conn.execute("CREATE INDEX idx_expense_occurred ON expense_entries(occurred_at)")
        conn.execute("CREATE INDEX idx_expense_kind ON expense_entries(kind)")
        conn.execute("CREATE INDEX idx_expense_category ON expense_entries(category)")
        conn.execute("CREATE INDEX idx_entries_deleted ON expense_entries(deleted_at)")
        conn.execute("CREATE INDEX idx_entries_kind_cat_t ON expense_entries(kind, category, occurred_at)")
        conn.execute("CREATE INDEX idx_entries_tags ON expense_entries(tags)")
        conn.execute("""
            CREATE UNIQUE INDEX idx_entries_external_id
            ON expense_entries(source_external_id)
            WHERE source_external_id IS NOT NULL AND deleted_at IS NULL
        """)
        conn.execute("""
            CREATE UNIQUE INDEX idx_entries_dedup_hash
            ON expense_entries(dedup_hash)
            WHERE source_external_id IS NULL AND dedup_hash IS NOT NULL AND deleted_at IS NULL
        """)
        # Recreate views
        conn.execute("""
            CREATE VIEW v_active_entries AS
            SELECT * FROM expense_entries WHERE deleted_at IS NULL
        """)
        conn.execute("""
            CREATE VIEW v_monthly_by_category AS
            SELECT strftime('%Y-%m', occurred_at) AS month,
                   kind, category,
                   COUNT(*) AS n,
                   SUM(amount) AS total
            FROM v_active_entries
            GROUP BY 1, 2, 3
        """)
        conn.execute("""
            CREATE VIEW v_yearly_by_category AS
            SELECT strftime('%Y', occurred_at) AS year,
                   kind, category,
                   COUNT(*) AS n,
                   SUM(amount) AS total
            FROM v_active_entries
            GROUP BY 1, 2, 3
        """)
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_payment_method(pm: str | None) -> str | None:
    if pm is None:
        return None
    # P4: accept wechat:subtype format from wechat importer
    if pm.strip().lower().startswith("wechat:"):
        return pm.strip()
    canonical = _PAYMENT_CANONICAL.get(pm.strip().lower())
    if canonical:
        return canonical
    # exact canonical match
    if pm.strip().lower() in {k.lower() for k in _PAYMENT_ALIASES}:
        return pm.strip().lower()
    # P5: accept any payment method string (unified importer passes raw values)
    return pm.strip()


_TP_CHANNELS = {"alipay", "wechat", "meituan", "douyin_pay"}
TP_COLLISION_TIME_THRESHOLD_SECONDS = 5


def _infer_source_channel(payment_method: str | None, source_text: str | None, tags: str | None) -> str:
    """Infer the import channel used for cross-source dedup."""
    source = (source_text or "").strip().lower()
    if source.startswith("alipay") or "alipay import:" in source:
        return "alipay"
    if source.startswith("wechat") or "wechat import:" in source:
        return "wechat"
    if source.startswith("meituan") or "meituan" in source:
        return "meituan"
    if source.startswith("douyin") or "douyin" in source:
        return "douyin_pay"
    if source.startswith(("credit_card", "debit_card")):
        return "card"

    pm = (payment_method or "").strip().lower()
    if pm == "alipay" or "支付宝" in pm:
        return "alipay"
    if pm == "wechat" or pm.startswith("wechat:") or "微信" in pm:
        return "wechat"
    if any(token in pm for token in ("信用卡", "储蓄卡", "借记卡", "银行卡", "bank.", "cmb", "credit_card", "debit_card")):
        return "card"

    tag_text = (tags or "").strip().lower()
    if "alipay" in tag_text:
        return "alipay"
    if "wechat" in tag_text:
        return "wechat"
    if any(token in tag_text for token in ("credit_card", "debit_card", "credit_card_cmb")):
        return "card"

    return "unknown"


def _semantic_match(card_merchant: str | None, tp_merchants: list[str]) -> tuple[bool, str]:
    """Return whether card and third-party merchant labels describe the same business."""
    card = (card_merchant or "").strip().lower()
    tps = [(m or "").strip().lower() for m in tp_merchants if (m or "").strip()]
    if not card or not tps:
        return False, "missing_merchant"

    for prefix in ("支付宝-", "财付通-", "微信支付-", "美团支付-", "alipay-", "wechat-"):
        if card.startswith(prefix):
            card = card[len(prefix):].strip()
            break

    if card and any(card == tp or card in tp or tp in card for tp in tps):
        return True, "merchant_substring"

    platform_tokens = ("淘宝", "天猫", "1688", "美团", "京东", "拼多多", "抖音", "盒马", "饿了么", "星巴克", "肯德基", "大润发", "永辉", "山姆", "costco", "航旅纵横")
    for token in platform_tokens:
        if token in card:
            tp_hits = sum(1 for tp in tps if token in tp)
            if tp_hits * 2 >= len(tps):
                return True, f"platform_{token}"

    if len(tps) > 1 and len(set(tps)) == 1:
        return True, "all_tp_same_merchant"

    token_sets = [set(re.split(r"\s+", tp)) for tp in tps]
    if len(token_sets) > 1:
        common = set.intersection(*token_sets)
        common = {token for token in common if len(token) >= 2}
        if common:
            return True, f"tp_share_token_{sorted(common)[0][:20]}"

    return False, "semantic_mismatch"


def _is_tp_channel(channel: str) -> bool:
    return channel in _TP_CHANNELS


def _abs_delta_seconds(ts_a: str | None, ts_b: str | None) -> int | None:
    """Return absolute seconds between two ISO-8601 timestamps, or None."""
    if not ts_a or not ts_b:
        return None
    try:
        from datetime import datetime

        a = datetime.fromisoformat(ts_a)
        b = datetime.fromisoformat(ts_b)
        return abs(int((a - b).total_seconds()))
    except (ValueError, TypeError):
        return None


def _looks_like_refund(status: str | None, kind: str | None, note: str | None, merchant: str | None) -> bool:
    text = f"{note or ''} {merchant or ''}".lower()
    return status == "refunded" or (kind == "income" and ("退款" in text or "refund" in text))


def _card_tail_from_text(*values: str | None) -> str | None:
    text = " ".join(v or "" for v in values)
    for pattern in (
        r"card_tail:(\d{4})",
        r"linked:credit_card:(\d{4})",
        r"account:(\d{4})",
        r"counter_tail:(\d{4})",
        r"(?:卡尾|尾号)\s*(\d{4})",
        r"[（(](\d{4})[）)]",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def _shared_card_tail(new_row: sqlite3.Row | None, existing_row: sqlite3.Row) -> bool:
    if new_row is None:
        return False
    new_tail = _card_tail_from_text(new_row["payment_method"], new_row["tags"], new_row["note"])
    existing_tail = _card_tail_from_text(existing_row["payment_method"], existing_row["tags"], existing_row["note"])
    return bool(new_tail and existing_tail and new_tail == existing_tail)


def _semantic_match_aggregate(card_merchant: str | None, tp_merchants: list[str]) -> tuple[bool, str]:
    tps = [(m or "").strip().lower() for m in tp_merchants if (m or "").strip()]
    if len(tps) > 1 and len(set(tps)) == 1:
        return True, "all_tp_same_merchant"
    return False, "aggregate_requires_single_tp_merchant"


def _merge_tags(existing: str | None, to_add: list[str]) -> str:
    parts = [part for part in (existing or "").split(",") if part]
    for tag in to_add:
        if tag and tag not in parts:
            parts.append(tag)
    return "," + ",".join(parts) + "," if parts else ""


def _append_note_once(existing: str | None, suffix: str) -> str:
    if not suffix:
        return existing or ""
    if suffix in (existing or ""):
        return existing or suffix
    if not existing:
        return suffix
    return f"{existing} || {suffix}"


def _row_channel(row: sqlite3.Row) -> str:
    return _infer_source_channel(row["payment_method"], row["source_text"], row["tags"])


def _find_cross_source_candidates(
    conn,
    *,
    new_row_id: int,
    occurred_at: str,
    amount_cents: int,
    kind: str,
    payment_method: str | None,
    merchant: str | None,
    new_channel: str,
    is_refund: bool,
) -> dict:
    """Return a read-only cross-source dedup decision for a newly inserted row."""
    clean = {"outcome": "clean", "twin_ids": [], "reason": "no_cross_source_match", "new_role": "none"}
    day = (occurred_at or "")[:10]
    if not day or amount_cents is None:
        return clean

    new_row = conn.execute(
        """SELECT id, kind, amount_cents, occurred_at, merchant, note, payment_method,
                  tags, source_text, status, deleted_at
           FROM expense_entries WHERE id = ?""",
        (new_row_id,),
    ).fetchone()
    new_status = new_row["status"] if new_row else "success"
    new_note = new_row["note"] if new_row else None
    if new_row is not None:
        is_refund = is_refund or _looks_like_refund(new_status, kind, new_note, merchant)

    if is_refund:
        return {"outcome": "clean", "twin_ids": [], "reason": "refund_auto_resolution_disabled", "new_role": "none"}

    if new_status != "success" or kind != "expense":
        return clean

    same_amount_rows = conn.execute(
        """SELECT id, kind, amount_cents, occurred_at, merchant, note, payment_method,
                  tags, source_text, status
           FROM expense_entries
           WHERE deleted_at IS NULL
             AND id != ?
             AND substr(occurred_at, 1, 10) = ?
             AND amount_cents = ?
             AND kind = ?
             AND status = 'success'""",
        (new_row_id, day, amount_cents, kind),
    ).fetchall()

    if _is_tp_channel(new_channel):
        tp_twins = [r for r in same_amount_rows if _is_tp_channel(_row_channel(r))]
        if tp_twins:
            close_twins = []
            for row in tp_twins:
                delta = _abs_delta_seconds(occurred_at, row["occurred_at"])
                if delta is not None and delta <= TP_COLLISION_TIME_THRESHOLD_SECONDS:
                    close_twins.append(row)
            if close_twins:
                return {
                    "outcome": "cross_tp_collision",
                    "twin_ids": [int(r["id"]) for r in close_twins],
                    "reason": f"same_day_amount_tp_within_{TP_COLLISION_TIME_THRESHOLD_SECONDS}s",
                    "new_role": "none",
                }
        card_twins = [r for r in same_amount_rows if _row_channel(r) == "card"]
        valid = []
        for row in card_twins:
            ok, reason = _semantic_match(row["merchant"], [merchant or ""])
            if not ok and _shared_card_tail(new_row, row):
                ok, reason = True, "shared_card_tail"
            if ok:
                valid.append((row, reason))
        if len(valid) == 1:
            return {"outcome": "shadow_1to1", "twin_ids": [int(valid[0][0]["id"])], "reason": valid[0][1], "new_role": "canonical"}
        if len(valid) > 1:
            return {"outcome": "ambiguous", "twin_ids": [int(r["id"]) for r, _ in valid], "reason": "multiple_card_twins", "new_role": "none"}
        return clean

    if new_channel == "card":
        tp_twins = [r for r in same_amount_rows if _is_tp_channel(_row_channel(r))]
        valid = []
        for row in tp_twins:
            ok, reason = _semantic_match(merchant, [row["merchant"] or ""])
            if not ok and _shared_card_tail(new_row, row):
                ok, reason = True, "shared_card_tail"
            if ok:
                valid.append((row, reason))
        if len(valid) == 1:
            return {"outcome": "shadow_1to1", "twin_ids": [int(valid[0][0]["id"])], "reason": valid[0][1], "new_role": "shadow"}
        if len(valid) > 1:
            return {"outcome": "ambiguous", "twin_ids": [int(r["id"]) for r, _ in valid], "reason": "multiple_tp_twins", "new_role": "none"}

        tp_rows = conn.execute(
            """SELECT id, amount_cents, merchant, payment_method, tags, source_text
               FROM expense_entries
               WHERE deleted_at IS NULL
                 AND id != ?
                 AND substr(occurred_at, 1, 10) = ?
                 AND kind = 'expense'
                 AND status = 'success'
                 AND amount_cents > 0
                 AND amount_cents <= ?""",
            (new_row_id, day, amount_cents),
        ).fetchall()
        tp_rows = [r for r in tp_rows if _is_tp_channel(_row_channel(r))]
        matches = []
        for size in range(2, min(5, len(tp_rows)) + 1):
            for subset in itertools.combinations(tp_rows, size):
                if sum(int(r["amount_cents"] or 0) for r in subset) != amount_cents:
                    continue
                ok, reason = _semantic_match_aggregate(merchant, [r["merchant"] or "" for r in subset])
                if ok:
                    matches.append((subset, reason))
            if matches:
                break
        if len(matches) == 1:
            subset, reason = matches[0]
            return {"outcome": "shadow_Nto1", "twin_ids": [int(r["id"]) for r in subset], "reason": reason, "new_role": "shadow"}
        if len(matches) > 1:
            return {
                "outcome": "ambiguous",
                "twin_ids": sorted({int(r["id"]) for subset, _ in matches for r in subset}),
                "reason": "multiple_aggregate_subsets",
                "new_role": "none",
            }

    return clean


def _add_tags_and_note(conn, row_id: int, tags_to_add: list[str], note_suffix: str | None, now: str) -> None:
    row = conn.execute("SELECT tags, note FROM expense_entries WHERE id = ?", (row_id,)).fetchone()
    if not row:
        return
    tags = _merge_tags(row["tags"], tags_to_add)
    note = _append_note_once(row["note"], note_suffix or "")
    conn.execute(
        "UPDATE expense_entries SET tags = ?, note = ?, updated_at = ? WHERE id = ?",
        (tags or None, note or None, now, row_id),
    )


def _soft_delete_row(conn, row_id: int, now: str) -> None:
    conn.execute(
        "UPDATE expense_entries SET deleted_at = COALESCE(deleted_at, ?), updated_at = ? WHERE id = ?",
        (now, now, row_id),
    )


def _apply_cross_source_resolution(
    conn,
    *,
    new_row_id: int,
    decision: dict,
    now: str,
) -> None:
    """Apply a cross-source dedup decision using only soft-delete and tag/note enrichment."""
    outcome = decision.get("outcome")
    twin_ids = [int(i) for i in decision.get("twin_ids", [])]
    new_role = decision.get("new_role")

    if outcome == "shadow_1to1" and twin_ids:
        if new_role == "canonical":
            card_id = twin_ids[0]
            tp_id = new_row_id
        elif new_role == "shadow":
            card_id = new_row_id
            tp_id = twin_ids[0]
        else:
            return
        _soft_delete_row(conn, card_id, now)
        tp_row = conn.execute("SELECT tags FROM expense_entries WHERE id = ?", (tp_id,)).fetchone()
        if tp_row and f",shadow_card:{card_id}," not in (tp_row["tags"] or ""):
            _add_tags_and_note(conn, tp_id, [f"shadow_card:{card_id}", f"dedup_merged:{now}"], None, now)
        return

    if outcome == "shadow_Nto1" and twin_ids and new_role == "shadow":
        card_id = new_row_id
        _soft_delete_row(conn, card_id, now)
        _add_tags_and_note(conn, card_id, ["aggregate_card"], f"aggregated_to_tp:{','.join(str(i) for i in twin_ids)}", now)
        for tp_id in twin_ids:
            _add_tags_and_note(conn, tp_id, [f"aggregate_part:{card_id}"], f"aggregate_card_id:{card_id}", now)
        return

    if outcome == "refund_shadow" and twin_ids:
        if new_role == "shadow":
            shadow_id = new_row_id
        elif new_role == "canonical":
            shadow_id = twin_ids[0]
        else:
            return
        _soft_delete_row(conn, shadow_id, now)
        _add_tags_and_note(conn, shadow_id, ["refund_shadow"], None, now)


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
    auto_classify: bool = False,
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
        _migrate_v4(conn)
        _migrate_v5(conn)
        _migrate_v6(conn)

        if action == "record":
            return _action_record(
                conn, kind, amount, category, occurred_at, currency,
                merchant, note, payment_method, tags, source_agent, source_text,
                confirm_new_category, auto_classify,
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
    confirm_new_category, auto_classify,
):
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError(f"amount must be > 0, got {amount!r}")
    if (not category or not category.strip()):
        if not auto_classify:
            raise ValueError("category is required (non-empty) when auto_classify=False")
        category = None

    auto_classified = False
    llm_category = None
    llm_confidence = None
    llm_reasoning = None

    category_id, canonical_name = _resolve_category(conn, category or "", kind)
    if category_id is None:
        if not confirm_new_category:
            candidates = _category_candidates(conn, category or "")
            # P3: auto_classify routing
            if auto_classify:
                llm_result = tm_llm.classify_expense(
                    kind=kind,
                    amount=amount,
                    merchant=merchant,
                    note=note,
                    tags=tags,
                    occurred_at=occurred_at,
                )
                if llm_result.get("ok") and llm_result.get("confidence", 0) >= 0.85:
                    # Use LLM-inferred category
                    llm_category = llm_result["category"]
                    llm_confidence = llm_result["confidence"]
                    llm_reasoning = llm_result.get("reasoning", "")
                    category_id, canonical_name = _resolve_category(conn, llm_category, kind)
                    if category_id is not None:
                        # Continue with record logic using LLM category
                        category = llm_category
                        auto_classified = True
                        # Fall through to insert below
                    else:
                        # LLM returned invalid category (shouldn't happen with validation)
                        return {
                            "ok": False,
                            "needs_confirmation": True,
                            "reason": "unknown category",
                            "input": (category or "").strip(),
                            "candidates": candidates or [{"name": "其他", "score": 0.1, "reason": "fallback"}],
                            "llm_attempted": True,
                            "llm_reason": f"LLM returned invalid category: {llm_category}",
                        }
                else:
                    # LLM failed or low confidence: silent fallback to needs_confirmation
                    llm_reason = llm_result.get("reason") or (
                        f"confidence {llm_result.get('confidence', 0):.2f} below threshold"
                        if llm_result.get("ok") else "LLM classification failed"
                    )
                    return {
                        "ok": False,
                        "needs_confirmation": True,
                        "reason": "unknown category",
                        "input": (category or "").strip(),
                        "candidates": candidates or [{"name": "其他", "score": 0.1, "reason": "fallback"}],
                        "hint": "Re-call with category='<canonical>' to use existing, or confirm_new_category=True to create.",
                        "llm_attempted": True,
                        "llm_reason": llm_reason,
                    }
            else:
                # P3: auto_classify=False, maintain status quo
                return {
                    "ok": False,
                    "needs_confirmation": True,
                    "reason": "unknown category",
                    "input": (category or "").strip(),
                    "candidates": candidates or [{"name": "其他", "score": 0.1, "reason": "fallback"}],
                    "hint": "Re-call with category='<canonical>' to use existing, or confirm_new_category=True to create.",
                }
        if category_id is None:
            # create new category on the fly (only when confirm_new_category=True and LLM didn't resolve)
            now = _now_iso()
            conn.execute(
                """INSERT INTO categories (name, kind, aliases, archived, sort_order, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 0, ?, ?)""",
                ((category or "").strip(), kind, "[]", now, now),
            )
            category_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            canonical_name = (category or "").strip()

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
    result = {
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
    if auto_classified:
        result["auto_classified"] = True
        result["llm_category"] = llm_category
        result["llm_confidence"] = llm_confidence
        result["llm_reasoning"] = llm_reasoning
    return result


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
    inserted = 0
    skipped_duplicate = 0
    cross_source_actions: list[dict] = []
    errors: list[dict] = []
    try:
        for i, entry in enumerate(entries):
            kind = entry.get("kind")
            amount = entry.get("amount")
            category = entry.get("category")
            status = entry.get("status", "success")
            if kind not in VALID_KINDS:
                errors.append({"index": i, "error": f"invalid kind: {kind}"})
                continue
            # P5: allow amount=0 for closed/internal_transfer
            if not isinstance(amount, (int, float)) or (amount < 0):
                errors.append({"index": i, "error": f"amount must be >= 0, got {amount}"})
                continue
            if amount == 0 and status not in ("closed", "internal_transfer"):
                errors.append({"index": i, "error": "amount=0 only allowed with status=closed or internal_transfer"})
                continue
            if not category or not category.strip():
                errors.append({"index": i, "error": "category is required"})
                continue

            category_id, canonical_name = _resolve_category(conn, category, kind)
            if category_id is None and not confirm_new_category:
                errors.append({"index": i, "error": f"unknown category '{category}'"})
                continue
            if category_id is None and confirm_new_category:
                try:
                    conn.execute(
                        """INSERT INTO categories (name, kind, aliases, archived, sort_order, created_at, updated_at)
                           VALUES (?, ?, ?, 0, 0, ?, ?)""",
                        (category.strip(), kind, "[]", now, now),
                    )
                    category_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    canonical_name = category.strip()
                except sqlite3.IntegrityError:
                    category_id, canonical_name = _resolve_category(conn, category, kind)
                    if category_id is None:
                        errors.append({"index": i, "error": f"failed to create category '{category}'"})
                        continue

            occurred_at = _normalize_occurred_at(entry.get("occurred_at"))
            currency = (entry.get("currency") or DEFAULT_CURRENCY).upper()
            if currency not in VALID_CURRENCIES:
                errors.append({"index": i, "error": f"invalid currency: {currency}"})
                continue
            pm = _normalize_payment_method(entry.get("payment_method"))
            tags = entry.get("tags")
            tags_str = "," + ",".join(t.strip() for t in tags if t.strip()) + "," if tags else None
            amount_cents = round(float(amount) * 100)

            source_external_id = (entry.get("source_external_id") or "").strip() or None

            # Compute dedup_hash for entries without external_id
            dedup_hash = None
            if not source_external_id:
                occurred_second = (occurred_at or "")[:19]
                note_hash = hashlib.md5((entry.get("note") or "").encode()).hexdigest()[:8]
                fingerprint = "|".join([
                    occurred_second,
                    str(amount_cents),
                    pm or "",
                    (entry.get("merchant") or ""),
                    status or "success",
                    note_hash,
                ])
                dedup_hash = hashlib.sha1(fingerprint.encode()).hexdigest()[:16]

            cur = conn.execute(
                """INSERT OR IGNORE INTO expense_entries
                   (kind, amount, currency, occurred_at, category, category_id, merchant,
                    note, payment_method, tags, source_agent, source_text,
                    created_at, updated_at, amount_cents, source_external_id, dedup_hash, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind, float(amount), currency, occurred_at,
                    canonical_name, category_id, entry.get("merchant"),
                    entry.get("note"), pm, tags_str,
                    entry.get("source_agent", "openclaw"), entry.get("source_text"),
                    now, now, amount_cents, source_external_id, dedup_hash, status,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
                new_row_id = int(cur.lastrowid)
                new_channel = _infer_source_channel(pm, entry.get("source_text"), tags_str)
                is_refund = _looks_like_refund(status, kind, entry.get("note"), entry.get("merchant"))
                decision = _find_cross_source_candidates(
                    conn,
                    new_row_id=new_row_id,
                    occurred_at=occurred_at,
                    amount_cents=amount_cents,
                    kind=kind,
                    payment_method=pm,
                    merchant=entry.get("merchant"),
                    new_channel=new_channel,
                    is_refund=is_refund,
                )
                if decision["outcome"] != "clean":
                    if decision["outcome"] not in {"ambiguous", "cross_tp_collision"}:
                        _apply_cross_source_resolution(conn, new_row_id=new_row_id, decision=decision, now=now)
                    cross_source_actions.append({
                        "index": i,
                        "new_id": new_row_id,
                        "outcome": decision["outcome"],
                        "new_role": decision["new_role"],
                        "twin_ids": decision["twin_ids"],
                        "reason": decision["reason"],
                    })
            else:
                skipped_duplicate += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "ok": True,
        "action": "batch_record",
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "cross_source_actions": cross_source_actions,
        "errors": errors,
    }


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
    # ---- mode=search ----
    query: str | None = None,
) -> dict[str, Any]:
    """Unified read endpoint for expense tracker v2.

    Modes: list, aggregate, trend, compare, anomaly, budget_status,
           categories, merchants, export, sql, search.
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
    if mode == "search":
        return _read_search(
            query, start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted, limit, offset,
        )
    raise ValueError(f"unknown mode: {mode!r}")


def _build_where(
    start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted,
    stats_only: bool = False,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if not include_deleted:
        where.append("deleted_at IS NULL")

    # P5: statistical queries (aggregate/trend/compare/anomaly/budget) exclude non-success
    if stats_only:
        where.append("status = 'success'")

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
        stats_only=True,
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
        stats_only=True,
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
        stats_only=True,
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
                # Use month arithmetic for precise cross-year handling
                current_period = (start_date[:7], end_date[:7])
                # Compute previous month by shifting year/month
                year = int(start_date[:4])
                month = int(start_date[5:7])
                if month == 1:
                    prev_year = year - 1
                    prev_month = 12
                else:
                    prev_year = year
                    prev_month = month - 1
                prev_start = f"{prev_year:04d}-{prev_month:02d}-01"
                # Last day of previous month
                if prev_month == 12:
                    prev_end = f"{prev_year:04d}-12-31"
                else:
                    # First day of next month minus 1 day
                    next_month = prev_month + 1
                    next_month_dt = datetime.datetime(prev_year, next_month, 1)
                    last_day = (next_month_dt - datetime.timedelta(days=1)).day
                    prev_end = f"{prev_year:04d}-{prev_month:02d}-{last_day:02d}"
            else:  # qoq
                # Use quarter arithmetic (shift by 3 months)
                current_period = (start_date[:4], end_date[:4])
                year = int(start_date[:4])
                month = int(start_date[5:7])
                # Shift back 3 months
                if month <= 3:
                    prev_year = year - 1
                    prev_month = month + 9
                else:
                    prev_year = year
                    prev_month = month - 3
                prev_start = f"{prev_year:04d}-{prev_month:02d}-01"
                # Last day of previous quarter's last month
                if prev_month in [3, 6, 9, 12]:
                    quarter_end_month = prev_month
                else:
                    # Find end of quarter (3, 6, 9, 12)
                    quarter_end_month = ((prev_month + 2) // 3) * 3
                if quarter_end_month == 12:
                    prev_end = f"{prev_year:04d}-12-31"
                else:
                    next_month_dt = datetime.datetime(prev_year, quarter_end_month + 1, 1)
                    last_day = (next_month_dt - datetime.timedelta(days=1)).day
                    prev_end = f"{prev_year:04d}-{quarter_end_month:02d}-{last_day:02d}"
        else:
            prev_start = prev_end = None

        prev_where, prev_params = _build_where(
            prev_start, prev_end, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
            stats_only=True,
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

        # Normalize dates to YYYY-MM-DD format (strip timezone info if present)
        def _normalize_date(d):
            if d and "T" in d:
                return d.split("T")[0]
            return d

        end_date = _normalize_date(end_date)
        start_date = _normalize_date(start_date)

        # historical stats from window
        hist_end = end_date
        hist_start = (datetime.datetime.strptime(hist_end, "%Y-%m-%d") - datetime.timedelta(days=anomaly_window_days)).strftime("%Y-%m-%d")
        hist_where, hist_params = _build_where(
            hist_start, hist_end, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
            stats_only=True,
        )
        # Get sample size and compute sample variance
        count_stats = conn.execute(
            f"SELECT COUNT(*) AS n FROM expense_entries WHERE {hist_where}",
            hist_params,
        ).fetchone()
        n = int(count_stats["n"] or 0)
        if n < 2:
            return {
                "ok": False,
                "reason": "insufficient sample",
                "n": n,
                "mode": "anomaly",
            }
        stats = conn.execute(
            f"SELECT AVG(amount) AS mean, SUM(amount*amount) AS sum_sq, SUM(amount) AS sum_amt FROM expense_entries WHERE {hist_where}",
            hist_params,
        ).fetchone()
        mean = float(stats["mean"] or 0)
        sum_sq = float(stats["sum_sq"] or 0)
        sum_amt = float(stats["sum_amt"] or 0)
        # Sample variance: var = (SUM(x^2) - n * mean^2) / (n - 1)
        var = (sum_sq - n * mean * mean) / (n - 1) if n > 1 else 0
        std = var ** 0.5 if var > 0 else 0
        upper = mean + anomaly_sigma * std
        lower = mean - anomaly_sigma * std if mean - anomaly_sigma * std > 0 else 0

        # Find anomalies in current period
        curr_where, curr_params = _build_where(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
            stats_only=True,
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
            stats_only=True,
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
        _migrate_v4(conn)
        _migrate_v5(conn)
        _migrate_v6(conn)
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
            # Dynamic full fields with fixed column order
            column_order = ["id", "kind", "amount", "currency", "category", "merchant", "payment_method", "occurred_at", "note", "tags", "deleted_at", "created_at"]
            if data:
                available_columns = [col for col in column_order if col in data[0].keys()]
                header = "| " + " | ".join(available_columns) + " |"
                separator = "| " + " | ".join("---" for _ in available_columns) + " |"
                lines = [header, separator]
                for r in data:
                    row_values = [str(r.get(col, "")) for col in available_columns]
                    lines.append("| " + " | ".join(row_values) + " |")
            else:
                lines = ["| id |", "|---|"]
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
    if _SQL_FORBIDDEN_RE.search(sql):
        return {"ok": False, "reason": "sql validation failed", "detail": "forbidden keyword detected"}
    if not _SQL_ALLOWED_START_RE.match(sql):
        return {"ok": False, "reason": "sql validation failed", "detail": "SQL must start with SELECT, WITH, or PRAGMA table_info/index_info"}

    conn = _get_conn()
    try:
        _ensure_schema(conn)
        try:
            rows = conn.execute(sql, (sql_params or {})).fetchall()
        except Exception as e:
            return {"ok": False, "reason": "sql execution failed", "detail": str(e)}
        # Return rows as list of dicts for consistency with other modes
        return {
            "ok": True,
            "mode": "sql",
            "rows": [dict(r) for r in rows],
            "row_count": len(rows),
        }
    finally:
        conn.close()


def _read_search(
    query, start_date, end_date, kind, category, merchant, payment_method,
    tags, min_amount, max_amount, include_deleted, limit, offset,
):
    if not query or not query.strip():
        return {"ok": False, "reason": "query is required for mode=search"}

    conn = _get_conn()
    try:
        _ensure_schema(conn)

        q = query.strip()
        # Substring search on note + tags (LIKE handles CJK natively;
        # FTS5 default tokenizer can't tokenize 2-char CJK substrings).
        like_pat = f"%{q}%"

        where, params = _build_where(
            start_date, end_date, kind, category, merchant, payment_method,
            tags, min_amount, max_amount, include_deleted,
        )

        search_clause = "(e.note LIKE ? OR e.tags LIKE ?)"
        search_params = [like_pat, like_pat]

        if where:
            sql = f"""
                SELECT e.* FROM expense_entries e
                WHERE {search_clause} AND {where}
                ORDER BY e.occurred_at DESC
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(sql, search_params + params + [limit, offset]).fetchall()
        else:
            sql = f"""
                SELECT e.* FROM expense_entries e
                WHERE {search_clause}
                ORDER BY e.occurred_at DESC
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(sql, search_params + [limit, offset]).fetchall()

        data = [dict(r) for r in rows]
        return {
            "ok": True,
            "mode": "search",
            "query": query,
            "row_count": len(data),
            "rows": data,
        }
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
