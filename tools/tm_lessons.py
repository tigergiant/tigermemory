"""Search wiki/self-evolution/lessons/ for relevant prevention lessons.

Usage (open a new task):

    py tools/tm_lessons.py search "git commit push"
    py tools/tm_lessons.py search "powershell encoding"

The output prints top-3 matching lessons. Each invocation appends a single
JSONL line to .tmp/preflight-lessons.log so tm_metrics.py can count how many
times AGENTS.md §9.1 step 4 (preflight lessons retrieval) was actually run.

Scoring: substring matches against title (×5), aliases (×3), body (×1) for
each whitespace-separated query token (case-insensitive). Chinese is matched
as substring without word segmentation — sufficient for v0.1 (lessons < 50).

This file is intentionally dependency-free (stdlib only) and Python 3.8+ safe.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
from typing import List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LESSONS_DIR = REPO_ROOT / "wiki" / "self-evolution" / "lessons"
LOG_DIR = REPO_ROOT / ".tmp"
LOG_FILE = LOG_DIR / "preflight-lessons.log"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
ALIASES_INLINE_RE = re.compile(r"^aliases:\s*\[(.+?)\]\s*$", re.MULTILINE)
ALIASES_BLOCK_RE = re.compile(r"^aliases:\s*\n((?:\s*-\s*.+(?:\n|$))+)", re.MULTILINE)
TITLE_RE = re.compile(r'^title:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)


def _configure_stdio() -> None:
    """Avoid crashing on Windows consoles that cannot encode every Unicode char."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            continue


def _parse_aliases(fm: str) -> List[str]:
    m = ALIASES_INLINE_RE.search(fm)
    if m:
        return [s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()]
    m = ALIASES_BLOCK_RE.search(fm)
    if m:
        out: List[str] = []
        for line in m.group(1).splitlines():
            mm = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if mm:
                out.append(mm.group(1).strip().strip('"').strip("'"))
        return out
    return []


def _parse_title(fm: str, body: str) -> str:
    m = TITLE_RE.search(fm)
    if m:
        return m.group(1).strip()
    aliases = _parse_aliases(fm)
    if aliases:
        return aliases[0]
    h1 = H1_RE.search(body)
    return h1.group(1).strip() if h1 else ""


def _split_frontmatter(text: str) -> Tuple[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def _score_lesson(text: str, query_tokens: List[str]) -> Tuple[int, str, List[str]]:
    """Return (score, title, aliases) for a lesson file content.

    Score: title hit ×5, aliases hit ×3, body hit ×1 per token.
    """
    fm, body = _split_frontmatter(text)
    title = _parse_title(fm, body)
    aliases = _parse_aliases(fm)

    title_lc = title.lower()
    aliases_lc = " ".join(aliases).lower()
    body_lc = body.lower()

    score = 0
    for tok in query_tokens:
        tok_lc = tok.lower()
        if not tok_lc:
            continue
        if tok_lc in title_lc:
            score += 5
        if tok_lc in aliases_lc:
            score += 3
        score += body_lc.count(tok_lc)
    return score, title, aliases


def _excerpt(text: str, query_tokens: List[str], width: int = 80) -> str:
    """Return a short excerpt around the first query token hit, or first line."""
    fm, body = _split_frontmatter(text)
    body_lc = body.lower()
    for tok in query_tokens:
        idx = body_lc.find(tok.lower())
        if idx >= 0:
            start = max(0, idx - width // 3)
            end = min(len(body), idx + width)
            snippet = body[start:end].replace("\n", " ").strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(body):
                snippet = snippet + "..."
            return snippet
    # Fallback: first non-heading line
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith(">"):
            return line[:width] + ("..." if len(line) > width else "")
    return ""


def _log_invocation(query: str, hits: List[str], purpose: str = "real") -> None:
    LOG_DIR.mkdir(exist_ok=True)
    record = {
        "ts": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds"),
        "agent": os.environ.get("TM_AGENT", "unknown"),
        "query": query,
        "hits": len(hits),
        "top": hits[:3],
        "purpose": purpose,
    }
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def cmd_search(args: argparse.Namespace) -> int:
    query = " ".join(args.keywords).strip()
    if not query:
        print("error: search needs at least one keyword", file=sys.stderr)
        return 2

    if not LESSONS_DIR.exists():
        print(f"error: lessons dir not found: {LESSONS_DIR}", file=sys.stderr)
        return 2

    tokens = [t for t in re.split(r"\s+", query) if t]

    scored: List[Tuple[int, pathlib.Path, str, List[str]]] = []
    for path in sorted(LESSONS_DIR.glob("*.md")):
        if path.name == "index.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        score, title, aliases = _score_lesson(text, tokens)
        if score > 0:
            scored.append((score, path, title, aliases))

    scored.sort(key=lambda x: (-x[0], x[1].name))
    top = scored[: args.top]
    top_slugs = [p.stem for _, p, _, _ in top]

    _log_invocation(query, top_slugs, purpose=args.purpose)

    if not top:
        print(f"no relevant lessons found for query: {query!r}")
        print(f"(searched {len(list(LESSONS_DIR.glob('*.md'))) - 1} lesson pages)")
        return 0

    print(f"relevant lessons (top {len(top)} of {len(scored)} hits) for: {query!r}")
    print()
    for i, (score, path, title, _aliases) in enumerate(top, 1):
        rel = path.relative_to(REPO_ROOT).as_posix()
        excerpt = _excerpt(path.read_text(encoding="utf-8"), tokens)
        print(f"{i}. [{path.stem}]  score={score}")
        print(f"   {title}")
        print(f"   {rel}")
        if excerpt:
            print(f"   > {excerpt}")
        print()
    return 0


def main() -> None:
    _configure_stdio()
    p = argparse.ArgumentParser(prog="tm_lessons.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="search lessons by keyword(s)")
    sp.add_argument("keywords", nargs="+", help="one or more search keywords")
    sp.add_argument("--top", type=int, default=3, help="number of results (default 3)")
    sp.add_argument("--purpose", default="real",
                    choices=["real", "test", "validation"],
                    help="real (counted in metrics) / test (manual probe) / validation (in-session audit)")
    sp.set_defaults(func=cmd_search)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
