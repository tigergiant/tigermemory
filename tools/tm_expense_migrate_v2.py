#!/usr/bin/env python3
"""
tools/tm_expense_migrate_v2.py — idempotent migration from v1 to v2 schema.

Usage:
    python tools/tm_expense_migrate_v2.py [--dry-run] [--ledger PATH]
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sqlite3
import sys
from pathlib import Path

# Import DB_PATH from tm_expense so we stay consistent
from tm_expense import DB_PATH, _TZ_CN


def _now_iso() -> str:
    return datetime.datetime.now(_TZ_CN).isoformat(timespec="seconds")


def migrate(dry_run: bool = False, ledger_path: Path | None = None) -> dict:
    db = ledger_path or DB_PATH
    if not db.exists():
        return {"ok": True, "note": "ledger does not exist yet; v2 schema will be created on first use"}

    # Backup
    ts = datetime.datetime.now(_TZ_CN).strftime("%Y%m%d_%H%M%S")
    backup = db.parent / f"ledger.sqlite.pre-v2-{ts}"
    if not dry_run:
        shutil.copy2(str(db), str(backup))

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 2:
            return {"ok": True, "note": f"already at v{user_version}, nothing to do"}

        if dry_run:
            return {"ok": True, "dry_run": True, "would_migrate_from": user_version}

        conn.execute("BEGIN")

        # 1. ALTER TABLE add columns
        for col, dtype in (
            ("category_id", "INTEGER"),
            ("merchant_id", "INTEGER"),
            ("tags", "TEXT"),
            ("deleted_at", "TEXT"),
            ("amount_cents", "INTEGER"),
        ):
            try:
                conn.execute(f"ALTER TABLE expense_entries ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

        # 2. CREATE new tables
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

        # 3. Indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_deleted ON expense_entries(deleted_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_kind_cat_t ON expense_entries(kind, category, occurred_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_tags ON expense_entries(tags)")

        # 4. Views
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

        # 5. Seed categories
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

        # 6. Backfill existing entries
        entries = conn.execute("SELECT id, category, amount FROM expense_entries").fetchall()
        for row in entries:
            row_id, cat, amount = row["id"], row["category"], row["amount"]
            # Find matching category
            cat_row = conn.execute(
                "SELECT id, name FROM categories WHERE name = ? OR aliases LIKE ?",
                (cat, f'%"{cat}"%'),
            ).fetchone()
            if cat_row:
                cat_id, canonical = cat_row["id"], cat_row["name"]
            else:
                # Create an unseeded category for this entry
                kind_guess = "expense"
                conn.execute(
                    """INSERT INTO categories (name, kind, aliases, archived, sort_order, created_at, updated_at)
                       VALUES (?, ?, ?, 0, 0, ?, ?)""",
                    (cat, kind_guess, "[]", now, now),
                )
                cat_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                canonical = cat
            conn.execute(
                "UPDATE expense_entries SET category_id = ?, category = ?, amount_cents = ? WHERE id = ?",
                (cat_id, canonical, round(float(amount) * 100), row_id),
            )

        # 7. Version bump
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

        return {
            "ok": True,
            "user_version": 2,
            "backup": str(backup.name),
            "entries_backfilled": len(entries),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Migrate expense tracker ledger to v2 schema")
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen without writing")
    ap.add_argument("--ledger", type=Path, default=None, help="Path to ledger.sqlite (default: data/expense_tracker/ledger.sqlite)")
    args = ap.parse_args()
    result = migrate(dry_run=args.dry_run, ledger_path=args.ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)
