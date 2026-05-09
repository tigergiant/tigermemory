#!/usr/bin/env python3
"""tests/test_tm_expense_p3.py — P3 AI auto-classify tests (mock httpx, no real LLM calls)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import tm_expense


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
    conn.close()
    return p


def test_auto_classify_disabled_keeps_needs_confirmation(monkeypatch):
    """auto_classify=False 未命中词表 → needs_confirmation（现状）"""
    db = _temp_db(monkeypatch)
    res = tm_expense.expense_write(
        action="record",
        kind="expense",
        amount=35,
        category="未知类",
        auto_classify=False,
    )
    assert res["ok"] is False
    assert res["needs_confirmation"] is True
    assert "llm_attempted" not in res


def test_auto_classify_success_high_confidence(monkeypatch):
    """LLM 返回高置信度 → 成功 record + auto_classified=True + DB 写入 LLM 推断的分类"""
    db = _temp_db(monkeypatch)
    monkeypatch.setattr(
        "tm_expense.tm_llm.classify_expense",
        lambda **kw: {
            "ok": True,
            "category": "餐饮",
            "confidence": 0.92,
            "reasoning": "星巴克是咖啡品牌",
            "raw": {},
        },
    )
    res = tm_expense.expense_write(
        action="record",
        kind="expense",
        amount=35,
        note="星巴克冰美式",
        auto_classify=True,
    )
    assert res["ok"] is True
    assert res["auto_classified"] is True
    assert res["llm_category"] == "餐饮"
    assert res["llm_confidence"] == 0.92
    assert "星巴克" in res["llm_reasoning"]
    rows = tm_expense.expense_read(mode="list").get("rows", [])
    assert len(rows) == 1
    assert rows[0]["category"] == "餐饮"


def test_auto_classify_low_confidence_falls_back(monkeypatch):
    """LLM 返回 confidence=0.6 → needs_confirmation + llm_attempted=True"""
    db = _temp_db(monkeypatch)
    monkeypatch.setattr(
        "tm_expense.tm_llm.classify_expense",
        lambda **kw: {
            "ok": True,
            "category": "餐饮",
            "confidence": 0.6,
            "reasoning": "confidence too low",
            "raw": {},
        },
    )
    res = tm_expense.expense_write(
        action="record",
        kind="expense",
        amount=35,
        note="星巴克冰美式",
        auto_classify=True,
    )
    assert res["ok"] is False
    assert res["needs_confirmation"] is True
    assert res["llm_attempted"] is True
    assert "confidence" in res["llm_reason"]


def test_auto_classify_unknown_category_falls_back(monkeypatch):
    """LLM 返回 "category":"虚构类"（不在词表） → needs_confirmation"""
    db = _temp_db(monkeypatch)
    monkeypatch.setattr(
        "tm_expense.tm_llm.classify_expense",
        lambda **kw: {
            "ok": True,
            "category": "虚构类",
            "confidence": 0.9,
            "reasoning": "invalid category",
            "raw": {},
        },
    )
    res = tm_expense.expense_write(
        action="record",
        kind="expense",
        amount=35,
        note="星巴克冰美式",
        auto_classify=True,
    )
    assert res["ok"] is False
    assert res["needs_confirmation"] is True
    assert res["llm_attempted"] is True
    assert "invalid category" in res["llm_reason"]


def test_auto_classify_llm_timeout_falls_back(monkeypatch):
    """LLM 返回 ok=False → needs_confirmation + llm_attempted=True"""
    db = _temp_db(monkeypatch)
    monkeypatch.setattr(
        "tm_expense.tm_llm.classify_expense",
        lambda **kw: {"ok": False, "reason": "LLM request timeout"},
    )
    res = tm_expense.expense_write(
        action="record",
        kind="expense",
        amount=35,
        note="星巴克冰美式",
        auto_classify=True,
    )
    assert res["ok"] is False
    assert res["needs_confirmation"] is True
    assert res["llm_attempted"] is True
    assert "timeout" in res["llm_reason"]


def test_auto_classify_no_api_key_falls_back(monkeypatch):
    """LLM 返回 ok=False（API key 未配置） → needs_confirmation + llm_attempted=True"""
    db = _temp_db(monkeypatch)
    monkeypatch.setattr(
        "tm_expense.tm_llm.classify_expense",
        lambda **kw: {"ok": False, "reason": "MINIMAX_API_KEY not configured"},
    )
    res = tm_expense.expense_write(
        action="record",
        kind="expense",
        amount=35,
        note="星巴克冰美式",
        auto_classify=True,
    )
    assert res["ok"] is False
    assert res["needs_confirmation"] is True
    assert res["llm_attempted"] is True
    assert "not configured" in res["llm_reason"]
