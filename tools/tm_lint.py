#!/usr/bin/env python3
"""
tools/tm_lint.py — L4 daily linter runner.

Runs a configurable subset of lint checks, updates the dashboard page,
and writes an inbox findings file iff any issues found.
Inputs: Repository markdown/python files, frontmatter, section text, git diff inputs, or CLI path arguments.
Outputs: Deterministic reports, rewritten generated files, validation errors, or patch proposals.
Depends-on (must-have): Python stdlib plus tm_core/path parsers; no Mem0 write path unless explicitly invoked by caller.
"""
from __future__ import annotations

import datetime as dt
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

import tm_core
import tm_review  # for DeepSeek LLM calls on F/G

REPO = tm_core.REPO_ROOT
DASHBOARD_REL = "wiki/operations/lint-dashboard.md"

# Thresholds (defaults; overridable via env)
STALE_DAYS = int(os.environ.get("LINT_STALE_DAYS", 90))
INBOX_AGE_DAYS = int(os.environ.get("LINT_INBOX_AGE_DAYS", 14))
UNREVIEWED_WINDOW_DAYS = int(os.environ.get("LINT_UNREVIEWED_DAYS", 7))
# C (unreviewed commits) alert threshold: only trigger inbox findings if count
# reaches this number. Single [unreviewed] commits are normal API blips;
# sustained batches indicate DeepSeek outage or systemic evasion.
UNREVIEWED_ALERT_THRESHOLD = int(os.environ.get("LINT_UNREVIEWED_ALERT", 5))


def today() -> dt.date:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


