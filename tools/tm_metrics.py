"""Compile self-evolution monthly metrics from local logs and git history.

Usage:
    py tools/tm_metrics.py compile --month 2026-05
    py tools/tm_metrics.py compile --month 2026-05 --write   # update metrics.md in place

Without --write, prints the proposed table row to stdout for review.
With --write, replaces (or inserts) the row in wiki/self-evolution/metrics.md.

Data sources (per AGENTS.md §9.3 / metrics.md schema, v0.1):
  1. New lessons        : git log --diff-filter=A on wiki/self-evolution/lessons/
  2. hook reject count  : .tmp/guard-rejects.jsonl (local only)
  3. lessons references : git log -p, count "lessons/" occurrences in diffs
  4. preflight hits     : .tmp/preflight-lessons.log (local only)
  5. inbox backlog      : len(inbox/*.md) at end of month (using `git ls-files`
                          of the latest commit within the month if available;
                          falls back to working tree count)
  6. repeat incidents   : left as "—" — manual recognition only (not auto-detected)
  7. guards added/edited: git log on .githooks/ + tools/tm_*.py

Limitations (v0.1 explicit):
  - Local logs (.tmp/*.jsonl) are NOT synced across worktrees.
    Run on the worktree where most commits happen; partial counts otherwise.
  - "lessons references" is a coarse grep; counts each occurrence in a diff
    line, not unique referencing pages. Good enough for trend.
  - "repeat incidents" requires human review — script never auto-fills it.

Stdlib only. Python 3.8+ safe.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / ".tmp"
GUARD_LOG = LOG_DIR / "guard-rejects.jsonl"
PREFLIGHT_LOG = LOG_DIR / "preflight-lessons.log"
METRICS_PAGE = REPO_ROOT / "wiki" / "self-evolution" / "metrics.md"
LESSONS_DIR_REL = "wiki/self-evolution/lessons"
INBOX_DIR = REPO_ROOT / "inbox"


# ---------- helpers ----------

MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _month_bounds(month: str) -> Tuple[str, str]:
    """Return (since, until) ISO date strings for git log --since / --until."""
    m = MONTH_RE.match(month)
    if not m:
        raise ValueError(f"invalid --month {month!r}, expected YYYY-MM")
    yr, mo = int(m.group(1)), int(m.group(2))
    since = f"{yr:04d}-{mo:02d}-01"
    if mo == 12:
        until_yr, until_mo = yr + 1, 1
    else:
        until_yr, until_mo = yr, mo + 1
    until = f"{until_yr:04d}-{until_mo:02d}-01"
    return since, until


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        return ""
    return out.stdout


def _count_jsonl_in_month(path: pathlib.Path, since: str, until: str) -> int:
    if not path.exists():
        return 0
    count = 0
    since_prefix = since[:7]  # "2026-05"
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts", "")
            # ts format: "2026-05-02T11:01:09+08:00" — month prefix match suffices
            if ts.startswith(since_prefix):
                count += 1
    return count


# ---------- collectors ----------

def collect_new_lessons(since: str, until: str) -> int:
    out = _git(
        "log",
        f"--since={since}",
        f"--until={until}",
        "--diff-filter=A",
        "--name-only",
        "--pretty=format:",
        "--",
        f"{LESSONS_DIR_REL}/",
    )
    files = {
        line.strip()
        for line in out.splitlines()
        if line.strip() and line.strip().endswith(".md") and not line.strip().endswith("/index.md")
    }
    return len(files)


def collect_lessons_references(since: str, until: str) -> int:
    """Count occurrences of 'lessons/' in added diff lines this month."""
    out = _git(
        "log",
        f"--since={since}",
        f"--until={until}",
        "-p",
        "--no-merges",
    )
    count = 0
    for line in out.splitlines():
        # Only count added lines that mention lessons/ (avoid double-counting
        # the diff header lines `+++ b/wiki/self-evolution/lessons/...`).
        if line.startswith("+") and not line.startswith("+++"):
            count += line.count("lessons/")
    return count


def collect_inbox_backlog() -> int:
    if not INBOX_DIR.exists():
        return 0
    return sum(
        1
        for p in INBOX_DIR.iterdir()
        if p.is_file() and p.suffix == ".md" and p.name != ".gitkeep"
    )


def collect_guards_modified(since: str, until: str) -> int:
    """Count distinct commits that touched .githooks/ or tools/tm_*.py."""
    out = _git(
        "log",
        f"--since={since}",
        f"--until={until}",
        "--pretty=format:%H",
        "--",
        ".githooks/",
        "tools/",
    )
    shas = {line.strip() for line in out.splitlines() if line.strip()}
    return len(shas)


# ---------- compile + render ----------

def compile_row(month: str) -> Dict[str, object]:
    since, until = _month_bounds(month)
    return {
        "month": month,
        "new_lessons": collect_new_lessons(since, until),
        "hook_rejects": _count_jsonl_in_month(GUARD_LOG, since, until),
        "lessons_refs": collect_lessons_references(since, until),
        "preflight_hits": _count_jsonl_in_month(PREFLIGHT_LOG, since, until),
        "inbox_backlog": collect_inbox_backlog(),
        "repeat_incidents": "—",  # human-judged only
        "guards_modified": collect_guards_modified(since, until),
    }


def render_row(row: Dict[str, object]) -> str:
    return (
        f"| {row['month']} | "
        f"{row['new_lessons']} | "
        f"{row['hook_rejects']} | "
        f"{row['lessons_refs']} | "
        f"{row['preflight_hits']} | "
        f"{row['inbox_backlog']} | "
        f"{row['repeat_incidents']} | "
        f"{row['guards_modified']} |"
    )


# ---------- write back to metrics.md ----------

ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2})\s*\|.*\|\s*$")


def update_metrics_md(month: str, rendered: str) -> Tuple[bool, str]:
    """Replace existing row for the same month, or insert in chronological order.

    Returns (changed, message).
    """
    if not METRICS_PAGE.exists():
        return False, f"metrics page not found: {METRICS_PAGE}"

    text = METRICS_PAGE.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)

    # Locate the data row block: after `## 核心指标` header table separator,
    # rows that match ROW_RE; ends at first blank or next ##.
    in_table = False
    table_start: Optional[int] = None
    table_end: Optional[int] = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_table:
            if ROW_RE.match(stripped):
                in_table = True
                table_start = i
        else:
            if ROW_RE.match(stripped):
                continue
            else:
                table_end = i
                break
    if not in_table or table_start is None:
        return False, "could not locate metrics data rows in metrics.md"
    if table_end is None:
        table_end = len(lines)

    rows = lines[table_start:table_end]
    new_rows: List[str] = []
    replaced = False
    for r in rows:
        m = ROW_RE.match(r.strip())
        if m and m.group(1) == month:
            new_rows.append(rendered)
            replaced = True
        else:
            new_rows.append(r)
    if not replaced:
        # Insert in chronological order
        inserted = False
        for i, r in enumerate(new_rows):
            m = ROW_RE.match(r.strip())
            if m and m.group(1) > month:
                new_rows.insert(i, rendered)
                inserted = True
                break
        if not inserted:
            new_rows.append(rendered)

    out_lines = lines[:table_start] + new_rows + lines[table_end:]
    new_text = "\n".join(out_lines)
    if not text.endswith("\n"):
        # Don't add a newline if the source didn't end with one
        pass
    else:
        if not new_text.endswith("\n"):
            new_text += "\n"
    if new_text == text:
        return False, "metrics.md already up to date"
    METRICS_PAGE.write_text(new_text, encoding="utf-8")
    return True, ("replaced row" if replaced else "inserted row")


# ---------- CLI ----------

def cmd_compile(args: argparse.Namespace) -> int:
    try:
        row = compile_row(args.month)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rendered = render_row(row)

    print(f"# tm_metrics compile --month {args.month}")
    print(f"# repo: {REPO_ROOT}")
    print(f"# guard log:     {GUARD_LOG}  (exists={GUARD_LOG.exists()})")
    print(f"# preflight log: {PREFLIGHT_LOG}  (exists={PREFLIGHT_LOG.exists()})")
    print()
    print("proposed metrics row:")
    print()
    print("| 月份 | 新增 lessons | hook reject 次数 | lessons 引用次数 | preflight 检索命中 | 未处理 inbox 堆积 | 重复事故数 | guard 新增/修订 |")
    print("|------|------|------|------|------|------|------|------|")
    print(rendered)
    print()

    if args.write:
        changed, msg = update_metrics_md(args.month, rendered)
        if changed:
            print(f"WROTE: {METRICS_PAGE.relative_to(REPO_ROOT).as_posix()}  ({msg})")
        else:
            print(f"NO-OP: {msg}")
        return 0
    print("(dry run — pass --write to update wiki/self-evolution/metrics.md)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tm_metrics.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("compile", help="compile one month of metrics")
    sp.add_argument("--month", required=True, help="month in YYYY-MM format, e.g. 2026-05")
    sp.add_argument("--write", action="store_true", help="update metrics.md in place")
    sp.set_defaults(func=cmd_compile)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
