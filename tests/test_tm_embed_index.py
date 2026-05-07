"""Tests for tools/tm_embed_index.py — P0-1 meta manifest + dim guard.

Why these tests exist: the embedding env (`runtime/openmemory/.env`) and
the on-disk index (`runtime/embed_index/wiki.jsonl`) drift independently.
Without a guard, `search()` will silently cosine a 2048-dim ARK query
against 1024-dim Qwen entries, returning zeros and poisoning every eval.
P0-1 plumbs a meta manifest so search() can refuse the mismatch loudly.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_embed_index  # type: ignore[import-not-found]


@pytest.fixture
def isolated_index_dir(tmp_path, monkeypatch):
    """Point INDEX_DIR at a per-test tmp dir so meta/jsonl writes don't
    touch the real `runtime/embed_index/`."""
    monkeypatch.setattr(tm_embed_index, "INDEX_DIR", tmp_path)
    return tmp_path


def _make_entry(rel: str, dim: int) -> dict:
    return {
        "path": rel,
        "title": rel,
        "hash": "deadbeef",
        "mtime": 0,
        "vec": [0.1] * dim,
    }


def test_build_meta_records_actual_dim_from_entries(isolated_index_dir, monkeypatch):
    """_build_meta should derive embedding_dimensions from the first vec
    in entries, not from EMBEDDING_DIMENSIONS env hint."""
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "9999")  # lying env hint

    entries = {"a.md": _make_entry("a.md", 1024), "b.md": _make_entry("b.md", 1024)}
    meta = tm_embed_index._build_meta("wiki", entries)
    assert meta["scope"] == "wiki"
    assert meta["embedding_dimensions"] == 1024  # from vec, not env
    assert meta["embedding_dimensions_env_hint"] == 9999
    assert meta["embedding_model"] == "test-model"
    assert meta["embedding_base_url"] == "http://example.test/v1"
    assert meta["entry_count"] == 2
    assert "hash_schema" in meta
    assert "built_at" in meta


def test_save_load_meta_roundtrip(isolated_index_dir):
    meta = {"scope": "wiki", "embedding_dimensions": 1024, "embedding_model": "x"}
    tm_embed_index._save_meta("wiki", meta)
    loaded = tm_embed_index._load_meta("wiki")
    assert loaded == meta


def test_load_meta_returns_none_when_missing(isolated_index_dir):
    assert tm_embed_index._load_meta("wiki") is None


def test_check_query_compat_raises_on_dim_mismatch(isolated_index_dir, monkeypatch):
    """Dimension mismatch must raise IndexConfigMismatch, not silently zero."""
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
    monkeypatch.setenv("EMBEDDING_MODEL", "doubao-embedding-vision")

    tm_embed_index._save_meta("wiki", {
        "scope": "wiki",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedding_dimensions": 1024,
    })
    with pytest.raises(tm_embed_index.IndexConfigMismatch) as exc:
        tm_embed_index._check_query_compat("wiki", query_dim=2048)
    msg = str(exc.value)
    # Error message must surface both sides for fast diagnosis
    assert "1024" in msg
    assert "2048" in msg
    assert "Qwen" in msg
    assert "doubao-embedding-vision" in msg


def test_check_query_compat_passes_on_dim_match(isolated_index_dir):
    tm_embed_index._save_meta("wiki", {
        "scope": "wiki",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedding_dimensions": 1024,
    })
    # No raise expected.
    tm_embed_index._check_query_compat("wiki", query_dim=1024)


def test_check_query_compat_warns_on_missing_meta(isolated_index_dir, capsys):
    """Legacy index (built before P0-1) has no meta — warn but don't block,
    so existing flows keep working until a single rebuild materializes meta."""
    tm_embed_index._check_query_compat("wiki", query_dim=1024)
    captured = capsys.readouterr()
    assert "no meta file" in captured.err


def test_iter_pages_includes_extra_root_files_in_wiki_scope():
    """FUTURE-INDEXER: root AGENTS.md must be yielded by `_iter_pages('wiki')`
    so semantic queries like '变基出现冲突怎么办' can hit it. Before this
    fix, _iter_pages only walked wiki/ + sources/ and AGENTS.md was
    completely absent from the embed index — root cause of the
    `semantic-cn-rebase-conflict` miss documented in Phase 2m."""
    rels = [rel for _p, rel, _t, _a, _b in tm_embed_index._iter_pages("wiki")]
    assert "AGENTS.md" in rels, (
        "AGENTS.md must be indexed under the 'wiki' scope. "
        "If you removed EXTRA_ROOT_FILES['wiki'], "
        "update Phase 2m and `semantic-cn-rebase-conflict` may regress."
    )


def test_iter_pages_skips_extra_root_files_in_narrow_scopes():
    """Narrow scopes (`wiki_only`, `sources_only`) must NOT pick up root
    governance files — those scopes are intentionally focused subsets."""
    wiki_only_rels = [rel for _p, rel, *_ in tm_embed_index._iter_pages("wiki_only")]
    sources_only_rels = [rel for _p, rel, *_ in tm_embed_index._iter_pages("sources_only")]
    assert "AGENTS.md" not in wiki_only_rels
    assert "AGENTS.md" not in sources_only_rels


def test_partition_of_root_file_is_empty():
    """Root files must have empty partition so they're naturally excluded
    from `compute_centroids` (no propagation pollution)."""
    assert tm_embed_index._partition_of("AGENTS.md") == ""


def test_stats_includes_meta(isolated_index_dir):
    """`stats()` must surface meta so `preflight` can show what model the
    index was built with."""
    # No index yet → exists False but meta still queried (None here).
    s = tm_embed_index.stats("wiki")
    assert s["exists"] is False
    assert s["meta"] is None

    # Materialize a meta and re-check.
    tm_embed_index._save_meta("wiki", {
        "scope": "wiki",
        "embedding_model": "test-model",
        "embedding_dimensions": 1024,
    })
    s = tm_embed_index.stats("wiki")
    assert s["meta"] is not None
    assert s["meta"]["embedding_model"] == "test-model"
