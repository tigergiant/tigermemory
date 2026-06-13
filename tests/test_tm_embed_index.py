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
    monkeypatch.setenv("TM_EMBED_SUMMARY_WEIGHT", "0.98")
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
    assert meta["summary_hash_schema"] == tm_embed_index.SUMMARY_HASH_SCHEMA.decode("utf-8")
    assert meta["summary_vector_weight"] == 0.98
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


def test_embed_texts_with_split_retry_splits_transient_batch(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        calls.append(tuple(texts))
        if len(texts) > 1:
            raise tm_embed_index.tm_core.EmbeddingError("timeout", kind="transient")
        return [[float(len(calls))]]

    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", fake_embed_texts)

    vectors, skipped = tm_embed_index._embed_texts_with_split_retry(
        ["a", "b", "c"],
        labels=["a.md", "b.md", "c.md"],
    )

    assert len(vectors) == 3
    assert skipped == []
    assert calls[0] == ("a", "b", "c")
    assert ("a",) in calls
    assert ("b",) in calls
    assert ("c",) in calls


def test_embed_texts_with_split_retry_preserves_permanent_failure(monkeypatch):
    def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        raise tm_embed_index.tm_core.EmbeddingError("bad shape", kind="permanent")

    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", fake_embed_texts)

    with pytest.raises(tm_embed_index.tm_core.EmbeddingError) as exc:
        tm_embed_index._embed_texts_with_split_retry(["a", "b"], labels=["a.md", "b.md"])

    assert exc.value.kind == "permanent"


def test_embed_texts_with_split_retry_waits_for_open_breaker(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise tm_embed_index.tm_core.EmbeddingError(
                "Embedding circuit breaker OPEN (5 consecutive transient failures); retry in 60s",
                kind="transient",
            )
        return [[0.1]]

    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(
        tm_embed_index.tm_core,
        "_embed_retry_config",
        lambda: {"breaker_reset": 0.0},
    )
    monkeypatch.setattr(tm_embed_index.time, "sleep", lambda seconds: sleeps.append(seconds))

    vectors, skipped = tm_embed_index._embed_texts_with_split_retry(["a"], labels=["a.md"])

    assert vectors == [[0.1]]
    assert skipped == []
    assert calls == 2
    assert sleeps == [1.0]


def test_embed_texts_with_split_retry_skips_single_transient(monkeypatch):
    def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        raise tm_embed_index.tm_core.EmbeddingError("timeout", kind="transient")

    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", fake_embed_texts)

    vectors, skipped = tm_embed_index._embed_texts_with_split_retry(["a"], labels=["a.md"])

    assert vectors == []
    assert skipped == ["a.md"]


def test_summary_hash_changes_when_deep_summary_changes():
    prefix = "head text\n" * (tm_embed_index.EMBED_TEXT_CHARS + 10)
    body_a = f"# Long\n\n{prefix}\n\n## 摘要\n\nalpha summary hidden after the embedding budget."
    body_b = f"# Long\n\n{prefix}\n\n## 摘要\n\nbeta summary hidden after the embedding budget."

    assert tm_embed_index._summary_hash(
        "wiki/systems/long.md",
        "Long",
        [],
        tm_embed_index._extract_summary(body_a),
    ) != tm_embed_index._summary_hash(
        "wiki/systems/long.md",
        "Long",
        [],
        tm_embed_index._extract_summary(body_b),
    )


def test_build_stores_summary_vec_when_summary_exists(isolated_index_dir, tmp_path, monkeypatch):
    repo = tmp_path
    page = repo / "wiki" / "systems" / "long.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: Long Page\n---\n"
        "# Long Page\n\n"
        "## 摘要\n\n"
        "Deep unique signal about p311 summary vectors.\n\n"
        "## Body\n\n"
        "head text",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index, "SCOPES", {"wiki": ("wiki",)})

    embedded_texts: list[str] = []

    def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        embedded_texts.extend(texts)
        return [[float(i + 1)] for i, _ in enumerate(texts)]

    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", fake_embed_texts)

    result = tm_embed_index.build(scope="wiki", force=True, batch_log=10)
    entries = tm_embed_index._load_index("wiki")

    assert result["summary_vectors"] == 1
    entry = entries["wiki/systems/long.md"]
    assert "summary" not in entry
    assert entry["summary_hash"]
    assert entry["summary_vec"] == [2.0]
    assert len(embedded_texts) == 2


def test_build_does_not_store_person_summary_plaintext(isolated_index_dir, tmp_path, monkeypatch):
    repo = tmp_path
    page = repo / "wiki" / "person" / "tiger.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: Tiger\n---\n"
        "# Tiger\n\n"
        "## 摘要\n\n"
        "Private profile summary should not be copied into runtime index.\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index, "SCOPES", {"wiki": ("wiki",)})
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", lambda texts: [[1.0] for _ in texts])

    result = tm_embed_index.build(scope="wiki", force=True, batch_log=10)
    entries = tm_embed_index._load_index("wiki")
    entry = entries["wiki/person/tiger.md"]

    assert result["summary_vectors"] == 0
    assert "summary" not in entry
    assert "summary_hash" not in entry
    assert "summary_vec" not in entry


def test_build_does_not_store_sources_person_summary_vector(isolated_index_dir, tmp_path, monkeypatch):
    repo = tmp_path
    page = repo / "sources" / "person" / "tiger.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: Tiger Source\n---\n"
        "# Tiger Source\n\n"
        "## 摘要\n\n"
        "Private source summary should not be copied into runtime index.\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index, "SCOPES", {"wiki": ("sources",)})
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", lambda texts: [[1.0] for _ in texts])

    result = tm_embed_index.build(scope="wiki", force=True, batch_log=10)
    entries = tm_embed_index._load_index("wiki")
    entry = entries["sources/person/tiger.md"]

    assert result["summary_vectors"] == 0
    assert "summary" not in entry
    assert "summary_hash" not in entry
    assert "summary_vec" not in entry


def test_iter_pages_skips_runtime_work_areas_even_if_scope_is_widened(tmp_path, monkeypatch):
    repo = tmp_path
    for rel in [
        ".tmp/scratch.md",
        "runtime/cache.md",
        "tests/fixture.md",
        "review-artifacts/audit.md",
        "sources/review-artifacts/audit.md",
        "wiki/systems/ok.md",
    ]:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test\n\ncontent", encoding="utf-8")
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(
        tm_embed_index,
        "SCOPES",
        {"wiki": (".tmp", "runtime", "tests", "review-artifacts", "sources", "wiki")},
    )

    rels = [rel for _p, rel, *_ in tm_embed_index._iter_pages("wiki")]

    assert rels == ["wiki/systems/ok.md"]


def test_build_backfills_summary_vec_with_cached_page_vec(isolated_index_dir, tmp_path, monkeypatch):
    repo = tmp_path
    page = repo / "wiki" / "systems" / "long.md"
    page.parent.mkdir(parents=True)
    body = (
        "---\ntitle: Long Page\n---\n"
        "# Long Page\n\n"
        "## 摘要\n\n"
        "Deep reusable summary signal.\n\n"
        "## Body\n\n"
        "head text"
    )
    page.write_text(body, encoding="utf-8")
    rel = "wiki/systems/long.md"
    title = tm_embed_index._extract_title(body, page)
    aliases = tm_embed_index._extract_aliases(body)
    tm_embed_index._save_index(
        "wiki",
        {
            rel: {
                "path": rel,
                "title": title,
                "hash": tm_embed_index._content_hash(rel, title, aliases, body),
                "mtime": 0,
                "vec": [1.0, 0.0],
            }
        },
    )
    tm_embed_index._save_meta(
        "wiki",
        {
            "scope": "wiki",
            "embedding_base_url": "http://old.test/v1",
            "embedding_model": "old-model",
            "embedding_dimensions": 2,
            "hash_schema": "test",
        },
    )
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index, "SCOPES", {"wiki": ("wiki",)})
    monkeypatch.setattr(tm_embed_index, "_current_embedding_identity", lambda: ("http://old.test/v1", "old-model"))
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", lambda texts: [[0.0, 1.0] for _ in texts])

    result = tm_embed_index.build(scope="wiki", force=False, batch_log=10)
    entry = tm_embed_index._load_index("wiki")[rel]

    assert result["embedded"] == 0
    assert result["summary_embedded"] == 1
    assert entry["vec"] == [1.0, 0.0]
    assert entry["summary_vec"] == [0.0, 1.0]


