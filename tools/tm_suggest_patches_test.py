#!/usr/bin/env python3
"""Unit tests for Phase B1: _strip_minimax_artifacts, suggest_wiki_patches,
save_wiki_patches_to_inbox.

LLM calls are mocked by monkey-patching tm_core._call_minimax_json; no network
access is required to run these tests.

Run: python3 tools/tm_suggest_patches_test.py
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tm_core  # noqa: E402


CATALOG = [
    {"page": "wiki/systems/alpha.md", "summary": "alpha summary"},
    {"page": "wiki/systems/beta.md", "summary": "beta summary"},
]
SUMMARY = "这是一段超过三十个字的对话摘要，涉及 alpha 和 beta 两个主题，用于测试 suggest_wiki_patches 的基本路径。"


def _with_mocked_llm(return_value):
    """Context manager-ish helper: swap _call_minimax_json, restore after."""
    orig = tm_core._call_minimax_json

    def fake(*_args, **_kwargs):
        return return_value

    tm_core._call_minimax_json = fake
    return orig


class StripArtifactsTest(unittest.TestCase):
    def test_plain_passthrough(self):
        self.assertEqual(
            tm_core._strip_minimax_artifacts('{"a":1}'),
            '{"a":1}',
        )

    def test_strip_think_only(self):
        raw = "<think>reasoning</think>\n{\"a\":1}"
        self.assertEqual(tm_core._strip_minimax_artifacts(raw), '{"a":1}')

    def test_strip_fence_only(self):
        raw = "```json\n{\"a\":1}\n```"
        self.assertEqual(tm_core._strip_minimax_artifacts(raw), '{"a":1}')

    def test_strip_think_and_fence(self):
        raw = "<think>why</think>\n```json\n{\"a\":1}\n```"
        self.assertEqual(tm_core._strip_minimax_artifacts(raw), '{"a":1}')

    def test_strip_multiline_think(self):
        raw = "<think>line1\nline2\nline3</think>\n{\"ok\":true}"
        self.assertEqual(tm_core._strip_minimax_artifacts(raw), '{"ok":true}')

    def test_fence_without_lang(self):
        raw = "```\n{\"a\":1}\n```"
        self.assertEqual(tm_core._strip_minimax_artifacts(raw), '{"a":1}')


class SuggestWikiPatchesTest(unittest.TestCase):
    def setUp(self):
        self._orig_llm = tm_core._call_minimax_json

    def tearDown(self):
        tm_core._call_minimax_json = self._orig_llm

    def _mock(self, ok, result):
        tm_core._call_minimax_json = lambda *a, **k: (ok, result)

    def test_short_summary_returns_empty(self):
        self.assertEqual(tm_core.suggest_wiki_patches("short", CATALOG), [])

    def test_empty_catalog_returns_empty(self):
        self.assertEqual(tm_core.suggest_wiki_patches(SUMMARY, []), [])

    def test_llm_failure_returns_empty(self):
        self._mock(False, "simulated network error")
        self.assertEqual(tm_core.suggest_wiki_patches(SUMMARY, CATALOG), [])

    def test_valid_patches_pass(self):
        self._mock(True, {"patches": [
            {
                "page": "wiki/systems/alpha.md",
                "type": "append",
                "section": "",
                "content": "new fact about alpha",
                "rationale": "conversation established alpha fact",
            },
        ]})
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["page"], "wiki/systems/alpha.md")

    def test_hallucinated_page_dropped(self):
        self._mock(True, {"patches": [
            {
                "page": "wiki/systems/ghost.md",  # not in catalog
                "type": "append",
                "content": "...",
            },
            {
                "page": "wiki/systems/beta.md",
                "type": "append",
                "content": "real beta content",
            },
        ]})
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["page"], "wiki/systems/beta.md")

    def test_invalid_type_dropped(self):
        self._mock(True, {"patches": [
            {"page": "wiki/systems/alpha.md", "type": "delete", "content": "..."},
        ]})
        self.assertEqual(tm_core.suggest_wiki_patches(SUMMARY, CATALOG), [])

    def test_update_section_requires_section_field(self):
        self._mock(True, {"patches": [
            {"page": "wiki/systems/alpha.md", "type": "update_section", "content": "..."},  # no section
            {"page": "wiki/systems/alpha.md", "type": "update_section", "section": "摘要", "content": "ok"},
        ]})
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["section"], "摘要")

    def test_empty_content_dropped(self):
        self._mock(True, {"patches": [
            {"page": "wiki/systems/alpha.md", "type": "append", "content": ""},
            {"page": "wiki/systems/alpha.md", "type": "append", "content": "   "},
        ]})
        self.assertEqual(tm_core.suggest_wiki_patches(SUMMARY, CATALOG), [])

    def test_max_patches_cap(self):
        self._mock(True, {"patches": [
            {"page": "wiki/systems/alpha.md", "type": "append", "content": f"p{i}"}
            for i in range(10)
        ]})
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG, max_patches=3)
        self.assertEqual(len(out), 3)

    def test_non_dict_patches_rejected(self):
        self._mock(True, {"patches": ["string", 123, None,
            {"page": "wiki/systems/alpha.md", "type": "append", "content": "ok"},
        ]})
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG)
        self.assertEqual(len(out), 1)


class LLMFallbackTest(unittest.TestCase):
    """Verify MiniMax -> DeepSeek fallback logic for `auto` mode."""

    def setUp(self):
        self._orig_mm = tm_core._call_minimax_json
        self._orig_ds = tm_core._call_deepseek_json

    def tearDown(self):
        tm_core._call_minimax_json = self._orig_mm
        tm_core._call_deepseek_json = self._orig_ds

    def _ok_patch(self, src):
        return {"patches": [
            {"page": "wiki/systems/alpha.md", "type": "append", "content": f"from {src}"}
        ]}

    def test_auto_falls_back_on_minimax_529(self):
        tm_core._call_minimax_json = lambda *a, **k: (False, "MiniMax HTTP 529")
        tm_core._call_deepseek_json = lambda *a, **k: (True, self._ok_patch("deepseek"))
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG)
        self.assertEqual(len(out), 1)
        self.assertIn("from deepseek", out[0]["content"])

    def test_auto_does_not_fallback_on_auth_error(self):
        tm_core._call_minimax_json = lambda *a, **k: (False, "MiniMax HTTP 401")
        ds_called = []
        def ds_fake(*a, **k):
            ds_called.append(1)
            return (True, self._ok_patch("deepseek"))
        tm_core._call_deepseek_json = ds_fake
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG)
        self.assertEqual(out, [])
        self.assertEqual(ds_called, [], "DeepSeek must not be called on 401 from MiniMax")

    def test_explicit_minimax_skips_fallback(self):
        tm_core._call_minimax_json = lambda *a, **k: (False, "MiniMax HTTP 529")
        ds_called = []
        def ds_fake(*a, **k):
            ds_called.append(1)
            return (True, self._ok_patch("deepseek"))
        tm_core._call_deepseek_json = ds_fake
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG, llm="minimax")
        self.assertEqual(out, [])
        self.assertEqual(ds_called, [], "llm='minimax' must not fall back")

    def test_explicit_deepseek_skips_minimax(self):
        mm_called = []
        def mm_fake(*a, **k):
            mm_called.append(1)
            return (True, self._ok_patch("minimax"))
        tm_core._call_minimax_json = mm_fake
        tm_core._call_deepseek_json = lambda *a, **k: (True, self._ok_patch("deepseek"))
        out = tm_core.suggest_wiki_patches(SUMMARY, CATALOG, llm="deepseek")
        self.assertEqual(len(out), 1)
        self.assertIn("from deepseek", out[0]["content"])
        self.assertEqual(mm_called, [], "llm='deepseek' must skip MiniMax")


class SaveWikiPatchesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="tm_b1_"))
        (self.tmp / "inbox").mkdir()
        self.orig_root = tm_core.REPO_ROOT
        tm_core.REPO_ROOT = self.tmp

    def tearDown(self):
        tm_core.REPO_ROOT = self.orig_root
        shutil.rmtree(self.tmp)

    def test_save_writes_valid_file(self):
        patches = [{
            "page": "wiki/systems/alpha.md",
            "type": "append",
            "section": "",
            "content": "new fact",
            "rationale": "test",
        }]
        rel = tm_core.save_wiki_patches_to_inbox(patches, "claude-code",
                                                  summary_excerpt="test summary")
        self.assertTrue(rel.startswith("inbox/"))
        self.assertTrue(rel.endswith("-claude-code-cross.md"))
        body = (self.tmp / rel).read_text(encoding="utf-8")
        self.assertIn("type: wiki-patches", body)
        self.assertIn("wiki/systems/alpha.md", body)
        self.assertIn("new fact", body)
        self.assertIn("test summary", body)

    def test_save_rejects_empty_patches(self):
        with self.assertRaises(ValueError):
            tm_core.save_wiki_patches_to_inbox([], "claude-code")

    def test_save_rejects_bad_agent(self):
        with self.assertRaises(ValueError):
            tm_core.save_wiki_patches_to_inbox(
                [{"page": "x", "type": "append", "content": "x"}], "ghost-agent"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
