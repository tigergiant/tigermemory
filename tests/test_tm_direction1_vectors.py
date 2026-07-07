"""Direction-1 local semantic search: mechanism tests with mock embeddings.

The VM cannot reach the embedding service (:19190), so these prove the PLUMBING
with controlled vectors: packing, cosine, storage, RRF fusion, graceful
fallback, and the key one — a lexically-disjoint memory is surfaced via its
vector. Real semantic quality is validated on WSL with real embeddings.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


@pytest.fixture()
def local_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "vec.sqlite"))
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)


def _write(text: str, topic: str = "systems") -> str:
    return json.loads(tm_core.mem0_write("codex", topic, text))["id"]


def test_pack_unpack_roundtrip(local_db):
    vec = [0.1, -0.2, 0.3, 0.4]
    blob = tm_core._pack_vec(vec)
    back = list(tm_core._unpack_vec(blob))
    assert len(back) == 4
    assert all(abs(a - b) < 1e-6 for a, b in zip(vec, back))


def test_cosine_identity_and_orthogonal(local_db):
    assert abs(tm_core._cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) - 1.0) < 1e-6
    assert abs(tm_core._cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])) < 1e-6


def test_store_get_embedding_sets_available(local_db):
    mid = _write("some memory content")
    tm_core.store_memory_embedding(mid, [1.0, 0.0, 0.0], model="mock")
    assert tm_core.get_memory_embedding(mid) == [1.0, 0.0, 0.0]
    stats = tm_core.local_memory_stats()
    assert stats["by_vector_status"].get("available") == 1
    assert stats["vectored_rows"] == 1


def test_memories_without_embedding(local_db):
    a = _write("memory a")
    _write("memory b")
    tm_core.store_memory_embedding(a, [1.0, 0.0], model="mock")
    pending = tm_core.memories_without_embedding(limit=10)
    ids = {p["id"] for p in pending}
    assert a not in ids  # already embedded
    assert len(ids) == 1  # only b remains


def test_embed_and_store_with_mock(local_db):
    mid = _write("embed me")
    ok = tm_core.embed_and_store_memory(mid, embed_fn=lambda t: [0.5, 0.5])
    assert ok is True
    assert tm_core.get_memory_embedding(mid) == [0.5, 0.5]


def test_embed_and_store_backend_down_returns_false(local_db):
    mid = _write("embed me")

    def _down(_t):
        raise RuntimeError("embedding service unreachable")

    assert tm_core.embed_and_store_memory(mid, embed_fn=_down) is False
    assert tm_core.get_memory_embedding(mid) is None  # nothing stored


def test_backfill_embeddings(local_db):
    _write("m1"); _write("m2"); _write("m3")
    res = tm_core.backfill_embeddings(limit=10, embed_fn=lambda t: [1.0, 2.0, 3.0])
    assert res == {"attempted": 3, "embedded": 3, "failed": 0}
    assert tm_core.local_memory_stats()["vectored_rows"] == 3


def test_hybrid_without_vectors_equals_lexical(local_db):
    mid = _write("outbox 退避重试机制")
    res = tm_core.local_search_hybrid("outbox 退避", size=5)
    ids = [r["id"] for r in res["results"]]
    assert mid in ids  # lexical still works with zero vectors


def test_hybrid_surfaces_lexically_disjoint_memory_via_vector(local_db):
    # THE key mechanism proof: a memory with no lexical overlap with the query
    # is still recalled because its vector is close to the query vector.
    macbook = _write("虎哥主力开发用的是 MacBook Pro")
    other = _write("组合最高权重是高股息现金流底仓", topic="investment")
    tm_core.store_memory_embedding(macbook, [1.0, 0.0, 0.0], model="mock")
    tm_core.store_memory_embedding(other, [0.0, 1.0, 0.0], model="mock")

    # Pure lexical: "苹果电脑" shares no characters with either memory -> miss.
    lexical = tm_core.local_search_hybrid("苹果电脑", size=5)  # no embed_fn given below
    # (with real embed_one unreachable on the VM this would be lexical-only;
    #  here we drive the vector path explicitly:)
    hybrid = tm_core.local_search_hybrid(
        "苹果电脑", size=5, embed_fn=lambda q: [0.98, 0.02, 0.0]
    )
    hybrid_ids = [r["id"] for r in hybrid["results"]]
    assert macbook in hybrid_ids  # surfaced via vector despite zero lexical overlap
    assert hybrid_ids[0] == macbook  # and it ranks first (closest vector)


def test_vector_candidates_reuse_query_norm(local_db, monkeypatch):
    ids = [_write(f"vector perf memory {i}") for i in range(3)]
    for idx, mid in enumerate(ids):
        tm_core.store_memory_embedding(mid, [1.0 - idx * 0.1, idx * 0.1, 0.0], model="mock")

    monkeypatch.setattr(tm_core, "_np", None)
    sqrt_calls = 0
    original_sqrt = tm_core.math.sqrt

    def counted_sqrt(value):
        nonlocal sqrt_calls
        sqrt_calls += 1
        return original_sqrt(value)

    monkeypatch.setattr(tm_core.math, "sqrt", counted_sqrt)
    conn = tm_core._local_db_conn()
    try:
        tm_core._ensure_local_memory_schema(conn)
        candidates = tm_core._local_vector_candidates(conn, [1.0, 0.0, 0.0], top_n=3)
    finally:
        conn.close()

    assert candidates[0] == ids[0]
    assert sqrt_calls == 4  # one query norm + one stored-vector norm per row


def test_hybrid_graceful_when_embed_raises(local_db):
    mid = _write("outbox 退避重试机制")
    tm_core.store_memory_embedding(mid, [1.0, 0.0], model="mock")

    def _down(_q):
        raise RuntimeError("embed backend down")

    res = tm_core.local_search_hybrid("outbox 退避", size=5, embed_fn=_down)
    assert mid in [r["id"] for r in res["results"]]  # fell back to lexical, no crash


def test_rrf_scores_normalized_range(local_db):
    fused = tm_core._rrf_fuse(["a", "b", "c"], ["c", "d"])
    assert fused  # non-empty
    assert all(0.2 - 1e-9 <= v <= 1.0 + 1e-9 for v in fused.values())
    # 'c' appears in both lists -> should score highest.
    assert fused["c"] == max(fused.values())


def test_mem0_search_gated_vector_wiring(local_db, monkeypatch):
    # Default OFF: local mem0_search stays pure lexical (search_backend=local).
    monkeypatch.delenv("TM_LOCAL_VECTOR_SEARCH", raising=False)
    mac = _write("虎哥主力开发用的是 MacBook Pro")
    tm_core.store_memory_embedding(mac, [1.0, 0.0, 0.0], model="mock")
    import json as _json
    off = _json.loads(tm_core.mem0_search("苹果电脑", size=5))
    assert off["search_backend"] == "local"
    assert mac not in [r["id"] for r in off["results"]]  # lexical can't cross languages

    # Flag ON: local mem0_search uses hybrid; with a query embedding close to the
    # macbook vector, the lexically-disjoint memory is now recalled.
    monkeypatch.setenv("TM_LOCAL_VECTOR_SEARCH", "1")
    monkeypatch.setattr(tm_core, "embed_one", lambda q: [0.98, 0.02, 0.0])
    on = _json.loads(tm_core.mem0_search("苹果电脑", size=5))
    assert on["search_backend"] == "local+vector"
    assert mac in [r["id"] for r in on["results"]]
