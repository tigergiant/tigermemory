#!/usr/bin/env python3
"""Read-only audit for Memory Tree subtopic frontmatter.

Default scope is top-level wiki pages: wiki/<partition>/*.md excluding index.md.
Nested generated or log-like pages can be included with --include-nested.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WIKI_ROOT = REPO_ROOT / "wiki"
PARTITIONS = ["brand", "investment", "operations", "person", "production", "self-evolution", "systems"]

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
INLINE_ARRAY_RE = re.compile(r"^\s*subtopic\s*:\s*\[(.*?)\]\s*$", re.MULTILINE)
BLOCK_ARRAY_RE = re.compile(r"^\s*subtopic\s*:\s*\n((?:\s*-\s*.+(?:\n|$))+)", re.MULTILINE)
SCALAR_RE = re.compile(r"^\s*subtopic\s*:\s*(.+?)\s*$", re.MULTILINE)
FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$", re.MULTILINE)


@dataclass
class PageSubtopic:
    path: str
    partition: str
    nested: bool
    has_frontmatter: bool
    title: str
    owner: str
    status: str
    subtopics: list[str]


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    return path.relative_to(root).as_posix()


def _split_items(raw: str) -> list[str]:
    items: list[str] = []
    for chunk in raw.split(","):
        value = chunk.strip().strip('"').strip("'")
        if value:
            items.append(value)
    return items


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str], bool]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, [], False
    fm = match.group(1)
    fields = {m.group(1): m.group(2).strip().strip('"').strip("'") for m in FIELD_RE.finditer(fm)}

    inline = INLINE_ARRAY_RE.search(fm)
    if inline:
        return fields, _split_items(inline.group(1)), True

    block = BLOCK_ARRAY_RE.search(fm)
    if block:
        values = []
        for line in block.group(1).splitlines():
            value = line.strip()
            if value.startswith("-"):
                value = value[1:].strip().strip('"').strip("'")
                if value:
                    values.append(value)
        return fields, values, True

    scalar = SCALAR_RE.search(fm)
    if scalar:
        value = scalar.group(1).strip().strip('"').strip("'")
        return fields, ([value] if value else []), True

    return fields, [], True


def iter_wiki_pages(repo_root: pathlib.Path, include_nested: bool = False) -> list[pathlib.Path]:
    wiki_root = repo_root / "wiki"
    pages: list[pathlib.Path] = []
    for partition in PARTITIONS:
        pdir = wiki_root / partition
        if not pdir.is_dir():
            continue
        pattern = "**/*.md" if include_nested else "*.md"
        for path in sorted(pdir.glob(pattern)):
            if path.name == "index.md":
                continue
            if path.is_file():
                pages.append(path)
    return pages


def collect_pages(repo_root: pathlib.Path = REPO_ROOT, include_nested: bool = False) -> list[PageSubtopic]:
    pages: list[PageSubtopic] = []
    for path in iter_wiki_pages(repo_root, include_nested=include_nested):
        rel = _rel(path, repo_root)
        parts = pathlib.PurePosixPath(rel).parts
        partition = parts[1] if len(parts) > 1 else ""
        nested = len(parts) > 3
        text = path.read_text(encoding="utf-8")
        fields, subtopics, has_frontmatter = parse_frontmatter(text)
        pages.append(PageSubtopic(
            path=rel,
            partition=partition,
            nested=nested,
            has_frontmatter=has_frontmatter,
            title=fields.get("title", ""),
            owner=fields.get("owner", ""),
            status=fields.get("status", ""),
            subtopics=subtopics,
        ))
    return pages


def _normal_form(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _similar_topic_groups(topics: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    seen: set[str] = set()
    for topic in sorted(topics):
        if topic in seen:
            continue
        group = [topic]
        for other in sorted(topics):
            if other == topic or other in seen:
                continue
            same_norm = _normal_form(topic) == _normal_form(other)
            similar = SequenceMatcher(None, topic.lower(), other.lower()).ratio() >= 0.86
            if same_norm or similar:
                group.append(other)
        if len(group) > 1:
            seen.update(group)
            groups.append(group)
    return groups


def build_report(pages: list[PageSubtopic], low_freq_threshold: int = 1) -> dict[str, Any]:
    topic_counter: Counter[str] = Counter()
    partition_topic: dict[str, Counter[str]] = defaultdict(Counter)
    partition_pages: Counter[str] = Counter()
    untagged: list[PageSubtopic] = []
    too_many: list[PageSubtopic] = []
    no_frontmatter: list[PageSubtopic] = []

    for page in pages:
        partition_pages[page.partition] += 1
        if not page.has_frontmatter:
            no_frontmatter.append(page)
        if not page.subtopics:
            untagged.append(page)
        if len(page.subtopics) > 3:
            too_many.append(page)
        for topic in page.subtopics:
            topic_counter[topic] += 1
            partition_topic[page.partition][topic] += 1

    low_freq = {topic: count for topic, count in sorted(topic_counter.items()) if count <= low_freq_threshold}
    return {
        "total_pages": len(pages),
        "partition_pages": dict(sorted(partition_pages.items())),
        "subtopic_counts": dict(sorted(topic_counter.items())),
        "partition_subtopics": {
            partition: dict(sorted(counter.items()))
            for partition, counter in sorted(partition_topic.items())
        },
        "untagged_count": len(untagged),
        "untagged_pages": [asdict(page) for page in untagged],
        "too_many_count": len(too_many),
        "too_many_pages": [asdict(page) for page in too_many],
        "no_frontmatter_count": len(no_frontmatter),
        "no_frontmatter_pages": [asdict(page) for page in no_frontmatter],
        "low_frequency_subtopics": low_freq,
        "possible_synonym_groups": _similar_topic_groups(list(topic_counter.keys())),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Memory Tree Subtopic Audit")
    lines.append("")
    lines.append(f"- total_pages: {report['total_pages']}")
    lines.append(f"- untagged_count: {report['untagged_count']}")
    lines.append(f"- too_many_count: {report['too_many_count']}")
    lines.append(f"- no_frontmatter_count: {report['no_frontmatter_count']}")
    lines.append("")

    lines.append("## Partition x Subtopic")
    lines.append("")
    lines.append("| partition | subtopic | count |")
    lines.append("|---|---|---:|")
    for partition, topics in report["partition_subtopics"].items():
        for topic, count in topics.items():
            lines.append(f"| `{partition}` | `{topic}` | {count} |")
    if not report["partition_subtopics"]:
        lines.append("| - | - | 0 |")
    lines.append("")

    lines.append("## Subtopic Counts")
    lines.append("")
    lines.append("| subtopic | count |")
    lines.append("|---|---:|")
    for topic, count in report["subtopic_counts"].items():
        lines.append(f"| `{topic}` | {count} |")
    if not report["subtopic_counts"]:
        lines.append("| - | 0 |")
    lines.append("")

    lines.append("## Untagged Top-Level Pages")
    lines.append("")
    for page in report["untagged_pages"]:
        lines.append(f"- `{page['path']}`")
    if not report["untagged_pages"]:
        lines.append("- none")
    lines.append("")

    lines.append("## Too Many Subtopics")
    lines.append("")
    for page in report["too_many_pages"]:
        lines.append(f"- `{page['path']}` :: {', '.join(page['subtopics'])}")
    if not report["too_many_pages"]:
        lines.append("- none")
    lines.append("")

    lines.append("## Low-Frequency Subtopics")
    lines.append("")
    for topic, count in report["low_frequency_subtopics"].items():
        lines.append(f"- `{topic}`: {count}")
    if not report["low_frequency_subtopics"]:
        lines.append("- none")
    lines.append("")

    lines.append("## Possible Synonym Groups")
    lines.append("")
    for group in report["possible_synonym_groups"]:
        lines.append("- " + ", ".join(f"`{item}`" for item in group))
    if not report["possible_synonym_groups"]:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit wiki subtopic frontmatter")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root")
    parser.add_argument("--include-nested", action="store_true", help="include nested wiki pages")
    parser.add_argument("--low-frequency-threshold", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(args.root).resolve()
    report = build_report(
        collect_pages(repo_root, include_nested=args.include_nested),
        low_freq_threshold=args.low_frequency_threshold,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
