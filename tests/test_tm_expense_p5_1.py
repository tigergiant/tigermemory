#!/usr/bin/env python3
"""tests/test_tm_expense_p5_1.py — P5.1 status inference + LLM fallback tests."""

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import tm_expense
import tm_llm
import tm_expense_import_unified


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


# ------------------------------------------------------------------
# Bug 1: 包退换 not misjudged as refunded
# ------------------------------------------------------------------


def test_baoTuiHuan_not_misjudged_as_refunded(monkeypatch):
    """商品说明含"包退换"应判 success，不是 refunded."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "p51_baotui.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:00:00", "交易类型": "购物", "交易对方": "淘宝",
         "商品说明": "包退换商品", "收/支": "支出", "金额(元)": "24.27",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 1
    assert r["status_counts"]["success"] == 1
    assert r["status_counts"]["refunded"] == 0
    assert r["llm_fallback_count"] == 0


# ------------------------------------------------------------------
# Bug 2: 信用卡还款 inferred as internal_transfer via trans_type
# ------------------------------------------------------------------


def test_credit_card_repayment_inferred_internal_transfer(monkeypatch):
    """交易类型=信用卡还款 + 收/支=/ → internal_transfer (script rule, no LLM)."""
    db = _temp_db(monkeypatch)
    csv_path = db.parent / "p51_credit.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:00:00", "交易类型": "信用卡还款", "交易对方": "招商银行",
         "商品说明": "信用卡还款", "收/支": "/", "金额(元)": "540.10",
         "支付方式": "招商银行(1234)", "来源": "bank", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 1
    assert r["status_counts"]["internal_transfer"] == 1
    assert r["status_counts"]["success"] == 0
    assert r["llm_fallback_count"] == 0


# ------------------------------------------------------------------
# LLM fallback: unknown triggers LLM
# ------------------------------------------------------------------


def test_unknown_status_triggers_llm_fallback(monkeypatch):
    """Script can't classify → LLM called → status written + note tagged."""
    db = _temp_db(monkeypatch)

    def mock_classify_status(**kwargs):
        return {"ok": True, "status": "internal_transfer", "confidence": 0.9,
                "reasoning": "looks like transfer", "raw": {}}

    monkeypatch.setattr(tm_llm, "classify_status", mock_classify_status)

    csv_path = db.parent / "p51_unknown.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:00:00", "交易类型": "未知奇怪类型", "交易对方": "某商户",
         "商品说明": "奇怪交易", "收/支": "/", "金额(元)": "100.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 1
    assert r["llm_fallback_count"] == 1
    assert r["llm_fallback_failed_count"] == 0
    assert len(r["llm_fallback_rows"]) == 1
    assert r["llm_fallback_rows"][0]["llm_status"] == "internal_transfer"
    assert r["llm_fallback_rows"][0]["llm_confidence"] == 0.9

    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 1
    assert rows[0]["status"] == "internal_transfer"
    assert "[LLM_STATUS:internal_transfer@0.90]" in (rows[0]["note"] or "")


# ------------------------------------------------------------------
# LLM fallback failure → closed
# ------------------------------------------------------------------


def test_llm_fallback_failure_falls_back_to_closed(monkeypatch):
    """LLM returns ok=False → status=closed + note tagged."""
    db = _temp_db(monkeypatch)

    def mock_classify_status(**kwargs):
        return {"ok": False, "reasoning": "API key missing"}

    monkeypatch.setattr(tm_llm, "classify_status", mock_classify_status)

    csv_path = db.parent / "p51_fail.csv"
    _write_unified_csv(csv_path, [
        {"交易时间": "2026-05-01 12:00:00", "交易类型": "未知奇怪类型", "交易对方": "某商户",
         "商品说明": "奇怪交易", "收/支": "/", "金额(元)": "50.00",
         "支付方式": "支付宝", "来源": "alipay", "月份": "2026-05"},
    ])
    r = tm_expense_import_unified.import_csv(str(csv_path))
    assert r["ok"] is True
    assert r["parsed"] == 1
    assert r["llm_fallback_count"] == 0
    assert r["llm_fallback_failed_count"] == 1
    assert len(r["llm_fallback_rows"]) == 1
    assert r["llm_fallback_rows"][0]["llm_failed"] is True

    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert "[LLM_STATUS_FAILED:" in (rows[0]["note"] or "")
