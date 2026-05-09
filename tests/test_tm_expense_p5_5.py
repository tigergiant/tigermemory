#!/usr/bin/env python3
"""tests/test_tm_expense_p5_5.py — P5.5 dedup_hash distinguish refund/original/batch tests."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import tm_expense


def _temp_db(monkeypatch) -> Path:
    import os
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    p = Path(path)
    monkeypatch.setattr(tm_expense, "DB_PATH", p)
    monkeypatch.setattr(tm_expense, "DATA_DIR", p.parent)
    conn = tm_expense._get_conn()
    tm_expense._ensure_schema(conn)
    tm_expense._seed_categories(conn)
    tm_expense._migrate_v4(conn)
    tm_expense._migrate_v5(conn)
    conn.close()
    return p


# ------------------------------------------------------------------
# 1. Refund vs original: same minute, same merchant, same amount → distinguished by status
# ------------------------------------------------------------------


def test_dedup_distinguishes_refund_from_original(monkeypatch):
    """¥0.10 expense + ¥0.10 refund same minute same merchant → both INSERT (status differs)."""
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(
        action="batch_record",
        entries=[
            {
                "kind": "expense", "amount": 0.10, "category": "购物",
                "occurred_at": "2026-01-28T20:58:41", "merchant": "x***7",
                "note": "原交易-Clawbot教程", "payment_method": "支付宝",
                "status": "success", "source_agent": "test",
            },
            {
                "kind": "income", "amount": 0.10, "category": "购物",
                "occurred_at": "2026-01-28T20:58:29", "merchant": "x***7",
                "note": "退款-Clawbot教程", "payment_method": "支付宝",
                "status": "refunded", "source_agent": "test",
            },
        ],
    )
    assert r["ok"] is True
    assert r["inserted"] == 2
    assert r["skipped_duplicate"] == 0


# ------------------------------------------------------------------
# 2. Batch refunds within same minute: different seconds → distinguished by [:19]
# ------------------------------------------------------------------


def test_dedup_distinguishes_batch_refunds_within_minute(monkeypatch):
    """2 × ¥299.50 refunded same minute, seconds differ (15:24:06 vs 15:24:17) → both INSERT."""
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(
        action="batch_record",
        entries=[
            {
                "kind": "income", "amount": 299.50, "category": "交通",
                "occurred_at": "2026-01-23T15:24:06", "merchant": "铁路12306",
                "note": "退款-火车票", "payment_method": "支付宝",
                "status": "refunded", "source_agent": "test",
            },
            {
                "kind": "income", "amount": 299.50, "category": "交通",
                "occurred_at": "2026-01-23T15:24:17", "merchant": "铁路12306",
                "note": "退款-火车票", "payment_method": "支付宝",
                "status": "refunded", "source_agent": "test",
            },
        ],
    )
    assert r["ok"] is True
    assert r["inserted"] == 2
    assert r["skipped_duplicate"] == 0


# ------------------------------------------------------------------
# 3. True duplicates: all fields identical → 2nd IGNORE
# ------------------------------------------------------------------


def test_dedup_still_blocks_true_duplicates(monkeypatch):
    """2 rows with all fields identical (note + status) → 2nd deduped."""
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(
        action="batch_record",
        entries=[
            {
                "kind": "expense", "amount": 12.34, "category": "餐饮",
                "occurred_at": "2026-05-01T12:00:00", "merchant": "麦当劳",
                "note": "巨无霸套餐", "payment_method": "微信支付",
                "status": "success", "source_agent": "test",
            },
            {
                "kind": "expense", "amount": 12.34, "category": "餐饮",
                "occurred_at": "2026-05-01T12:00:00", "merchant": "麦当劳",
                "note": "巨无霸套餐", "payment_method": "微信支付",
                "status": "success", "source_agent": "test",
            },
        ],
    )
    assert r["ok"] is True
    assert r["inserted"] == 1
    assert r["skipped_duplicate"] == 1


# ------------------------------------------------------------------
# 4. Same merchant/amount/status but different note → distinguished by note md5
# ------------------------------------------------------------------


def test_dedup_distinguishes_different_products_same_merchant(monkeypatch):
    """Same minute, same merchant, same amount, same status, different note → both INSERT."""
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(
        action="batch_record",
        entries=[
            {
                "kind": "expense", "amount": 50.00, "category": "购物",
                "occurred_at": "2026-05-01T10:00:00", "merchant": "京东",
                "note": "购买鼠标", "payment_method": "微信支付",
                "status": "success", "source_agent": "test",
            },
            {
                "kind": "expense", "amount": 50.00, "category": "购物",
                "occurred_at": "2026-05-01T10:00:00", "merchant": "京东",
                "note": "购买键盘", "payment_method": "微信支付",
                "status": "success", "source_agent": "test",
            },
        ],
    )
    assert r["ok"] is True
    assert r["inserted"] == 2
    assert r["skipped_duplicate"] == 0


# ------------------------------------------------------------------
# 5. v4 migration idempotent with new algo
# ------------------------------------------------------------------


def test_v4_migration_idempotent_with_new_algo(monkeypatch):
    """Simulate v4 DB with existing dedup_hash → migrate doesn't error or recompute."""
    db = _temp_db(monkeypatch)
    # Insert a row with a pre-existing dedup_hash (old algo style)
    conn = tm_expense._get_conn()
    conn.execute(
        """INSERT INTO expense_entries
           (kind, amount, currency, occurred_at, category, category_id, merchant,
            note, payment_method, source_agent, source_text,
            created_at, updated_at, amount_cents, dedup_hash, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "expense", 10.00, "CNY", "2026-01-01T12:00:00", "餐饮", 1, "老店",
            "老交易", "支付宝", "test", "test",
            "2026-01-01T00:00:00", "2026-01-01T00:00:00", 1000, "abc123def4567890", "success",
        ),
    )
    conn.commit()
    conn.close()

    # Run migration — should not error, should not change existing hash
    conn = tm_expense._get_conn()
    tm_expense._migrate_v4(conn)
    row = conn.execute(
        "SELECT dedup_hash FROM expense_entries WHERE merchant = '老店'"
    ).fetchone()
    assert row is not None
    assert row["dedup_hash"] == "abc123def4567890"  # unchanged
    conn.close()
