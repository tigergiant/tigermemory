"""TradingAgents stability evaluation runner.

The evaluator fans out multiple deep-dive jobs for the same ticker/date/profile,
waits for completion, compares final decisions, and writes a compact consensus
report to the investment decision log.
Inputs: CLI args, fixture cases, trace JSONL, wiki/Mem0 data, or local index files as selected by the command.
Outputs: Search/eval/trace/index reports printed to stdout or written to the requested output path.
Depends-on (must-have): tm_core search/memory helpers, local Markdown/JSONL files, and optional configured LLM or embedding providers.
"""

from __future__ import annotations

import datetime
import json
import math
import pathlib
import re
import time
from collections import Counter
from itertools import combinations
from typing import Any

import tm_core
import tm_deep_dive_jobs


LABELS = {"stable", "edge", "unstable", "single"}
CN_LABELS = {
    "stable": "稳定",
    "edge": "边缘稳定",
    "unstable": "不稳定",
    "single": "单跑",
}


def _today_cn() -> str:
    return datetime.datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d")


def _decision_log_root() -> pathlib.Path:
    return tm_core.REPO_ROOT / "wiki" / "investment" / "decision-log"


def _safe_eval_slug(ticker: str, trade_date: str) -> str:
    base = ticker.strip().upper().split(".")[0]
    return f"{base}-{trade_date}-stability-eval.md"


def _monthly_log_path(ticker: str, trade_date: str) -> pathlib.Path:
    month = trade_date[:7]
    return _decision_log_root() / f"{ticker.strip().upper()}-{month}.md"


def _eval_path(ticker: str, trade_date: str) -> pathlib.Path:
    return _decision_log_root() / _safe_eval_slug(ticker, trade_date)


def _parse_price_target(result: dict[str, Any]) -> float | None:
    raw = result.get("price_target")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    markdown = str(result.get("decision_markdown") or "")
    match = re.search(r"\*\*Price Target\*\*\s*:\s*([0-9]+(?:\.[0-9]+)?)", markdown, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _extract_thesis_text(result: dict[str, Any]) -> str:
    markdown = str(result.get("decision_markdown") or "")
    match = re.search(
        r"\*\*Investment Thesis\*\*\s*:\s*(.*?)(?:\n\n\*\*Price Target\*\*|\n\n\*\*Time Horizon\*\*|$)",
        markdown,
        re.IGNORECASE | re.DOTALL,
    )
    text = match.group(1) if match else markdown
    return text.strip()


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z][a-z0-9_\-]{2,}", lowered))
    chinese_terms = set(re.findall(r"[\u4e00-\u9fff]{2,8}", text))
    numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))
    stop = {
        "this",
        "that",
        "with",
        "from",
        "and",
        "the",
        "for",
        "是",
        "但",
        "因此",
        "当前",
        "公司",
        "需要",
    }
    return {item for item in words | chinese_terms | numbers if item not in stop}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _mean_pairwise_jaccard(token_sets: list[set[str]]) -> float:
    if len(token_sets) < 2:
        return 1.0
    scores = [_jaccard(a, b) for a, b in combinations(token_sets, 2)]
    return sum(scores) / len(scores) if scores else 1.0


def _provider_completion(result: dict[str, Any]) -> dict[str, Any]:
    by_provider = ((result.get("provider_trace") or {}).get("by_provider") or {})
    ok = 0
    error = 0
    for key, count in by_provider.items():
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        if str(key).endswith(":ok"):
            ok += value
        elif str(key).endswith(":error"):
            error += value
    total = ok + error
    return {
        "ok": ok,
        "error": error,
        "total": total,
        "rate": round(ok / total, 4) if total else None,
    }


def classify_stability(
    *,
    ratings: list[str | None],
    jaccard_mean: float,
    failed_count: int,
) -> str:
    if failed_count:
        return "unstable"
    if len(ratings) <= 1:
        return "single"
    present = [item for item in ratings if item]
    if not present:
        return "unstable"
    counts = Counter(present)
    if len(counts) == 1 and jaccard_mean >= 0.6:
        return "stable"
    if jaccard_mean < 0.4:
        return "unstable"
    if counts.most_common(1)[0][1] >= len(ratings) - 1 or 0.4 <= jaccard_mean < 0.6:
        return "edge"
    return "unstable"


