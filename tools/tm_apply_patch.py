#!/usr/bin/env python3
"""
Phase C2: Patch Apply Tool

消化 inbox/*-tigermemory-ce-cross.md 补丁提案，写入 wiki 页。

CLI:
  python3 tools/tm_apply_patch.py <inbox_path>          # dry-run (default)
  python3 tools/tm_apply_patch.py <inbox_path> --apply  # 真实写入
  python3 tools/tm_apply_patch.py <inbox_path> --apply --yes  # 跳过 confirm
Inputs: Repository markdown/python files, frontmatter, section text, git diff inputs, or CLI path arguments.
Outputs: Deterministic reports, rewritten generated files, validation errors, or patch proposals.
Depends-on (must-have): Python stdlib plus tm_core/path parsers; no Mem0 write path unless explicitly invoked by caller.
"""

import os
import sys
import re
import shutil
import argparse
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tm_md_sections import parse_sections, replace_section, append_to_section, add_new_section, update_frontmatter_date

# 支持的 patch types
SUPPORTED_TYPES = {'update_section', 'append_section', 'new_section'}
UNSUPPORTED_TYPES = {'add_page', 'rename_page', 'delete_section'}


def read_patches(inbox_path: str) -> list[dict]:
    """
    解析 inbox 文件中的 patches。
    
    格式：
    ### N. `page` — type
    - **section**: Title
    - **rationale**: ...
    
    ```markdown
    content
    ```
    """
    content = Path(inbox_path).read_text(encoding='utf-8')
    patches = []
    
    # 正则匹配 patch 头
    patch_header_re = re.compile(
        r'###\s*\d+\.\s*`([^`]+)`\s*—\s*(\w+)\s*\n'
        r'(?:- \*\*section\*\*:\s*([^\n]+)\n)?'
        r'(?:- \*\*rationale\*\*:\s*([^\n]+)\n)?'
    )
    
    # 找到所有 fenced code blocks
    code_blocks = []
    for match in re.finditer(r'```markdown\n(.*?)```', content, re.DOTALL):
        code_blocks.append((match.start(), match.group(1).strip()))
    
    # 解析 headers 并关联 code blocks
    for i, match in enumerate(re.finditer(patch_header_re, content)):
        page = match.group(1).strip()
        ptype = match.group(2).strip()
        section = (match.group(3) or '').strip()
        rationale = (match.group(4) or '').strip()
        
        # 找紧随其后的 code block
        patch_end = match.end()
        content_text = ''
        for cb_start, cb_content in code_blocks:
            if cb_start >= patch_end and cb_start < patch_end + 500:  # 在 patch 附近
                content_text = cb_content
                break
        
        patches.append({
            'page': page,
            'type': ptype,
            'section': section,
            'rationale': rationale,
            'content': content_text,
        })
    
    return patches


def apply_patch(patch: dict, repo_root: str, dry_run: bool = True) -> tuple[bool, str]:
    """
    应用单个 patch。
    
    Returns: (success, message)
    """
    page = patch['page']
    ptype = patch['type']
    section = patch.get('section', '')
    content = patch.get('content', '')
    
    # 安全检查
    if not page.startswith('wiki/'):
        return False, f'Invalid page path: {page}'
    
    page_path = Path(repo_root) / page
    if not page_path.exists():
        return False, f'Page does not exist: {page}'
    
    if ptype in UNSUPPORTED_TYPES:
        return False, f'Unsupported type: {ptype}'
    
    if ptype not in SUPPORTED_TYPES:
        return False, f'Unknown type: {ptype}'
    
    if not section:
        return False, 'Missing section title'
    
    if not content.strip():
        return False, 'Empty content'
    
    # 读取原文件
    original_text = page_path.read_text(encoding='utf-8')
    
    try:
        if ptype == 'update_section':
            new_text = replace_section(original_text, section, content)
        elif ptype == 'append_section':
            new_text = append_to_section(original_text, section, content)
        elif ptype == 'new_section':
            # 如果 section 已存在则追加，否则创建新 section
            try:
                new_text = append_to_section(original_text, section, content)
            except ValueError:
                new_text = add_new_section(original_text, section, content)
        else:
            return False, f'Unhandled type: {ptype}'
        
        # 更新 frontmatter date
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        new_text = update_frontmatter_date(new_text, today)
        
        if not dry_run:
            page_path.write_text(new_text, encoding='utf-8')
        
        return True, f'{ptype} "{section}" in {page}'
    
    except ValueError as e:
        return False, f'Section error: {e}'


def l2_review(content: str) -> tuple[int, str]:
    """
    L2 review：调用 tm_review 对 content 打分。
    
    Returns: (score, feedback)
    """
    try:
        import tm_review
        result = tm_review.review_draft(content)
        return result.get('score', 0), result.get('feedback', '')
    except Exception as e:
        # L2 挂了，fail-warn
        return 50, f'Review skipped: {e}'


