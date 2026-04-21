#!/usr/bin/env python3
"""
中文化 aliases 注入工具

扫描 wiki 页面，从 H1 提取中文别名，写入 frontmatter。
"""

import os
import sys
import re
import argparse
from pathlib import Path

PARTITIONS = ("brand", "investment", "operations", "production", "systems", "person")


def extract_frontmatter(text: str) -> tuple[str | None, str]:
    """提取 frontmatter 和正文"""
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        return None, text
    
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            fm = '\n'.join(lines[:i+1])
            body = '\n'.join(lines[i+1:])
            return fm, body
    return None, text


def has_aliases_field(fm_text: str) -> bool:
    """检查 frontmatter 是否已有 aliases 字段（非空）"""
    if not fm_text:
        return False
    # 匹配 aliases: [...] 或 aliases:\n  - ... 形式
    if re.search(r'^aliases:\s*\[.+\]', fm_text, re.MULTILINE):
        return True
    if re.search(r'^aliases:\s*$', fm_text, re.MULTILINE):
        # 检查是否有列表项
        lines = fm_text.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('aliases:'):
                # 检查后续行是否有 - 开头的列表项
                for j in range(i+1, len(lines)):
                    if lines[j].strip().startswith('- '):
                        return True
                    if lines[j].strip() and not lines[j].startswith(' '):
                        break
    return False


def extract_h1(text: str) -> str | None:
    """从正文提取第一个 H1（不在 code block 中）"""
    lines = text.split('\n')
    in_code = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        # 匹配 H1
        match = re.match(r'^#\s+(.+)$', stripped)
        if match:
            title = match.group(1).strip()
            # 去除尾部括号注释如 (2026-04)
            title = re.sub(r'\s*\(\d{4}[-/]\d{2}[^)]*\)\s*$', '', title)
            # 去除 emoji
            title = re.sub(r'[\U0001F300-\U0001F9FF]', '', title).strip()
            return title
    return None


def is_chinese_friendly(text: str) -> bool:
    """检查文本是否中文友好（中文字符占比 >= 30%）"""
    if not text:
        return False
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    total_chars = len([c for c in text if c.isalpha() or re.match(r'[\u4e00-\u9fff]', c)])
    if total_chars == 0:
        return False
    return chinese_chars / total_chars >= 0.30


def generate_alias(h1: str | None) -> tuple[str | None, str]:
    """
    从 H1 生成 alias。
    Returns: (alias, reason)
    - alias: str 或 None（skip）
    - reason: 说明为何使用或跳过
    """
    if not h1:
        return None, "SKIP: no H1 found"
    
    if not is_chinese_friendly(h1):
        return None, f"SKIP: English-only H1 '{h1}'"
    
    return h1, "OK"


def inject_aliases_to_frontmatter(fm_text: str, alias: str) -> str:
    """
    在 frontmatter 中插入 aliases 字段。
    策略：在 status: 后、updated: 前插入（如果存在），否则在 frontmatter 末尾。
    """
    lines = fm_text.split('\n')
    
    # 找到 --- 结束位置
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end_idx = i
            break
    
    if end_idx is None:
        return fm_text  # 无效的 frontmatter
    
    # 查找关键字段位置
    status_idx = None
    updated_idx = None
    
    for i, line in enumerate(lines[:end_idx]):
        if line.strip().startswith('status:'):
            status_idx = i
        if line.strip().startswith('updated:'):
            updated_idx = i
    
    # 决定插入位置
    insert_idx = end_idx  # 默认在结束标记前
    if status_idx is not None:
        insert_idx = status_idx + 1
    elif updated_idx is not None:
        insert_idx = updated_idx
    
    # 转义特殊字符
    safe_alias = alias.replace('"', '\\"')
    new_line = f'aliases: ["{safe_alias}"]'
    
    # 插入并保留格式
    new_lines = lines[:insert_idx] + [new_line] + lines[insert_idx:]
    return '\n'.join(new_lines)


def process_page(page_path: Path, dry_run: bool = True, overwrite: bool = False) -> tuple[str, str]:
    """
    处理单个页面。
    Returns: (status, detail)
    - status: "SKIP", "OK", "ERROR"
    - detail: 说明
    """
    text = page_path.read_text(encoding='utf-8')
    fm, body = extract_frontmatter(text)
    
    if fm is None:
        return "ERROR", "no valid frontmatter"
    
    # 检查已有 aliases
    if has_aliases_field(fm) and not overwrite:
        return "SKIP", "existing aliases preserved"
    
    # 提取 H1 并生成 alias
    h1 = extract_h1(body)
    alias, reason = generate_alias(h1)
    
    if alias is None:
        return "SKIP", reason
    
    # 写入
    if not dry_run:
        new_fm = inject_aliases_to_frontmatter(fm, alias)
        new_text = new_fm + body
        page_path.write_text(new_text, encoding='utf-8')
    
    return "OK", f"alias='{alias}'"


def cmd_check(args):
    """列出所有页的 proposed alias，不写"""
    repo_root = Path(__file__).parent.parent
    
    skip_count = 0
    ok_count = 0
    error_count = 0
    
    partitions = [args.partition] if args.partition else PARTITIONS
    
    for part in partitions:
        part_dir = repo_root / "wiki" / part
        if not part_dir.exists():
            continue
        
        for md_file in sorted(part_dir.glob("*.md")):
            if md_file.name == "index.md":
                continue
            
            rel_path = f"wiki/{part}/{md_file.name}"
            status, detail = process_page(md_file, dry_run=True, overwrite=args.overwrite)
            
            if status == "OK":
                print(f"{rel_path}: {detail}")
                ok_count += 1
            elif status == "SKIP":
                print(f"{rel_path}: {detail}", file=sys.stderr)
                skip_count += 1
            else:
                print(f"{rel_path}: ERROR - {detail}", file=sys.stderr)
                error_count += 1
    
    print(f"\nSummary: OK={ok_count}, SKIP={skip_count}, ERROR={error_count}", file=sys.stderr)
    return 0


def cmd_write(args):
    """写入 frontmatter aliases"""
    repo_root = Path(__file__).parent.parent
    
    skip_count = 0
    ok_count = 0
    error_count = 0
    
    partitions = [args.partition] if args.partition else PARTITIONS
    
    for part in partitions:
        part_dir = repo_root / "wiki" / part
        if not part_dir.exists():
            continue
        
        for md_file in sorted(part_dir.glob("*.md")):
            if md_file.name == "index.md":
                continue
            
            rel_path = f"wiki/{part}/{md_file.name}"
            status, detail = process_page(md_file, dry_run=False, overwrite=args.overwrite)
            
            if status == "OK":
                print(f"WRITTEN: {rel_path}: {detail}")
                ok_count += 1
            elif status == "SKIP":
                print(f"SKIPPED: {rel_path}: {detail}")
                skip_count += 1
            else:
                print(f"ERROR: {rel_path}: {detail}", file=sys.stderr)
                error_count += 1
    
    print(f"\nSummary: WRITTEN={ok_count}, SKIPPED={skip_count}, ERROR={error_count}")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Inject Chinese aliases into wiki frontmatter')
    parser.add_argument('command', choices=['check', 'write'])
    parser.add_argument('--partition', help='Limit to specific partition')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing aliases')
    parser.add_argument('--dry', action='store_true', help='Alias for check (no-op)')
    
    args = parser.parse_args()
    
    if args.command == 'check' or args.dry:
        return cmd_check(args)
    else:
        return cmd_write(args)


if __name__ == '__main__':
    sys.exit(main())
