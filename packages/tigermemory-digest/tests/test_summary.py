from __future__ import annotations

import json

from tigermemory_digest import summary


def test_tokenize_keeps_cjk_runs_and_english_words():
    assert summary._tokenize("OpenClaw 鉴权 sandboxing") == ["鉴权", "OpenClaw", "sandboxing"]


def test_propose_aliases_dedupes_existing_case_insensitively():
    aliases = summary._propose_aliases("OpenClaw 鉴权", ["MCP"], None, ["openclaw"])

    assert aliases == ["鉴权", "MCP"]


def test_has_summary_detects_frontmatter_summary_field():
    text = "---\nsummary: 已有摘要\n---\n# Title\nbody"

    assert summary._has_summary(text) is True


def test_propose_summary_returns_none_when_summary_already_exists():
    text = "# Title\n\n## 摘要\n\n已有摘要\n"

    assert summary._propose_summary(text) is None


def test_render_report_marks_missing_expected_path(tmp_path):
    cases = [{"id": "case-1", "query": "missing", "scope": "wiki", "must_contain": [], "expected_paths": ["wiki/systems/missing.md"]}]

    report = summary.render_report(cases, ["case-1"], tmp_path)

    assert "MISSING" in report
    assert "wiki/systems/missing.md" in report
