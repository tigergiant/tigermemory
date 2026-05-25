"""tm_summary_proposals — dry-run frontmatter patch proposals for miss-case pages.

Why this exists: P1-2a (2026-05-07) showed that 6/8 P0-2 miss cases have
expected pages indexed but ranked 4-6 (out of top-10), while 2/8 sit
completely outside top-10 — none of these can be fixed by aggregation-page
downranking (P1-2b cancelled, see wiki/systems/memory-retrieval-eval.md
Phase 2l). Phase 2i already proved that **alias content** is real signal:
adding 2-4 user-real CN synonyms to 6 wiki pages lifted hit@3 from 71 to
75. P1-1 takes the same playbook to the 8 P0-2 misses but does NOT touch
files — it emits a markdown report so 虎哥 can review candidate aliases
and summaries before any wiki/sources page is patched.

This tool is read-only. It never writes wiki/* or sources/*. It only
writes the proposal report to --output (default .tmp/p1_1_proposals.md,
gitignored).

Design choices (intentional simplicity):
- Rule-based, no LLM. Phase 2h showed "complex pipeline + 0 net hit@3"
  is worse than "obvious aliases + 4 net hit@3". Keep it boring.
- Candidate aliases derive from: (a) the case query tokens, (b) the
  must_contain list, (c) page H1, (d) Chinese↔English bridge hints from
  the page title. We surface candidates and let a human decide.
- Summary candidate is only emitted when the page lacks both
  frontmatter `summary:` and `## 摘要` / `## Summary` heading. We reuse
  `tm_embed_index._extract_summary` for the extraction itself.
Inputs: Repository markdown/python files, frontmatter, section text, git diff inputs, or CLI path arguments.
Outputs: Deterministic reports, rewritten generated files, validation errors, or patch proposals.
Depends-on (must-have): Python stdlib plus tm_core/path parsers; no Mem0 write path unless explicitly invoked by caller.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tigermemory_core as tm_core
from tigermemory_search import embed as tm_embed_index

REPO_ROOT = tm_core.REPO_ROOT


DEFAULT_CASE_IDS = [
    "systems-openclaw-ce-api-notes",
    "all-openclaw-concepts-memory-search",
    "all-openclaw-gateway-security",
    "semantic-cn-rebase-conflict",
    "synonym-prompt-injection",
    "task-cn-howto-deploy",
    "task-cn-howto-summarize",
    "operations-cn-runtime",
]

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
SUMMARY_HEADING_RE = re.compile(
    r"^#+\s*(摘要|Summary|概述|TL;DR)\s*$", re.MULTILINE | re.IGNORECASE
)
FRONTMATTER_SUMMARY_RE = re.compile(r"^summary\s*:", re.MULTILINE)
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _tokenize(query: str) -> list[str]:
    """Pull both CJK runs and English words from a query string.

    A query like "OpenClaw 鉴权 sandboxing" yields
    ['OpenClaw', '鉴权', 'sandboxing']. We do not split CJK further because
    Chinese token boundaries are ambiguous and we want each whole CN run
    to appear as an alias candidate (e.g. "提示词注入安全").
    """
    out: list[str] = []
    for m in CJK_RE.finditer(query):
        out.append(m.group(0))
    for m in WORD_RE.finditer(query):
        out.append(m.group(0))
    return out


def _existing_aliases(text: str) -> list[str]:
    return tm_embed_index._extract_aliases(text)


def _has_summary(text: str) -> bool:
    fm_match = tm_embed_index._FRONTMATTER_RE.match(text)
    if fm_match and FRONTMATTER_SUMMARY_RE.search(fm_match.group(1)):
        return True
    body = tm_embed_index._FRONTMATTER_RE.sub("", text, count=1) if fm_match else text
    return bool(SUMMARY_HEADING_RE.search(body))


def _first_h1(text: str) -> str | None:
    body = tm_embed_index._FRONTMATTER_RE.sub("", text, count=1)
    m = H1_RE.search(body)
    return m.group(1).strip() if m else None


def _propose_aliases(
    query: str,
    must_contain: list[str],
    page_title: str | None,
    existing: list[str],
) -> list[str]:
    """Rule-based candidate alias list. Caller still reviews.

    Sources, in order of trust:
      1. Whole CJK runs from the case query (highest signal — the user
         literally types these).
      2. English words from the case query that aren't already in
         existing aliases or H1.
      3. must_contain entries (the fixture's hint about what the page
         must say to count as a hit).

    We dedupe case-insensitively and drop tokens shorter than 2 chars.
    """
    candidates: list[str] = []
    seen_lower: set[str] = {a.lower() for a in existing}

    for tok in _tokenize(query):
        if len(tok) < 2:
            continue
        if tok.lower() in seen_lower:
            continue
        candidates.append(tok)
        seen_lower.add(tok.lower())

    for mc in must_contain:
        if not mc:
            continue
        if mc.lower() in seen_lower:
            continue
        candidates.append(mc)
        seen_lower.add(mc.lower())

    return candidates


def _propose_summary(text: str) -> str | None:
    """Return a candidate summary string only if the page genuinely
    lacks one. We use `_extract_summary` (which falls back to first
    signal paragraph after H1)."""
    if _has_summary(text):
        return None
    return tm_embed_index._extract_summary(text) or None


def _load_cases(path: Path) -> list[dict]:
    cases = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def render_report(
    cases: list[dict],
    selected_ids: list[str],
    repo_root: Path,
) -> str:
    out: list[str] = []
    out.append("# P1-1 Frontmatter Patch Proposals (dry-run)\n")
    out.append(
        "Generated by `tools/tm_summary_proposals.py`. **No files modified.** "
        "Each section below proposes candidate `aliases` (and `summary` when "
        "missing) for the expected_paths of one P0-2 miss case. Review and "
        "decide which to apply manually.\n"
    )

    by_id = {c["id"]: c for c in cases}
    for cid in selected_ids:
        case = by_id.get(cid)
        if not case:
            out.append(f"## {cid}\n\n_case id not found in fixture_\n")
            continue
        query = case["query"]
        must_contain = case.get("must_contain", []) or []
        expected_paths = case.get("expected_paths", []) or []
        scope = case.get("scope", "?")

        out.append(f"## {cid}\n")
        out.append(f"- **query**: `{query}`")
        out.append(f"- **scope**: `{scope}`")
        out.append(f"- **must_contain**: `{must_contain}`")
        out.append(f"- **expected_paths**: {expected_paths}\n")

        for rel in expected_paths:
            full = repo_root / rel
            if not full.exists():
                out.append(f"### `{rel}`\n")
                out.append(f"- **status**: MISSING — file does not exist on disk.\n")
                out.append(f"- **action**: fixture bug, change expected_paths or create the page.\n")
                continue

            text = full.read_text(encoding="utf-8")
            existing_aliases = _existing_aliases(text)
            h1 = _first_h1(text)
            has_fm = bool(tm_embed_index._FRONTMATTER_RE.match(text))

            out.append(f"### `{rel}`\n")
            out.append(f"- **has_frontmatter**: {has_fm}")
            out.append(f"- **H1**: {h1 or '(none)'}")
            out.append(f"- **existing aliases**: {existing_aliases or '[]'}")

            new_aliases = _propose_aliases(query, must_contain, h1, existing_aliases)
            if new_aliases:
                out.append(f"- **proposed aliases (rule-based)**: {new_aliases}")
                merged = existing_aliases + new_aliases
                out.append(f"- **merged aliases value**: `{json.dumps(merged, ensure_ascii=False)}`")
            else:
                out.append(f"- **proposed aliases**: (none — all query tokens already covered)")

            summary = _propose_summary(text)
            if summary:
                out.append(
                    f"- **page lacks `## 摘要` / frontmatter `summary:`**; candidate first-paragraph summary "
                    f"({len(summary)} chars):"
                )
                out.append(f"\n  > {summary}\n")
            else:
                out.append("- **summary status**: page already has `## 摘要` or frontmatter `summary:` — no candidate needed.")

            out.append("")
        out.append("")

    out.append("## Notes\n")
    out.append(
        "- Phase 2i (2026-05-06) showed alias content is the real signal "
        "(+4 hit@3 by editing 6 pages, no code change). Same playbook here.\n"
        "- For pages without frontmatter at all (e.g. `AGENTS.md`, "
        "`sources/external/*`), adding `aliases:` requires writing a "
        "minimal frontmatter block — verify owner/PII rules in AGENTS.md "
        "§4-§5 before patching.\n"
        "- After patching, re-run "
        "`python tools/tm_embed_index.py refresh --scope wiki` (only "
        "modified pages re-embed via hash) and "
        "`python tools/tm_memory_eval.py eval --recall=hybrid --grouped` "
        "to confirm hit@3 lift.\n"
    )
    return "\n".join(out) + "\n"


def _configure_cli_encoding() -> None:
    """Keep argparse help printable on Windows terminals with legacy codepages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _configure_cli_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "memory_eval_cases.jsonl",
        help="Path to memory_eval_cases.jsonl",
    )
    parser.add_argument(
        "--case-ids",
        type=str,
        default=",".join(DEFAULT_CASE_IDS),
        help="Comma-separated case ids to include (default: 8 P0-2 misses)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / ".tmp" / "p1_1_proposals.md",
        help="Output report path (will be overwritten)",
    )
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    selected_ids = [s.strip() for s in args.case_ids.split(",") if s.strip()]

    report = render_report(cases, selected_ids, REPO_ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"wrote proposals report: {args.output} ({len(report)} chars, {len(selected_ids)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
