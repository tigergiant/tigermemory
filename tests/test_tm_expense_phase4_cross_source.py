import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import tm_expense


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    tm_expense._ensure_schema(conn)
    conn.execute("DROP TRIGGER IF EXISTS fts_entries_insert")
    conn.execute("DROP TRIGGER IF EXISTS fts_entries_update")
    conn.execute("DROP TRIGGER IF EXISTS fts_entries_delete")
    return conn


def insert_entry(
    conn,
    *,
    row_id,
    amount_cents,
    occurred_at="2026-05-01T12:00:00+08:00",
    merchant="测试商户",
    payment_method="alipay",
    source_text="alipay",
    tags=None,
    kind="expense",
    status="success",
    note=None,
    deleted_at=None,
):
    conn.execute(
        """INSERT INTO expense_entries
           (id, kind, amount, currency, occurred_at, category, merchant, note,
            payment_method, source_agent, source_text, created_at, updated_at,
            tags, deleted_at, amount_cents, status)
           VALUES (?, ?, ?, 'CNY', ?, '其他', ?, ?, ?, 'test', ?, ?, ?, ?, ?, ?, ?)""",
        (
            row_id,
            kind,
            amount_cents / 100,
            occurred_at,
            merchant,
            note,
            payment_method,
            source_text,
            "2026-05-01T00:00:00+08:00",
            "2026-05-01T00:00:00+08:00",
            tags,
            deleted_at,
            amount_cents,
            status,
        ),
    )


def decision_for(conn, row_id, *, is_refund=False):
    row = conn.execute("SELECT * FROM expense_entries WHERE id = ?", (row_id,)).fetchone()
    channel = tm_expense._infer_source_channel(row["payment_method"], row["source_text"], row["tags"])
    return tm_expense._find_cross_source_candidates(
        conn,
        new_row_id=row["id"],
        occurred_at=row["occurred_at"],
        amount_cents=row["amount_cents"],
        kind=row["kind"],
        payment_method=row["payment_method"],
        merchant=row["merchant"],
        new_channel=channel,
        is_refund=is_refund,
    )


def test_infer_source_channel_current_forms():
    assert tm_expense._infer_source_channel("alipay", "alipay import: x", None) == "alipay"
    assert tm_expense._infer_source_channel("wechat:零钱", "wechat import: x", None) == "wechat"
    assert tm_expense._infer_source_channel("招商银行信用卡(3958)", "unified import: x", None) == "card"
    assert tm_expense._infer_source_channel(None, None, ",credit_card_cmb,") == "card"


def test_semantic_match_accepts_real_and_rejects_coincidental():
    assert tm_expense._semantic_match("支付宝-盒马", ["盒马"])[0] is True
    assert tm_expense._semantic_match("支付宝-上海拉扎斯信息科技有限公司", ["淘宝闪购", "淘宝闪购"])[0] is True
    assert tm_expense._semantic_match("便利店A", ["完全不同商户"])[0] is False


def test_one_to_one_card_after_tp_is_shadow():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=990, merchant="盒马", payment_method="alipay", source_text="alipay")
    insert_entry(conn, row_id=2, amount_cents=990, merchant="支付宝-盒马", payment_method="招商银行信用卡(3958)", source_text="credit_card")

    decision = decision_for(conn, 2)

    assert decision["outcome"] == "shadow_1to1"
    assert decision["new_role"] == "shadow"
    assert decision["twin_ids"] == [1]


def test_one_to_one_tp_after_card_is_canonical_and_apply_is_idempotent():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=990, merchant="支付宝-盒马", payment_method="招商银行信用卡(3958)", source_text="credit_card")
    insert_entry(conn, row_id=2, amount_cents=990, merchant="盒马", payment_method="alipay", source_text="alipay")

    decision = decision_for(conn, 2)
    assert decision["outcome"] == "shadow_1to1"
    assert decision["new_role"] == "canonical"

    tm_expense._apply_cross_source_resolution(conn, new_row_id=2, decision=decision, now="2026-05-16T10:00:00+08:00")
    tm_expense._apply_cross_source_resolution(conn, new_row_id=2, decision=decision, now="2026-05-16T11:00:00+08:00")

    card = conn.execute("SELECT deleted_at FROM expense_entries WHERE id = 1").fetchone()
    tp = conn.execute("SELECT tags FROM expense_entries WHERE id = 2").fetchone()
    assert card["deleted_at"] == "2026-05-16T10:00:00+08:00"
    assert (tp["tags"] or "").count("shadow_card:1") == 1
    assert (tp["tags"] or "").count("dedup_merged:") == 1


def test_many_to_one_valid_aggregate():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=1, merchant="淘宝闪购", payment_method="alipay", source_text="alipay")
    insert_entry(conn, row_id=2, amount_cents=2731, merchant="淘宝闪购", payment_method="alipay", source_text="alipay")
    insert_entry(conn, row_id=3, amount_cents=2732, merchant="支付宝-上海拉扎斯信息科技有限公司", payment_method="招商银行信用卡(3958)", source_text="credit_card")

    decision = decision_for(conn, 3)

    assert decision["outcome"] == "shadow_Nto1"
    assert decision["new_role"] == "shadow"
    assert decision["twin_ids"] == [1, 2]