def test_build_rejects_incremental_backfill_model_mismatch(isolated_index_dir, tmp_path, monkeypatch):
    repo = tmp_path
    page = repo / "wiki" / "systems" / "long.md"
    page.parent.mkdir(parents=True)
    body = "# Long Page\n\n## 摘要\n\nDeep reusable summary signal."
    page.write_text(body, encoding="utf-8")
    rel = "wiki/systems/long.md"
    title = tm_embed_index._extract_title(body, page)
    aliases = tm_embed_index._extract_aliases(body)
    tm_embed_index._save_index(
        "wiki",
        {
            rel: {
                "path": rel,
                "title": title,
                "hash": tm_embed_index._content_hash(rel, title, aliases, body),
                "mtime": 0,
                "vec": [1.0, 0.0],
            }
        },
    )
    tm_embed_index._save_meta(
        "wiki",
        {
            "scope": "wiki",
            "embedding_base_url": "http://old.test/v1",
            "embedding_model": "old-model",
            "embedding_dimensions": 2,
        },
    )
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index, "SCOPES", {"wiki": ("wiki",)})
    monkeypatch.setattr(tm_embed_index, "_current_embedding_identity", lambda: ("http://new.test/v1", "new-model"))
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", lambda texts: [[0.0, 1.0] for _ in texts])

    with pytest.raises(tm_embed_index.IndexConfigMismatch):
        tm_embed_index.build(scope="wiki", force=False, batch_log=10)


