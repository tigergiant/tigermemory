#!/usr/bin/env python3
"""
tools/tm_expense_digest.py — monthly digest CLI for expense tracker.

Usage:
    python tools/tm_expense_digest.py --month 2026-05 [--output PATH]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

# Import from tm_expense
from tm_expense import DB_PATH, _TZ_CN, expense_read


def digest(month: str, output_path: Path | None = None) -> dict:
    """Generate monthly digest markdown report."""
    try:
        # Parse month (YYYY-MM format)
        year, month_num = month.split("-")
        year, month_num = int(year), int(month_num)
    except Exception:
        return {"ok": False, "reason": "month must be in YYYY-MM format"}

    # Calculate date range for the month
    start_date = f"{year:04d}-{month_num:02d}-01T00:00:00+08:00"
    if month_num == 12:
        end_date = f"{year+1:04d}-01-01T00:00:00+08:00"
    else:
        end_date = f"{year:04d}-{month_num+1:02d}-01T00:00:00+08:00"

    # 1. Get monthly summary (expense/income/net)
    agg = expense_read(
        mode="aggregate",
        start_date=start_date,
        end_date=end_date,
        group_by=["kind"],
        metric="sum",
    )
    if not agg["ok"]:
        return {"ok": False, "reason": "failed to get monthly summary"}

    expense_total = 0.0
    income_total = 0.0
    for g in agg.get("groups", []):
        kind = g.get("kind")
        total = g.get("total")
        if total is None:
            continue
        if kind == "expense":
            expense_total = float(total)
        elif kind == "income":
            income_total = float(total)
    net_flow = income_total - expense_total

    # 2. Get category top 10
    cat_agg = expense_read(
        mode="aggregate",
        start_date=start_date,
        end_date=end_date,
        kind="expense",
        group_by=["category"],
        metric="sum",
        limit=10,
        order_by="total DESC",
    )
    if cat_agg["ok"] and cat_agg.get("groups"):
        cat_total = sum(g.get("total", 0) for g in cat_agg["groups"])
        for g in cat_agg["groups"]:
            g["pct"] = (g.get("total", 0) / cat_total * 100) if cat_total > 0 else 0

    # 3. Get merchant top 10
    merch_agg = expense_read(
        mode="aggregate",
        start_date=start_date,
        end_date=end_date,
        kind="expense",
        group_by=["merchant"],
        metric="sum",
        limit=10,
        order_by="total DESC",
    )
    if merch_agg["ok"] and merch_agg.get("groups"):
        merch_total = sum(g.get("total", 0) for g in merch_agg["groups"])
        for g in merch_agg["groups"]:
            g["pct"] = (g.get("total", 0) / merch_total * 100) if merch_total > 0 else 0

    # 4. Get anomalies
    anomaly = expense_read(
        mode="anomaly",
        start_date=start_date,
        end_date=end_date,
        anomaly_window_days=90,
        anomaly_sigma=2.0,
    )

    # 5. Get MoM comparison
    compare = expense_read(
        mode="compare",
        compare="mom",
        start_date=start_date,
        end_date=end_date,
        group_by=["kind"],
    )

    # 6. Get budget status
    budget = expense_read(
        mode="budget_status",
        start_date=start_date,
        end_date=end_date,
    )

    # Build markdown report
    lines = [
        f"# 月度账单摘要 - {month}",
        "",
        "## 本月概览",
        f"- 支出: ¥{expense_total:.2f}",
        f"- 收入: ¥{income_total:.2f}",
        f"- 净流: ¥{net_flow:.2f}",
        "",
        "## 分类 Top 10",
        ""
    ]
    if cat_agg["ok"] and cat_agg["groups"]:
        lines.append("| 分类 | 金额 | 占比 |")
        lines.append("|------|------|------|")
        for g in cat_agg["groups"]:
            total = g.get("total", 0)
            lines.append(f"| {g['category']} | ¥{total:.2f} | {g.get('pct', 0):.1f}% |")
    else:
        lines.append("(无数据)")

    lines.extend([
        "",
        "## 商家 Top 10",
        ""
    ])
    if merch_agg["ok"] and merch_agg.get("groups"):
        lines.append("| 商家 | 金额 | 占比 |")
        lines.append("|------|------|------|")
        for g in merch_agg["groups"]:
            total = g.get("total", 0)
            merch_name = g.get("merchant") or "(未分类)"
            lines.append(f"| {merch_name} | ¥{total:.2f} | {g.get('pct', 0):.1f}% |")
    else:
        lines.append("(无数据)")

    lines.extend([
        "",
        "## 异常笔列表",
        ""
    ])
    if anomaly["ok"] and anomaly.get("anomalies"):
        lines.append("| ID | 金额 | 分类 | 商家 | 备注 |")
        lines.append("|----|------|------|------|------|")
        for a in anomaly["anomalies"]:
            lines.append(f"| {a['id']} | ¥{a['amount']:.2f} | {a.get('category', '')} | {a.get('merchant', '')} | {a.get('note', '')} |")
    else:
        lines.append("(无异常)")

    lines.extend([
        "",
        "## 与上月环比",
        ""
    ])
    if compare["ok"] and compare["groups"]:
        lines.append("| 分类 | 本月 | 上月 | 变动 | 变动率 |")
        lines.append("|------|------|------|------|--------|")
        for g in compare["groups"]:
            delta_pct = f"{g['delta_pct']:+.1f}%" if g.get("delta_pct") is not None else "N/A"
            lines.append(f"| {g.get('kind', '')} | ¥{g['current']:.2f} | ¥{g['previous']:.2f} | ¥{g['delta']:.2f} | {delta_pct} |")
    else:
        lines.append("(无数据)")

    lines.extend([
        "",
        "## 预算执行情况",
        ""
    ])
    if budget["ok"] and budget.get("budgets"):
        lines.append("| 分类 | 预算 | 已用 | 剩余 | 进度 |")
        lines.append("|------|------|------|------|------|")
        for b in budget["budgets"]:
            progress = f"{b['progress']:.1f}%" if b.get("progress") is not None else "N/A"
            lines.append(f"| {b.get('category', '')} | ¥{b['budget']:.2f} | ¥{b['spent']:.2f} | ¥{b['remaining']:.2f} | {progress} |")
    else:
        lines.append("(无预算)")

    content = "\n".join(lines)

    # Write to file
    if output_path is None:
        output_path = DB_PATH.parent / "digests" / f"digest-{month}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    return {
        "ok": True,
        "month": month,
        "output_path": str(output_path),
        "expense_total": expense_total,
        "income_total": income_total,
        "net_flow": net_flow,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate monthly expense digest")
    ap.add_argument("--month", type=str, required=True, help="Month in YYYY-MM format (e.g., 2026-05)")
    ap.add_argument("--output", type=Path, default=None, help="Output path (default: data/expense_tracker/digests/digest-YYYY-MM.md)")
    args = ap.parse_args()
    result = digest(month=args.month, output_path=args.output)
    print(f"ok: {result['ok']}")
    if result.get("ok"):
        print(f"month: {result['month']}")
        print(f"output: {result['output_path']}")
        print(f"expense: ¥{result['expense_total']:.2f}")
        print(f"income: ¥{result['income_total']:.2f}")
        print(f"net: ¥{result['net_flow']:.2f}")
    else:
        print(f"reason: {result.get('reason')}")
    sys.exit(0 if result.get("ok") else 1)
