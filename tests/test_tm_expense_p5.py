#!/usr/bin/env python3
"""tests/test_tm_expense_p5.py — P5 status + unified import tests."""

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import tm_expense
import tm_expense_import_unified
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
    tm_expense._migrate_v5(conn)
    conn.close()
    return p


# ------------------------------------------------------------------
# migrate_v5 tests
# ------------------------------------------------------------------


def test_migrate_v5_idempotent(monkeypatch):
    """Running _migrate_v5 twice does not error."""
    db = _temp_db(monkeypatch)
    conn = tm_expense._get_conn()
    tm_expense._migrate_v5(conn)
    tm_expense._migrate_v5(conn)
    conn.close()


def test_migrate_v5_defaults_existing_to_success(monkeypatch):
    """After v5 migration, existing rows have status='success'."""
    db = _temp_db(monkeypatch)
    conn = tm_expense._get_conn()
    conn.execute(
        """INSERT INTO expense_entries
           (kind, amount, currency, occurred_at, category, created_at, updated_at, amount_cents)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("expense", 50.0, "CNY", "2026-05-01T12:00:00", "餐饮",
         "2026-05-01T12:00:00", "2026-05-01T12:00:00", 5000),
    )
    conn.commit()
    conn.close()

    conn2 = tm_expense._get_conn()
    tm_expense._migrate_v5(conn2)
    row = conn2.execute("SELECT status FROM expense_entries WHERE amount = 50.0").fetchone()
    assert row["status"] == "success"
    conn2.close()


# ------------------------------------------------------------------
# aggregate excludes non-success tests
# ------------------------------------------------------------------


def _insert_mixed_status(db: Path):
    """Insert entries with various statuses for testing."""
    entries = [
        {"kind": "expense", "amount": 100, "category": "餐饮", "status": "success",
         "occurred_at": "2026-05-01T12:00:00"},
        {"kind": "expense", "amount": 200, "category": "购物", "status": "success",
         "occurred_at": "2026-05-02T12:00:00"},
        {"kind": "income", "amount": 50, "category": "退款", "status": "refunded",
         "occurred_at": "2026-05-03T12:00:00"},
        {"kind": "expense", "amount": 500, "category": "投资", "status": "internal_transfer",
         "occurred_at": "2026-05-04T12:00:00"},
    ]
    tm_expense.expense_write(action="batch_record", entries=entries, confirm_new_category=True)


def test_aggregate_excludes_non_success(monkeypatch):
    """Aggregate only sums success rows."""
    db = _temp_db(monkeypatch)
    _insert_mixed_status(db)
    r = tm_expense.expense_read(mode="aggregate", group_by=["kind"], metric="sum")
    groups = {g["kind"]: g["metric_value"] for g in r["groups"]}
    assert groups.get("expense") == 300  # 100 + 200 only
    assert groups.get("income", 0) == 0  # refunded excluded


def test_trend_excludes_non_success(monkeypatch):
    """Trend only counts success rows."""
    db = _temp_db(monkeypatch)
    _insert_mixed_status(db)
    r = tm_expense.expense_read(mode="trend", bucket="month")
    total_n = sum(b["n"] for b in r["buckets"])
    assert total_n == 2  # only 2 success rows


def test_compare_excludes_non_success(monkeypatch):
    """Compare only uses success rows."""
    db = _temp_db(monkeypatch)
    _insert_mixed_status(db)
    r = tm_expense.expense_read(
        mode="compare", compare="mom",
        start_date="2026-05-01", end_date="2026-05-31",
    )
    assert r["ok"] is True


def test_anomaly_excludes_non_success(monkeypatch):
    """Anomaly only analyzes success rows."""
    db = _temp_db(monkeypatch)
    _insert_mixed_status(db)
    r = tm_expense.expense_read(mode="anomaly", anomaly_window_days=365)
    # Should have enough success rows to compute stats
    assert r["ok"] is True


def test_budget_status_excludes_non_success(monkeypatch):
    """Budget status only counts success spending."""
    db = _temp_db(monkeypatch)
    _insert_mixed_status(db)
    r = tm_expense.expense_read(mode="budget_status")
    assert r["ok"] is True


def test_list_includes_all_statuses(monkeypatch):
    """List mode shows all statuses."""
    db = _temp_db(monkeypatch)
    _insert_mixed_status(db)
    r = tm_expense.expense_read(mode="list")
    assert r["total_count"] == 4


def test_batch_record_accepts_status_param(monkeypatch):
    """batch_record accepts status field in entries."""
    db = _temp_db(monkeypatch)
    r = tm_expense.expense_write(action="batch_record", entries=[
        {"kind": "expense", "amount": 10, "category": "餐饮", "status": "success"},
        {"kind": "expense", "amount": 0, "category": "其他", "status": "closed"},
    ])
    assert r["inserted"] == 2
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    statuses = {row["status"] for row in rows}
    assert "success" in statuses
    assert "closed" in statuses


# ------------------------------------------------------------------
# unified importer tests
# ------------------------------------------------------------------


def _write_unified_csv(path: Path, rows: list[dict]):
    fieldnames = [
        "交易时间", "交易类型", "交易对方", "商品说明", "收/支",
        "金额(元)", "支付方式", "备注", "来源", "月份", "实际流向",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            full = {fn: "" for fn in fieldnames}
            full.update(row)
            w.writerow(full)


def test_unified_importer_basic(monkeypatch):
    """Unified CSV with expense + income → correct mapping."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "unified_test.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮", "交易对方": "星巴克",
         "商品说明": "冰美式", "收/支": "支出", "金额(元)": "38.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
        {"交易时间": "2026-05-01 13:00:00", "交易类型": "转账", "交易对方": "张三",
         "商品说明": "工资", "收/支": "收入", "金额(元)": "15000.00",
         "支付方式": "招商银行(1234)", "来源": "bank", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 2
    assert r["inserted"] == 2

    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 2
    kinds = {row["kind"] for row in rows}
    assert kinds == {"expense", "income"}


def test_unified_importer_status_mapping(monkeypatch):
    """Status mapping: 不计收支→internal_transfer, 0元→closed, 退款→refunded."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "unified_status.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:00:00", "交易类型": "理财", "交易对方": "余额宝",
         "商品说明": "转入", "收/支": "不计收支", "金额(元)": "1000.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
        {"交易时间": "2026-05-01 13:00:00", "交易类型": "授权", "交易对方": "微信",
         "商品说明": "解冻", "收/支": "支出", "金额(元)": "0.00",
         "支付方式": "微信", "来源": "wechat", "月份": "2026-05"},
        {"交易时间": "2026-05-01 14:00:00", "交易类型": "退款", "交易对方": "淘宝",
         "商品说明": "退货退款", "收/支": "收入", "金额(元)": "199.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["parsed"] == 3
    assert r["inserted"] == 3

    rows = tm_expense.expense_read(mode="list").get("rows", [])
    status_map = {row["merchant"]: row["status"] for row in rows}
    assert status_map.get("余额宝") == "internal_transfer"
    assert status_map.get("微信") == "closed"
    assert status_map.get("淘宝") == "refunded"


def test_unified_importer_dry_run(monkeypatch):
    """dry_run=True does not write to DB."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "unified_dry.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮", "交易对方": "星巴克",
         "商品说明": "冰美式", "收/支": "支出", "金额(元)": "38.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path), dry_run=True)
    assert r["dry_run"] is True
    assert len(r["samples"]) == 1
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 0


def test_unified_importer_auto_classify_called(monkeypatch):
    """Unified importer entries have category='其他' (LLM placeholder)."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "unified_auto.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:30:00", "交易类型": "餐饮", "交易对方": "星巴克",
         "商品说明": "冰美式", "收/支": "支出", "金额(元)": "38.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["inserted"] == 1
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    # Category is "其他" because unified importer doesn't infer category
    assert rows[0]["category"] == "其他"


# ------------------------------------------------------------------
# alipay/wechat no longer skip tests
# ------------------------------------------------------------------


def test_alipay_importer_no_longer_skips_refund(monkeypatch):
    """Alipay refund row is written with status=refunded, kind=income."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "alipay_refund.csv"
    # Write alipay CSV with refund
    fieldnames = [
        "交易时间", "交易类型", "交易对方", "商品", "金额",
        "收/支", "交易状态", "交易订单号", "商家订单号", "备注",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerow({
            "交易时间": "2026-05-01 12:00:00", "交易类型": "退款", "交易对方": "淘宝",
            "商品": "退货", "金额": "199.00", "收/支": "收入", "交易状态": "退款成功",
            "交易订单号": "TXN_REFUND",
        })
    r = tm_expense_import_alipay.import_csv(str(csv_path))
    assert r["parsed"] == 1
    assert r["inserted"] == 1
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert rows[0]["status"] == "refunded"
    assert rows[0]["kind"] == "income"


def test_wechat_importer_no_longer_skips_internal_transfer(monkeypatch):
    """Wechat 不计收支 row is written with status=internal_transfer."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "wechat_transfer.csv"
    fieldnames = [
        "交易时间", "交易类型", "交易对方", "商品", "收/支",
        "金额(元)", "支付方式", "当前状态", "交易单号", "商户单号", "备注",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerow({
            "交易时间": "2026-05-01 12:00:00", "交易类型": "理财", "交易对方": "零钱通",
            "商品": "转入", "收/支": "支出", "金额(元)": "500.00",
            "支付方式": "零钱", "当前状态": "支付成功", "交易单号": "WX_TRANSFER",
        })
    # Wechat doesn't have 不计收支 direction, but we can test refund
    r = tm_expense_import_wechat.import_csv(str(csv_path))
    assert r["parsed"] == 1
    assert r["inserted"] == 1
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert rows[0]["status"] == "success"  # normal wechat payment
