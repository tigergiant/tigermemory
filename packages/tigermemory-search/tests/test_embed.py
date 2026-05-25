from __future__ import annotations

import math

from tigermemory_search import embed


def test_embed_text_composes_title_alias_slug_and_body_for_short_input():
    text = embed._embed_text(
        "wiki/systems/memory-answer.md",
        "Memory Answer",
        ["证据问答", "answer roadmap"],
        "正文内容",
    )

    assert "Memory Answer" in text
    assert "证据问答; answer roadmap" in text
    assert "wiki systems memory answer md" in text
    assert "正文内容" in text


def test_load_index_handles_missing_file_path(tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "INDEX_DIR", tmp_path)

    assert embed._load_index("wiki") == {}


def test_content_hash_is_stable_for_same_text():
    first = embed._content_hash("wiki/a.md", "A", ["alias"], "body")
    second = embed._content_hash("wiki/a.md", "A", ["alias"], "body")

    assert first == second
    assert len(first) == 32


def test_search_returns_top_k_records_sorted_by_score(tmp_path, monkeypatch):
    monkeypatch.setattr(embed, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(embed.tm_core, "embed_one", lambda _query: [1.0, 0.0])
    entries = {
        "best.md": {
            "path": "best.md",
            "title": "Best",
            "hash": "1",
            "mtime": 0,
            "vec": [1.0, 0.0],
        },
        "second.md": {
            "path": "second.md",
            "title": "Second",
            "hash": "2",
            "mtime": 0,
            "vec": [0.5, math.sqrt(0.75)],
        },
        "third.md": {
            "path": "third.md",
            "title": "Third",
            "hash": "3",
            "mtime": 0,
            "vec": [0.0, 1.0],
        },
    }
    embed._save_index("wiki", entries)
    embed._save_meta("wiki", {"scope": "wiki", "embedding_dimensions": 2})

    hits = embed.search("query", scope="wiki", k=2)

    assert [hit["path"] for hit in hits] == ["best.md", "second.md"]
    assert hits[0]["score"] > hits[1]["score"]


def test_extract_summary_extracts_first_signal_paragraph_after_summary_heading():
    body = """---
title: Example
---
# Example

## 摘要

这是用于测试的摘要段落，长度超过二十个字符，应该被优先提取。

## 细节

后续正文不应该进入摘要。
"""

    summary = embed._extract_summary(body)

    assert summary.startswith("这是用于测试的摘要段落")
    assert "后续正文" not in summary
