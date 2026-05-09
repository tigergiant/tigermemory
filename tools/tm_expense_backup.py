#!/usr/bin/env python3
"""
tools/tm_expense_backup.py — automatic backup CLI for expense tracker ledger.

Usage:
    python tools/tm_expense_backup.py [--ledger PATH]
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

# Import DB_PATH from tm_expense so we stay consistent
from tm_expense import DB_PATH, _TZ_CN


def backup(ledger_path: Path | None = None, keep: int = 30) -> dict:
    db = ledger_path or DB_PATH
    if not db.exists():
        return {"ok": False, "reason": "ledger does not exist"}

    # Create backups directory
    backup_dir = db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Generate backup filename with unique timestamp
    ts = datetime.datetime.now(_TZ_CN).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"ledger-{ts}.db"
    
    # If backup file already exists (rare), add microseconds
    if backup_path.exists():
        ts = datetime.datetime.now(_TZ_CN).strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / f"ledger-{ts}.db"

    # Perform VACUUM INTO backup
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(f"VACUUM INTO '{backup_path}'")
        conn.commit()
    finally:
        conn.close()

    # Verify backup
    if not backup_path.exists():
        return {"ok": False, "reason": "backup file not created"}

    # Verify backup is valid SQLite database
    try:
        verify_conn = sqlite3.connect(str(backup_path))
        verify_conn.execute("SELECT COUNT(*) FROM sqlite_master")
        verify_conn.close()
    except Exception as e:
        return {"ok": False, "reason": f"backup verification failed: {e}"}

    # Verify row count matches source
    try:
        source_conn = sqlite3.connect(str(db))
        source_count = source_conn.execute("SELECT COUNT(*) FROM expense_entries").fetchone()[0]
        source_conn.close()

        backup_conn = sqlite3.connect(str(backup_path))
        backup_count = backup_conn.execute("SELECT COUNT(*) FROM expense_entries").fetchone()[0]
        backup_conn.close()

        if source_count != backup_count:
            return {"ok": False, "reason": f"row count mismatch: source={source_count}, backup={backup_count}"}
    except Exception as e:
        return {"ok": False, "reason": f"row count verification failed: {e}"}

    # Rolling retention: keep last N backups
    backups = sorted(backup_dir.glob("ledger-*.db"), reverse=True)
    if len(backups) > keep:
        for old_backup in backups[keep:]:
            old_backup.unlink()

    return {
        "ok": True,
        "backup_path": str(backup_path),
        "backup_size": backup_path.stat().st_size,
        "retained": len(backups[:keep]) if len(backups) > keep else len(backups),
        "deleted": len(backups) - keep if len(backups) > keep else 0,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backup expense tracker ledger")
    ap.add_argument("--ledger", type=Path, default=None, help="Path to ledger.sqlite (default: data/expense_tracker/ledger.sqlite)")
    ap.add_argument("--keep", type=int, default=30, help="Number of backups to retain (default: 30)")
    args = ap.parse_args()
    result = backup(ledger_path=args.ledger, keep=args.keep)
    print(f"ok: {result['ok']}")
    if result.get("ok"):
        print(f"backup: {result['backup_path']}")
        print(f"size: {result['backup_size']} bytes")
        print(f"retained: {result['retained']}")
        print(f"deleted: {result['deleted']}")
    else:
        print(f"reason: {result.get('reason')}")
    sys.exit(0 if result.get("ok") else 1)
