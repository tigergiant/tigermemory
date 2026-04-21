#!/usr/bin/env python3
"""
Phase C1: Backlinks Dashboard Compiler

扫描 wiki 所有页面，生成反向链接拓扑图：
- 每个页被谁引用
- 孤立页（零反链）检测
- 枢纽页（Top 10 按反链数）

CLI:
  python3 tools/tm_backlinks.py check    # exit 0/1
  python3 tools/tm_backlinks.py write    # 写入 dashboard
  python3 tools/tm_backlinks.py diff     # stdout 显示差异
"""

import os
import re
import sys
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import NamedTuple


class Section(NamedTuple):
    level: int
    title: str
    body_lines: list[str]
    start_line: int
    end_line: int


# 忽略的分区/路径
IGNORE_PATHS = ('inbox/', 'archive/', 'sources/', '.git/', '.tmp/')

# 正则：匹配各种链接形式
RE_MD_RELATIVE_LINK = re.compile(r'\[([^\]]+)\]\((\.\.?/[^)]+\.md)\)')
RE_MD_SAME_PARTITION_LINK = re.compile(r'\[([^\]]+)\]\(([a-zA-Z0-9\-_]+\.md)\)')
RE_OBSIDIAN_WIKILINK = re.compile(r'\[\[([^\]|]+)(\|[^\]]+)?\]\]')
RE_CODE_BLOCK = re.compile(r'```[\s\S]*?```')

PARTITIONS = ("brand", "investment", "operations", "production", "systems", "person")


def _strip_code_blocks(text: str) -> str:
    """移除 fenced code block 后再匹配链接，防止误伤。"""
    return RE_CODE_BLOCK.sub('', text)


def _normalize_path(target: str, from_partition: str, repo_root: str) -> str | None:
    """
    将各种路径形式归一化为 wiki/<partition>/<page>.md
    返回 None 表示非 wiki 内部链接（如外部 URL）
    """
    target = target.strip()
    
    # 跳过外部 URL
    if target.startswith(('http://', 'https://', 'mailto:')):
        return None
    
    # 跳过已归档/非 wiki 路径
    if any(target.startswith(p) for p in IGNORE_PATHS):
        return None
    
    # 已经是标准形式
    if target.startswith('wiki/'):
        return target.replace('\\', '/')
    
    # 相对路径解析
    if target.startswith('./'):
        # 同目录：./other.md -> wiki/<partition>/other.md
        return f"wiki/{from_partition}/{target[2:]}".replace('\\', '/')
    
    if target.startswith('../'):
        # 跨分区：../systems/other.md -> wiki/systems/other.md
        parts = target[3:].replace('\\', '/').split('/')
        if len(parts) >= 2 and parts[0] in PARTITIONS:
            return f"wiki/{'/'.join(parts)}"
        return None
    
    # 同分区裸文件名：other.md -> wiki/<partition>/other.md
    if target.endswith('.md') and '/' not in target:
        return f"wiki/{from_partition}/{target}"
    
    # Obsidian wikilink [[page_name]]：需要后续全局搜索匹配
    return None


def _resolve_wikilink(wikilink: str, all_pages: set[str]) -> str | None:
    """
    解析 Obsidian wikilink 到实际路径。
    策略：按文件名匹配，唯一命中则返回，否则返回 None。
    """
    name = wikilink.strip().lower().replace(' ', '-')
    candidates = []
    
    for page in all_pages:
        # page 格式：wiki/partition/slug.md
        slug = Path(page).stem.lower()
        if slug == name or slug.replace('-', '') == name.replace('-', ''):
            candidates.append(page)
    
    if len(candidates) == 1:
        return candidates[0]
    return None  # 零命中或多命中


def scan_wiki_references(repo_root: str) -> dict[str, list[str]]:
    """
    扫描 repo_root/wiki/ 下的所有 .md 文件，返回 {target_page: [source_pages]}
    """
    backlinks: dict[str, list[str]] = {}
    all_pages: set[str] = set()
    wiki_root = Path(repo_root) / "wiki"
    
    # 第一遍：收集所有页面
    for partition in PARTITIONS:
        part_dir = wiki_root / partition
        if not part_dir.exists():
            continue
        for md_file in part_dir.rglob("*.md"):
            rel_path = f"wiki/{partition}/{md_file.relative_to(part_dir)}".replace('\\', '/')
            all_pages.add(rel_path)
            backlinks[rel_path] = []
    
    # 第二遍：解析引用
    for partition in PARTITIONS:
        part_dir = wiki_root / partition
        if not part_dir.exists():
            continue
        for md_file in part_dir.rglob("*.md"):
            source_path = f"wiki/{partition}/{md_file.relative_to(part_dir)}".replace('\\', '/')
            content = md_file.read_text(encoding='utf-8')
            
            # 先去掉 code block
            content_clean = _strip_code_blocks(content)
            
            # 匹配相对链接
            for match in RE_MD_RELATIVE_LINK.finditer(content_clean):
                target = match.group(2)
                normalized = _normalize_path(target, partition, repo_root)
                if normalized and normalized in all_pages:
                    backlinks[normalized].append(source_path)
            
            # 匹配同分区链接
            for match in RE_MD_SAME_PARTITION_LINK.finditer(content_clean):
                target = match.group(1)
                if target.endswith('.md'):
                    normalized = _normalize_path(target, partition, repo_root)
                    if normalized and normalized in all_pages:
                        backlinks[normalized].append(source_path)
            
            # 匹配 Obsidian wikilink
            for match in RE_OBSIDIAN_WIKILINK.finditer(content_clean):
                wikilink = match.group(1).strip()
                resolved = _resolve_wikilink(wikilink, all_pages)
                if resolved:
                    backlinks[resolved].append(source_path)
    
    # 去重并保持顺序
    for target in backlinks:
        seen = set()
        unique = []
        for src in backlinks[target]:
            if src not in seen:
                seen.add(src)
                unique.append(src)
        backlinks[target] = sorted(unique)
    
    return backlinks, sorted(all_pages)


