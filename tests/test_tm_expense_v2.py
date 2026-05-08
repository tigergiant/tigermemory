#!/usr/bin/env python3
"""tests/test_tm_expense_v2.py — P0 v2 expense tracker tests."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

# Add tools/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import tm_expense


def _temp_db(monkeypatch) -> Path:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    import os
    os.close(fd)
    p = Path(path)
    monkeypatch.setattr(tm_expense, "DB_PATH", p)
    monkeypatch.setattr(tm_expense, "DATA_DIR", p.parent)
    return p


# ------------------------------------------------------------------
# Migration
# ------------------------------------------------------------------


def test_migration_idempotent(monkeypatch):
    db = _temp_db(monkeypatch)
    # Create a v1-like db manually
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE expense_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'CNY',
            occurred_at TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            merchant TEXT,
            note TEXT,
            payment_method TEXT,
            source_agent TEXT DEFAULT 'openclaw',
            source_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO expense_entries VALUES (1,'expense',36.5,'CNY','2026-05-07T12:00:00+08:00','餐饮',NULL,NULL,NULL,'openclaw',NULL,'2026-05-07T12:00:00+08:00','2026-05-07T12:00:00+08:00')")
    conn.commit()
    conn.close()

    import tm_expense_migrate_v2 as mig
    monkeypatch.setattr(mig, "DB_PATH", db)
    result = mig.migrate()
    assert result["ok"] is True
    assert result["user_version"] == 2
    assert result["entries_backfilled"] == 1

    # Run again — idempotent
    result2 = mig.migrate()
    assert result2["ok"] is True
    assert "already at v2" in result2["note"]

    # Verify schema
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(expense_entries)")}
    assert {"category_id", "merchant_id", "tags", "deleted_at", "amount_cents"} <= cols
    conn.close()


# ------------------------------------------------------------------
# Record + alias
# ------------------------------------------------------------------


def test_expense_record_alias_with_category_normalization(monkeypatch):
    db = _temp_db(monkeypatch)
    result = tm_expense.expense_record(
        kind="expense", amount=36.5, category="吃饭",
    )
    assert result["ok"] is True
    assert result["normalized"]["category"] == "餐饮"
    assert result["normalized"]["category_id"] is not None


def test_expense_write_unknown_category_needs_confirmation(monkeypatch):
    db = _temp_db(monkeypatch)
    result = tm_expense.expense_write(
        action="record", kind="expense", amount=10, category="未知新类别",
    )
    assert result["ok"] is False
    assert result["needs_confirmation"] is True
    assert result["reason"] == "unknown category"


def test_expense_write_confirm_new_category(monkeypatch):
    db = _temp_db(monkeypatch)
    result = tm_expense.expense_write(
        action="record", kind="expense", amount=10, category="未知新类别",
        confirm_new_category=True,
    )
    assert result["ok"] is True
    assert result["normalized"]["category"] == "未知新类别"


# ------------------------------------------------------------------
# Update
# ------------------------------------------------------------------


def test_update_existing(monkeypatch):
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮")
    eid = r["id"]
    upd = tm_expense.expense_write(action="update", id=eid, amount=20)
    assert upd["ok"] is True
    assert upd["action"] == "update"


def test_update_nonexistent(monkeypatch):
    db = _temp_db(monkeypatch)
    upd = tm_expense.expense_write(action="update", id=99999, amount=20)
    assert upd["ok"] is False
    assert "not found" in upd["error"]


# ------------------------------------------------------------------
# Delete + restore
# ------------------------------------------------------------------


def test_delete_and_restore(monkeypatch):
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮")
    eid = r["id"]

    # delete
    d = tm_expense.expense_write(action="delete", id=eid)
    assert d["ok"] is True

    # default query hides it
    q = tm_expense.expense_query(group_by="none")
    assert q["total_count"] == 0

    # list with include_deleted shows it
    lst = tm_expense.expense_read(mode="list", include_deleted=True)
    assert lst["total_count"] == 1

    # restore
    rest = tm_expense.expense_write(action="restore", id=eid)
    assert rest["ok"] is True
    lst2 = tm_expense.expense_read(mode="list")
    assert lst2["total_count"] == 1


# ------------------------------------------------------------------
# Batch record
# ------------------------------------------------------------------


def test_batch_record_success(monkeypatch):
    db = _temp_db(monkeypatch)
    result = tm_expense.expense_write(
        action="batch_record",
        entries=[
            {"kind": "expense", "amount": 10, "category": "餐饮"},
            {"kind": "expense", "amount": 20, "category": "交通"},
        ],
    )
    assert result["ok"] is True
    assert result["count"] == 2


def test_batch_record_rollback_on_failure(monkeypatch):
    db = _temp_db(monkeypatch)
    try:
        tm_expense.expense_write(
            action="batch_record",
            entries=[
                {"kind": "expense", "amount": 10, "category": "餐饮"},
                {"kind": "expense", "amount": -1, "category": "交通"},
            ],
        )
        assert False, "should raise"
    except ValueError as e:
        assert "amount must be > 0" in str(e)

    # Verify nothing was written
    lst = tm_expense.expense_read(mode="list")
    assert lst["total_count"] == 0


# ------------------------------------------------------------------
# Read modes
# ------------------------------------------------------------------


def test_read_list_pagination(monkeypatch):
    db = _temp_db(monkeypatch)
    for i in range(5):
        tm_expense.expense_write(action="record", kind="expense", amount=10 + i, category="餐饮")
    lst = tm_expense.expense_read(mode="list", limit=3)
    assert lst["ok"] is True
    assert len(lst["rows"]) == 3
    assert lst["total_count"] == 5


def test_read_aggregate_multigroup(monkeypatch):
    db = _temp_db(monkeypatch)
    tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮")
    tm_expense.expense_write(action="record", kind="expense", amount=20, category="交通")
    tm_expense.expense_write(action="record", kind="income", amount=100, category="工资")
    agg = tm_expense.expense_read(mode="aggregate", group_by=["kind", "category"])
    assert agg["ok"] is True
    assert len(agg["groups"]) == 3


def test_read_trend_monthly(monkeypatch):
    db = _temp_db(monkeypatch)
    from datetime import datetime, timedelta
    base = datetime(2026, 1, 15, 12, 0, 0)
    for i in range(6):
        dt = (base + timedelta(days=i * 30)).isoformat(timespec="seconds")
        tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮", occurred_at=dt)
    tr = tm_expense.expense_read(mode="trend", bucket="month")
    assert tr["ok"] is True
    assert len(tr["buckets"]) == 6


# ------------------------------------------------------------------
# SQL mode
# ------------------------------------------------------------------


def test_sql_select_allowed(monkeypatch):
    db = _temp_db(monkeypatch)
    tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮")
    res = tm_expense.expense_read(mode="sql", sql="SELECT 1 as n")
    assert res["ok"] is True
    assert res["rows"] == [[1]]


def test_sql_drop_rejected(monkeypatch):
    db = _temp_db(monkeypatch)
    res = tm_expense.expense_read(mode="sql", sql="DROP TABLE expense_entries")
    assert res["ok"] is False
    assert res["reason"] == "sql validation failed"


def test_sql_multistatement_rejected(monkeypatch):
    db = _temp_db(monkeypatch)
    res = tm_expense.expense_read(
        mode="sql",
        sql="SELECT 1; DROP TABLE expense_entries",
    )
    assert res["ok"] is False
    assert "semicolons" in res["detail"]


def test_sql_help(monkeypatch):
    db = _temp_db(monkeypatch)
    res = tm_expense.expense_read(mode="sql", sql=":help")
    assert res["ok"] is True
    assert "expense_entries" in res["help"]["tables"]


# ------------------------------------------------------------------
# Payment normalization
# ------------------------------------------------------------------


def test_payment_method_normalization(monkeypatch):
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮", payment_method="微信")
    assert r["ok"] is True
    assert r["normalized"]["payment_method"] == "wechat"


def test_payment_method_invalid(monkeypatch):
    db = _temp_db(monkeypatch)
    try:
        tm_expense.expense_write(action="record", kind="expense", amount=10, category="餐饮", payment_method="Bitcoin")
        assert False, "should raise"
    except ValueError as e:
        assert "unknown payment_method" in str(e)
