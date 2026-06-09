from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-eval" / "src"))

import tm_memory_eval  # type: ignore[import-not-found]
import tigermemory_eval.memory as tm_memory_eval_pkg  # type: ignore[import-not-found]


def test_load_eval_env_loads_only_embedding_vars(tmp_path, monkeypatch):
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "EMBEDDING_BASE_URL=http://127.0.0.1:19190/v1",
            "EMBEDDING_MODEL=qwen3-embedding",
            "OPENAI_API_KEY=test-key",
            "MEM0_API_KEY=should-not-load",
        ]),
        encoding="utf-8",
    )

    tm_memory_eval.load_eval_env(env_file)

    assert os.environ["EMBEDDING_BASE_URL"] == "http://127.0.0.1:19190/v1"
    assert os.environ["EMBEDDING_MODEL"] == "qwen3-embedding"
    assert os.environ["OPENAI_API_KEY"] == "test-key"
    assert "MEM0_API_KEY" not in os.environ


def test_load_eval_env_rewrites_docker_host_alias_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_memory_eval.os, "name", "nt", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDING_BASE_URL=http://host.docker.internal:19190/v1\n"
        "EMBEDDING_MODEL=qwen3-embedding\n"
        "OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )

    tm_memory_eval.load_eval_env(env_file)

    assert os.environ["EMBEDDING_BASE_URL"] == "http://localhost:19190/v1"


def test_load_eval_env_preserves_docker_host_alias_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_memory_eval.os, "name", "posix", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EMBEDDING_BASE_URL=http://host.docker.internal:19190/v1\n"
        "EMBEDDING_MODEL=qwen3-embedding\n"
        "OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )

    tm_memory_eval.load_eval_env(env_file)

    assert os.environ["EMBEDDING_BASE_URL"] == "http://host.docker.internal:19190/v1"


def test_memory_eval_loader_accepts_optional_fields_and_gates_paper_seed_tmp(tmp_path):
    baseline = tmp_path / "memory_eval.jsonl"
    baseline.write_text(
        "\n".join([
            "{\"id\":\"case-1\",\"query\":\"alpha\",\"scope\":\"wiki\",\"expected_paths\":[\"wiki/a.md\"],\"must_contain\":[\"alpha\"],\"notes\":\"n\",\"case_source\":\"real_failure\",\"case_source_ref\":\"trace:1\",\"eval_dimension\":\"static_state_recall\",\"freshness_mode\":\"current\",\"expected_warning\":\"warn-1\"}",
        ]),
        encoding="utf-8",
    )

    loaded = tm_memory_eval_pkg.load_cases(baseline)
    assert loaded[0].case_source == "real_failure"
    assert loaded[0].expected_warning == ["warn-1"]

    experimental = tmp_path / "paper_seed_tmp.jsonl"
    experimental.write_text(
        "{\"id\":\"case-2\",\"query\":\"beta\",\"scope\":\"wiki\",\"expected_paths\":[\"wiki/b.md\"],\"must_contain\":[\"beta\"],\"notes\":\"n\",\"case_source\":\"paper_seed_tmp\"}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="paper_seed_tmp"):
        tm_memory_eval_pkg.load_cases(experimental)

    loaded_experimental = tm_memory_eval_pkg.load_cases(experimental, allow_paper_seed_tmp=True)
    assert loaded_experimental[0].case_source == "paper_seed_tmp"


def test_memory_eval_reports_grouped_metrics(tmp_path, monkeypatch):
    cases = [
        tm_memory_eval_pkg.EvalCase(
            id="case-1",
            query="alpha",
            scope="wiki",
            expected_paths=["wiki/a.md"],
            must_contain=["alpha"],
            notes="n",
            case_source="real_failure",
            eval_dimension="static_state_recall",
            freshness_mode="current",
            expected_warning=["warn-1"],
        ),
        tm_memory_eval_pkg.EvalCase(
            id="case-2",
            query="beta",
            scope="wiki",
            expected_paths=["wiki/b.md"],
            must_contain=["beta"],
            notes="n",
            case_source="patrol",
            eval_dimension="stale_obsolete",
            freshness_mode="stale_sensitive",
            expected_trace_flags=["planner"],
        ),
        tm_memory_eval_pkg.EvalCase(
            id="case-3",
            query="gamma",
            scope="wiki",
            expected_paths=["wiki/c.md"],
            must_contain=["gamma"],
            notes="n",
            case_source="system_contract",
            eval_dimension="workflow_knowledge",
            freshness_mode="not_applicable",
            expected_warning=["missing-warn"],
        ),
    ]

    def fake_run_search(scope, query, top_k, **kwargs):
        if query == "alpha":
            return [
                tm_memory_eval_pkg.SearchHit(path="wiki/a.md", title="Alpha", snippet="alpha", score=1.0, source="wiki"),
            ], ["warn-1"]
        if query == "gamma":
            return [
                tm_memory_eval_pkg.SearchHit(path="wiki/c.md", title="Gamma", snippet="gamma", score=1.0, source="wiki"),
            ], []
        return [
            tm_memory_eval_pkg.SearchHit(path="wiki/b.md", title="Beta", snippet="beta", score=1.0, source="wiki"),
        ], []

    monkeypatch.setattr(tm_memory_eval_pkg, "run_search", fake_run_search)
    report = tm_memory_eval_pkg.evaluate(cases, top_k=3, grouped=True)

    assert report["case_count_by_dimension"]["static_state_recall"] == 1
    assert report["case_count_by_dimension"]["stale_obsolete"] == 1
    assert report["case_count_by_source"]["real_failure"] == 1
    assert report["hit1_by_dimension"]["static_state_recall"] == 1
    assert report["warning_hit_by_dimension"]["static_state_recall"] == 1
    assert report["stale_penalty_count"] == 1
    assert report["action_seed_count"] == 0
    assert report["expected_warning_case_count"] == 2
    assert report["expected_warning_miss_count"] == 1
    assert report["expected_trace_flag_case_count"] == 1
    assert report["expected_trace_flag_miss_count"] == 1
    assert report["contract_failure_count"] == 2
    assert {item["id"] for item in report["contract_failures"]} == {"case-2", "case-3"}
    assert all("query" not in item for item in report["contract_failures"])
