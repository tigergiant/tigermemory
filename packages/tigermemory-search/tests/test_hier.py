from __future__ import annotations

import json

import pytest

from tigermemory_search import hier


@pytest.fixture
def isolated_hier_index(tmp_path, monkeypatch):
    monkeypatch.setattr(hier, "INDEX_DIR", tmp_path)
    return tmp_path


def _write_layers(index_dir, entries):
    path = index_dir / "wiki_layers.jsonl"
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    (index_dir / "wiki_layers.meta.json").write_text(
        json.dumps({"embedding_dimensions": 2}, ensure_ascii=False),
        encoding="utf-8",
    )


def _entry(path: str, layer: str, vec: list[float], title: str = "Title"):
    return {
        "path": path,
        "layer": layer,
        "title": title,
        "aliases": [],
        "partition": "systems",
        "text_hash": f"{path}-{layer}",
        "vec": vec,
        "preview": f"{path} {layer}",
    }


def test_load_hierarchical_index_stats_returns_three_layer_counts(isolated_hier_index):
    _write_layers(
        isolated_hier_index,
        [
            _entry("a.md", "L0", [1.0, 0.0]),
            _entry("a.md", "L1", [1.0, 0.0]),
            _entry("a.md", "L2", [1.0, 0.0]),
        ],
    )

    stats = hier.stats()

    assert stats["exists"] is True
    assert stats["layer_counts"] == {"L0": 1, "L1": 1, "L2": 1}


def test_search_l0_l1_l2_aggregates_by_layer(isolated_hier_index, monkeypatch):
    monkeypatch.setattr(hier.tm_core, "embed_texts", lambda _texts: [[1.0, 0.0]])
    _write_layers(
        isolated_hier_index,
        [
            _entry("a.md", "L0", [1.0, 0.0]),
            _entry("a.md", "L1", [0.0, 1.0]),
            _entry("a.md", "L2", [1.0, 0.0]),
        ],
    )

    hits = hier.search_pages("query", k=1)

    assert hits[0]["path"] == "a.md"
    assert hits[0]["layer_scores"]["L0"] == pytest.approx(1.0)
    assert hits[0]["layer_scores"]["L2"] == pytest.approx(1.0)
    assert "L1" not in hits[0]["layer_scores"]


def test_layer_score_is_weighted_average(isolated_hier_index, monkeypatch):
    monkeypatch.setattr(hier.tm_core, "embed_texts", lambda _texts: [[1.0, 0.0]])
    _write_layers(
        isolated_hier_index,
        [
            _entry("a.md", "L0", [1.0, 0.0]),
            _entry("a.md", "L1", [0.0, 1.0]),
            _entry("a.md", "L2", [1.0, 0.0]),
        ],
    )

    hit = hier.search_pages("query", k=1, weights=(0.5, 0.3, 0.2))[0]

    assert hit["score"] == pytest.approx(0.7)


def test_query_with_no_match_returns_empty(isolated_hier_index, monkeypatch):
    monkeypatch.setattr(hier.tm_core, "embed_texts", lambda _texts: [[1.0, 0.0]])
    _write_layers(isolated_hier_index, [_entry("a.md", "L0", [0.0, 1.0])])

    assert hier.search("query", k=5) == []


def test_search_top_k_respects_argument(isolated_hier_index, monkeypatch):
    monkeypatch.setattr(hier.tm_core, "embed_texts", lambda _texts: [[1.0, 0.0]])
    _write_layers(
        isolated_hier_index,
        [
            _entry("a.md", "L0", [1.0, 0.0]),
            _entry("b.md", "L0", [0.8, 0.6]),
            _entry("c.md", "L0", [0.6, 0.8]),
        ],
    )

    hits = hier.search_pages("query", k=2)

    assert [hit["path"] for hit in hits] == ["a.md", "b.md"]
