#!/usr/bin/env python3
"""tm_inject_aliases.py 单元测试
Inputs: Repository markdown/python files, frontmatter, section text, git diff inputs, or CLI path arguments.
Outputs: Deterministic reports, rewritten generated files, validation errors, or patch proposals.
Depends-on (must-have): Python stdlib plus tm_core/path parsers; no Mem0 write path unless explicitly invoked by caller.
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tm_inject_aliases import (
    extract_frontmatter,
    has_aliases_field,
    extract_h1,
    is_chinese_friendly,
    generate_alias,
    inject_aliases_to_frontmatter,
    process_page,
)


class TestInjectAliases:
    """测试 suite"""
    
    def test_h1_chinese_extracted(self):
        """# AI 云大脑 页 → alias = AI 云大脑"""
        text = '''---
owner: test
---

# AI 云大脑

内容
'''
        fm, body = extract_frontmatter(text)
        h1 = extract_h1(body)
        assert h1 == "AI 云大脑", f"Expected 'AI 云大脑', got {h1}"
        
        alias, reason = generate_alias(h1)
        assert alias == "AI 云大脑", f"Expected alias 'AI 云大脑', got {alias}"
        assert reason == "OK"
        print("PASS: test_h1_chinese_extracted")
    
    def test_h1_english_skipped_with_warning(self):
        """# Backlinks Dashboard → skip + stderr"""
        h1 = "Backlinks Dashboard"
        alias, reason = generate_alias(h1)
        
        assert alias is None, f"Expected None for English H1, got {alias}"
        assert "English-only" in reason, f"Expected English-only warning, got: {reason}"
        print("PASS: test_h1_english_skipped")
    
    def test_existing_aliases_preserved(self):
        """页已有 aliases: [X] → 跳过不改"""
        text = '''---
owner: test
aliases: [已有别名]
---

# 标题

内容
'''
        fm, body = extract_frontmatter(text)
        assert has_aliases_field(fm), "Should detect existing aliases"
        print("PASS: test_existing_aliases_preserved")
    
    def test_no_h1_skipped(self):
        """页无 H1 → skip + stderr"""
        text = '''---
owner: test
---

内容段落

- 列表项
'''
        fm, body = extract_frontmatter(text)
        h1 = extract_h1(body)
        
        assert h1 is None, f"Expected no H1, got {h1}"
        
        alias, reason = generate_alias(h1)
        assert alias is None
        assert "no H1" in reason
        print("PASS: test_no_h1_skipped")
    
    def test_inject_preserves_other_fields(self):
        """注入后 owner/status/updated 字段值不变"""
        fm_text = '''---
owner: test-owner
status: active
updated: 2026-01-01
---'''
        
        new_fm = inject_aliases_to_frontmatter(fm_text, "新别名")
        
        assert 'owner: test-owner' in new_fm, "owner preserved"
        assert 'status: active' in new_fm, "status preserved"
        assert 'updated: 2026-01-01' in new_fm, "updated preserved"
        assert 'aliases: ["新别名"]' in new_fm, "aliases injected"
        print("PASS: test_inject_preserves_other_fields")
    
    def test_dry_run_no_write(self):
        """dry-run 后文件字节不变"""
        tmpdir = tempfile.mkdtemp()
        try:
            page = Path(tmpdir) / "test.md"
            original = '''---
owner: test
---

# 中文标题

内容
'''
            page.write_text(original, encoding='utf-8')
            original_bytes = page.read_bytes()
            
            # dry-run (默认)
            process_page(page, dry_run=True, overwrite=False)
            
            after_bytes = page.read_bytes()
            assert original_bytes == after_bytes, "File should be unchanged in dry-run"
            print("PASS: test_dry_run_no_write")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_write_is_idempotent(self):
        """跑第二次 write 无变化"""
        tmpdir = tempfile.mkdtemp()
        try:
            page = Path(tmpdir) / "test.md"
            original = '''---
owner: test
---

# 中文标题

内容
'''
            page.write_text(original, encoding='utf-8')
            
            # 第一次 write
            process_page(page, dry_run=False, overwrite=False)
            first_write = page.read_text(encoding='utf-8')
            
            # 第二次 write（应该跳过，因为已有 aliases）
            status, detail = process_page(page, dry_run=False, overwrite=False)
            second_write = page.read_text(encoding='utf-8')
            
            assert first_write == second_write, "Second write should not change file"
            assert status == "SKIP", f"Expected SKIP on second run, got {status}"
            print("PASS: test_write_is_idempotent")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_chinese_detection_unicode(self):
        """中文检测 Unicode 范围正确"""
        assert is_chinese_friendly("AI 云大脑") == True
        assert is_chinese_friendly("中文标题") == True
        assert is_chinese_friendly("Backlinks Dashboard") == False
        assert is_chinese_friendly("英文") == True  # 2/4 = 50%
        print("PASS: test_chinese_detection_unicode")
    
    def test_h1_emoji_removed(self):
        """H1 中的 emoji 被去除"""
        text = '''---
---

# 🚀 火箭标题

内容
'''
        fm, body = extract_frontmatter(text)
        h1 = extract_h1(body)
        assert h1 == "火箭标题", f"Expected '火箭标题' (no emoji), got {h1}"
        print("PASS: test_h1_emoji_removed")
    
    def test_h1_date_suffix_removed(self):
        """H1 尾部日期括号被去除"""
        text = '''---
---

# 标题 (2026-04)

内容
'''
        fm, body = extract_frontmatter(text)
        h1 = extract_h1(body)
        assert h1 == "标题", f"Expected '标题' (no date suffix), got {h1}"
        print("PASS: test_h1_date_suffix_removed")


def run_all_tests():
    """运行所有测试"""
    test_class = TestInjectAliases()
    tests = [
        test_class.test_h1_chinese_extracted,
        test_class.test_h1_english_skipped_with_warning,
        test_class.test_existing_aliases_preserved,
        test_class.test_no_h1_skipped,
        test_class.test_inject_preserves_other_fields,
        test_class.test_dry_run_no_write,
        test_class.test_write_is_idempotent,
        test_class.test_chinese_detection_unicode,
        test_class.test_h1_emoji_removed,
        test_class.test_h1_date_suffix_removed,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f'FAIL: {test.__name__}: {e}')
    
    print(f'\n{"="*40}')
    print(f'Total: {len(tests)} | Passed: {passed} | Failed: {failed}')
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