def test_build_drops_stale_summary_vec_when_page_summary_removed(isolated_index_dir, tmp_path, monkeypatch):
    repo = tmp_path
    page = repo / "wiki" / "systems" / "long.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Long Page\n\nNo summary remains.", encoding="utf-8")
    rel = "wiki/systems/long.md"
    tm_embed_index._save_index(
        "wiki",
        {
            rel: {
                "path": rel,
                "title": "Long Page",
                "hash": "old",
                "mtime": 0,
                "vec": [1.0, 0.0],
                "summary_hash": "old-summary",
                "summary_vec": [0.0, 1.0],
            }
        },
    )
    monkeypatch.setattr(tm_embed_index, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index.tm_core, "REPO_ROOT", repo)
    monkeypatch.setattr(tm_embed_index, "SCOPES", {"wiki": ("wiki",)})
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])

    result = tm_embed_index.build(scope="wiki", force=False, batch_log=10)
    entry = tm_embed_index._load_index("wiki")[rel]

    assert result["summary_vectors"] == 0
    assert "summary_hash" not in entry
    assert "summary_vec" not in entry


def test_search_uses_summary_vec_as_secondary_signal(isolated_index_dir, monkeypatch):
    tm_embed_index._save_index(
        "wiki",
        {
            "wiki/systems/long.md": {
                "path": "wiki/systems/long.md",
                "title": "Long",
                "hash": "h",
                "mtime": 0,
                "vec": [0.0, 1.0],
                "summary": "summary-only p311 signal",
                "summary_hash": "s",
                "summary_vec": [1.0, 0.0],
            },
            "wiki/systems/head.md": {
                "path": "wiki/systems/head.md",
                "title": "Head",
                "hash": "h2",
                "mtime": 0,
                "vec": [0.0, 0.9],
            },
        },
    )
    tm_embed_index._save_meta(
        "wiki",
        {"scope": "wiki", "embedding_dimensions": 2, "embedding_model": "test"},
    )
    monkeypatch.setenv("TM_EMBED_SUMMARY_WEIGHT", "0.98")
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_one", lambda query: [1.0, 0.0])

    hits = tm_embed_index.search("summary query", scope="wiki", k=1)

    assert hits[0]["path"] == "wiki/systems/long.md"
    breakdown = hits[0]["score_breakdown"]
    assert breakdown["summary_score"] > breakdown["page_score"]
    assert breakdown["summary_boosted"] is True
    assert breakdown["summary_weight"] == 0.98