def _consensus_rating(ratings: list[str | None]) -> str | None:
    present = [item for item in ratings if item]
    if not present:
        return None
    counts = Counter(present)
    top_count = counts.most_common(1)[0][1]
    winners = sorted(rating for rating, count in counts.items() if count == top_count)
    return winners[0] if len(winners) == 1 else None


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failed_count = 0
    for idx, result in enumerate(results, start=1):
        status = result.get("status") or result.get("job_status", {}).get("status") or "completed"
        ok = result.get("ok") is not False and status != "failed"
        if not ok:
            failed_count += 1
        rating = result.get("rating") or result.get("processed_signal")
        price_target = _parse_price_target(result)
        thesis = _extract_thesis_text(result)
        rows.append(
            {
                "run": idx,
                "job_id": result.get("job_id"),
                "ok": ok,
                "status": status,
                "rating": rating,
                "price_target": price_target,
                "thesis_text": thesis,
                "tokens": sorted(_tokens(thesis)),
                "provider_completion": _provider_completion(result),
                "warnings": result.get("warnings") or [],
            }
        )
    ratings = [row["rating"] for row in rows]
    token_sets = [set(row["tokens"]) for row in rows]
    jaccard_mean = round(_mean_pairwise_jaccard(token_sets), 3)
    label = classify_stability(ratings=ratings, jaccard_mean=jaccard_mean, failed_count=failed_count)
    consensus = _consensus_rating(ratings)
    price_values = [row["price_target"] for row in rows if row["price_target"] is not None]
    rating_score = Counter(r for r in ratings if r).most_common(1)[0][1] / len(ratings) if any(ratings) else 0.0
    completeness = len(price_values) / len(rows) if rows else 0.0
    score = round(max(0.0, min(1.0, rating_score * 0.45 + jaccard_mean * 0.4 + completeness * 0.15)), 3)
    return {
        "stability": label,
        "stability_label": label,
        "stability_label_cn": CN_LABELS[label],
        "consensus_rating": consensus,
        "score": score,
        "stability_score": score,
        "rating_counts": dict(Counter(r for r in ratings if r)),
        "ratings": ratings,
        "price_targets": price_values,
        "price_target_complete": len(price_values) == len(rows),
        "jaccard_mean": jaccard_mean,
        "failed_count": failed_count,
        "runs": rows,
    }


