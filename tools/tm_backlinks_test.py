#!/usr/bin/env python3
"""Phase C1: Unit tests for tm_backlinks.py
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

from tm_backlinks import (
    scan_wiki_references,
    compile_dashboard,
    _strip_code_blocks,
    _normalize_path,
    _resolve_wikilink,
)


class TestBacklinks:
    """Test suite for backlink scanner"""
    
    def setup_tmp_wiki(self, structure: dict[str, str]) -> str:
        """创建临时 wiki 目录结构"""
        tmpdir = tempfile.mkdtemp()
        wiki_root = Path(tmpdir) / "wiki"
        
        for path, content in structure.items():
            full_path = wiki_root / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding='utf-8')
        
        return str(tmpdir)
    
    def test_empty_wiki(self):
        """扫描空 wiki → 输出 0 页"""
        tmpdir = tempfile.mkdtemp()
        wiki_root = Path(tmpdir) / "wiki"
        wiki_root.mkdir()
        
        try:
            backlinks, all_pages = scan_wiki_references(str(tmpdir))
            assert len(all_pages) == 0, f"Expected 0 pages, got {len(all_pages)}"
            assert backlinks == {}, f"Expected empty backlinks, got {backlinks}"
            print("PASS: test_empty_wiki")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_single_link(self):
        """A 页引用 B 页 → B 反链含 A"""
        structure = {
            "systems/a.md": "# Page A\n\nLink to [B](../systems/b.md)",
            "systems/b.md": "# Page B\n\nContent here.",
        }
        tmpdir = self.setup_tmp_wiki(structure)
        
        try:
            backlinks, all_pages = scan_wiki_references(str(tmpdir))
            assert "wiki/systems/b.md" in backlinks
            assert "wiki/systems/a.md" in backlinks["wiki/systems/b.md"]
            print("PASS: test_single_link")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_ignore_code_block(self):
        """code block 里的链接不计入"""
        structure = {
            "systems/a.md": '# Page A\n\n```\nLink to [B](../systems/b.md)\n```\n',
            "systems/b.md": "# Page B\n\nContent.",
        }
        tmpdir = self.setup_tmp_wiki(structure)
        
        try:
            backlinks, _ = scan_wiki_references(str(tmpdir))
            # B 应该没有反链，因为链接在 code block 里
            assert backlinks.get("wiki/systems/b.md", []) == [], \
                f"Expected empty refs for B, got {backlinks.get('wiki/systems/b.md')}"
            print("PASS: test_ignore_code_block")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_ignore_external_url(self):
        """外部 URL 不计入"""
        structure = {
            "systems/a.md": "# Page A\n\n[External](https://example.com/page.md)",
        }
        tmpdir = self.setup_tmp_wiki(structure)
        
        try:
            # 没有 b.md，所以 a.md 的链接不会指向任何 wiki 页
            backlinks, all_pages = scan_wiki_references(str(tmpdir))
            # 外部 URL 不应该产生反链
            assert "https://example.com/page.md" not in backlinks
            print("PASS: test_ignore_external_url")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_obsidian_wikilink(self):
        """Obsidian wikilink [[tiger]] 解析为 tiger.md"""
        structure = {
            "person/tiger.md": "# Tiger\n\nProfile page.",
            "systems/brain.md": "# Brain\n\nSee [[tiger]] for details.",
        }
        tmpdir = self.setup_tmp_wiki(structure)
        
        try:
            backlinks, all_pages = scan_wiki_references(str(tmpdir))
            # tiger 应该被 brain 引用
            assert "wiki/person/tiger.md" in backlinks
            assert "wiki/systems/brain.md" in backlinks["wiki/person/tiger.md"]
            print("PASS: test_obsidian_wikilink")
        finally:
            shutil.rmtree(tmpdir)
    
    def test_stable_ordering(self):
        """两次扫描输出 byte-level 相同"""
        structure = {
            "systems/z.md": "# Z\n\nLink to [A](../systems/a.md)",
            "systems/a.md": "# A\n\nLink to [B](../systems/b.md)",
            "systems/b.md": "# B\n\nContent.",
        }
        tmpdir = self.setup_tmp_wiki(structure)
        
        try:
            backlinks1, all_pages1 = scan_wiki_references(str(tmpdir))
            dashboard1 = compile_dashboard(backlinks1, all_pages1)
            
            backlinks2, all_pages2 = scan_wiki_references(str(tmpdir))
            dashboard2 = compile_dashboard(backlinks2, all_pages2)
            
            # 忽略 updated 日期
            lines1 = [l for l in dashboard1.split('\n') if not l.startswith('updated:')]
            lines2 = [l for l in dashboard2.split('\n') if not l.startswith('updated:')]
            
            assert lines1 == lines2, f"Dashboards differ!\n1: {lines1[:5]}\n2: {lines2[:5]}"
            print("PASS: test_stable_ordering")
        finally:
            shutil.rmtree(tmpdir)


def run_all_tests():
    """运行所有测试并统计结果"""
    test_class = TestBacklinks()
    tests = [
        test_class.test_empty_wiki,
        test_class.test_single_link,
        test_class.test_ignore_code_block,
        test_class.test_ignore_external_url,
        test_class.test_obsidian_wikilink,
        test_class.test_stable_ordering,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"FAIL: {test.__name__}: {e}")
    
    print(f"\n{'='*40}")
    print(f"Total: {len(tests)} | Passed: {passed} | Failed: {failed}")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