def test_many_to_one_coincidental_subset_rejected():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=100, merchant="早餐店", payment_method="alipay", source_text="alipay")
    insert_entry(conn, row_id=2, amount_cents=200, merchant="文具店", payment_method="alipay", source_text="alipay")
    insert_entry(conn, row_id=3, amount_cents=300, merchant="招商银行账单", payment_method="招商银行信用卡(3958)", source_text="credit_card")

    decision = decision_for(conn, 3)

    assert decision["outcome"] == "clean"


def test_refund_card_after_tp_is_not_auto_deleted_in_phase4():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=500, merchant="盒马退款", payment_method="alipay", source_text="alipay", kind="income", status="refunded")
    insert_entry(conn, row_id=2, amount_cents=500, merchant="支付宝-盒马退款", payment_method="招商银行信用卡(3958)", source_text="credit_card", kind="income", status="refunded")

    decision = decision_for(conn, 2, is_refund=True)

    assert decision["outcome"] == "clean"
    assert decision["reason"] == "refund_auto_resolution_disabled"


def test_batch_record_cross_source_import_twice_keeps_active_count():
    conn = make_conn()
    entries = [
        {
            "kind": "expense",
            "amount": 9.90,
            "category": "test-food",
            "occurred_at": "2026-05-01T12:00:00+08:00",
            "merchant": "alipay hema",
            "note": "card tail 3958 | bill 2026-05",
            "payment_method": "CMB credit card 3958",
            "tags": ["credit_card_cmb", "card_tail:3958"],
            "source_text": "credit_card",
        },
        {
            "kind": "expense",
            "amount": 9.90,
            "category": "test-food",
            "occurred_at": "2026-05-01T12:00:00+08:00",
            "merchant": "hema",
            "note": "order payment",
            "payment_method": "CMB credit card 3958 & coupon",
            "tags": ["alipay"],
            "source_text": "alipay",
        },
    ]

    first = tm_expense._action_batch_record(conn, entries, confirm_new_category=True)
    assert first["inserted"] == 2
    assert [a["outcome"] for a in first["cross_source_actions"]] == ["shadow_1to1"]
    active_total = conn.execute("SELECT COUNT(*) FROM expense_entries WHERE deleted_at IS NULL").fetchone()[0]
    assert active_total == 1

    second = tm_expense._action_batch_record(conn, entries, confirm_new_category=True)
    assert second["inserted"] == 1
    assert second["skipped_duplicate"] == 1
    assert [a["outcome"] for a in second["cross_source_actions"]] == ["shadow_1to1"]
    active_total = conn.execute("SELECT COUNT(*) FROM expense_entries WHERE deleted_at IS NULL").fetchone()[0]
    assert active_total == 1


def test_tp_collision_far_apart_returns_clean():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=1000, occurred_at="2026-06-01T10:00:00+08:00", merchant="merchant-a", payment_method="alipay", source_text="alipay")

    decision = tm_expense._find_cross_source_candidates(
        conn,
        new_row_id=99,
        occurred_at="2026-06-01T10:00:30+08:00",
        amount_cents=1000,
        kind="expense",
        payment_method="alipay",
        merchant="merchant-a",
        new_channel="alipay",
        is_refund=False,
    )

    assert decision["outcome"] == "clean"


def test_tp_collision_within_threshold_returns_collision():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=2000, occurred_at="2026-06-02T15:00:00+08:00", merchant="merchant-b", payment_method="wechat", source_text="wechat")

    decision = tm_expense._find_cross_source_candidates(
        conn,
        new_row_id=99,
        occurred_at="2026-06-02T15:00:03+08:00",
        amount_cents=2000,
        kind="expense",
        payment_method="wechat",
        merchant="merchant-b",
        new_channel="wechat",
        is_refund=False,
    )

    assert decision["outcome"] == "cross_tp_collision"
    assert decision["twin_ids"] == [1]
    assert decision["reason"].endswith("_within_5s")


def test_tp_collision_far_falls_through_to_card():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=3000, occurred_at="2026-06-03T08:00:00+08:00", merchant="meituan Wang San Hotpot", payment_method="alipay", source_text="alipay")
    insert_entry(conn, row_id=2, amount_cents=3000, occurred_at="2026-06-03T00:00:00+08:00", merchant="Wang San Hotpot", payment_method="CMB credit card 3958", source_text="credit_card")

    decision = tm_expense._find_cross_source_candidates(
        conn,
        new_row_id=99,
        occurred_at="2026-06-03T20:00:00+08:00",
        amount_cents=3000,
        kind="expense",
        payment_method="alipay",
        merchant="meituan Wang San Hotpot",
        new_channel="alipay",
        is_refund=False,
    )

    assert decision["outcome"] == "shadow_1to1"
    assert decision["twin_ids"] == [2]


def test_tp_collision_unparseable_timestamp_does_not_crash():
    conn = make_conn()
    insert_entry(conn, row_id=1, amount_cents=4000, occurred_at="notadate01", merchant="merchant-c", payment_method="alipay", source_text="alipay")

    decision = tm_expense._find_cross_source_candidates(
        conn,
        new_row_id=99,
        occurred_at="notadate01",
        amount_cents=4000,
        kind="expense",
        payment_method="alipay",
        merchant="merchant-c",
        new_channel="alipay",
        is_refund=False,
    )

    assert decision["outcome"] == "clean"
