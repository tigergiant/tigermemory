#!/usr/bin/env python3
"""tools/tm_expense_import_unified.py — P5 unified CSV importer (OpenClaw 4-channel merge).
Inputs: Expense CSV exports, backup files, local SQLite data, or CLI import/filter arguments.
Outputs: Normalized expense records, backup artifacts, digest reports, or migration results.
Depends-on (must-have): Python stdlib, local expense database/files, and tm_expense shared helpers.
"""

import csv
import datetime
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tm_expense
import tm_llm


def import_csv(csv_path: str, dry_run: bool = False) -> dict[str, Any]:
    """Parse unified CSV (11 columns) and write via batch_record with auto_classify.

    Returns:
        {"ok": True, "parsed": N, "inserted": M, "skipped_duplicate": K,
         "skipped_invalid": J, "errors": [...], "samples": [...],
         "status_counts": {"success": N, "refunded": N, ...},
         "amount_summary": {"expense_success": X, "income_success": Y, ...}}
    """
    path = Path(csv_path)
    if not path.exists():
        return {"ok": False, "error": f"file not found: {csv_path}"}

    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb2312", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return {"ok": False, "error": "failed to decode CSV file"}

    lines = text.splitlines()
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        return {"ok": False, "error": "CSV has no header row"}

    fieldnames = [f.strip().lstrip("\ufeff") for f in reader.fieldnames]
    reader.fieldnames = fieldnames

    entries: list[dict] = []
    skipped_invalid = 0
    errors: list[dict] = []
    samples: list[dict] = []
    status_counts: dict[str, int] = {"success": 0, "refunded": 0, "closed": 0, "internal_transfer": 0}
    amount_summary: dict[str, float] = {
        "expense_success": 0.0, "income_success": 0.0,
        "refunded_total": 0.0, "internal_transfer_total": 0.0,
    }
    llm_fallback_count = 0
    llm_fallback_failed_count = 0
    llm_fallback_rows: list[dict] = []

    for row_idx, row in enumerate(reader):
        if not any(v.strip() for v in row.values() if v):
            continue

        try:
            # --- 收/支 ---
            direction = (row.get("收/支") or "").strip()

            # --- 金额(元) ---
            amount_str = (row.get("金额(元)") or "0").strip().lstrip("¥").strip()
            try:
                amount = abs(float(amount_str))
            except ValueError:
                skipped_invalid += 1
                errors.append({"row": row_idx + 2, "error": f"invalid amount: {amount_str}"})
                continue

            # --- 商品说明 ---
            product = (row.get("商品说明") or "").strip()
            remark = (row.get("备注") or "").strip()
            note_parts = [p for p in (product, remark) if p]
            note = " | ".join(note_parts) if note_parts else None

            # --- 交易类型 ---
            trans_type = (row.get("交易类型") or "").strip()

            # --- 来源 ---
            source = (row.get("来源") or "").strip()

            # --- 支付方式 ---
            payment_method = (row.get("支付方式") or "").strip() or None

            # --- Status inference ---
            status = _infer_status(direction, amount, product, remark, trans_type)

            # --- LLM fallback for unknown ---
            if status == "unknown":
                llm_res = tm_llm.classify_status(
                    direction=direction, amount=amount, product=product, remark=remark,
                    trans_type=trans_type, payment_method=payment_method or "", source=source,
                )
                if llm_res.get("ok"):
                    status = llm_res["status"]
                    llm_fallback_count += 1
                    note = (note or "") + f" [LLM_STATUS:{status}@{llm_res['confidence']:.2f}]"
                    llm_fallback_rows.append({
                        "row": row_idx + 2,
                        "merchant": (row.get("交易对方") or "").strip(),
                        "product": product,
                        "direction": direction,
                        "amount": amount,
                        "trans_type": trans_type,
                        "llm_status": status,
                        "llm_confidence": llm_res["confidence"],
                    })
                else:
                    status = "closed"
                    llm_fallback_failed_count += 1
                    note = (note or "") + f" [LLM_STATUS_FAILED:{llm_res.get('reasoning','')[:50]}]"
                    llm_fallback_rows.append({
                        "row": row_idx + 2,
                        "merchant": (row.get("交易对方") or "").strip(),
                        "product": product,
                        "direction": direction,
                        "amount": amount,
                        "trans_type": trans_type,
                        "llm_failed": True,
                        "llm_reason": llm_res.get("reasoning", "")[:50],
                    })

            # --- kind ---
            if status == "refunded":
                kind = "income"  # refund is income (offset original expense)
            elif direction == "支出":
                kind = "expense"
            elif direction == "收入":
                kind = "income"
            elif direction == "不计收支":
                kind = "expense"  # direction doesn't matter for internal_transfer
            else:
                kind = "expense"

            # --- 交易时间 ---
            time_str = (row.get("交易时间") or "").strip()
            occurred_at = _parse_datetime(time_str)
            if occurred_at is None:
                skipped_invalid += 1
                errors.append({"row": row_idx + 2, "error": f"invalid 交易时间: {time_str}"})
                continue

            # --- 交易对方 ---
            merchant = (row.get("交易对方") or "").strip() or None

            # Track status counts
            status_counts[status] = status_counts.get(status, 0) + 1

            # Track amounts
            if status == "success":
                if kind == "expense":
                    amount_summary["expense_success"] += amount
                else:
                    amount_summary["income_success"] += amount
            elif status == "refunded":
                amount_summary["refunded_total"] += amount
            elif status == "internal_transfer":
                amount_summary["internal_transfer_total"] += amount

            entry = {
                "kind": kind,
                "amount": amount,
                "category": "其他",  # placeholder, LLM will override
                "occurred_at": occurred_at,
                "merchant": merchant,
                "note": note,
                "payment_method": payment_method,
                "status": status,
                "source_agent": "openclaw:unified_import",
                "source_text": f"unified import: {csv_path}",
            }
            entries.append(entry)

            if len(samples) < 5:
                samples.append({
                    "kind": entry["kind"],
                    "amount": entry["amount"],
                    "category": entry["category"],
                    "occurred_at": entry["occurred_at"],
                    "merchant": entry["merchant"],
                    "note": entry["note"],
                    "payment_method": entry["payment_method"],
                    "status": entry["status"],
                    "source_agent": entry["source_agent"],
                })

        except Exception as e:
            skipped_invalid += 1
            errors.append({"row": row_idx + 2, "error": str(e)})

    # Round amounts
    for k in amount_summary:
        amount_summary[k] = round(amount_summary[k], 2)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "parsed": len(entries),
            "skipped_invalid": skipped_invalid,
            "errors": errors,
            "samples": samples,
            "status_counts": status_counts,
            "amount_summary": amount_summary,
            "llm_fallback_count": llm_fallback_count,
            "llm_fallback_failed_count": llm_fallback_failed_count,
            "llm_fallback_rows": llm_fallback_rows,
        }

    if not entries:
        return {
            "ok": True,
            "parsed": 0,
            "inserted": 0,
            "skipped_duplicate": 0,
            "skipped_invalid": skipped_invalid,
            "errors": errors,
            "samples": [],
            "status_counts": status_counts,
            "amount_summary": amount_summary,
            "llm_fallback_count": llm_fallback_count,
            "llm_fallback_failed_count": llm_fallback_failed_count,
            "llm_fallback_rows": llm_fallback_rows,
        }

    result = tm_expense.expense_write(
        action="batch_record",
        entries=entries,
        confirm_new_category=True,
    )
    return {
        "ok": result.get("ok", False),
        "parsed": len(entries),
        "inserted": result.get("inserted", 0),
        "skipped_duplicate": result.get("skipped_duplicate", 0),
        "skipped_invalid": skipped_invalid,
        "errors": errors + result.get("errors", []),
        "samples": samples,
        "status_counts": status_counts,
        "amount_summary": amount_summary,
        "llm_fallback_count": llm_fallback_count,
        "llm_fallback_failed_count": llm_fallback_failed_count,
        "llm_fallback_rows": llm_fallback_rows,
    }


