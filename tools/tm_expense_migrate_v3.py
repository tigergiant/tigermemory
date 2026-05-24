#!/usr/bin/env python3
"""
tools/tm_expense_migrate_v3.py — idempotent migration from v2 to v3 schema (FTS5 search).

Usage:
    python tools/tm_expense_migrate_v3.py [--dry-run] [--ledger PATH]
Inputs: Expense CSV exports, backup files, local SQLite data, or CLI import/filter arguments.
Outputs: Normalized expense records, backup artifacts, digest reports, or migration results.
Depends-on (must-have): Python stdlib, local expense database/files, and tm_expense shared helpers.
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
        return {"ok": True, "note": "ledger does not exist yet; v3 schema will be created on first use"}

    # Backup
    ts = datetime.datetime.now(_TZ_CN).strftime("%Y%m%d_%H%M%S")
    backup = db.parent / f"ledger.sqlite.pre-v3-{ts}"
    if not dry_run:
        shutil.copy2(str(db), str(backup))

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if user_version >= 3:
            return {"ok": True, "note": f"already at v{user_version}, nothing to do"}

        if dry_run:
            return {"ok": True, "dry_run": True, "would_migrate_from": user_version}

        conn.execute("BEGIN")

        # 1. Create FTS5 virtual table
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS expense_entries_fts
            USING fts5(note, tags, content='expense_entries', content_rowid='id')
        """)

        # 2. Create FTS5 triggers for auto-sync
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

        # 3. Backfill existing entries to FTS table
        entries = conn.execute("SELECT id, note, tags FROM expense_entries").fetchall()
        for row in entries:
            row_id, note, tags = row["id"], row["note"], row["tags"]
            conn.execute(
                "INSERT INTO expense_entries_fts(rowid, note, tags) VALUES (?, ?, ?)",
                (row_id, note, tags),
            )

        # 4. Version bump
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

        return {
            "ok": True,
            "user_version": 3,
            "backup": str(backup.name),
            "entries_backfilled": len(entries),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Migrate expense tracker ledger to v3 schema (FTS5 search)")
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen without writing")
    ap.add_argument("--ledger", type=Path, default=None, help="Path to ledger.sqlite (default: data/expense_tracker/ledger.sqlite)")
    args = ap.parse_args()
    result = migrate(dry_run=args.dry_run, ledger_path=args.ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)
