from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import re
import subprocess
from zoneinfo import ZoneInfo


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TZ_CN = ZoneInfo("Asia/Shanghai")
OUT_DIR = REPO_ROOT / ".tmp" / "dev-supervisor" / "context-packs"

DEFAULT_READ_PAGES = (
    "wiki/operations/project-canvas.md",
    "wiki/systems/tigermemory-development-supervisor.md",
    "wiki/operations/development-supervisor-ledger.md",
    "wiki/systems/tigermemory-project-map-for-claude.md",
)
DEFAULT_MEMORY_QUERIES = (
    "memory_type: session-handoff tigermemory development supervisor",
)
MAX_RECOMMENDED_FILES = 10
MAX_RECOMMENDED_ARCHIVES = 3
MAX_RECOMMENDED_READ_PAGES = 8
MAX_RECOMMENDED_MEMORY_QUERIES = 5


def _now() -> _dt.datetime:
    return _dt.datetime.now(TZ_CN)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug[:80] or "context-pack"


def _resolve_path(value: str) -> pathlib.Path:
    path = pathlib.Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _display_path(value: str) -> str:
    path = _resolve_path(value)
    return str(path)


def _path_status(value: str) -> str:
    return "exists" if _resolve_path(value).exists() else "missing"


def _budget_warnings(*, file_count: int, archive_count: int, read_page_count: int, memory_query_count: int) -> list[str]:
    warnings: list[str] = []
    if file_count > MAX_RECOMMENDED_FILES:
        warnings.append(f"local_files={file_count} exceeds recommended {MAX_RECOMMENDED_FILES}; split or summarize first.")
    if archive_count > MAX_RECOMMENDED_ARCHIVES:
        warnings.append(
            f"review_archives={archive_count} exceeds recommended {MAX_RECOMMENDED_ARCHIVES}; pass only decision-critical archives."
        )
    if read_page_count > MAX_RECOMMENDED_READ_PAGES:
        warnings.append(
            f"read_pages={read_page_count} exceeds recommended {MAX_RECOMMENDED_READ_PAGES}; prefer project-map/index pages."
        )
    if memory_query_count > MAX_RECOMMENDED_MEMORY_QUERIES:
        warnings.append(
            f"memory_queries={memory_query_count} exceeds recommended {MAX_RECOMMENDED_MEMORY_QUERIES}; merge overlapping queries."
        )
    return warnings


