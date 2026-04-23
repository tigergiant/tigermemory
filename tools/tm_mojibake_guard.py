#!/usr/bin/env python3
"""
tm_mojibake_guard.py — pre-commit hook that blocks mojibake ('?') writes
into tracked markdown files.

Background (2026-04-23 incident):
  Several wiki/systems/*.md files received new sections where Chinese was
  lost to ASCII '?' because an agent's writing pipeline (PowerShell cp936
  code page) silently transcoded UTF-8 Chinese bytes to 0x3F. The bytes
  are destroyed at write time; L2 LLM review cannot recover the original
  content. The only effective defense is to refuse the commit.

Rule:
  For each staged markdown file (*.md) under the repo, scan the '+' side
  of the staged diff. If any contiguous run of 4+ ASCII '?' characters
  appears on a newly-added line, reject the commit.

  A run of 4+ '?' characters never occurs in legitimate Chinese-authored
  markdown. The check is zero-LLM, <0.1 s, and has effectively no false
  positives on this repo's content.

Bypass:
  --no-verify  (human-only per AGENTS.md)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 4+ consecutive ASCII question marks on a single line — classic
# cp936 / GBK lossy-encode signature. Legitimate Chinese text has none.
MOJIBAKE_RE = re.compile(r"\?{4,}")

# Only scan markdown files; extend later if we see the bug in other text.
CHECKED_SUFFIXES = {".md"}


def staged_markdown_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    result = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if Path(line).suffix.lower() in CHECKED_SUFFIXES:
            result.append(line)
    return result


def staged_diff_added_lines(path: str) -> list[tuple[int, str]]:
    """Return list of (new_line_number, content) for '+' lines in staged diff."""
    out = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--", path],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    lines: list[tuple[int, str]] = []
    new_line_no = 0
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in out.splitlines():
        m = hunk_re.match(raw)
        if m:
            new_line_no = int(m.group(1))
            continue
        if raw.startswith("+++"):
            continue
        if raw.startswith("+"):
            lines.append((new_line_no, raw[1:]))
            new_line_no += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line_no += 1
    return lines


def main() -> int:
    try:
        files = staged_markdown_files()
    except subprocess.CalledProcessError as exc:
        print(f"tm_mojibake_guard: git failed: {exc}", file=sys.stderr)
        return 1

    offenses: list[tuple[str, int, str]] = []
    for path in files:
        try:
            added = staged_diff_added_lines(path)
        except subprocess.CalledProcessError as exc:
            print(f"tm_mojibake_guard: cannot diff {path}: {exc}", file=sys.stderr)
            return 1
        for lineno, content in added:
            if MOJIBAKE_RE.search(content):
                offenses.append((path, lineno, content))

    if not offenses:
        return 0

    print("=" * 60, file=sys.stderr)
    print("tm_mojibake_guard: commit REJECTED — mojibake detected.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Cause: UTF-8 Chinese bytes were transcoded to ASCII '?'", file=sys.stderr)
    print("during write (often PowerShell cp936 pipe). The original", file=sys.stderr)
    print("content is LOST — this commit would write corrupted data.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Offending lines (4+ consecutive '?' on an added line):", file=sys.stderr)
    for path, lineno, content in offenses[:20]:
        snippet = content.strip()
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        print(f"  {path}:{lineno}  {snippet}", file=sys.stderr)
    if len(offenses) > 20:
        print(f"  ... and {len(offenses) - 20} more", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix: rewrite the affected sections with a UTF-8-safe path", file=sys.stderr)
    print("     (tools/tm_io.py write-inbox, MCP propose_wiki_page,", file=sys.stderr)
    print("     or an editor that writes UTF-8 directly — not piped", file=sys.stderr)
    print("     through Windows cp936 console).", file=sys.stderr)
    print("", file=sys.stderr)
    print("Bypass (human emergency only, per AGENTS.md):", file=sys.stderr)
    print("     git commit --no-verify", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
