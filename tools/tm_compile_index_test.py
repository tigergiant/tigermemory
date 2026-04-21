#!/usr/bin/env python3
"""Unit tests for tm_compile_index.

Synthetic wiki tree is built under a tempdir; WIKI_ROOT and PARTITIONS are
monkey-patched so tests don't depend on the real repo state.

Run: python3 tools/tm_compile_index_test.py
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tm_compile_index as m  # noqa: E402


PAGE_WITH_SUMMARY = """---
owner: claude-code
status: active
updated: 2026-04-21
---

# Alpha Page

## 摘要

Alpha 页的第一段摘要文字，说明它解决什么问题。

## 已验证现状

...
"""

PAGE_NO_SUMMARY = """---
owner: claude-code
status: active
updated: 2026-04-21
---

# Beta Page

Beta 页没有摘要标题，只有 h1 下的直接段落。
"""


class CompileIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="tm_ci_"))
        self.orig_wiki = m.WIKI_ROOT
        m.WIKI_ROOT = self.tmp
        (self.tmp / "test").mkdir()
        self.orig_parts = m.PARTITIONS
        m.PARTITIONS = ["test"]

    def tearDown(self):
        m.WIKI_ROOT = self.orig_wiki
        m.PARTITIONS = self.orig_parts
        shutil.rmtree(self.tmp)

    def _write(self, name: str, content: str):
        (self.tmp / "test" / name).write_text(content, encoding="utf-8")

    def test_empty_partition_writes_placeholder(self):
        new, _ = m.compile_partition_index("test")
        self.assertIn("（暂无页面）", new)

    def test_new_page_appended_with_summary(self):
        self._write("alpha.md", PAGE_WITH_SUMMARY)
        new, _ = m.compile_partition_index("test")
        self.assertIn("[Alpha Page](alpha.md)", new)
        self.assertIn("Alpha 页的第一段摘要文字", new)
        self.assertIn(" — ", new)

    def test_page_without_summary_section_falls_back(self):
        self._write("beta.md", PAGE_NO_SUMMARY)
        new, _ = m.compile_partition_index("test")
        self.assertIn("[Beta Page](beta.md)", new)
        self.assertIn("Beta 页没有摘要标题", new)

    def test_existing_bullet_preserved_byte_for_byte(self):
        self._write("alpha.md", PAGE_WITH_SUMMARY)
        (self.tmp / "test" / "index.md").write_text(
            "# Test\n\n## 页面\n\n- [Alpha Page](alpha.md) — 手工精简摘要\n",
            encoding="utf-8",
        )
        new, old = m.compile_partition_index("test")
        self.assertIn("- [Alpha Page](alpha.md) — 手工精简摘要", new)
        self.assertNotIn("Alpha 页的第一段摘要文字", new)
        self.assertEqual(new, old)

    def test_removed_page_dropped_from_index(self):
        (self.tmp / "test" / "index.md").write_text(
            "# Test\n\n## 页面\n\n- [Gone](gone.md) — old\n- [Here](here.md) — here\n",
            encoding="utf-8",
        )
        self._write("here.md", PAGE_WITH_SUMMARY.replace("Alpha Page", "Here"))
        new, _ = m.compile_partition_index("test")
        self.assertNotIn("gone.md", new)
        self.assertIn("here.md", new)

    def test_order_preserved_new_page_appended(self):
        (self.tmp / "test" / "index.md").write_text(
            "# Test\n\n## 页面\n\n- [Alpha](alpha.md) — a\n- [Charlie](charlie.md) — c\n",
            encoding="utf-8",
        )
        self._write("alpha.md", PAGE_WITH_SUMMARY)
        self._write("charlie.md", PAGE_WITH_SUMMARY.replace("Alpha Page", "Charlie"))
        self._write("bravo.md", PAGE_WITH_SUMMARY.replace("Alpha Page", "Bravo"))
        new, _ = m.compile_partition_index("test")
        alpha = new.index("alpha.md")
        charlie = new.index("charlie.md")
        bravo = new.index("bravo.md")
        self.assertLess(alpha, charlie)
        self.assertLess(charlie, bravo)

    def test_stable_second_compile_is_noop(self):
        self._write("alpha.md", PAGE_WITH_SUMMARY)
        new, _ = m.compile_partition_index("test")
        (self.tmp / "test" / "index.md").write_text(new, encoding="utf-8")
        new2, old2 = m.compile_partition_index("test")
        self.assertEqual(new2, old2)

    def test_extract_summary_truncation(self):
        long = "一" * 200
        page = f"# Page\n\n## 摘要\n\n{long}\n"
        s = m.extract_page_summary(page)
        self.assertLessEqual(len(s), m.MAX_SUMMARY_LEN + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
