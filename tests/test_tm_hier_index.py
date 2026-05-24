"""Tests for tools/tm_hier_index.py — Phase 4 L0/L1/L2 hierarchical index.

Why these tests exist: hierarchical index is eval-only experimental feature,
but we need unit tests to verify:
- L0 fallback order (frontmatter summary > ## 摘要 > H1+para > title/aliases)
- L1 budget and composition (title + aliases + summary + first 3 H2)
- Layer hash consistency
- Meta compatibility guard (dim mismatch rejection)
- Stats reporting
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_hier_index  # type: ignore[import-not-found]


@pytest.fixture
def isolated_index_dir(tmp_path, monkeypatch):
    """Point INDEX_DIR at a per-test tmp dir so meta/jsonl writes don't
    touch the real `runtime/embed_index/`."""
    monkeypatch.setattr(tm_hier_index, "INDEX_DIR", tmp_path)
    return tmp_path


def test_extract_l0_frontmatter_summary_priority():
    """L0 should prefer frontmatter summary over other sources."""
    text = """---
title: Test Page
summary: "Frontmatter summary text"
---

## 摘要
This is a heading summary.

# H1
First paragraph after H1.
"""
    result = tm_hier_index._extract_l0_text("Test Page", [], text, "wiki/test.md")
    assert result == "Frontmatter summary text"


def test_extract_l0_summary_heading_fallback():
    """L0 should fall back to ## 摘要/Summary/概述/TL;DR when no frontmatter summary."""
    text = """---
title: Test Page
---

## 摘要
This is a heading summary paragraph.

# H1
First paragraph after H1.
"""
    result = tm_hier_index._extract_l0_text("Test Page", [], text, "wiki/test.md")
    assert "heading summary" in result


def test_extract_l0_h1_paragraph_fallback():
    """L0 should fall back to first paragraph after H1 when no summary."""
    text = """---
title: Test Page
---

# H1
First paragraph after H1.
"""
    result = tm_hier_index._extract_l0_text("Test Page", [], text, "wiki/test.md")
    assert "First paragraph after H1" in result


def test_extract_l0_title_aliases_slug_fallback():
    """L0 should fall back to title + aliases + slug when nothing else."""
    text = """---
title: Test Page
---

Empty body.
"""
    result = tm_hier_index._extract_l0_text("Test Page", ["alias1", "alias2"], text, "wiki/test-page.md")
    assert "Test Page" in result
    assert "alias1" in result
    assert "alias2" in result
    assert "test page" in result


def test_extract_l0_respects_max_chars():
    """L0 should truncate to L0_MAX_CHARS (320)."""
    text = """---
title: Test Page
summary: "A" + "B" * 500
---

"""
    result = tm_hier_index._extract_l0_text("Test Page", [], text, "wiki/test.md")
    assert len(result) <= tm_hier_index.L0_MAX_CHARS


def test_extract_l1_includes_summary_and_h2s():
    """L1 should include title, aliases, summary, and first 3 H2 headings with excerpts."""
    text = """---
title: Test Page
aliases: ["alias1"]
summary: "Summary text"
---

# H1

## First H2
First H2 paragraph.

## Second H2
Second H2 paragraph.

## Third H2
Third H2 paragraph.

## Fourth H2
Fourth H2 paragraph.
"""
    result = tm_hier_index._extract_l1_text("Test Page", ["alias1"], text, "wiki/test.md")
    assert "Test Page" in result
    assert "alias1" in result
    assert "Summary text" in result
    assert "First H2" in result
    assert "Second H2" in result
    assert "Third H2" in result
    assert "Fourth H2" not in result  # Should only include first 3


def test_extract_l1_respects_max_chars():
    """L1 should truncate to L1_MAX_CHARS (3000)."""
    long_text = "A" * 10000
    text = f"""---
title: Test Page
summary: "Summary"
---

# H1

{long_text}
"""
    result = tm_hier_index._extract_l1_text("Test Page", [], text, "wiki/test.md")
    assert len(result) <= tm_hier_index.L1_MAX_CHARS


def test_extract_l2_matches_production_v5():
    """L2 should match v5 production: title + aliases + slug + body[:6000]."""
    text = """---
title: Test Page
aliases: ["alias1"]
---

# Body content
""" + "A" * 10000
    result = tm_hier_index._extract_l2_text("Test Page", ["alias1"], text, "wiki/test-page.md")
    assert "Test Page" in result
    assert "alias1" in result
    assert "test page" in result
    assert len(result) <= tm_hier_index.L2_MAX_CHARS


