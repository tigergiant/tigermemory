#!/usr/bin/env python3
"""
tigermemory_index — deterministic compiler for wiki/<partition>/index.md.

Purpose: replace the fragile "every agent updates index.md in the same commit"
rule with a mechanical compiler that reads each page and regenerates the
`## 页面` listing. Index drift becomes impossible to miss (CI runs --check).

Scope (V1):
- Compiles the `## 页面` section of each partition's index.md.
- Preserves the preamble (frontmatter, h1, intro paragraph, `## 页面` heading).
- Preserves existing page order; new pages appended (sorted); missing pages removed.
- One-line summary extracted from each page's `## 摘要` section (first non-empty
  line, truncated to ~120 chars at the nearest whitespace).

Not handled in V1:
- Root index.md (manually curated; can be added later).
- Non-partition pages like schemas/, inbox/, sources/.

CLI:
  tm_compile_index.py check [--partition systems]   # exit 1 if any diff
  tm_compile_index.py diff  [--partition systems]   # print diffs, always exit 0
  tm_compile_index.py write [--partition systems]   # rewrite in place
  tigermemory-index check [--partition systems]      # package console entrypoint

Exit codes:
  0 no diff (check) / success (write|diff)
  1 diff present (check only)
  2 bad usage / validation failure

Inputs: wiki/<partition>/*.md 单分区下所有非 index.md 页面（读 frontmatter `title` / `aliases` 和 `## 摘要` 段第一行作为一行 summary）；CLI 子命令 check / diff / write + 可选 --partition。
Outputs: write 模式重写 wiki/<partition>/index.md 的 `## 页面` 段（preamble 保留）；check 模式仅打印差异并返 exit 1；diff 模式打印 unified diff 并返 exit 0。
Depends-on (must-have): Python stdlib 纯文本处理（pathlib / re / difflib / argparse）。零外部依赖——不读 Mem0、不调 LLM、不打 git；CI 兜底每天 write 一次。
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import os
import pathlib
import re
import sys
from typing import Iterable

__all__ = [
    "MAX_SUMMARY_LEN",
    "PARTITIONS",
    "PREVIEW_FILENAME",
    "PREVIEW_LINK_LINE",
    "REPO_ROOT",
    "WIKI_ROOT",
    "cmd_check",
    "cmd_check_preview",
    "cmd_diff",
    "cmd_preview",
    "cmd_write",
    "compile_partition_index",
    "extract_page_aliases",
    "extract_page_status",
    "extract_page_subtopics",
    "extract_page_summary",
    "extract_page_title",
    "list_partition_pages",
    "main",
    "normalize_index_preamble",
    "render_preview",
    "split_index",
]


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (ancestor / ".git").exists() and (ancestor / "wiki").is_dir():
            return ancestor
    return here.parent.parent.parent.parent


REPO_ROOT = _detect_repo_root()
WIKI_ROOT = REPO_ROOT / "wiki"
PARTITIONS = ["brand", "investment", "operations", "person", "production", "self-evolution", "systems"]
PREVIEW_FILENAME = "index-by-subtopic.md"
PREVIEW_LINK_LINE = f"*主题视图*：[按 subtopic 浏览]({PREVIEW_FILENAME})"
PARTITION_LABELS = {
    "brand": "品牌",
    "investment": "投资",
    "operations": "运营",
    "person": "人物",
    "production": "生产",
    "self-evolution": "自我进化",
    "systems": "系统",
}

PAGES_HEADING = "## 页面"
SUMMARY_HEADING_RE = re.compile(r"^##\s+摘要\s*$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
FRONTMATTER_BLOCK_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
INDEX_ITEM_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\(([^)]+)\)")
TZ_CN = dt.timezone(dt.timedelta(hours=8))

MAX_SUMMARY_LEN = 120


# ---------------- index preamble standard ----------------


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_inline_list(values: list[str]) -> str:
    return "[" + ", ".join(_yaml_quote(value) for value in values) + "]"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _frontmatter_scalar(fm: str, field: str) -> str:
    m = re.search(rf"^{re.escape(field)}:\s*(.+?)\s*$", fm, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _remove_frontmatter_fields(fm: str, fields: set[str]) -> list[str]:
    lines = fm.splitlines()
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_-]+):(?:\s+.*)?$", line)
        if m and m.group(1) in fields:
            i += 1
            while i < len(lines) and re.match(r"^\s*-\s+", lines[i]):
                i += 1
            continue
        kept.append(line)
        i += 1
    return [line for line in kept if line.strip()]


def _partition_label(partition: str) -> str:
    return PARTITION_LABELS.get(partition, partition)


def _index_aliases(partition: str, existing_aliases: list[str]) -> list[str]:
    label = _partition_label(partition)
    return _dedupe(
        existing_aliases
        + [
            label,
            f"{label}分区",
            f"{label}目录",
            f"{label}索引",
            f"{label}入口",
            f"{label}有哪些页面",
            f"{label}有哪些内容",
            f"{partition} index",
            f"wiki/{partition} index",
        ]
    )


def _is_generic_index_title(value: str, partition: str) -> bool:
    label = _partition_label(partition)
    generic = {
        "",
        label,
        partition,
        partition.capitalize(),
        partition.title(),
        f"{partition} Index",
        f"{partition.capitalize()} Index",
    }
    return value.strip() in generic


def _index_title(partition: str, existing_title: str) -> str:
    if existing_title and not _is_generic_index_title(existing_title, partition):
        return existing_title
    return f"{_partition_label(partition)}分区入口"


def _index_description(partition: str) -> str:
    label = _partition_label(partition)
    return f"{label}分区的目录和导航页，用于回答有哪些页面、从哪里开始、分区怎么组织；具体事实以具体页面为准。"


def _index_summary(partition: str) -> str:
    return (
        f"本页是 `{partition}` 分区的目录和导航页，用于回答“有哪些页面 / 从哪里开始 / 分区怎么组织”这类问题。"
        "具体事实、规则和操作细节应继续阅读下方具体页面。"
    )


def _render_index_frontmatter(partition: str, fm: str) -> str:
    owner = _frontmatter_scalar(fm, "owner") or "codex"
    status = _frontmatter_scalar(fm, "status") or "active"
    updated = _frontmatter_scalar(fm, "updated") or _today_cn()
    title = _index_title(partition, _frontmatter_scalar(fm, "title"))
    aliases = _index_aliases(partition, _parse_aliases(fm))
    subtopics = _dedupe(_parse_string_list_field(fm, "subtopic") + ["navigation", "index"])
    description = _frontmatter_scalar(fm, "description") or _index_description(partition)
    other_lines = _remove_frontmatter_fields(
        fm,
        {"owner", "status", "updated", "aliases", "subtopic", "title", "description"},
    )

    lines = [
        f"owner: {owner}",
        f"status: {status}",
        f"updated: {updated}",
        f"aliases: {_format_inline_list(aliases)}",
        f"subtopic: {_format_inline_list(subtopics)}",
        f"title: {_yaml_quote(title)}",
        f"description: {_yaml_quote(description)}",
    ]
    lines.extend(other_lines)
    return "---\n" + "\n".join(lines) + "\n---\n"


def _normalize_index_body(partition: str, body: str) -> str:
    title = _index_title(partition, "")
    h1 = H1_RE.search(body)
    if h1:
        current_h1 = h1.group(1).strip()
        if _is_generic_index_title(current_h1, partition):
            body = body[: h1.start()] + f"# {title}" + body[h1.end():]
    else:
        body = f"# {title}\n\n" + body.lstrip()

    if "\n## 摘要" not in "\n" + body:
        h1 = H1_RE.search(body)
        insert_at = h1.end() if h1 else 0
        summary_block = "\n\n## 摘要\n\n" + _index_summary(partition) + "\n"
        body = body[:insert_at].rstrip() + summary_block + body[insert_at:].lstrip("\n")

    if "\n## 来源" not in "\n" + body:
        source_block = (
            "\n\n## 来源\n\n"
            "- 本页 `## 页面` 部分由 `tools/tm_compile_index.py` 自动编译。\n"
            "- 本页作为分区入口页参与自然语言召回；具体事实以链接到的具体页面为准。\n"
        )
        body = body.rstrip() + source_block

    return body.strip() + "\n"


def normalize_index_preamble(partition: str, preamble: str) -> str:
    """Apply the standard index.md navigation preamble without churning bullets."""
    if partition == "person":
        return preamble
    preamble = preamble.lstrip("\ufeff")
    m = re.search(rf"(?m)^{re.escape(PAGES_HEADING)}\s*$", preamble)
    body = preamble[: m.start()].rstrip() if m else preamble.rstrip()

    fm_match = FRONTMATTER_BLOCK_RE.match(body + "\n")
    if fm_match:
        fm = fm_match.group(1)
        rest = body[fm_match.end() - 1 :]
    else:
        fm = ""
        rest = body

    frontmatter = _render_index_frontmatter(partition, fm)
    normalized_body = _normalize_index_body(partition, rest)
    return frontmatter + "\n" + normalized_body.rstrip() + "\n\n" + PAGES_HEADING + "\n"


def _refresh_index_updated(text: str, date: str | None = None) -> str:
    return re.sub(
        r"^updated:\s*\S+\s*$",
        f"updated: {date or _today_cn()}",
        text,
        count=1,
        flags=re.MULTILINE,
    )


# ---------------- page parsing ----------------


def _strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def _first_nonempty_line(block: str) -> str:
    for line in block.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _truncate(s: str, limit: int = MAX_SUMMARY_LEN) -> str:
    if len(s) <= limit:
        return s
    cut = s.rfind(" ", 0, limit)
    if cut < limit // 2:
        cut = limit
    return s[:cut].rstrip(" ，。,.；;:") + "…"


def _parse_string_list_field(fm: str, field: str) -> list[str]:
    """Parse a simple YAML string/list field from frontmatter text.

    Supports two forms:
      inline: field: [A, B, C]
      block:  field:
                - A
                - B
      scalar: field: A
    """
    # Inline form
    m = re.search(rf"^{re.escape(field)}:\s*\[(.+?)\]\s*$", fm, re.MULTILINE)
    if m:
        items = [s.strip().strip('"').strip("'") for s in m.group(1).split(",")]
        return [s for s in items if s]
    # Block form
    m = re.search(rf"^{re.escape(field)}:\s*\n((?:\s*-\s*.+(?:\n|$))+)", fm, re.MULTILINE)
    if m:
        results: list[str] = []
        for line in m.group(1).splitlines():
            mm = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if mm:
                v = mm.group(1).strip().strip('"').strip("'")
                if v:
                    results.append(v)
        return results
    # Scalar form
    m = re.search(rf"^{re.escape(field)}:\s*(.+?)\s*$", fm, re.MULTILINE)
    if m:
        v = m.group(1).strip().strip('"').strip("'")
        return [v] if v else []
    return []


def _parse_aliases(fm: str) -> list[str]:
    """Parse YAML aliases field from frontmatter text."""
    return _parse_string_list_field(fm, "aliases")


def extract_page_aliases(text: str) -> list[str]:
    """Return list of frontmatter aliases (empty if none or no frontmatter)."""
    m = FRONTMATTER_BLOCK_RE.match(text)
    if not m:
        return []
    return _parse_aliases(m.group(1))


def extract_page_status(text: str) -> str:
    """Return frontmatter status lowercased; empty means legacy/unspecified."""
    m = FRONTMATTER_BLOCK_RE.match(text)
    if not m:
        return ""
    return _frontmatter_scalar(m.group(1), "status").lower()


def extract_page_title(text: str) -> str:
    """Return the preferred display label for the page.

    Priority:
      1. frontmatter aliases[0]  (Chinese-friendly display name)
      2. H1 heading
      3. empty (caller falls back to filename stem)
    """
    aliases = extract_page_aliases(text)
    if aliases:
        return aliases[0]
    body = _strip_frontmatter(text)
    m = H1_RE.search(body)
    return m.group(1).strip() if m else ""


def extract_page_summary(text: str) -> str:
    """Return a one-line summary extracted from the page.

    Preference order:
      1. First non-empty, non-heading line under `## 摘要`.
      2. First non-empty, non-heading line under the first `##` section
         (fallback when no explicit 摘要).
      3. Empty string.
    """
    body = _strip_frontmatter(text)
    m = SUMMARY_HEADING_RE.search(body)
    if m:
        rest = body[m.end():]
        # Cut at next heading
        next_h = re.search(r"^##\s+", rest, re.MULTILINE)
        block = rest[: next_h.start()] if next_h else rest
        line = _first_nonempty_line(block)
        if line:
            return _truncate(line)
    # Fallback: first non-empty non-heading line after the h1
    h1 = H1_RE.search(body)
    if h1:
        rest = body[h1.end():]
        line = _first_nonempty_line(rest)
        if line:
            return _truncate(line)
    return ""


def extract_page_subtopics(text: str) -> list[str]:
    """Return frontmatter subtopic values (empty if absent or no frontmatter)."""
    m = FRONTMATTER_BLOCK_RE.match(text)
    if not m:
        return []
    return _parse_string_list_field(m.group(1), "subtopic")


# ---------------- index parsing ----------------


def split_index(text: str) -> tuple[str, list[str], dict[str, str]]:
    """Split existing index.md into (preamble, page_filenames_in_order, existing_lines_by_fn).

    The preamble ends with the `## 页面` heading line (inclusive).
    `existing_lines_by_fn` maps filename -> the full raw bullet line from the
    current index, which we preserve byte-for-byte to avoid churning
    human-curated summaries.
    """
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    heading_idx = None
    for i, line in enumerate(lines):
        if line.strip() == PAGES_HEADING:
            heading_idx = i
            break

    if heading_idx is None:
        preamble = text.rstrip() + "\n\n" + PAGES_HEADING + "\n"
        return preamble, [], {}

    preamble_lines = lines[: heading_idx + 1]
    preamble = "\n".join(preamble_lines) + "\n"

    filenames: list[str] = []
    existing_lines: dict[str, str] = {}
    for line in lines[heading_idx + 1:]:
        m = INDEX_ITEM_RE.match(line)
        if m:
            fn = m.group(2).strip()
            filenames.append(fn)
            existing_lines[fn] = line.rstrip()
    return preamble, filenames, existing_lines


# ---------------- compilation ----------------


def list_partition_pages(partition_dir: pathlib.Path) -> list[pathlib.Path]:
    pages = []
    for p in sorted(partition_dir.iterdir()):
        if p.is_file() and p.suffix == ".md" and p.name not in {"index.md", PREVIEW_FILENAME}:
            status = extract_page_status(p.read_text(encoding="utf-8"))
            if status in {"draft", "archived"}:
                continue
            pages.append(p)
    return pages


def compile_partition_index(
    partition: str,
    refresh_labels: bool = False,
) -> tuple[str, str]:
    """Return (new_index_text, old_index_text).

    Creates a fresh index.md if one does not exist.

    When ``refresh_labels`` is True, every bullet is regenerated so the label
    reflects the current frontmatter aliases or H1. The human-curated summary
    (the ' — ...' tail) is preserved from the existing bullet when available.
    """
    partition_dir = WIKI_ROOT / partition
    if not partition_dir.is_dir():
        raise ValueError(f"partition not found: {partition}")

    index_path = partition_dir / "index.md"
    old_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if old_text:
        preamble, existing_order, existing_lines = split_index(old_text)
    else:
        preamble = f"# {partition.capitalize()}\n\n{PAGES_HEADING}\n"
        existing_order, existing_lines = [], {}
    preamble = normalize_index_preamble(partition, preamble)

    pages = list_partition_pages(partition_dir)
    present = {p.name: p for p in pages}

    # Preserve order from existing index; drop missing; append new (sorted).
    ordered: list[str] = [fn for fn in existing_order if fn in present]
    new_pages = sorted(fn for fn in present if fn not in ordered)
    ordered.extend(new_pages)

    # Build bullet lines. When refresh_labels is False, reuse existing bullets
    # byte-for-byte to preserve human-curated summaries. When True, regenerate
    # every bullet with the latest title (aliases/H1) but preserve summary tail.
    lines: list[str] = []
    for fn in ordered:
        text = present[fn].read_text(encoding="utf-8")
        title = extract_page_title(text) or fn[:-3]
        summary = extract_page_summary(text)

        if fn in existing_lines and not refresh_labels:
            # Preserve existing bullet as-is
            lines.append(existing_lines[fn])
            continue

        # Try to preserve summary from existing bullet when refreshing
        if fn in existing_lines and refresh_labels:
            old_line = existing_lines[fn]
            if " — " in old_line:
                summary = old_line.split(" — ", 1)[1].rstrip("\n")

        if summary:
            lines.append(f"- [{title}]({fn}) — {summary}")
        else:
            lines.append(f"- [{title}]({fn})")

    if not lines:
        lines.append("（暂无页面）")

    new_text = preamble.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"
    if (partition_dir / PREVIEW_FILENAME).exists():
        new_text += "\n" + PREVIEW_LINK_LINE + "\n"
    if new_text != old_text:
        new_text = _refresh_index_updated(new_text)
    return new_text, old_text


def _today_cn() -> str:
    return dt.datetime.now(TZ_CN).strftime("%Y-%m-%d")


def render_preview(partition: str, date: str | None = None) -> str:
    """Render wiki/<partition>/index-by-subtopic.md preview text."""
    partition_dir = WIKI_ROOT / partition
    if not partition_dir.is_dir():
        raise ValueError(f"partition not found: {partition}")

    page_infos: list[dict[str, object]] = []
    for path in list_partition_pages(partition_dir):
        text = path.read_text(encoding="utf-8")
        page_infos.append(
            {
                "filename": path.name,
                "title": extract_page_title(text) or path.stem,
                "summary": extract_page_summary(text),
                "subtopics": extract_page_subtopics(text),
            }
        )

    groups: dict[str, list[dict[str, object]]] = {}
    untagged: list[dict[str, object]] = []
    for info in page_infos:
        subtopics = info["subtopics"]
        if not isinstance(subtopics, list) or not subtopics:
            untagged.append(info)
            continue
        for subtopic in subtopics:
            groups.setdefault(str(subtopic), []).append(info)

    lines: list[str] = [
        "---",
        "owner: linter",
        "status: draft",
        f"updated: {date or _today_cn()}",
        'subtopic: ["memory-engine"]',
        f'title: "{partition} Index (by subtopic, preview)"',
        "---",
        "",
        f"# {partition} Index — by subtopic (PoC preview)",
        "",
        "## 摘要",
        "",
        "本页是 `tools/tm_compile_index.py preview` 的实验性输出，按 subtopic 分组展示。原 `index.md` 仍是 partition 的事实索引，不受本预览影响。",
        "",
        f"> 重新生成命令：`py tools/tm_compile_index.py preview --partition {partition}`",
        "",
    ]

    for subtopic, pages in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.extend([f"## {subtopic} ({len(pages)})", ""])
        for info in sorted(pages, key=lambda page: str(page["filename"])):
            title = str(info["title"])
            filename = str(info["filename"])
            summary = str(info["summary"])
            if summary:
                lines.append(f"- [{title}]({filename}) — {summary}")
            else:
                lines.append(f"- [{title}]({filename})")
        lines.append("")

    if untagged:
        lines.extend([f"## 未打 subtopic ({len(untagged)})", ""])
        for info in sorted(untagged, key=lambda page: str(page["filename"])):
            title = str(info["title"])
            filename = str(info["filename"])
            summary = str(info["summary"])
            if summary:
                lines.append(f"- [{title}]({filename}) — {summary}")
            else:
                lines.append(f"- [{title}]({filename})")
        lines.append("")

    lines.extend(
        [
            "## 来源",
            "",
            "- 设计依据：`wiki/systems/memory-tree-subtopic-index-poc.md`",
            "- subtopic 词表稳定记录：`sources/internal-analysis/2026-05-24-memory-tree-v0-self-evolution-poc.md`",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------- CLI ----------------


def _resolve_partitions(arg: str | None) -> list[str]:
    if arg:
        if arg not in PARTITIONS:
            raise ValueError(f"unknown partition: {arg}")
        return [arg]
    return list(PARTITIONS)


def _diff(old: str, new: str, label: str) -> Iterable[str]:
    return difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )


def cmd_check(args: argparse.Namespace) -> int:
    any_diff = False
    for part in _resolve_partitions(args.partition):
        new, old = compile_partition_index(part, refresh_labels=args.refresh_labels)
        if new != old:
            any_diff = True
            print(f"DIFF wiki/{part}/index.md", file=sys.stderr)
            sys.stderr.writelines(_diff(old, new, f"wiki/{part}/index.md"))
    return 1 if any_diff else 0


def cmd_diff(args: argparse.Namespace) -> int:
    for part in _resolve_partitions(args.partition):
        new, old = compile_partition_index(part, refresh_labels=args.refresh_labels)
        if new != old:
            print(f"=== wiki/{part}/index.md ===")
            sys.stdout.writelines(_diff(old, new, f"wiki/{part}/index.md"))
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    changed = []
    for part in _resolve_partitions(args.partition):
        new, old = compile_partition_index(part, refresh_labels=args.refresh_labels)
        if new != old:
            (WIKI_ROOT / part / "index.md").write_text(new, encoding="utf-8")
            changed.append(part)
    if changed:
        print("WROTE: " + ", ".join(f"wiki/{p}/index.md" for p in changed))
    else:
        print("NO CHANGES")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    partition = args.partition or "brand"
    if partition not in PARTITIONS:
        raise ValueError(f"unknown partition: {partition}")
    out_path = WIKI_ROOT / partition / PREVIEW_FILENAME
    out_path.write_text(render_preview(partition), encoding="utf-8")
    print(f"WROTE: wiki/{partition}/{PREVIEW_FILENAME}")
    return 0


def cmd_check_preview(args: argparse.Namespace) -> int:
    partition = args.partition or "brand"
    if partition not in PARTITIONS:
        raise ValueError(f"unknown partition: {partition}")
    out_path = WIKI_ROOT / partition / PREVIEW_FILENAME
    old = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    new = render_preview(partition)
    if new != old:
        print(f"DIFF wiki/{partition}/{PREVIEW_FILENAME}", file=sys.stderr)
        sys.stderr.writelines(_diff(old, new, f"wiki/{partition}/{PREVIEW_FILENAME}"))
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tm_compile_index.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("check", cmd_check), ("diff", cmd_diff), ("write", cmd_write)):
        sp = sub.add_parser(name)
        sp.add_argument("--partition", default=None, help="limit to one partition")
        sp.add_argument(
            "--refresh-labels",
            action="store_true",
            help="regenerate bullet labels from aliases/H1 (preserves summaries)",
        )
        sp.set_defaults(func=fn)

    preview = sub.add_parser("preview")
    preview.add_argument("--partition", default="brand", help="partition to preview (default: brand)")
    preview.set_defaults(func=cmd_preview)

    check_preview = sub.add_parser("check-preview")
    check_preview.add_argument("--partition", default="brand", help="partition to check (default: brand)")
    check_preview.set_defaults(func=cmd_check_preview)

    args = p.parse_args(argv)
    try:
        code = args.func(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(code)


if __name__ == "__main__":
    main()