def check_A_stale_pages() -> list[str]:
    """Pages with status: active and updated > STALE_DAYS ago."""
    findings = []
    cutoff = today() - dt.timedelta(days=STALE_DAYS)
    for p in REPO.glob("wiki/**/*.md"):
        if p.name == "index.md":
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if not text.startswith("---\n"):
            continue
        fm_end = text.find("\n---\n", 4)
        if fm_end < 0:
            continue
        fm = text[4:fm_end]
        status_m = re.search(r"^status:\s*(\S+)", fm, re.MULTILINE)
        updated_m = re.search(r"^updated:\s*(\d{4}-\d{2}-\d{2})", fm, re.MULTILINE)
        if not status_m or status_m.group(1) != "active":
            continue
        if not updated_m:
            continue
        try:
            d = dt.date.fromisoformat(updated_m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            rel = p.relative_to(REPO).as_posix()
            findings.append(f"{rel} last updated {d} ({(today()-d).days} days ago)")
    return findings


def check_B_inbox_aging() -> list[str]:
    """inbox/*.md whose filename date prefix is older than INBOX_AGE_DAYS."""
    findings = []
    cutoff = today() - dt.timedelta(days=INBOX_AGE_DAYS)
    for p in (REPO / "inbox").glob("*.md"):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", p.name)
        if not m:
            continue
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            findings.append(f"inbox/{p.name} ({(today()-d).days} days old)")
    return findings


def check_C_unreviewed_commits() -> int:
    """Count commits with '[unreviewed]' tag in the last UNREVIEWED_WINDOW_DAYS days."""
    since = f"{UNREVIEWED_WINDOW_DAYS}.days.ago"
    out = subprocess.run(
        ["git", "log", f"--since={since}", "--pretty=%s", "--grep=[unreviewed]", "--fixed-strings"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    lines = [ln for ln in out.stdout.splitlines() if "[unreviewed]" in ln]
    return len(lines)


def check_D_format_drift() -> list[str]:
    """Run tm_core.lint_page_errors on every wiki content page.

    index.md pages are TOC-style lists and exempt from the '## 摘要' /
    '## 来源' requirement — they should be skipped.
    """
    findings = []
    for p in REPO.glob("wiki/**/*.md"):
        if p.name == "index.md":
            continue
        rel = p.relative_to(REPO).as_posix()
        if tm_core.is_auto_generated_path(rel):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        errs = tm_core.lint_page_errors(text)
        if errs:
            findings.append(f"{rel}: {', '.join(errs)}")
    return findings


MD_PAGE_LINK_RE = re.compile(r"\]\(([^)#?]+\.md)(?:#[^)]+)?\)")

# Auto-generated / archival page prefixes that should not be linted as orphans.
# These pages are not meant to be manually linked from other wiki pages:
# - decision-log/**: TradingAgents auto-written per-ticker per-date analysis reports.
# - inbox-archive/**: Time-based inbox archives organized by date, not by topic links.
ORPHAN_AUTO_GENERATED_PREFIXES: tuple[str, ...] = (
    "wiki/investment/decision-log/",
    "wiki/operations/inbox-archive/",
)


def _is_orphan_excluded(rel_path: str) -> bool:
    """Return True if a wiki page should be excluded from E orphan check."""
    return any(rel_path.startswith(prefix) for prefix in ORPHAN_AUTO_GENERATED_PREFIXES)


def check_E_orphan_pages() -> list[str]:
    """Wiki pages not referenced by any other wiki page via relative link.

    Auto-generated and archival pages (see ORPHAN_AUTO_GENERATED_PREFIXES) are
    excluded because they are not meant to be linked manually.
    """
    all_pages = {
        p.relative_to(REPO).as_posix() for p in REPO.glob("wiki/**/*.md")
        if p.name != "index.md"
        and not _is_orphan_excluded(p.relative_to(REPO).as_posix())
    }
    linked = set()
    for src in REPO.glob("wiki/**/*.md"):
        src_text = src.read_text(encoding="utf-8", errors="ignore")
        for m in MD_PAGE_LINK_RE.finditer(src_text):
            link = m.group(1)
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link):
                continue
            # Resolve relative to the page containing the link.
            resolved = (src.parent / link).resolve()
            try:
                rel = resolved.relative_to(REPO).as_posix()
                linked.add(rel)
            except ValueError:
                pass
    orphans = sorted(all_pages - linked)
    return orphans


# --- F: Missing sources (LLM) ---
MISSING_SRC_PROMPT = """你是 tigermemory wiki 页面的审查员。判断这篇页面是否有"事实断言但缺少来源"的问题。

规则：
- 页面的"## 来源"章节是否为空 / 只有占位 / 链接无效（不像真实来源）
- 如果正文里有具体事实（数字、日期、人名、API endpoint、机器 IP 等）而"## 来源"没有任何可追溯的原始出处，算违规
- 如果正文只是设计/规划/推断（没有需要外部证据的事实），缺来源也不算违规

输出严格 JSON：
{
  "missing_sources": <true | false>,
  "reason": <不超过 80 字的具体说明>
}

只输出 JSON，不要加解释。
"""


def check_F_missing_sources() -> list[str]:
    """LLM judges each wiki page for unsupported factual claims."""
    findings = []
    for p in REPO.glob("wiki/**/*.md"):
        if p.name == "index.md":
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        # skip very short pages
        if len(text) < 500:
            continue
        # call DeepSeek via tm_review's low-level helper (reuse the opener pattern)
        verdict = _deepseek_judge(MISSING_SRC_PROMPT, text)
        if verdict is None:
            continue  # skip on API fail
        if verdict.get("missing_sources") is True:
            rel = p.relative_to(REPO).as_posix()
            findings.append(f"{rel}: {verdict.get('reason', '')}")
    return findings


# --- G: Contradictions (LLM, pairwise within partition) ---
CONTRADICTION_PROMPT = """你是 tigermemory wiki 的一致性审查员。对比两篇页面，判断它们是否存在事实冲突。

规则：
- 只标"硬冲突"：同一事实，两页说法互斥（如 A 页说"端口是 9766"，B 页说"端口是 8766"）
- 不要标"措辞差异"或"细节粒度不同"或"一页更新/一页陈旧"（那是陈旧，不是冲突）
- 如果两页主题根本不重叠，直接说 false

输出严格 JSON：
{
  "contradicts": <true | false>,
  "reason": <不超过 100 字，引用冲突的具体句子>
}

只输出 JSON，不要加解释。
"""


def check_G_contradictions() -> list[str]:
    """Pairwise LLM check within each partition's pages. Quadratic, so partition-scoped."""
    findings = []
    for partition in tm_core.PARTITION_OWNERS:
        pages = sorted(REPO.glob(f"wiki/{partition}/*.md"))
        pages = [p for p in pages if p.name != "index.md"]
        # Budget cap: only top 20 largest pages per partition, and only pairs where both > 1KB
        pages = [p for p in pages if p.stat().st_size > 1024]
        pages = sorted(pages, key=lambda p: p.stat().st_size, reverse=True)[:20]
        for i, a in enumerate(pages):
            for b in pages[i+1:]:
                text_a = a.read_text(encoding="utf-8", errors="ignore")
                text_b = b.read_text(encoding="utf-8", errors="ignore")
                user_msg = f"# 页面 A: {a.name}\n\n{text_a}\n\n---\n\n# 页面 B: {b.name}\n\n{text_b}"
                verdict = _deepseek_judge(CONTRADICTION_PROMPT, user_msg)
                if verdict is None:
                    continue
                if verdict.get("contradicts") is True:
                    findings.append(
                        f"{partition}/{a.name} vs {partition}/{b.name}: "
                        f"{verdict.get('reason', '')}"
                    )
    return findings


def _deepseek_judge(system_prompt: str, user_msg: str) -> dict | None:
    """Low-level DeepSeek JSON judge. Returns None on any failure."""
    import urllib.request, urllib.error
    try:
        key = os.environ["DEEPSEEK_API_KEY"]
    except KeyError:
        return None
    payload = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg[:8000]},  # cap context
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 2048,  # generous; with thinking disabled typical use is <300 tokens
        "thinking": {"type": "disabled"},  # 2026-04-30: skip reasoning for lint JSON; 4x faster, equivalent to legacy deepseek-chat
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None


# --- Dashboard + inbox writers ---

def render_dashboard(results: dict[str, Any]) -> str:
    """Render the dashboard page content. Overwrites each run."""
    date = today().isoformat()
    lines = [
        "---",
        "owner: linter",
        "status: active",
        'aliases: ["Lint 仪表板"]',
        f"updated: {date}",
        "---",
        "",
        "# Lint Dashboard",
        "",
        "## 摘要",
        "",
        f"L4 自动化 lint 每日 22:00 UTC+8 运行（GitHub Actions）。本次 run 于 {date} 完成。",
        "",
        "## Checks",
        "",
    ]
    for key, label in [
        ("A", f"陈旧活跃页（updated > {STALE_DAYS} 天）"),
        ("B", f"inbox 积压（> {INBOX_AGE_DAYS} 天）"),
        ("C", f"未评审 commit（近 {UNREVIEWED_WINDOW_DAYS} 天）"),
        ("D", "格式漂移（lint_page_errors 全量扫描）"),
        ("E", "孤儿页（未被 Wiki 页面链到）"),
        ("F", "断言缺来源（DeepSeek 判断）"),
        ("G", "跨页矛盾（DeepSeek 两两对比）"),
    ]:
        r = results.get(key)
        if r is None:
            lines.append(f"### {key}. {label}：_(本次未跑)_")
            lines.append("")
            continue
        if isinstance(r, int):
            lines.append(f"### {key}. {label}：**{r}**")
            lines.append("")
        else:
            count = len(r)
            lines.append(f"### {key}. {label}：**{count}** 条")
            lines.append("")
            if r:
                for item in r[:50]:  # cap display
                    lines.append(f"- {item}")
                if len(r) > 50:
                    lines.append(f"- ... 以及 {len(r)-50} 条未显示")
                lines.append("")
    lines.extend([
        "## 来源",
        "",
        "- 自动生成于 `tools/tm_lint.py`",
        "- GitHub Actions workflow: `.github/workflows/daily-lint.yml`",
        "",
    ])
    return "\n".join(lines)


def has_any_findings(results: dict[str, Any]) -> bool:
    for k, v in results.items():
        if isinstance(v, list) and v:
            return True
        if isinstance(v, int) and v > 0:
            return True
    return False


def render_inbox_findings(results: dict[str, Any]) -> str:
    date = today().isoformat()
    lines = [
        "---",
        "owner: linter",
        "status: draft",
        f"updated: {date}",
        "routed_by: tigermemory",
        "route_decision_reason: lint findings generated by tools/tm_lint.py",
        "---",
        "",
        "# Lint Findings",
        "",
        f"L4 每日 lint 于 {date} 发现以下问题，供人工 / claude-code 处理。",
        "",
    ]
    for key in "ABCDEFG":
        r = results.get(key)
        if r is None:
            continue
        if isinstance(r, int) and r > 0:
            lines.append(f"## {key}: count={r}")
            lines.append("")
        elif isinstance(r, list) and r:
            lines.append(f"## {key}: {len(r)} findings")
            lines.append("")
            for item in r:
                lines.append(f"- {item}")
            lines.append("")
    return "\n".join(lines)


def collect_results(run_all: bool = False) -> dict[str, Any]:
    now_date = today()
    dow = now_date.isoweekday()  # Mon=1..Sun=7
    doy = now_date.timetuple().tm_yday

    # Select which checks to run today
    run_ae_g_weekly = (dow == 7)  # Sunday
    run_f_3day = (doy % 3 == 0)

    if run_all:
        run_ae_g_weekly = True
        run_f_3day = True

    results: dict[str, Any] = {}
    results["A"] = check_A_stale_pages()
    results["B"] = check_B_inbox_aging()
    results["C"] = check_C_unreviewed_commits()
    results["D"] = check_D_format_drift()
    if run_ae_g_weekly:
        results["E"] = check_E_orphan_pages()
    if run_f_3day:
        results["F"] = check_F_missing_sources()
    if run_ae_g_weekly:
        results["G"] = check_G_contradictions()
    return results


def write_lint_outputs(results: dict[str, Any]) -> bool:
    """Write the dashboard and optional inbox findings. Returns whether inbox was written."""

    # Write dashboard (always reflects full results)
    dashboard = render_dashboard(results)
    (REPO / DASHBOARD_REL).write_text(dashboard, encoding="utf-8")

    # Build trigger_results: same as results but suppress C below threshold
    # (isolated [unreviewed] commits are noise, batches are signal).
    trigger_results = dict(results)
    if isinstance(trigger_results.get("C"), int) and trigger_results["C"] < UNREVIEWED_ALERT_THRESHOLD:
        trigger_results["C"] = 0

    # Write inbox findings iff any non-noise issue
    wrote_inbox = False
    if has_any_findings(trigger_results):
        stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d-%H%M")
        inbox_rel = f"inbox/{stamp}-linter-lint.md"
        (REPO / inbox_rel).write_text(render_inbox_findings(trigger_results), encoding="utf-8")
        wrote_inbox = True
        print(f"wrote {inbox_rel}")
    return wrote_inbox


def print_results(results: dict[str, Any], wrote_dashboard: bool = False) -> None:
    if wrote_dashboard:
        print(f"updated {DASHBOARD_REL}")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in results.items()}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tm_lint.py",
        description="Run tigermemory lint checks. Default mode writes the lint dashboard for CI compatibility.",
    )
    parser.add_argument("--all", dest="all_global", action="store_true", help="run weekly/LLM checks too")
    sub = parser.add_subparsers(dest="cmd")

    check_p = sub.add_parser("check", help="run checks without writing files")
    check_p.add_argument("--all", dest="all_sub", action="store_true", help="run weekly/LLM checks too")

    write_p = sub.add_parser("write-dashboard", help="run checks and write dashboard/inbox outputs")
    write_p.add_argument("--all", dest="all_sub", action="store_true", help="run weekly/LLM checks too")

    args = parser.parse_args(argv)
    cmd = args.cmd or "write-dashboard"
    run_all = bool(args.all_global or getattr(args, "all_sub", False))

    results = collect_results(run_all=run_all)
    if cmd == "check":
        print_results(results, wrote_dashboard=False)
        return 0

    write_lint_outputs(results)
    print_results(results, wrote_dashboard=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