def _render_eval_markdown(ticker: str, trade_date: str, profile: str, summary: dict[str, Any]) -> str:
    rows = summary["runs"]
    price_targets = ", ".join("" if row["price_target"] is None else str(row["price_target"]) for row in rows)
    lines = [
        "---",
        "owner: codex",
        "status: generated",
        f"updated: {_today_cn()}",
        f'title: "{ticker} Stability Eval"',
        "---",
        "",
        f"# {ticker} Stability Eval",
        "",
        "## TL;DR",
        "",
        f"- 判定：**{summary['stability_label_cn']}** (`{summary['stability']}`)。",
        f"- Consensus rating：`{summary.get('consensus_rating') or 'None'}`。",
        f"- Stability score：`{summary['stability_score']}`。",
        f"- Ratings：{', '.join(str(item) for item in summary['ratings'])}.",
        f"- Price targets：{price_targets}.",
        f"- Thesis Jaccard mean：`{summary['jaccard_mean']}`。",
        "",
        "## Runs",
        "",
        "| run | job_id | status | rating | price_target | provider_ok/error/rate | warnings |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        provider = row["provider_completion"]
        lines.append(
            "| {run} | `{job_id}` | {status} | {rating} | {price_target} | {ok}/{error}/{rate} | {warnings} |".format(
                run=row["run"],
                job_id=row.get("job_id") or "",
                status=row.get("status") or "",
                rating=row.get("rating") or "",
                price_target="" if row["price_target"] is None else row["price_target"],
                ok=provider["ok"],
                error=provider["error"],
                rate="" if provider["rate"] is None else provider["rate"],
                warnings=", ".join(str(item) for item in row.get("warnings") or []),
            )
        )
    lines.extend(
        [
            "",
            "## 判定规则",
            "",
            "- `stable`: 3 跑 rating 全一致且 Jaccard >= 0.6。",
            "- `edge`: 3 跑 2 致 1 异或 Jaccard 0.4-0.6，但不触发 unstable 条件。",
            "- `unstable`: 任意一跑 fail、3 跑全异、或 Jaccard < 0.4。",
            "- `single`: 只有 1 跑。",
            "",
            "## 结论",
            "",
            f"- `{ticker}` / `{trade_date}` / `{profile}` 当前标签为 **{summary['stability_label_cn']}**。",
            "- 不稳定或边缘稳定时，不应只按单次评级直出；需要保留人工复核提示。",
            "",
        ]
    )
    return "\n".join(lines)


def _append_monthly_log(ticker: str, trade_date: str, summary: dict[str, Any], eval_file: pathlib.Path) -> None:
    path = _monthly_log_path(ticker, trade_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    month = trade_date[:7]
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = "\n".join(
            [
                "---",
                "owner: codex",
                "status: generated",
                f"updated: {_today_cn()}",
                f'title: "TradingAgents Decision Log {ticker}"',
                "---",
                "",
                f"# TradingAgents Decision Log {ticker}",
                "",
                f"## {month}",
                "",
            ]
        )
    rel = eval_file.relative_to(_decision_log_root()).as_posix()
    line = (
        f"- {trade_date} stability eval: "
        f"stability_label=`{summary['stability_label']}`, "
        f"stability_score=`{summary['stability_score']}`, "
        f"consensus_rating=`{summary.get('consensus_rating') or 'None'}` "
        f"-> [{rel}]({rel})"
    )
    if line not in text:
        if f"## {month}" not in text:
            text = text.rstrip() + f"\n\n## {month}\n"
        text = text.rstrip() + "\n\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def _wait_for_jobs(job_ids: list[str], *, poll_interval_sec: float, timeout_sec: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    terminal: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        for job_id in job_ids:
            if job_id in terminal:
                continue
            status = tm_deep_dive_jobs.get_status(job_id)
            if status.get("status") in tm_deep_dive_jobs.TERMINAL_STATUSES:
                terminal[job_id] = status
        if len(terminal) == len(job_ids):
            break
        time.sleep(poll_interval_sec)
    results: list[dict[str, Any]] = []
    for job_id in job_ids:
        status = terminal.get(job_id) or tm_deep_dive_jobs.get_status(job_id)
        if status.get("status") == "completed":
            results.append(tm_deep_dive_jobs.fetch_result(job_id))
        else:
            results.append({"ok": False, "job_id": job_id, "status": status.get("status"), "job_status": status})
    return results


def start_stability_eval(
    ticker: str,
    trade_date: str,
    profile: str = "deep",
    n: int = 3,
    *,
    poll_interval_sec: float = 30.0,
    timeout_sec: int = 3600,
) -> dict[str, Any]:
    ticker = tm_deep_dive_jobs.validate_ticker(ticker)
    trade_date = tm_deep_dive_jobs.validate_trade_date(trade_date)
    profile = tm_deep_dive_jobs.validate_profile(profile)
    n = int(n)
    if n < 1 or n > 5:
        raise ValueError("n must be between 1 and 5")
    started = [tm_deep_dive_jobs.start_job(ticker, trade_date, profile=profile) for _ in range(n)]
    job_ids = [item["job_id"] for item in started]
    results = _wait_for_jobs(job_ids, poll_interval_sec=poll_interval_sec, timeout_sec=timeout_sec)
    summary = summarize_results(results)
    eval_file = _eval_path(ticker, trade_date)
    eval_file.parent.mkdir(parents=True, exist_ok=True)
    eval_file.write_text(_render_eval_markdown(ticker, trade_date, profile, summary), encoding="utf-8")
    _append_monthly_log(ticker, trade_date, summary, eval_file)
    return {
        "ok": True,
        "ticker": ticker,
        "trade_date": trade_date,
        "profile": profile,
        "n": n,
        "job_ids": job_ids,
        "stability": summary["stability"],
        "stability_label": summary["stability_label"],
        "stability_label_cn": summary["stability_label_cn"],
        "consensus_rating": summary.get("consensus_rating"),
        "score": summary["stability_score"],
        "stability_score": summary["stability_score"],
        "jaccard_mean": summary["jaccard_mean"],
        "rating_counts": summary["rating_counts"],
        "eval_path": str(eval_file),
        "monthly_log_path": str(_monthly_log_path(ticker, trade_date)),
        "summary": summary,
    }