def test_search_uses_default_summary_weight_off_when_env_missing(isolated_index_dir, monkeypatch):
    tm_embed_index._save_index(
        "wiki",
        {
            "wiki/systems/long.md": {
                "path": "wiki/systems/long.md",
                "title": "Long",
                "hash": "h",
                "mtime": 0,
                "vec": [0.0, 0.8],
                "summary_hash": "s",
                "summary_vec": [1.0, 0.0],
            },
            "wiki/systems/head.md": {
                "path": "wiki/systems/head.md",
                "title": "Head",
                "hash": "h2",
                "mtime": 0,
                "vec": [0.0, 1.0],
            },
        },
    )
    tm_embed_index._save_meta(
        "wiki",
        {"scope": "wiki", "embedding_dimensions": 2, "embedding_model": "test"},
    )
    monkeypatch.delenv("TM_EMBED_SUMMARY_WEIGHT", raising=False)
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_one", lambda query: [1.0, 0.0])

    hits = tm_embed_index.search("summary query", scope="wiki", k=1)

    assert hits[0]["path"] == "wiki/systems/head.md"
    assert hits[0]["score_breakdown"]["summary_weight"] == 0.0
    assert hits[0]["score_breakdown"]["summary_boosted"] is False


@pytest.mark.parametrize(
    "weight_env",
    ["missing", "invalid"],
)
def test_search_does_not_boost_summary_when_summary_weight_is_zero_even_with_negative_page_score(
    isolated_index_dir, monkeypatch, weight_env
):
    tm_embed_index._save_index(
        "wiki",
        {
            "wiki/systems/negative-page.md": {
                "path": "wiki/systems/negative-page.md",
                "title": "Negative Page",
                "hash": "h",
                "mtime": 0,
                "vec": [-1.0, 0.0],
                "summary_hash": "s",
                "summary_vec": [1.0, 0.0],
            },
        },
    )
    tm_embed_index._save_meta(
        "wiki",
        {"scope": "wiki", "embedding_dimensions": 2, "embedding_model": "test"},
    )
    if weight_env == "missing":
        monkeypatch.delenv("TM_EMBED_SUMMARY_WEIGHT", raising=False)
    else:
        monkeypatch.setenv("TM_EMBED_SUMMARY_WEIGHT", "not-a-number")
    monkeypatch.setattr(tm_embed_index.tm_core, "embed_one", lambda query: [1.0, 0.0])

    hits = tm_embed_index.search("summary query", scope="wiki", k=1)

    assert hits[0]["path"] == "wiki/systems/negative-page.md"
    breakdown = hits[0]["score_breakdown"]
    assert breakdown["summary_weight"] == 0.0
    assert breakdown["summary_boosted"] is False
    assert breakdown["selected_vector"] == "page"
    assert hits[0]["score"] == breakdown["page_score"]
    assert breakdown["page_score"] == -1.0
    assert breakdown["summary_score"] == 1.0


def test_summary_vector_weight_invalid_env_defaults_to_zero(monkeypatch):
    monkeypatch.setenv("TM_EMBED_SUMMARY_WEIGHT", "not-a-number")
    assert tm_embed_index._summary_vector_weight() == 0.0