def _git_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(REPO_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def build_context_pack(
    *,
    objective: str,
    stage: str,
    files: list[str],
    review_archives: list[str],
    memory_queries: list[str],
    read_pages: list[str],
    notes: list[str],
) -> str:
    all_read_pages = list(dict.fromkeys([*DEFAULT_READ_PAGES, *read_pages]))
    all_memory_queries = list(dict.fromkeys([*DEFAULT_MEMORY_QUERIES, *memory_queries]))
    all_files = list(dict.fromkeys(files))
    all_archives = list(dict.fromkeys(review_archives))
    warnings = _budget_warnings(
        file_count=len(all_files),
        archive_count=len(all_archives),
        read_page_count=len(all_read_pages),
        memory_query_count=len(all_memory_queries),
    )

    lines: list[str] = [
        "# TigerMemory Claude Context Pack",
        "",
        "## Objective",
        "",
        objective.strip(),
        "",
        "## Reviewer Instruction",
        "",
        "- 不要默认全仓扫描；先读取本上下文包列出的文件、tigermemory 页面和记忆查询。",
        "- 简单资料收集优先交给 `tiger-context-collector`；跨文件上下文整理优先交给 `tiger-workspace-researcher`。",
        "- 最终 verdict 只能由 `tiger-development-reviewer` 或 `tiger-investment-quant-reviewer` 给出。",
        "- 如果这些文件仍不足以判断，先列 `Missing Evidence`，不要自行扩大到无边界扫描。",
        "- 不要输出密钥、cookie、Bearer token、OAuth credential 或私有 session id。",
        "",
        "## Current Snapshot",
        "",
        f"- repo: `{REPO_ROOT}`",
        f"- git_head: `{_git_head()}`",
        f"- stage: `{stage}`",
        "",
        "## Pack Budget",
        "",
        f"- local_files: {len(all_files)} / recommended <= {MAX_RECOMMENDED_FILES}",
        f"- review_archives: {len(all_archives)} / recommended <= {MAX_RECOMMENDED_ARCHIVES}",
        f"- read_pages: {len(all_read_pages)} / recommended <= {MAX_RECOMMENDED_READ_PAGES}",
        f"- memory_queries: {len(all_memory_queries)} / recommended <= {MAX_RECOMMENDED_MEMORY_QUERIES}",
        f"- budget_status: {'needs_shrinking' if warnings else 'ok'}",
        "",
        "## Budget Warnings",
        "",
    ]
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## TigerMemory Pages To Read First",
            "",
        ]
    )
    for page in all_read_pages:
        lines.append(f"- `read_page(path=\"{page}\")`")

    lines.extend(["", "## TigerMemory Memory Queries To Prefer", ""])
    for query in all_memory_queries:
        lines.append(f"- `search_memories(query=\"{query}\", size=3)`")

    lines.extend(["", "## Local Files To Read First", ""])
    if all_files:
        for item in all_files:
            lines.append(f"- [{_path_status(item)}] `{_display_path(item)}`")
    else:
        lines.append("- none specified")

    lines.extend(["", "## Review Archives / Evidence Artifacts", ""])
    if all_archives:
        for item in all_archives:
            lines.append(f"- [{_path_status(item)}] `{_display_path(item)}`")
    else:
        lines.append("- none specified")

    lines.extend(["", "## Extra Notes", ""])
    if notes:
        for note in notes:
            lines.append(f"- {note.strip()}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Expected Output",
            "",
            "请返回：",
            "",
            "- `Verdict`: 一句话结论。",
            "- `Verified Evidence`: 已实际读取或通过 MCP 查到的证据，带 Windows 绝对路径或 tigermemory 查询。",
            "- `Missing Evidence`: 还缺哪些文件、运行结果或记忆查询。",
            "- `Risks`: 如果按当前证据继续做，最大的风险是什么。",
            "- `Suggested Action`: 最小下一步。",
            "",
        ]
    )
    return "\n".join(lines)


def write_context_pack(text: str, *, stage: str, out_path: str | None = None) -> pathlib.Path:
    if out_path:
        path = pathlib.Path(out_path)
    else:
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        path = OUT_DIR / f"{stamp}-{_slug(stage)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a bounded Claude context pack for TigerMemory supervisor reviews")
    parser.add_argument("--objective", required=True, help="Review objective to send to Claude")
    parser.add_argument("--stage", default="context-pack", help="Supervisor stage name")
    parser.add_argument("--file", action="append", default=[], help="Local file path Claude should read first")
    parser.add_argument("--archive", action="append", default=[], help="Review archive or evidence artifact path")
    parser.add_argument("--memory-query", action="append", default=[], help="TigerMemory search_memories query Claude should prefer")
    parser.add_argument("--read-page", action="append", default=[], help="TigerMemory read_page path Claude should read first")
    parser.add_argument("--note", action="append", default=[], help="Extra bounded instruction or caveat")
    parser.add_argument("--out", help="Output markdown path; defaults under .tmp/dev-supervisor/context-packs")
    parser.add_argument("--print", action="store_true", help="Print the generated markdown instead of only the output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = build_context_pack(
        objective=args.objective,
        stage=args.stage,
        files=args.file,
        review_archives=args.archive,
        memory_queries=args.memory_query,
        read_pages=args.read_page,
        notes=args.note,
    )
    out_path = write_context_pack(text, stage=args.stage, out_path=args.out)
    if args.print:
        print(text)
    else:
        print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