def compile_dashboard(backlinks: dict[str, list[str]], all_pages: list[str]) -> str:
    """编译 dashboard markdown 内容。"""
    
    # 按分区组织
    by_partition: dict[str, list[tuple[str, list[str]]]] = {p: [] for p in PARTITIONS}
    orphan_count = 0
    linked_count = 0
    
    for page in all_pages:
        refs = backlinks.get(page, [])
        partition = page.split('/')[1]  # wiki/partition/file.md
        by_partition[partition].append((page, refs))
        if not refs:
            orphan_count += 1
        else:
            linked_count += 1
    
    # 计算枢纽页（Top 10）
    all_with_counts = [(p, len(backlinks.get(p, []))) for p in all_pages]
    hubs = sorted(all_with_counts, key=lambda x: -x[1])[:10]
    
    # 生成日期
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    date_str = now.strftime("%Y-%m-%d")
    
    lines = [
        "---",
        "owner: linter",
        "status: active",
        'aliases: ["反链仪表板"]',
        f"updated: {date_str}",
        "---",
        "",
        "# Backlinks Dashboard",
        "",
        "本页由 `tools/tm_backlinks.py write` 每日自动生成。不要手工编辑。",
        "",
        "## 摘要",
        "",
        f"- 总页数: {len(all_pages)}",
        f"- 有反链的页: {linked_count}",
        f"- 孤立页（零反链）: {orphan_count}",
        "",
        "## 枢纽页（Top 10，按反链数降序）",
        "",
        "| 页 | 反链数 | 摘录 |",
        "|----|-------|------|",
    ]
    
    for page, count in hubs:
        if count > 0:
            # 摘录前 3 个反链
            excerpt = ', '.join(backlinks.get(page, [])[:3])
            if len(backlinks.get(page, [])) > 3:
                excerpt += '...'
            lines.append(f"| {page} | {count} | {excerpt} |")
    
    lines.extend([
        "",
        "## 全量反链",
        "",
    ])
    
    # 按分区字母序输出
    for partition in sorted(PARTITIONS):
        entries = by_partition.get(partition, [])
        if not entries:
            continue
        
        # 分区内按文件名排序
        entries_sorted = sorted(entries, key=lambda x: x[0])
        
        for page, refs in entries_sorted:
            lines.append(f"### {page}")
            if refs:
                lines.append(f"- Referenced by ({len(refs)}):")
                for src in refs:
                    lines.append(f"  - {src}")
            else:
                lines.append("- Referenced by (0): *(orphan)*")
            lines.append("")
    
    lines.extend([
        "## 来源",
        "",
        "- 内部文档",
        "",
    ])
    
    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tools/tm_backlinks.py {check|write|diff}", file=sys.stderr)
        sys.exit(1)
    
    cmd = sys.argv[1]
    repo_root = Path(__file__).parent.parent.resolve()
    dashboard_path = repo_root / "wiki" / "operations" / "backlinks-dashboard.md"
    
    backlinks, all_pages = scan_wiki_references(str(repo_root))
    new_content = compile_dashboard(backlinks, all_pages)
    
    if cmd == "write":
        dashboard_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard_path.write_text(new_content, encoding='utf-8')
        print(f"Written: {dashboard_path}")
        sys.exit(0)
    
    elif cmd == "check":
        if not dashboard_path.exists():
            print(f"MISSING: {dashboard_path}", file=sys.stderr)
            sys.exit(1)
        old_content = dashboard_path.read_text(encoding='utf-8')
        # 忽略 updated 字段比较（日期会变）
        old_lines = [l for l in old_content.split('\n') if not l.startswith('updated:')]
        new_lines = [l for l in new_content.split('\n') if not l.startswith('updated:')]
        if old_lines == new_lines:
            print("OK: dashboard matches scan")
            sys.exit(0)
        else:
            print("DIFF: dashboard needs refresh", file=sys.stderr)
            sys.exit(1)
    
    elif cmd == "diff":
        if not dashboard_path.exists():
            print(f"MISSING: {dashboard_path}", file=sys.stderr)
            sys.exit(1)
        old_content = dashboard_path.read_text(encoding='utf-8')
        import difflib
        diff = difflib.unified_diff(
            old_content.split('\n'), new_content.split('\n'),
            fromfile="old", tofile="new", lineterm=""
        )
        for line in diff:
            print(line)
        sys.exit(0)
    
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
