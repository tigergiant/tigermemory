#!/usr/bin/env python3
"""
Phase C2: Markdown Section Parser

解析 markdown 文本中的 section 结构，支持替换/追加操作。
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Section:
    level: int
    title: str
    body_lines: list[str]
    start_line: int
    end_line: int


def _is_in_code_block(lines: list[str], index: int) -> bool:
    """检查某行是否在 fenced code block 内"""
    in_code = False
    for i, line in enumerate(lines):
        if i > index:
            break
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
    return in_code


def parse_sections(text: str) -> list[Section]:
    """
    把 markdown 文本切成 section 列表。
    
    返回：[(level, title, body_lines, start_line, end_line), ...]
    - frontmatter 被跳过
    - code block 内的 heading 不计为 section
    - section 包含其子节，直到下一个同级或更高级 heading
    """
    lines = text.split('\n')
    sections = []
    
    # 跳过 frontmatter
    start_idx = 0
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                start_idx = i + 1
                break
    
    # 查找所有 heading（排除 frontmatter 和 code block）
    headings = []
    in_code = False
    for i, line in enumerate(lines):
        if i < start_idx:
            continue
        stripped = line.rstrip()
        if stripped.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        # 匹配 heading: # Title 或 ## Title 等
        match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            headings.append((i, level, title))
    
    # 构建 sections
    for idx, (line_idx, level, title) in enumerate(headings):
        # 确定 section 结束位置
        if idx + 1 < len(headings):
            next_line_idx = headings[idx + 1][0]
        else:
            next_line_idx = len(lines)
        
        # section body 是 heading 行之后到下一个同级/高级 heading 之前
        # 但包含子节
        body_start = line_idx + 1
        body_end = next_line_idx
        
        body_lines = lines[body_start:body_end] if body_start < body_end else []
        
        sections.append(Section(
            level=level,
            title=title,
            body_lines=body_lines,
            start_line=line_idx,
            end_line=next_line_idx - 1
        ))
    
    return sections


def replace_section(text: str, section_title: str, new_body: str, level: Optional[int] = None) -> str:
    """
    替换指定 title 的 section 的 body（保留标题行）。
    若 title 不存在 raise ValueError。
    """
    sections = parse_sections(text)
    
    # 查找匹配的 section
    target = None
    for sec in sections:
        if sec.title.strip() == section_title.strip():
            if level is None or sec.level == level:
                target = sec
                break
    
    if target is None:
        available = [s.title for s in sections]
        raise ValueError(f'Section "{section_title}" not found. Available: {available}')
    
    lines = text.split('\n')
    
    # 构建新内容：前部分 + 标题行 + 新 body + 后部分
    heading_line = lines[target.start_line]
    
    before = lines[:target.start_line + 1]  # 包含标题行
    after = lines[target.end_line + 1:] if target.end_line + 1 < len(lines) else []
    
    new_body_lines = new_body.split('\n') if new_body else []
    
    result = before + new_body_lines + after
    return '\n'.join(result)


def add_new_section(text: str, section_title: str, content: str, level: int = 2) -> str:
    """
    在文档末尾添加全新的 section。
    """
    lines = text.split('\n')
    
    # 在文件末尾添加新 section
    new_section_lines = [f"{'#' * level} {section_title}", ""] + content.split('\n')
    
    # 确保末尾有空行
    if lines and lines[-1].strip() != '':
        lines.append('')
    
    result = lines + new_section_lines
    return '\n'.join(result)


def append_to_section(text: str, section_title: str, addition: str, level: Optional[int] = None) -> str:
    """
    在指定 section 末尾追加内容（空行分隔）。
    """
    sections = parse_sections(text)
    
    target = None
    for sec in sections:
        if sec.title.strip() == section_title.strip():
            if level is None or sec.level == level:
                target = sec
                break
    
    if target is None:
        available = [s.title for s in sections]
        raise ValueError(f'Section "{section_title}" not found. Available: {available}')
    
    lines = text.split('\n')
    
    # 在 section 的 body 末尾插入
    insert_pos = target.end_line + 1
    
    addition_lines = addition.split('\n') if addition else []
    
    # 确保有空行分隔
    if lines[insert_pos - 1].strip() != '' and addition_lines:
        addition_lines = [''] + addition_lines
    
    before = lines[:insert_pos]
    after = lines[insert_pos:] if insert_pos < len(lines) else []
    
    result = before + addition_lines + after
    return '\n'.join(result)


def extract_frontmatter(text: str) -> tuple[Optional[str], str]:
    """提取 frontmatter 和正文，返回 (frontmatter_str, body)"""
    lines = text.split('\n')
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                fm = '\n'.join(lines[:i+1])
                body = '\n'.join(lines[i+1:])
                return fm, body
    return None, text


def update_frontmatter_date(text: str, new_date: str) -> str:
    """更新 frontmatter 中的 updated 字段"""
    fm, body = extract_frontmatter(text)
    if fm is None:
        return text
    
    # 替换 updated 行
    lines = fm.split('\n')
    updated_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('updated:'):
            updated_idx = i
            break
    
    if updated_idx is not None:
        lines[updated_idx] = f'updated: {new_date}'
        new_fm = '\n'.join(lines)
        return new_fm + '\n' + body
    return text
