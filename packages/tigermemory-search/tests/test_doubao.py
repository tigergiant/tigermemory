from __future__ import annotations

import json
import sys

import pytest

from tigermemory_search import doubao


@pytest.fixture
def isolated_doubao_index(tmp_path, monkeypatch):
    monkeypatch.setattr(doubao, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(doubao, "INDEX_PATH", tmp_path / "wiki_doubao_hybrid.jsonl")
    monkeypatch.setattr(doubao, "META_PATH", tmp_path / "wiki_doubao_hybrid.meta.json")
    monkeypatch.setattr(doubao, "_INDEX_CACHE", None)
    return tmp_path


def _write_index(entries):
    doubao.INDEX_PATH.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _entry(path: str, dense, sparse):
    return {
        "path": path,
        "title": path,
        "aliases": [],
        "partition": "systems",
        "hash": path,
        "mtime": 0,
        "dim": len(dense),
        "sparse_count": len(sparse),
        "dense": dense,
        "sparse": sparse,
        "preview": path,
    }


def test_doubao_search_returns_hybrid_score(isolated_doubao_index, monkeypatch):
    _write_index([_entry("a.md", [1.0, 0.0], [[1, 1.0]])])
    monkeypatch.setattr(doubao, "_api_key", lambda: "k")
    monkeypatch.setattr(
        doubao,
        "_embed_with_retry",
        lambda *_args, **_kwargs: {"data": {"embedding": [1.0, 0.0], "sparse_embedding": [{"index": 1, "value": 1.0}]}},
    )

    hit = doubao.search("query", k=1)[0]

    assert hit["path"] == "a.md"
    assert hit["source"] == "doubao-hybrid"
    assert hit["score"] == pytest.approx(1.0)


def test_doubao_weighted_fusion_combines_dense_and_sparse_scores(isolated_doubao_index, monkeypatch):
    _write_index([
        _entry("dense.md", [1.0, 0.0], [[9, 1.0]]),
        _entry("sparse.md", [0.0, 1.0], [[1, 1.0]]),
    ])
    monkeypatch.setattr(doubao, "_api_key", lambda: "k")
    monkeypatch.setattr(
        doubao,
        "_embed_with_retry",
        lambda *_args, **_kwargs: {"data": {"embedding": [1.0, 0.0], "sparse_embedding": [{"index": 1, "value": 1.0}]}},
    )

    hits = doubao.search("query", k=2, dense_weight=0.55, sparse_weight=0.45)

    assert [(hit["path"], hit["score"]) for hit in hits] == [("dense.md", 0.55), ("sparse.md", 0.45)]


def test_doubao_score_normalization_stays_within_unit_range():
    dense = doubao._cosine_dense([2.0, 0.0], [4.0, 0.0])
    sparse = doubao._cosine_sparse({1: 2.0}, {1: 5.0})

    assert dense == pytest.approx(1.0)
    assert sparse == pytest.approx(1.0)


def test_doubao_search_top_k_excludes_lower_ranked_entries(isolated_doubao_index, monkeypatch):
    _write_index([
        _entry("best.md", [1.0, 0.0], [[1, 1.0]]),
        _entry("low.md", [0.0, 1.0], [[9, 1.0]]),
    ])
    monkeypatch.setattr(doubao, "_api_key", lambda: "k")
    monkeypatch.setattr(
        doubao,
        "_embed_with_retry",
        lambda *_args, **_kwargs: {"data": {"embedding": [1.0, 0.0], "sparse_embedding": [{"index": 1, "value": 1.0}]}},
    )

    hits = doubao.search("query", k=1)

    assert [hit["path"] for hit in hits] == ["best.md"]


def test_doubao_main_help_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["tm_doubao_hybrid_index.py", "--help"])

    with pytest.raises(SystemExit) as exc:
        doubao.main()

    assert exc.value.code == 0
    assert "doubao-hybrid" in capsys.readouterr().out
