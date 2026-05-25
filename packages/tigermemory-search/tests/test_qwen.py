from __future__ import annotations

import json
import sys

import pytest

from tigermemory_search import qwen


@pytest.fixture
def isolated_qwen_index(tmp_path, monkeypatch):
    monkeypatch.setattr(qwen, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(qwen, "_ENTRY_CACHE", {})
    monkeypatch.setattr(qwen, "_QUERY_CACHE", {})
    return tmp_path


def _write_index(path, entries):
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _entry(path: str, dense, sparse=None):
    out = {
        "path": path,
        "title": path,
        "aliases": [],
        "partition": "systems",
        "hash": path,
        "mtime": 0,
        "dim": len(dense),
        "dense": dense,
        "preview": path,
    }
    if sparse is not None:
        out["sparse"] = sparse
        out["sparse_count"] = len(sparse)
    return out


def test_qwen_v4_search_returns_dense_score(isolated_qwen_index, monkeypatch):
    dense_path, _meta_path = qwen._paths("dense")
    _write_index(dense_path, [_entry("a.md", [1.0, 0.0])])
    monkeypatch.setattr(qwen, "_embed_query", lambda *_args, **_kwargs: ([1.0, 0.0], {}))

    hit = qwen.search_dense("query", k=1)[0]

    assert hit["path"] == "a.md"
    assert hit["score_dense"] == pytest.approx(1.0)
    assert hit["source"] == "qwen-v4-dense-1024"


def test_qwen_v4_2048_uses_higher_dimension_path(isolated_qwen_index):
    index_path, meta_path = qwen._paths("dense", 2048)

    assert index_path.name == "wiki_qwen_v4_dense_2048.jsonl"
    assert meta_path.name == "wiki_qwen_v4_dense_2048.meta.json"


def test_qwen_v4_triple_rrf_branch_inputs_combine_dense_and_sparse_sources(isolated_qwen_index, monkeypatch):
    hybrid_path, _meta_path = qwen._paths("hybrid")
    _write_index(
        hybrid_path,
        [
            _entry("dense.md", [1.0, 0.0], [[9, 1.0]]),
            _entry("sparse.md", [0.0, 1.0], [[1, 1.0]]),
        ],
    )
    monkeypatch.setattr(qwen, "_embed_query", lambda *_args, **_kwargs: ([1.0, 0.0], {1: 1.0}))

    branches = qwen.search_hybrid_branches("query", k=1)

    assert branches["dense"][0]["path"] == "dense.md"
    assert branches["sparse"][0]["path"] == "sparse.md"


def test_qwen_v4_search_top_k_respects_argument(isolated_qwen_index, monkeypatch):
    dense_path, _meta_path = qwen._paths("dense")
    _write_index(
        dense_path,
        [
            _entry("a.md", [1.0, 0.0]),
            _entry("b.md", [0.8, 0.6]),
            _entry("c.md", [0.0, 1.0]),
        ],
    )
    monkeypatch.setattr(qwen, "_embed_query", lambda *_args, **_kwargs: ([1.0, 0.0], {}))

    hits = qwen.search_dense("query", k=2)

    assert [hit["path"] for hit in hits] == ["a.md", "b.md"]


def test_qwen_v4_main_help_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["tm_qwen_v4_index.py", "--help"])

    with pytest.raises(SystemExit) as exc:
        qwen.main()

    assert exc.value.code == 0
    assert "qwen-v4" in capsys.readouterr().out
