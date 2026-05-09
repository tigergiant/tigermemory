#!/usr/bin/env python3
"""tests/test_tm_expense_p4.py — P4 import + dedup tests."""

import csv
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import tm_expense
import tm_expense_import_alipay
import tm_expense_import_wechat


def _temp_db(monkeypatch) -> Path:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    import os
    os.close(fd)
    p = Path(path)
    monkeypatch.setattr(tm_expense, "DB_PATH", p)
    monkeypatch.setattr(tm_expense, "DATA_DIR", p.parent)
    conn = tm_expense._get_conn()
    tm_expense._ensure_schema(conn)
    tm_expense._seed_categories(conn)
    tm_expense._migrate_v4(conn)
    conn.close()
    return p


# ------------------------------------------------------------------
# migrate_v4 tests
# ------------------------------------------------------------------


def test_migrate_v4_idempotent(monkeypatch):
    """Running _migrate_v4 twice does not error."""
    db = _temp_db(monkeypatch)
    conn = tm_expense._get_conn()
    tm_expense._migrate_v4(conn)  # first run
    tm_expense._migrate_v4(conn)  # second run — should be no-op
    conn.close()


def test_migrate_v4_backfills_dedup_hash(monkeypatch):
    """After migration, historical rows have non-null dedup_hash."""
    db = _temp_db(monkeypatch)
    # Insert a row without dedup_hash (simulating pre-v4 data)
    conn = tm_expense._get_conn()
    conn.execute(
        """INSERT INTO expense_entries
           (kind, amount, currency, occurred_at, category, created_at, updated_at, amount_cents, payment_method, merchant)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("expense", 50.0, "CNY", "2026-05-01T12:30:00", "餐饮",
         "2026-05-01T12:30:00", "2026-05-01T12:30:00", 5000, "alipay", "测试商家"),
    )
    conn.commit()
    conn.close()

    # Run migration
    conn2 = tm_expense._get_conn()
    tm_expense._migrate_v4(conn2)

    row = conn2.execute(
        "SELECT dedup_hash FROM expense_entries WHERE amount = 50.0"
    ).fetchone()
    assert row is not None
    assert row["dedup_hash"] is not None
    assert len(row["dedup_hash"]) == 16
    conn2.close()


# ------------------------------------------------------------------
# batch_record dedup tests
# ------------------------------------------------------------------


def test_batch_record_dedup_by_external_id(monkeypatch):
    """Same source_external_id second INSERT is skipped."""
    db = _temp_db(monkeypatch)
    entry = {
        "kind": "expense", "amount": 35, "category": "餐饮",
        "occurred_at": "2026-05-01T12:00:00", "merchant": "星巴克",
        "source_external_id": "ALIPAY_TXN_001",
    }
    r1 = tm_expense.expense_write(action="batch_record", entries=[entry])
    assert r1["inserted"] == 1
    assert r1["skipped_duplicate"] == 0

    r2 = tm_expense.expense_write(action="batch_record", entries=[entry])
    assert r2["inserted"] == 0
    assert r2["skipped_duplicate"] == 1


def test_batch_record_dedup_by_hash_fallback(monkeypatch):
    """No external_id: same (occurred_at_minute, amount, merchant) is skipped."""
    db = _temp_db(monkeypatch)
    entry = {
        "kind": "expense", "amount": 35, "category": "餐饮",
        "occurred_at": "2026-05-01T12:00:00", "merchant": "星巴克",
        "payment_method": "alipay",
    }
    r1 = tm_expense.expense_write(action="batch_record", entries=[entry])
    assert r1["inserted"] == 1

    r2 = tm_expense.expense_write(action="batch_record", entries=[entry])
    assert r2["inserted"] == 0
    assert r2["skipped_duplicate"] == 1


def test_batch_record_returns_inserted_skipped_counts(monkeypatch):
    """Return shape includes inserted + skipped_duplicate."""
    db = _temp_db(monkeypatch)
    entries = [
        {"kind": "expense", "amount": 10, "category": "餐饮"},
        {"kind": "expense", "amount": 20, "category": "交通"},
    ]
    r = tm_expense.expense_write(action="batch_record", entries=entries)
    assert r["ok"] is True
    assert "inserted" in r
    assert "skipped_duplicate" in r
    assert "errors" in r
    assert r["inserted"] == 2
    assert r["skipped_duplicate"] == 0


# ------------------------------------------------------------------
# alipay importer tests
# ------------------------------------------------------------------


def _write_alipay_csv(path: Path, rows: list[dict]):
    """Write a minimal alipay-format CSV."""
    fieldnames = [
        "交易时间", "交易类型", "交易对方", "商品", "金额",
        "收/支", "交易状态", "交易订单号", "商家订单号", "备注",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            full = {fn: "" for fn in fieldnames}
            full.update(row)
            w.writerow(full)


def test_alipay_importer_basic(monkeypatch):
    """Alipay CSV with expense + income + 不计收支 → correct mapping."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "alipay_test.csv"
    _write_alipay_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮美食", "交易对方": "星巴克",
         "商品": "冰美式", "金额": "38.00", "收/支": "支出", "交易状态": "交易成功",
         "交易订单号": "TXN001"},
        {"交易时间": "2026-05-01 13:00:00", "交易类型": "转账", "交易对方": "张三",
         "商品": "转账", "金额": "100.00", "收/支": "收入", "交易状态": "交易成功",
         "交易订单号": "TXN002"},
        {"交易时间": "2026-05-01 14:00:00", "交易类型": "理财", "交易对方": "余额宝",
         "商品": "转入", "金额": "500.00", "收/支": "不计收支", "交易状态": "交易成功",
         "交易订单号": "TXN003"},
    ])
    r = tm_expense_import_alipay.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 2  # 不计收支 skipped
    assert r["inserted"] == 2
    assert r["skipped_invalid"] == 1

    # Verify DB
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 2
    kinds = {row["kind"] for row in rows}
    assert kinds == {"expense", "income"}