def _infer_status(direction: str, amount: float, product: str, remark: str,
                  trans_type: str = "") -> str:
    """
    Infer transaction status. Returns one of:
    'success' | 'refunded' | 'closed' | 'internal_transfer' | 'unknown'

    'unknown' triggers LLM fallback in caller.
    """
    direction = direction.strip()
    trans_type = trans_type.strip()

    # 1. 交易类型列优先（明确语义）
    if trans_type in ("退款", "退货", "已退款"):
        return "refunded"
    if trans_type in ("信用卡还款", "余额宝转入", "余额宝转出", "余利宝转入",
                       "余利宝转出", "理财申购", "理财赎回", "转账给自己",
                       "零钱通转入", "零钱通转出"):
        return "internal_transfer"

    # 2. 金额 0 → 授权解冻等
    if amount == 0.0:
        return "closed"

    # 3. 商品说明/备注明确退款关键字（双字以上，避免单字误伤）
    refund_keywords = ["退款", "退货", "已退", "退还款", "原路退回"]
    if any(kw in product for kw in refund_keywords) or \
       any(kw in remark for kw in refund_keywords):
        return "refunded"

    # 4. 收/支列
    if direction == "支出":
        return "success"
    if direction == "收入":
        return "success"
    if direction == "不计收支":
        return "internal_transfer"

    # 5. 兜底失败 → 让 caller 走 LLM 兜底
    return "unknown"


def _parse_datetime(s: str) -> str | None:
    """Parse datetime formats to ISO 8601."""
    s = s.strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日 %H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None