def archive_inbox(inbox_path: str, archive_dir: str) -> str:
    """归档 inbox 文件到 archive/applied-patches/"""
    inbox_name = Path(inbox_path).name
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    archive_name = f'{today}-{inbox_name}'
    
    dest_dir = Path(archive_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    dest_path = dest_dir / archive_name
    shutil.move(inbox_path, str(dest_path))
    return str(dest_path)


def git_commit_patches(repo_root: str, inbox_name: str, patches: list[dict], scores: list[int]) -> bool:
    """Commit 所有 applied patches"""
    try:
        # 添加修改的 wiki 页
        for p in patches:
            subprocess.run(['git', 'add', p['page']], cwd=repo_root, check=True)
        
        # 构建 commit message
        lines = [f'[tigermemory-ce] ingest: apply {len(patches)} wiki patches from {inbox_name}', '']
        lines.append('Sources:')
        for p in patches:
            lines.append(f'  - {p["page"]} ({p["type"]} "{p["section"]}")')
        lines.append('')
        lines.append(f'L2 review scores: {scores}')
        
        message = '\n'.join(lines)
        
        subprocess.run(['git', 'commit', '-m', message], cwd=repo_root, check=True)
        subprocess.run(['git', 'push'], cwd=repo_root, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f'Git commit failed: {e}', file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description='Apply wiki patches from inbox')
    parser.add_argument('inbox_path', help='Path to inbox file')
    parser.add_argument('--apply', action='store_true', help='Actually apply patches (default dry-run)')
    parser.add_argument('--yes', action='store_true', help='Skip interactive confirmation')
    parser.add_argument('--skip-review', action='store_true', help='Skip L2 review')
    parser.add_argument('--archive-to', default='archive/applied-patches', help='Archive directory')
    
    args = parser.parse_args()
    
    repo_root = Path(__file__).parent.parent.resolve()
    
    if not os.path.exists(args.inbox_path):
        print(f'Error: {args.inbox_path} not found', file=sys.stderr)
        sys.exit(1)
    
    # 解析 patches
    patches = read_patches(args.inbox_path)
    if not patches:
        print('No patches found in inbox file')
        sys.exit(0)
    
    print(f'Found {len(patches)} patch(es) in {args.inbox_path}')
    print()
    
    applied = []
    scores = []
    skipped = []
    
    for i, patch in enumerate(patches, 1):
        print(f'Patch {i}:')
        print(f'  Page: {patch["page"]}')
        print(f'  Type: {patch["type"]}')
        print(f'  Section: {patch["section"]}')
        print(f'  Rationale: {patch["rationale"][:60]}...' if len(patch.get('rationale', '')) > 60 else f'  Rationale: {patch.get("rationale", "")}')
        
        # L2 review
        if not args.skip_review:
            score, feedback = l2_review(patch['content'])
            scores.append(score)
            print(f'  L2 score: {score}')
            if score < 30:
                print(f'  -> SKIPPED (score < 30)')
                skipped.append((i, 'low score'))
                continue
        else:
            scores.append(None)
            print('  L2 review: skipped')
        
        # Dry-run preview
        success, msg = apply_patch(patch, str(repo_root), dry_run=True)
        if not success:
            print(f'  -> FAILED: {msg}')
            skipped.append((i, msg))
            continue
        
        print(f'  Preview: OK ({msg})')
        applied.append(patch)
        print()
    
    if not applied:
        print('No patches to apply (all skipped or failed)')
        sys.exit(0)
    
    if not args.apply:
        print(f'\nDry-run complete. {len(applied)} patch(es) ready to apply.')
        print('Use --apply to execute.')
        sys.exit(0)
    
    # Confirm
    if not args.yes:
        confirm = input(f'\nApply {len(applied)} patch(es)? [y/N] ')
        if confirm.lower() != 'y':
            print('Aborted.')
            sys.exit(0)
    
    # Apply
    print('\nApplying patches...')
    for patch in applied:
        success, msg = apply_patch(patch, str(repo_root), dry_run=False)
        if success:
            print(f'  OK: {msg}')
        else:
            print(f'  FAIL: {msg}')
    
    # Archive inbox
    archive_path = archive_inbox(args.inbox_path, str(repo_root / args.archive_to))
    print(f'Archived to: {archive_path}')
    
    # Git commit
    inbox_name = Path(args.inbox_path).name
    if git_commit_patches(str(repo_root), inbox_name, applied, [s for s in scores if s is not None]):
        print('Committed and pushed.')
    else:
        print('Git commit failed (manual intervention needed)', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
