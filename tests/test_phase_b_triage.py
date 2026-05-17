from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "expense_import"))

import _phase_b_triage as triage


def make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE expense_entries (
            id INTEGER PRIMARY KEY,
            tags TEXT,
            status TEXT,
            source_agent TEXT,
            created_at TEXT,
            source_text TEXT,
            amount_cents INTEGER,
            merchant TEXT,
            occurred_at TEXT,
            kind TEXT,
            deleted_at TEXT,
            source_external_id TEXT
        )"""
    )
    conn.commit()
    return conn


def insert_entry(
    conn: sqlite3.Connection,
    *,
    row_id: int,
    tags: str | None = None,
    status: str = "success",
    source_agent: str = "expense-import-test",
    created_at: str = "2026-05-16T00:00:00+08:00",
    source_text: str = "test",
    amount_cents: int = 1000,
    merchant: str = "merchant",
    occurred_at: str = "2026-05-01T12:00:00+08:00",
    kind: str = "expense",
    deleted_at: str | None = None,
    source_external_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO expense_entries
           (id, tags, status, source_agent, created_at, source_text, amount_cents,
            merchant, occurred_at, kind, deleted_at, source_external_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row_id,
            tags,
            status,
            source_agent,
            created_at,
            source_text,
            amount_cents,
            merchant,
            occurred_at,
            kind,
            deleted_at,
            source_external_id,
        ),
    )
    conn.commit()


def classify(conn: sqlite3.Connection, row: dict) -> str:
    return triage.classify_row(conn, row)[0]


def base_row(**overrides) -> dict:
    row = {
        "amount_cents": 1000,
        "kind": "expense",
        "match_deleted": [],
        "match_ids": [],
        "occurred_at": "2026-05-01T12:00:00+08:00",
        "reason": "no_ledger_candidate",
        "record_redacted": {
            "amount_cents": 1000,
            "kind": "expense",
            "merchant": "merchant",
            "needs_review": False,
            "occurred_at": "2026-05-01T12:00:00+08:00",
            "source_external_id": "alipay:order-1:out",
            "status": "success",
        },
        "status": "missing",
    }
    row.update(overrides)
    return row


def row_with_identity(*, sha_prefix: str = "abcdef123456", source_row_no: int = 7, pair_role: str = "out", **overrides) -> dict:
    record = {
        **base_row()["record_redacted"],
        "source_file_sha256_12": sha_prefix,
        "source_row_no": source_row_no,
        "pair_role": pair_role,
    }
    record.update(overrides.pop("record_redacted", {}))
    return base_row(record_redacted=record, **overrides)


def write_merge_context(
    tmp_path: Path,
    *,
    full_sha: str = "abcdef1234567890",
    source_row_no: int = 7,
    pair_role: str = "out",
    absorbed_idxs: list[int] | None = None,
    canonical_idx: int | None = None,
) -> triage.MergeContext:
    max_idx = max([canonical_idx or 0, *(absorbed_idxs or [0])])
    normalized = []
    for idx in range(max_idx + 1):
        normalized.append(
            {
                "source_file_sha256": full_sha if idx == max_idx else f"other{idx:012d}",
                "source_row_no": source_row_no if idx == max_idx else idx,
                "pair_role": pair_role if idx == max_idx else "out",
            }
        )
    normalized_path = tmp_path / "normalized_transactions.jsonl"
    merge_groups_path = tmp_path / "merge_groups.jsonl"
    normalized_path.write_text(
        "\n".join(json.dumps(row) for row in normalized) + "\n",
        encoding="utf-8",
    )
    merge_groups_path.write_text(
        json.dumps({"canonical_idx": canonical_idx, "absorbed_idxs": absorbed_idxs or []}) + "\n",
        encoding="utf-8",
    )
    return triage.load_merge_context(normalized_path, merge_groups_path)


def test_internal_transfer_classified_b1(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    row = base_row(record_redacted={**base_row()["record_redacted"], "status": "internal_transfer"})
    assert classify(conn, row) == "B1"


def test_needs_review_classified_b2(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    row = base_row(record_redacted={**base_row()["record_redacted"], "needs_review": True})
    assert classify(conn, row) == "B2"


def test_phase2_card_collapsed_classified_b4(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    insert_entry(conn, row_id=9, tags=",shadow_card:99,dedup_merged:2026-05-16,", deleted_at="2026-05-16T00:00:00+08:00")
    row = base_row(match_ids=[9], match_deleted=[True])
    assert classify(conn, row) == "B4"


def test_phase3_aggregate_classified_b5(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    insert_entry(conn, row_id=10, tags=",aggregate_card,", deleted_at="2026-05-16T00:00:00+08:00")
    row = base_row(match_ids=[10], match_deleted=[True])
    assert classify(conn, row) == "B5"


def test_unexplained_missing_classified_b8(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    assert classify(conn, base_row()) == "B8"


def test_first_bucket_wins(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    row = base_row(
        status="ambiguous",
        match_ids=[1, 2],
        match_deleted=[False, True],
        record_redacted={**base_row()["record_redacted"], "status": "internal_transfer"},
    )
    assert classify(conn, row) == "B1"


def test_refund_pair_income_leg_classified_b3(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    insert_entry(
        conn,
        row_id=11,
        status="refunded",
        amount_cents=2500,
        merchant="refund merchant",
        occurred_at="2026-05-02T10:00:00+08:00",
        source_external_id="alipay:refund-order:out",
    )
    row = base_row(
        amount_cents=2500,
        kind="income",
        occurred_at="2026-05-02T11:00:00+08:00",
        record_redacted={
            **base_row()["record_redacted"],
            "amount_cents": 2500,
            "kind": "income",
            "merchant": "refund merchant",
            "occurred_at": "2026-05-02T11:00:00+08:00",
            "source_external_id": "alipay:refund-order:in",
        },
    )
    assert classify(conn, row) == "B3"


def test_no_db_writes(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    conn = make_db(db_path)
    insert_entry(conn, row_id=9, tags=",shadow_card:99,", deleted_at="2026-05-16T00:00:00+08:00")
    before = conn.execute("SELECT COUNT(*) FROM expense_entries").fetchone()[0]
    conn.close()

    ro_conn = triage.open_readonly_ledger(db_path)
    try:
        classified = triage.triage_rows(ro_conn, [base_row(match_ids=[9], match_deleted=[True])])
        assert classified[0]["triage_bucket"] == "B4"
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("INSERT INTO expense_entries (id) VALUES (999)")
    finally:
        ro_conn.close()

    check = sqlite3.connect(db_path)
    after = check.execute("SELECT COUNT(*) FROM expense_entries").fetchone()[0]
    check.close()
    assert before == after
    assert "mode=ro" in triage.readonly_sqlite_uri(db_path)


def test_jsonl_roundtrip(tmp_path):
    out = tmp_path / "out.jsonl"
    rows = [{"z": 1, "text": "中文"}]
    triage.write_jsonl(out, rows)
    assert json.loads(out.read_text(encoding="utf-8")) == rows[0]


def test_b9_merge_group_absorbed_classified(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    merge_context = write_merge_context(tmp_path, absorbed_idxs=[5])
    row = row_with_identity()
    assert triage.classify_row(conn, row, merge_context=merge_context)[0] == "B9"


def test_b9_canonical_idx_not_classified_b9(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    merge_context = write_merge_context(tmp_path, canonical_idx=5)
    row = row_with_identity()
    assert triage.classify_row(conn, row, merge_context=merge_context)[0] == "B8"


def test_b9_sha256_prefix_match(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    merge_context = write_merge_context(tmp_path, full_sha="123456789abcffff", absorbed_idxs=[5])
    row = row_with_identity(sha_prefix="123456789abc")
    assert triage.classify_row(conn, row, merge_context=merge_context)[0] == "B9"


def test_b9_missing_merge_groups_file_fails_loudly(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    conn = make_db(db_path)
    conn.close()
    unmatched = tmp_path / "unmatched.jsonl"
    unmatched.write_text(json.dumps(row_with_identity()) + "\n", encoding="utf-8")
    normalized = tmp_path / "normalized_transactions.jsonl"
    normalized.write_text(
        json.dumps({"source_file_sha256": "abcdef1234567890", "source_row_no": 7, "pair_role": "out"}) + "\n",
        encoding="utf-8",
    )

    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "tools/expense_import/_phase_b_triage.py",
            "--unmatched",
            str(unmatched),
            "--ledger-db",
            str(db_path),
            "--normalized",
            str(normalized),
            "--merge-groups",
            str(tmp_path / "missing.jsonl"),
            "--report",
            str(tmp_path / "report.md"),
            "--out-jsonl",
            str(tmp_path / "out.jsonl"),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "merge_groups file is required" in result.stderr


def test_b9_inserted_before_b7_in_priority(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    merge_context = write_merge_context(tmp_path, absorbed_idxs=[5])
    row = row_with_identity(status="ambiguous", match_ids=[1, 2], match_deleted=[False, True])
    assert triage.classify_row(conn, row, merge_context=merge_context)[0] == "B9"


def test_b9_after_b4_b5_in_priority(tmp_path):
    conn = make_db(tmp_path / "ledger.sqlite")
    insert_entry(conn, row_id=10, tags=",aggregate_card,", deleted_at="2026-05-16T00:00:00+08:00")
    merge_context = write_merge_context(tmp_path, absorbed_idxs=[5])
    row = row_with_identity(match_ids=[10], match_deleted=[True])
    assert triage.classify_row(conn, row, merge_context=merge_context)[0] == "B5"
