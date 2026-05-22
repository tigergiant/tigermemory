# -*- coding: utf-8 -*-
"""
tests/test_tm_eval_runner.py — 针对 tm_eval_runner.py 的评估运行器和 CJK 对齐工具的单元测试。
"""

import os
import sys
import unittest
import json
import shutil
from pathlib import Path
from unittest.mock import patch

# 动态将 tools/ 放入 sys.path 以支持直接调用 pytest
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "tools"))

# 导入待测核心函数
import tm_eval_runner


class TestEvalRunner(unittest.TestCase):
    def test_visual_width(self):
        # ASCII characters length
        self.assertEqual(tm_eval_runner._visual_width("ASCII"), 5)
        # Chinese characters length (2 cell each)
        self.assertEqual(tm_eval_runner._visual_width("中文"), 4)
        # Hybrid
        self.assertEqual(tm_eval_runner._visual_width("中A"), 3)

    def test_pad_visual(self):
        # pad "中文" to 10 width, visual width should be exactly 10
        padded = tm_eval_runner._pad_visual("中文", 10)
        self.assertEqual(tm_eval_runner._visual_width(padded), 10)
        self.assertEqual(padded, "中文      ")

    @patch("tm_eval_runner.REPO_ROOT")
    def test_load_or_create_eval_suite_creates_eight_cases(self, mock_repo_root):
        # 创建临时文件夹来模拟 fresh install
        tmp_dir = Path("d:/tigermemory/.tmp/test_eval_runner_temp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        
        mock_repo_root.resolve.return_value = tmp_dir
        # 因为 Path 的 resolve() 会被调用
        mock_repo_root.__truediv__.side_effect = lambda x: tmp_dir / x

        try:
            # 运行 fresh create
            cases = tm_eval_runner.load_or_create_eval_suite()
            self.assertEqual(len(cases), 8)
            
            # 确认物理文件生成了
            suite_file = tmp_dir / "data" / "eval_suites.json"
            self.assertTrue(suite_file.exists())
            
            # 确认不覆盖已有文件
            with open(suite_file, "w", encoding="utf-8") as f:
                f.write('{"custom": true}')
            
            cases_modified = tm_eval_runner.load_or_create_eval_suite()
            self.assertIn("custom", cases_modified)
            
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)

    @patch("tm_core.search_wiki")
    def test_run_wiki_eval_lexical(self, mock_search_wiki):
        mock_search_wiki.return_value = [
            {"path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md"}
        ]
        case = {
            "query": "mojibake gbk",
            "expected_path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md"
        }
        rank, duration, degraded = tm_eval_runner.run_wiki_eval(case, mode="lexical")
        self.assertEqual(rank, 1)
        self.assertFalse(degraded)
        mock_search_wiki.assert_called_once_with("mojibake gbk", size=5, include_sources=True, include_inbox=False)

    @patch("tm_core.search_wiki_hybrid")
    def test_run_wiki_eval_hybrid(self, mock_search_wiki_hybrid):
        mock_search_wiki_hybrid.return_value = [
            {
                "path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md",
                "score_breakdown": {"degraded": False}
            }
        ]
        case = {
            "query": "mojibake gbk",
            "expected_path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md"
        }
        rank, duration, degraded = tm_eval_runner.run_wiki_eval(case, mode="hybrid")
        self.assertEqual(rank, 1)
        self.assertFalse(degraded)
        mock_search_wiki_hybrid.assert_called_once_with("mojibake gbk", size=5, include_sources=True, include_inbox=False, explain=True)

    @patch("tm_core.search_wiki_hybrid")
    def test_run_wiki_eval_hybrid_degraded(self, mock_search_wiki_hybrid):
        mock_search_wiki_hybrid.return_value = [
            {
                "path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md",
                "score_breakdown": {"degraded": True}
            }
        ]
        case = {
            "query": "mojibake gbk",
            "expected_path": "wiki/self-evolution/lessons/2026-04-23-powershell-gbk-mojibake.md"
        }
        rank, duration, degraded = tm_eval_runner.run_wiki_eval(case, mode="hybrid")
        self.assertEqual(rank, 1)
        self.assertTrue(degraded)
