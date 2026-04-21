#!/usr/bin/env python3
"""Phase C2: Unit tests for tm_apply_patch.py"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tm_md_sections import parse_sections, replace_section, append_to_section, update_frontmatter_date


class TestApplyPatch:
    """Test suite for patch apply tool"""
    
    def test_parse_patches_minimal(self):
        """读取单个 update_section patch"""
        from tm_apply_patch import read_patches
        
        inbox_content = '''---
owner: tigermemory-ce
type: wiki-patches
---

# Wiki Patch Suggestions

### 1. `wiki/systems/test.md` — update_section
- **section**: 摘要
- **rationale**: 添加新信息

```markdown
新摘要内容
```
'''
        # 写临时文件
        tmpdir = tempfile.mkdtemp()
        try:
            inbox_path = Path(tmpdir) / 'test.md'
            inbox_path.write_text(inbox_content, encoding='utf-8')
            patches = read_patches(str(inbox_path))
            
            assert len(patches) == 1, f'Expected 1 patch, got {len(patches)}'
            assert patches[0]['page'] == 'wiki/systems/test.md'
            assert patches[0]['type'] == 'update_section'
            assert patches[0]['section'] == '摘要'
            assert '新摘要内容' in patches[0]['content']
            print('PASS: test_parse_patches_minimal')
        finally:
            shutil.rmtree(tmpdir)
    
    def test_parse_patches_multiple(self):
        """读取多个 patch"""
        from tm_apply_patch import read_patches
        
        inbox_content = '''---
type: wiki-patches
---

### 1. `wiki/a.md` — update_section
- **section**: S1

```markdown
Content A
```

### 2. `wiki/b.md` — append_section
- **section**: S2

```markdown
Content B
```
'''
        tmpdir = tempfile.mkdtemp()
        try:
            inbox_path = Path(tmpdir) / 'test.md'
            inbox_path.write_text(inbox_content, encoding='utf-8')
            patches = read_patches(str(inbox_path))
            
            assert len(patches) == 2, f'Expected 2 patches, got {len(patches)}'
            assert patches[0]['page'] == 'wiki/a.md'
            assert patches[1]['page'] == 'wiki/b.md'
            print('PASS: test_parse_patches_multiple')
        finally:
            shutil.rmtree(tmpdir)
    
    def test_replace_section(self):
        """section 替换正确"""
        text = '''---
owner: test
updated: 2026-01-01
---

# Page Title

## 摘要

旧摘要内容

## 规划

规划内容
'''
        new_text = replace_section(text, '摘要', '新摘要内容')
        
        assert '新摘要内容' in new_text
        assert '旧摘要内容' not in new_text
        assert '规划内容' in new_text  # 其他 section 保留
        print('PASS: test_replace_section')
    
    def test_append_section(self):
        """section 追加正确"""
        text = '''---
owner: test
---

## 摘要

原有内容
'''
        new_text = append_to_section(text, '摘要', '追加内容')
        
        assert '原有内容' in new_text
        assert '追加内容' in new_text
        # 追加内容应在原有内容之后
        idx_orig = new_text.find('原有内容')
        idx_add = new_text.find('追加内容')
        assert idx_add > idx_orig
        print('PASS: test_append_section')
    
    def test_unknown_section(self):
        """指定 section 不存在 → 报错"""
        text = '''---
owner: test
---

## 摘要

内容
'''
        try:
            replace_section(text, '不存在的节', '新内容')
            assert False, 'Should have raised ValueError'
        except ValueError as e:
            assert '不存在的节' in str(e)
            print('PASS: test_unknown_section')
    
    def test_unsupported_type(self):
        """add_page 不被支持"""
        from tm_apply_patch import apply_patch
        
        patch = {
            'page': 'wiki/systems/new.md',
            'type': 'add_page',
            'section': '',
            'content': '内容'
        }
        
        tmpdir = tempfile.mkdtemp()
        try:
            success, msg = apply_patch(patch, tmpdir, dry_run=True)
            assert not success, 'add_page should fail'
            assert 'Unsupported' in msg or 'does not exist' in msg
            print('PASS: test_unsupported_type')
        finally:
            shutil.rmtree(tmpdir)
    
    def test_code_block_not_section(self):
        """code block 里的 ## xxx 不被当 section"""
        text = '''---
owner: test
---

## 真实节

内容

```python
## 这不是节
代码
```

## 另一个节

更多内容
'''
        sections = parse_sections(text)
        section_titles = [s.title for s in sections]
        
        assert '真实节' in section_titles
        assert '另一个节' in section_titles
        assert '这不是节' not in section_titles
        print('PASS: test_code_block_not_section')
    
    def test_frontmatter_preserved(self):
        """apply 后 frontmatter 完整保留，updated 刷新为今日"""
        text = '''---
owner: test
status: active
updated: 2026-01-01
---

## 摘要

内容
'''
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        new_text = update_frontmatter_date(text, today)
        
        # frontmatter 应保留
        assert 'owner: test' in new_text
        assert 'status: active' in new_text
        # updated 应刷新
        assert f'updated: {today}' in new_text
        assert 'updated: 2026-01-01' not in new_text
        # body 应保留
        assert '## 摘要' in new_text
        print('PASS: test_frontmatter_preserved')


def run_all_tests():
    """运行所有测试"""
    test_class = TestApplyPatch()
    tests = [
        test_class.test_parse_patches_minimal,
        test_class.test_parse_patches_multiple,
        test_class.test_replace_section,
        test_class.test_append_section,
        test_class.test_unknown_section,
        test_class.test_unsupported_type,
        test_class.test_code_block_not_section,
        test_class.test_frontmatter_preserved,
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
