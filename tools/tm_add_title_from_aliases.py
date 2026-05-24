#!/usr/bin/env python3
"""
从 aliases 生成 title，供 Notebook Navigator 的 Name fields 识别。
已有 aliases 但无 title 的页面，在 frontmatter 追加 title: 第一个 alias。
Inputs: CLI arguments, local repository files, or data supplied by the caller.
Outputs: A deterministic stdout report, file rewrite, or helper return value documented by the command.
Depends-on (must-have): Python stdlib and local tigermemory helper modules; external services only when explicitly requested.
"""
from __future__ import annotations

import re
import argparse
from pathlib import Path

PARTITIONS = ("brand", "investment", "operations", "production", "systems", "person", "self-evolution")


def extract_frontmatter(text: str) -> tuple[str | None, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[: i + 1]), "\n".join(lines[i + 1 :])
    return None, text


def has_title_field(fm_text: str) -> bool:
    return bool(re.search(r"^title:", fm_text, re.MULTILINE))


def extract_first_alias(fm_text: str) -> str | None:
    # 匹配 aliases: ["...", ...] 格式
    m = re.search(r'^aliases:\s*\[\s*"([^"]+)"', fm_text, re.MULTILINE)
    if m:
        return m.group(1)
    # 匹配 aliases:
    #   - ... 格式
    m = re.search(r'^aliases:\s*$\n(?:\s+-\s+.+\n)*?\s+-\s+(.+)', fm_text, re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"')
    return None


def add_title_to_frontmatter(fm_text: str, title: str) -> str:
    """在 frontmatter 结尾 --- 之前插入 title 字段"""
    lines = fm_text.split("\n")
    # 找到最后一个 ---
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "---":
            insert_idx = i
            # 如果上一行空行，就插在那行位置
            if i > 0 and lines[i - 1].strip() == "":
                insert_idx = i - 1
            lines.insert(insert_idx, f'title: "{title}"')
            return "\n".join(lines)
    # 保底
    return fm_text.rstrip() + f'\ntitle: "{title}"\n'


def process_page(page_path: Path, dry_run: bool = True) -> tuple[str, str]:
    try:
        text = page_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = page_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = page_path.read_text(encoding="utf-8", errors="replace")
    fm, body = extract_frontmatter(text)
    if fm is None:
        return "SKIP", "no valid frontmatter"
    if has_title_field(fm):
        return "SKIP", "title already exists"
    alias = extract_first_alias(fm)
    if not alias:
        return "SKIP", "no aliases found"
    new_fm = add_title_to_frontmatter(fm, alias)
    new_text = new_fm + "\n" + body
    if dry_run:
        return "DRY", f'would add title: "{alias}"'
    page_path.write_text(new_text, encoding="utf-8")
    return "OK", f'added title: "{alias}"'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="实际写入文件（默认只检查）")
    args = parser.parse_args()

    dry_run = not args.write
    wiki_dir = Path(__file__).resolve().parent.parent / "wiki"

    total = {"OK": 0, "SKIP": 0, "DRY": 0}
    for part in PARTITIONS:
        part_dir = wiki_dir / part
        if not part_dir.exists():
            continue
        for md_file in sorted(part_dir.glob("*.md")):
            status, detail = process_page(md_file, dry_run=dry_run)
            total[status] = total.get(status, 0) + 1
            if status != "SKIP":
                print(f"{part}/{md_file.name}: {status} – {detail}")

    print(f"\n总计: {total}")
    if dry_run:
        print("提示: 加上 --write 参数执行实际写入")


if __name__ == "__main__":
    main()
