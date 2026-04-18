#!/usr/bin/env python3
"""
tools/tm_inbox_triage.py — weekly inbox triage.

Classify each inbox/*.md by L2 review score + target-page existence.
Write a decision dashboard to wiki/operations/inbox-triage.md.
Does NOT auto-promote to wiki — that's human/Cascade decision.

Categories:
  ready          score >= 70 and target wiki page does NOT exist → new page candidate
  merge          score >= 70 and target wiki page EXISTS → needs semantic merge
  review         30 <= score < 70 → Cascade judgment
  reject         score < 30 → archive or delete
  stale          age > 30d (regardless of score) → archive
  review_error   tm_review raised
  review_skipped DeepSeek API unreachable (API blip)
  malformed      inbox filename does not match convention

Run:
    python3 tools/tm_inbox_triage.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import tm_core
import tm_review

REPO = tm_core.REPO_ROOT
DASHBOARD_REL = "wiki/operations/inbox-triage.md"

STALE_AGE_DAYS = int(__import__("os").environ.get("TRIAGE_STALE_DAYS", 30))
READY_THRESHOLD = int(__import__("os").environ.get("TRIAGE_READY", 70))
REVIEW_THRESHOLD = int(__import__("os").environ.get("TRIAGE_REVIEW", 30))

INBOX_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<hhmm>\d{4})-(?P<agent>[a-z0-9\-]+)-(?P<topic>[a-z]+)\.md$"
)


def today() -> dt.date:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


def extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def slugify(title: str) -> str:
    if not title:
        return ""
    s = title.lower()
    s = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = s.strip("-")
    return s[:60]


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    return parts[2].strip()


def classify_file(path: pathlib.Path) -> dict:
    rel = path.relative_to(REPO).as_posix()
    m = INBOX_RE.search(path.name)
    if not m:
        return {"rel": rel, "category": "malformed", "reason": "filename not YYYY-MM-DD-HHMM-<agent>-<topic>.md"}

    agent = m.group("agent")
    topic = m.group("topic")
    try:
        created = dt.date.fromisoformat(m.group("date"))
    except ValueError:
        created = today()
    age_days = (today() - created).days

    text = path.read_text(encoding="utf-8", errors="ignore")
    body = strip_frontmatter(text)
    title = extract_title(text)

    base = {
        "rel": rel, "agent": agent, "topic": topic,
        "title": title, "age_days": age_days,
    }

    if age_days > STALE_AGE_DAYS:
        base["category"] = "stale"
        base["reason"] = f"age {age_days}d > {STALE_AGE_DAYS}d"
        return base

    try:
        result = tm_review.review_draft(body=body)
    except Exception as e:
        base["category"] = "review_error"
        base["reason"] = f"tm_review exception: {type(e).__name__}: {e}"
        return base

    if result.get("review_skipped"):
        base["category"] = "review_skipped"
        base["reason"] = result.get("reason", "review_skipped=true")
        return base

    score = result.get("score")
    if score is None:
        base["category"] = "review_skipped"
        base["reason"] = "score=null"
        return base
    base["score"] = score

    # Target inference: topic=cross has no partition
    target = None
    target_exists = False
    if topic != "cross" and title:
        slug = slugify(title)
        if slug:
            target_rel = f"wiki/{topic}/{slug}.md"
            target = target_rel
            target_exists = (REPO / target_rel).exists()
    base["target"] = target

    if score < REVIEW_THRESHOLD:
        base["category"] = "reject"
    elif score < READY_THRESHOLD:
        base["category"] = "review"
    else:
        if target is None:
            base["category"] = "review"
            base["reason"] = "cross-partition or missing title, no auto-target"
        elif target_exists:
            base["category"] = "merge"
        else:
            base["category"] = "ready"

    return base


def render_dashboard(items: list[dict]) -> str:
    buckets: dict[str, list[dict]] = {
        "ready": [], "merge": [], "review": [], "reject": [],
        "stale": [], "review_error": [], "review_skipped": [], "malformed": [],
    }
    for it in items:
        buckets.setdefault(it["category"], []).append(it)

    lines = [
        "---",
        "owner: linter",
        "status: active",
        f"updated: {today().isoformat()}",
        "---",
        "",
        "# Inbox Triage Dashboard",
        "",
        "## 摘要",
        "",
        "本页由 linter 每周覆盖。对 `inbox/*.md` 按 L2 评审分数 + 目标 wiki 页存在性分类，输出「下一步该做什么」的建议。",
        "",
        "不自动合并——分类结果供 Cascade / 人类判断后执行。",
        "",
        "## 已验证现状",
        "",
        f"扫描时间：{today().isoformat()} (UTC+8)。inbox 文件总数：{len(items)}。",
        "",
    ]

    labels = [
        ("ready", f"✅ Ready to promote（score ≥ {READY_THRESHOLD}，目标页不存在）"),
        ("merge", f"⚠️ Needs merge（score ≥ {READY_THRESHOLD}，目标页已存在）"),
        ("review", f"🟡 Needs review（{REVIEW_THRESHOLD} ≤ score < {READY_THRESHOLD}，或跨分区无目标）"),
        ("reject", f"❌ Reject（score < {REVIEW_THRESHOLD}）"),
        ("stale", f"💤 Stale（age > {STALE_AGE_DAYS}d，建议 archive）"),
        ("review_error", "🚫 Review error（tm_review 异常）"),
        ("review_skipped", "🔸 Review skipped（API blip / no key）"),
        ("malformed", "🔴 Malformed filename（inbox 命名违规）"),
    ]

    for key, label in labels:
        group = buckets.get(key, [])
        lines.append(f"### {label}: {len(group)}")
        lines.append("")
        if not group:
            lines.append("_无_")
            lines.append("")
            continue
        for it in group:
            parts = [f"`{it['rel']}`"]
            if "score" in it:
                parts.append(f"score={it['score']}")
            if it.get("target"):
                parts.append(f"→ `{it['target']}`")
            if "age_days" in it:
                parts.append(f"age={it['age_days']}d")
            if "reason" in it:
                parts.append(f"_{it['reason']}_")
            lines.append(f"- " + " ".join(parts))
        lines.append("")

    lines.append("## 来源")
    lines.append("")
    lines.append("- `inbox/*.md`（按 `<agent>-<topic>` 命名归类）")
    lines.append("- L2 评审：`tools/tm_review.py`")
    lines.append("- 分类逻辑：`tools/tm_inbox_triage.py`")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    items: list[dict] = []
    for p in sorted((REPO / "inbox").glob("*.md")):
        if p.name == ".gitkeep":
            continue
        items.append(classify_file(p))

    dashboard = render_dashboard(items)
    (REPO / DASHBOARD_REL).write_text(dashboard, encoding="utf-8")
    print(f"updated {DASHBOARD_REL}")

    summary: dict[str, int] = {}
    for it in items:
        summary[it["category"]] = summary.get(it["category"], 0) + 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
