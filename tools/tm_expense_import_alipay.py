#!/usr/bin/env python3
"""tools/tm_expense_import_alipay.py — P4 alipay CSV bill importer."""

import csv
import datetime
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tm_expense


def import_csv(csv_path: str, dry_run: bool = False) -> dict[str, Any]:
    """Parse alipay CSV bill and write via batch_record.

    Returns:
        {"ok": True, "parsed": N, "inserted": M, "skipped_duplicate": K,
         "skipped_invalid": J, "errors": [...], "samples": [...]}
    """
    path = Path(csv_path)
    if not path.exists():
        return {"ok": False, "error": f"file not found: {csv_path}"}

    # Read CSV with encoding detection (alipay exports are UTF-8 with BOM or GBK)
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

    # Normalize field names (strip whitespace, handle BOM)
    fieldnames = [f.strip().lstrip("\ufeff") for f in reader.fieldnames]
    reader.fieldnames = fieldnames

    entries: list[dict] = []
    skipped_invalid = 0
    errors: list[dict] = []
    samples: list[dict] = []

    for row_idx, row in enumerate(reader):
        # Skip empty rows
        if not any(v.strip() for v in row.values() if v):
            continue

        try:
            # --- 收/支 ---
            direction = (row.get("收/支") or "").strip()
            if direction == "不计收支":
                kind = "expense"
                entry_status = "internal_transfer"
            elif direction == "支出":
                kind = "expense"
                entry_status = "success"
            elif direction == "收入":
                kind = "income"
                entry_status = "success"
            else:
                skipped_invalid += 1
                errors.append({"row": row_idx + 2, "error": f"unknown 收/支: {direction}"})
                continue

            # --- 交易状态 ---
            txn_status = (row.get("交易状态") or "").strip()
            if txn_status in ("交易关闭", "关闭"):
                entry_status = "closed"
            elif txn_status not in ("交易成功", "支付成功"):
                # Refund statuses
                if "退款" in txn_status or "退" in txn_status:
                    entry_status = "refunded"
                    kind = "income"
                else:
                    skipped_invalid += 1
                    errors.append({"row": row_idx + 2, "error": f"unknown 交易状态: {txn_status}"})
                    continue

            # --- 金额 ---
            amount_str = (row.get("金额") or "0").strip()
            try:
                amount = abs(float(amount_str))
            except ValueError:
                skipped_invalid += 1
                errors.append({"row": row_idx + 2, "error": f"invalid amount: {amount_str}"})
                continue
            if amount == 0:
                entry_status = "closed"

            # --- 交易时间 ---
            time_str = (row.get("交易时间") or "").strip()
            occurred_at = _parse_datetime(time_str)
            if occurred_at is None:
                skipped_invalid += 1
                errors.append({"row": row_idx + 2, "error": f"invalid 交易时间: {time_str}"})
                continue

            # --- 交易对方 ---
            merchant = (row.get("交易对方") or "").strip() or None

            # --- 商品 + 备注 ---
            product = (row.get("商品") or "").strip()
            remark = (row.get("备注") or "").strip()
            note_parts = [p for p in (product, remark) if p]
            note = " | ".join(note_parts) if note_parts else None

            # --- 交易订单号 ---
            source_external_id = (row.get("交易订单号") or "").strip() or None

            # --- 交易类型（用于判断是否需要 LLM） ---
            txn_type = (row.get("交易类型") or "").strip()

            # Determine category
            category = _infer_category(txn_type, merchant, note)

            entry = {
                "kind": kind,
                "amount": amount,
                "category": category,
                "occurred_at": occurred_at,
                "merchant": merchant,
                "note": note,
                "payment_method": "alipay",
                "source_external_id": source_external_id,
                "source_agent": "cascade",
                "source_text": f"alipay import: {csv_path}",
                "status": entry_status,
            }
            entries.append(entry)

            if len(samples) < 5:
                samples.append({k: v for k, v in entry.items() if k != "source_text"})

        except Exception as e:
            skipped_invalid += 1
            errors.append({"row": row_idx + 2, "error": str(e)})

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "parsed": len(entries),
            "skipped_invalid": skipped_invalid,
            "errors": errors,
            "samples": samples,
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
    }


def _parse_datetime(s: str) -> str | None:
    """Parse alipay datetime formats to ISO 8601."""
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


def _infer_category(txn_type: str, merchant: str | None, note: str | None) -> str:
    """Infer category from transaction type / merchant / note.

    Returns a category string. For ambiguous cases, returns a placeholder
    that the caller should handle with auto_classify.
    """
    txn_type_lower = txn_type.lower().strip()
    merchant_lower = (merchant or "").lower().strip()
    note_lower = (note or "").lower().strip()

    # Known alipay transaction types → direct mapping
    type_map = {
        "餐饮美食": "餐饮",
        "餐饮": "餐饮",
        "交通出行": "交通",
        "交通": "交通",
        "购物消费": "购物",
        "购物": "购物",
        "生活缴费": "居家",
        "住房": "居家",
        "休闲娱乐": "娱乐",
        "娱乐": "娱乐",
        "医疗健康": "医疗",
        "医疗": "医疗",
        "教育培训": "教育",
        "教育": "教育",
        "通讯物流": "通讯",
        "通讯": "通讯",
        "投资理财": "投资",
        "投资": "投资",
    }
    for key, cat in type_map.items():
        if key in txn_type_lower:
            return cat

    # Merchant name heuristics
    merchant_heuristics = [
        (["星巴克", "瑞幸", "麦当劳", "肯德基", "海底捞", "喜茶", "奈雪", "costa",
          "starbucks", "mcdonald", "kfc", "pizza", "咖啡", "奶茶", "餐厅", "饭店",
          "小吃", "外卖", "美团", "饿了么"], "餐饮"),
        (["滴滴", "地铁", "公交", "高铁", "火车", "机票", "航空", "加油站",
          "中石化", "中石油", "etc", "高速"], "交通"),
        (["淘宝", "京东", "拼多多", "天猫", "苏宁", "超市", "商场",
          "沃尔玛", "盒马", "便利店"], "购物"),
        (["房租", "水电", "燃气", "物业", "清洁", "家政"], "居家"),
        (["电影", "游戏", "ktv", "旅游", "景点", "门票", "酒店", "民宿",
          "网吧", "剧本杀", "密室"], "娱乐"),
        (["医院", "药房", "诊所", "体检", "医保"], "医疗"),
        (["书", "课", "培训", "考试", "学费"], "教育"),
        (["话费", "流量", "宽带", "快递", "中国移动", "中国联通", "中国电信"], "通讯"),
    ]
    for keywords, cat in merchant_heuristics:
        for kw in keywords:
            if kw in merchant_lower or kw in note_lower:
                return cat

    # Default: use "其他" — caller can override with auto_classify
    return "其他"