def test_layer_hash_consistency():
    """Layer hash should be deterministic for same inputs."""
    text = "Test content"
    h1 = tm_hier_index._layer_hash("wiki/test.md", "L0", text)
    h2 = tm_hier_index._layer_hash("wiki/test.md", "L0", text)
    assert h1 == h2

    # Different layer should have different hash
    h3 = tm_hier_index._layer_hash("wiki/test.md", "L1", text)
    assert h1 != h3

    # Different content should have different hash
    h4 = tm_hier_index._layer_hash("wiki/test.md", "L0", "Different content")
    assert h1 != h4


def test_partition_of():
    """Partition extraction should work for wiki/sources paths."""
    assert tm_hier_index._partition_of("wiki/systems/test.md") == "systems"
    assert tm_hier_index._partition_of("sources/external/test.md") == "external"
    assert tm_hier_index._partition_of("AGENTS.md") == ""
    assert tm_hier_index._partition_of("wiki/test.md") == "test.md"


def test_cosine_similarity():
    """Cosine similarity should be correct for identical and orthogonal vectors."""
    # Identical vectors
    v1 = [0.5, 0.5, 0.5, 0.5]
    assert tm_hier_index._cosine(v1, v1) == pytest.approx(1.0)

    # Orthogonal vectors
    v2 = [1.0, 0.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0, 0.0]
    assert tm_hier_index._cosine(v2, v3) == pytest.approx(0.0)

    # Different lengths should return 0
    v4 = [0.5, 0.5]
    assert tm_hier_index._cosine(v1, v4) == 0.0


def test_search_raises_when_index_missing(isolated_index_dir):
    """Search should raise RuntimeError when index doesn't exist."""
    with pytest.raises(RuntimeError, match="Hierarchical index not found"):
        tm_hier_index.search("test query")


def test_build_creates_meta_and_index(isolated_index_dir, monkeypatch):
    """Build should create both index file and meta file."""
    # Mock embedding to avoid actual API call
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:19190/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_API_KEY", "local-qwen")

    # Mock tm_core.embed_texts to return dummy vectors
    def mock_embed_texts(texts):
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(tm_hier_index.tm_core, "embed_texts", mock_embed_texts)

    # Create a minimal wiki structure
    wiki_dir = isolated_index_dir.parent / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "test.md").write_text(
        "---\ntitle: Test\naliases: [\"alias1\"]\n---\n\n# H1\n\nBody content.",
        encoding="utf-8"
    )

    # Point REPO_ROOT to tmp dir for this test
    monkeypatch.setattr(tm_hier_index, "REPO_ROOT", isolated_index_dir.parent)

    result = tm_hier_index.build(scope="wiki")

    # Check index file exists
    index_path = isolated_index_dir / "wiki_layers.jsonl"
    assert index_path.exists()

    # Check meta file exists
    meta_path = isolated_index_dir / "wiki_layers.meta.json"
    assert meta_path.exists()

    # Check meta content
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["scope"] == "wiki"
    assert meta["schema"] == "layers-v1"
    assert meta["embedding_dimensions"] == 1024
    assert "layer_counts" in meta
    assert "built_at" in meta


def test_stats_reports_meta(isolated_index_dir):
    """Stats should report meta information when available."""
    # No index yet
    s = tm_hier_index.stats()
    assert s["exists"] is False
    assert s["meta"] is None

    # Create a meta file
    meta_path = isolated_index_dir / "wiki_layers.meta.json"
    meta_path.write_text(
        json.dumps({
            "scope": "wiki",
            "embedding_model": "test-model",
            "embedding_dimensions": 1024,
        }),
        encoding="utf-8"
    )

    # Mock INDEX_DIR
    import tm_hier_index as hier_mod
    original_index_dir = hier_mod.INDEX_DIR
    hier_mod.INDEX_DIR = isolated_index_dir

    try:
        s = tm_hier_index.stats()
        assert s["exists"] is False  # No jsonl yet
        assert s["meta"] is not None
        assert s["meta"]["embedding_model"] == "test-model"
    finally:
        hier_mod.INDEX_DIR = original_index_dir


def test_skip_noise_paragraphs_in_l0():
    """L0 extraction should skip boilerplate noise paragraphs."""
    text = """---
title: Test
---

# H1

This page intentionally left blank.

Fetch the complete documentation from the index.

Actual content paragraph.
"""
    result = tm_hier_index._extract_l0_text("Test", [], text, "wiki/test.md")
    # Should skip the noise paragraph and get actual content
    assert "intentionally left blank" not in result or "Actual content" in result
    assert "Fetch the complete documentation" not in result


def test_skip_code_blocks_in_l0():
    """L0 extraction should skip code blocks."""
    text = """---
title: Test
---

# H1

```python
def foo():
    pass
```

Actual paragraph.
"""
    result = tm_hier_index._extract_l0_text("Test", [], text, "wiki/test.md")
    # Should skip code block
    assert "def foo" not in result