def test_alipay_importer_skip_failed_status(monkeypatch):
    """Transaction with 交易状态 != 交易成功 is skipped."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "alipay_failed.csv"
    _write_alipay_csv(csv_path, [
        {"交易时间": "2026-05-01 12:00:00", "交易类型": "购物", "交易对方": "淘宝",
         "金额": "99.00", "收/支": "支出", "交易状态": "交易关闭", "交易订单号": "TXN_FAIL"},
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "购物", "交易对方": "京东",
         "金额": "50.00", "收/支": "支出", "交易状态": "交易成功", "交易订单号": "TXN_OK"},
    ])
    r = tm_expense_import_alipay.import_csv(str(csv_path))
    assert r["parsed"] == 1
    assert r["inserted"] == 1
    assert r["skipped_invalid"] == 1


def test_alipay_importer_dry_run(monkeypatch):
    """dry_run=True does not write to DB, returns samples."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "alipay_dry.csv"
    _write_alipay_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮", "交易对方": "星巴克",
         "金额": "38.00", "收/支": "支出", "交易状态": "交易成功", "交易订单号": "TXN_DRY"},
    ])
    r = tm_expense_import_alipay.import_csv(str(csv_path), dry_run=True)
    assert r["ok"] is True
    assert r["dry_run"] is True
    assert len(r["samples"]) == 1
    assert r["samples"][0]["merchant"] == "星巴克"

    # DB should be empty
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 0


# ------------------------------------------------------------------
# wechat importer tests
# ------------------------------------------------------------------


def _write_wechat_csv(path: Path, rows: list[dict]):
    """Write a minimal wechat-format CSV."""
    fieldnames = [
        "交易时间", "交易类型", "交易对方", "商品", "收/支",
        "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            full = {fn: "" for fn in fieldnames}
            full.update(row)
            w.writerow(full)


def test_wechat_importer_basic(monkeypatch):
    """Wechat CSV with expense → correct mapping."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "wechat_test.csv"
    _write_wechat_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮", "交易对方": "麦当劳",
         "商品": "巨无霸套餐", "收/支": "支出", "金额(元)": "42.00",
         "支付方式": "零钱", "当前状态": "支付成功", "交易单号": "WX_TXN001"},
    ])
    r = tm_expense_import_wechat.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 1
    assert r["inserted"] == 1

    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 1
    assert rows[0]["payment_method"] == "wechat:零钱"


def test_wechat_importer_payment_method_prefix(monkeypatch):
    """Wechat payment_method always has 'wechat:' prefix."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "wechat_pm.csv"
    _write_wechat_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "交通", "交易对方": "滴滴出行",
         "商品": "快车", "收/支": "支出", "金额(元)": "25.50",
         "支付方式": "招商银行储蓄卡(1234)", "当前状态": "支付成功", "交易单号": "WX_TXN002"},
    ])
    r = tm_expense_import_wechat.import_csv(str(csv_path))
    assert r["inserted"] == 1
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert rows[0]["payment_method"].startswith("wechat:")


def test_double_import_same_csv_no_duplicate(monkeypatch):
    """Importing the same CSV twice: second run all skipped."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "alipay_double.csv"
    _write_alipay_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮", "交易对方": "星巴克",
         "金额": "38.00", "收/支": "支出", "交易状态": "交易成功", "交易订单号": "TXN_DUP"},
        {"交易时间": "2026-05-01 13:00:00", "交易类型": "交通", "交易对方": "滴滴",
         "金额": "25.00", "收/支": "支出", "交易状态": "交易成功", "交易订单号": "TXN_DUP2"},
    ])
    r1 = tm_expense_import_alipay.import_csv(str(csv_path))
    assert r1["inserted"] == 2

    r2 = tm_expense_import_alipay.import_csv(str(csv_path))
    assert r2["inserted"] == 0
    assert r2["skipped_duplicate"] == 2

    # DB should still have only 2 rows
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 2
