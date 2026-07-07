"""Tests for tm_recall_eval: the local event-memory retrieval ruler.

Runs against a temp DB in local profile; never touches production. Asserts
structural correctness + the stable baseline signal (lexical cases hit,
semantic cases are honestly counted), and that arm=vector fails loudly until
direction-1 exists (so the baseline can never be mislabeled as a vector score).
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]
import tm_recall_eval  # type: ignore[import-not-found]

FIXTURE = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "memory_recall_eval.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def local_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "recall.sqlite"))
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_LOCAL)


def test_fixture_shape():
    assert FIXTURE["corpus"] and FIXTURE["cases"]
    keys = {c["key"] for c in FIXTURE["corpus"]}
    for case in FIXTURE["cases"]:
        assert case["expect"] in keys, f"{case['id']} expects unknown corpus key"
        assert case["dimension"] in {"plain", "cross_language", "paraphrase", "distractor"}


def test_current_arm_baseline(local_db):
    result = tm_recall_eval.run_eval(FIXTURE, k=5, arm="current")
    assert result["schema"] == "tm-recall-eval-v1"
    assert result["arm"] == "current"
    assert result["total_cases"] == len(FIXTURE["cases"])
    # Lexical-overlap cases must be recalled by the current system.
    assert result["by_dimension"]["plain"]["recall"] == 1.0
    # The semantic gap is the whole point: cross_language must currently be weak
    # (this is the honest baseline that direction-1 will lift). Guard against a
    # fixture that accidentally became lexically trivial.
    assert result["by_dimension"]["cross_language"]["recall"] < 0.5
    assert 0.0 <= result["overall_recall"] <= 1.0


def test_vector_arm_fails_until_direction1(local_db, monkeypatch):
    # Ensure the hybrid hook is absent, then arm=vector must refuse (never score
    # the FTS baseline as if it were the vector arm).
    monkeypatch.delattr(tm_core, "local_search_hybrid", raising=False)
    with pytest.raises(SystemExit):
        tm_recall_eval.run_eval(FIXTURE, k=5, arm="vector")


def test_seed_never_touches_production(local_db):
    # run_eval seeds only into the temp DB pointed at by TIGERMEMORY_LOCAL_DB.
    result = tm_recall_eval.run_eval(FIXTURE, k=5, arm="current")
    assert result["hit_total"] >= 3  # at least the plain cases


def test_render_text_smoke(local_db):
    text = tm_recall_eval.render_text(tm_recall_eval.run_eval(FIXTURE, k=5, arm="current"))
    assert "overall recall@5" in text
    assert "cross_language" in text


# A concept-level mock embedder: texts sharing a concept get identical vectors,
# different concepts are orthogonal. This proves the eval->hybrid->vector
# pipeline lifts the semantic dimensions WITHOUT needing the real :19190 model
# (real model quality is validated on WSL).
_CONCEPTS = {
    "computer": ["macbook", "苹果", "电脑", "笔记本"],
    "cloud": ["火山", "volcengine", "服务器", "vps"],
    "vecsearch": ["向量", "余弦", "语义", "搜索", "重排", "检索"],
    "dividend": ["股息", "分红", "现金流", "底仓", "板块", "高"],
    "outbox": ["outbox", "退避", "重试"],
    "dedup": ["去重", "sha256", "精确"],
    "handoff": ["cursor", "会话", "接力"],
}
_CONCEPT_NAMES = sorted(_CONCEPTS)


def _concept_embed(text: str) -> list[float]:
    low = text.lower()
    best = None
    best_hits = 0
    for name in _CONCEPT_NAMES:
        hits = sum(1 for kw in _CONCEPTS[name] if kw in low)
        if hits > best_hits:
            best_hits = hits
            best = name
    vec = [0.0] * (len(_CONCEPT_NAMES) + 1)
    if best is None:
        vec[-1] = 1.0  # "other" bucket
    else:
        vec[_CONCEPT_NAMES.index(best)] = 1.0
    return vec


def test_vector_arm_lifts_semantic_dimensions(local_db):
    result = tm_recall_eval.run_eval(FIXTURE, k=5, arm="vector", embed_fn=_concept_embed)
    dims = result["by_dimension"]
    # The whole point: semantic dimensions go from 0 (baseline) up.
    assert dims["cross_language"]["recall"] == 1.0
    assert dims["paraphrase"]["recall"] == 1.0
    # And lexical cases must not regress.
    assert dims["plain"]["recall"] == 1.0
    # Overall clearly beats the 3/9 lexical baseline.
    assert result["overall_recall"] >= 0.77


def test_cli_json(local_db, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "cli.sqlite"))
    rc = tm_recall_eval.main(["--json", "--db", str(tmp_path / "cli.sqlite")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "tm-recall-eval-v1"
    assert payload["by_dimension"]["plain"]["recall"] == 1.0
