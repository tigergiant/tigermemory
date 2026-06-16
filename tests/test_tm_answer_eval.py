from __future__ import annotations

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_answer_eval  # type: ignore[import-not-found]
import tm_answer_funnel_compare  # type: ignore[import-not-found]
import tm_answer_miss_ledger  # type: ignore[import-not-found]


class _DummyWikiMap:
    def __init__(self, hits: list[dict[str, object]]):
        self._hits = hits

    def map_recall(self, *args, **kwargs):
        return list(self._hits)


def test_eval_case_passes_run_id_to_memory_answer(monkeypatch):
    calls: dict[str, object] = {}

    def fake_memory_answer_core(query: str, **kwargs):
        calls["query"] = query
        calls.update(kwargs)
        return {
            "status": "not_found",
            "answer": "",
            "summary": "none",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-1",
            "trace": None,
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.eval_case(
        {"id": "c1", "query": "missing thing", "expected_status": "not_found"},
        run_id="eval-run-1",
    )

    assert calls["query"] == "missing thing"
    assert calls["include_trace"] is False
    assert calls["run_id"] == "eval-run-1"
    assert result["run_id"] == "eval-run-1"


def test_tool_cli_shim_invokes_eval_main(tmp_path):
    cases = tmp_path / "empty-cases.jsonl"
    cases.write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "tm_answer_eval.py"),
            "eval",
            "--cases",
            str(cases),
            "--json",
            "--compact",
            "--run-id",
            "shim-smoke",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    report = json.loads(result.stdout)
    assert report["run_id"] == "shim-smoke"
    assert report["case_count"] == 0
    assert result.stdout.strip()


def test_diagnose_case_classifies_missing_expected_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])

    def fake_memory_answer_core(query: str, **kwargs):
        assert kwargs["include_trace"] is True
        assert kwargs["write_trace"] is False
        return {
            "status": "not_found",
            "answer": "",
            "summary": "none",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-1",
            "trace": {"calls": [], "evidence_gate": []},
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "missing",
            "query": "private hook architecture",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/not-created.md"],
        },
        run_id="diag-test",
    )

    assert result["failure_layer"] == "missing_knowledge"
    assert result["missing_expected_paths"] == ["wiki/systems/not-created.md"]


def test_diagnose_case_classifies_evidence_selection_miss(tmp_path, monkeypatch):
    expected = tmp_path / "wiki" / "systems" / "answer-contract.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("# Answer Contract\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)

    hit = {
        "path": "wiki/systems/answer-contract.md",
        "title": "Answer Contract",
        "snippet": "alpha",
        "score": 1.0,
    }
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [hit])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [hit])

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "not_found",
            "answer": "",
            "summary": "none",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-2",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{
                    "path": "wiki/systems/answer-contract.md",
                    "keep": True,
                    "reason": "kept",
                }],
            },
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "selection",
            "query": "answer contract alpha",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/answer-contract.md"],
        },
        run_id="diag-test",
    )

    assert result["failure_layer"] == "evidence_selection_miss"
    assert result["lexical_rank"] == 1
    assert result["hybrid_rank"] == 1
    assert result["evidence_gate_rank"] == 1
    assert result["answer_evidence_rank"] is None


def test_diagnose_case_counts_planner_compensated_hit_as_pass(tmp_path, monkeypatch):
    expected = tmp_path / "wiki" / "systems" / "planner-compensated.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("# Planner Compensated\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "ok",
            "answer": "alpha",
            "summary": "alpha",
            "claims": [{"id": "c1", "text": "alpha", "support": ["e1"]}],
            "evidence": [{"id": "e1", "path": "wiki/systems/planner-compensated.md", "excerpt": "alpha"}],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-planner",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{
                    "path": "wiki/systems/planner-compensated.md",
                    "keep": True,
                    "reason": "kept by planner",
                }],
            },
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "planner-compensated",
            "query": "natural question alpha",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/planner-compensated.md"],
            "must_contain": ["alpha"],
        },
        run_id="diag-test",
    )

    assert result["passed"] is True
    assert result["failure_layer"] == "ok"
    assert result["raw_retrieval_hit"] is False
    assert result["planner_compensated_hit"] is True
    summary = tm_answer_eval.summarize_diagnosis([result])
    assert summary["planner_compensated_hit"] == 1


def test_diagnose_case_counts_recommendation_candidate_and_evidence_hit(tmp_path, monkeypatch):
    expected = tmp_path / "wiki" / "systems" / "recommended-protocol.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("# Recommended Protocol\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "ok",
            "answer": "alpha",
            "summary": "alpha",
            "claims": [{"id": "c1", "text": "alpha", "support": ["e1"]}],
            "evidence": [{"id": "e1", "path": "wiki/systems/recommended-protocol.md", "excerpt": "alpha"}],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-recommendation",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{
                    "path": "wiki/systems/recommended-protocol.md",
                    "selected": True,
                    "keep": True,
                    "reason": "kept recommendation",
                }],
                "related_evidence_candidates": {
                    "status": "ok",
                    "candidate_count": 2,
                    "candidates": [
                        {"path": "wiki/systems/other.md"},
                        {"path": "wiki/systems/recommended-protocol.md"},
                    ],
                },
                "recommendation_boosted_candidates": {
                    "status": "ok",
                    "candidate_count": 1,
                    "accepted_count": 1,
                    "rejected_count": 0,
                    "candidates": [{
                        "path": "wiki/systems/recommended-protocol.md",
                        "action": "accepted_to_evidence",
                        "gate_outcome": "evidence_gate_passed",
                    }],
                },
            },
            "related_evidence_candidates": [{"path": "wiki/systems/recommended-protocol.md"}],
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "recommendation-hit",
            "query": "natural recommendation alpha",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/recommended-protocol.md"],
            "must_contain": ["alpha"],
        },
        run_id="diag-test",
    )

    assert result["passed"] is True
    assert result["recommendation_candidate_rank"] == 2
    assert result["recommendation_candidate_hit@5"] is True
    assert result["recommendation_evidence_hit"] is True

    summary = tm_answer_eval.summarize_diagnosis([result])
    assert summary["recommendation_candidate_hit@5"] == 1
    assert summary["recommendation_candidate_hit@5_rate"] == 1.0
    assert summary["recommendation_evidence_hit"] == 1
    assert summary["recommendation_evidence_hit_rate"] == 1.0


def test_diagnose_case_buckets_map_top10_not_in_gate(tmp_path, monkeypatch):
    expected = tmp_path / "wiki" / "systems" / "bridge-target.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("# Bridge Target\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])
    monkeypatch.setattr(
        tm_answer_eval,
        "tm_llm_wiki_map",
        _DummyWikiMap([{"path": "wiki/systems/bridge-target.md", "score": 99.0}]),
    )

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "not_found",
            "answer": "",
            "summary": "",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-map-not-gated",
            "trace": {"calls": [{"primary_scope": "wiki"}], "evidence_gate": []},
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "map-not-gated",
            "query": "bridge alpha",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/bridge-target.md"],
        },
        run_id="diag-test",
    )

    assert result["map_rank"] == 1
    assert result["map_rank_band"] == "top10"
    assert result["map_hit_but_evidence_miss"] is True
    assert result["map_bridge_bucket"] == "map_top10_not_in_gate"
    assert result["evidence_gate_reason_category"] == "not_in_gate"

    summary = tm_answer_eval.summarize_diagnosis([result])
    assert summary["map_hit_but_evidence_miss"] == 1
    assert summary["map_bridge_bucket_counts"] == {"map_top10_not_in_gate": 1}
    assert summary["map_leak_bucket_counts"] == {"map_top10_not_in_gate": 1}
    assert summary["map_leak_reason_category_counts"] == {"not_in_gate": 1}


def test_diagnose_case_buckets_map_gate_rejected(tmp_path, monkeypatch):
    expected = tmp_path / "wiki" / "systems" / "gate-target.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("# Gate Target\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])
    monkeypatch.setattr(
        tm_answer_eval,
        "tm_llm_wiki_map",
        _DummyWikiMap([{"path": "wiki/systems/gate-target.md", "score": 99.0}]),
    )

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "not_found",
            "answer": "",
            "summary": "",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-gate-rejected",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{
                    "path": "wiki/systems/gate-target.md",
                    "keep": False,
                    "reason": "low relevance",
                }],
            },
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "map-gate-rejected",
            "query": "gate alpha",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/gate-target.md"],
        },
        run_id="diag-test",
    )

    assert result["evidence_gate_rank"] == 1
    assert result["evidence_gate_rejected_rank"] == 1
    assert result["map_bridge_bucket"] == "evidence_gate_rejected"
    assert result["evidence_gate_keep"] is False
    assert result["evidence_gate_reason_category"] == "relevance"

    summary = tm_answer_eval.summarize_diagnosis([result])
    assert summary["map_leak_bucket_counts"] == {"evidence_gate_rejected": 1}
    assert summary["map_leak_reason_category_counts"] == {"relevance": 1}
    assert summary["evidence_gate_reason_category_counts"] == {"relevance": 1}


def test_diagnose_case_buckets_trace_missing(tmp_path, monkeypatch):
    expected = tmp_path / "wiki" / "systems" / "trace-target.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("# Trace Target\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])
    monkeypatch.setattr(
        tm_answer_eval,
        "tm_llm_wiki_map",
        _DummyWikiMap([{"path": "wiki/systems/trace-target.md", "score": 99.0}]),
    )

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "not_found",
            "answer": "",
            "summary": "",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-missing",
            "trace": None,
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "trace-missing",
            "query": "trace alpha",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/trace-target.md"],
        },
        run_id="diag-test",
    )

    assert result["trace_present"] is False
    assert result["map_bridge_bucket"] == "trace_missing"
    assert result["evidence_gate_reason_category"] == "not_in_gate"


def test_diagnose_case_flags_mixed_partition_evidence(tmp_path, monkeypatch):
    for rel in [
        "wiki/systems/answer-contract.md",
        "wiki/operations/wrong-contract.md",
    ]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Contract\nalpha", encoding="utf-8")
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)

    hit = {
        "path": "wiki/systems/answer-contract.md",
        "title": "Answer Contract",
        "snippet": "alpha",
        "score": 1.0,
    }
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [hit])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [hit])

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "ok",
            "answer": "alpha",
            "summary": "alpha",
            "claims": [{"id": "c1", "text": "alpha", "support": ["e1"]}],
            "evidence": [
                {"id": "e1", "path": "wiki/systems/answer-contract.md", "excerpt": "alpha"},
                {"id": "e2", "path": "wiki/operations/wrong-contract.md", "excerpt": "alpha"},
            ],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-3",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{
                    "path": "wiki/systems/answer-contract.md",
                    "keep": True,
                    "reason": "kept",
                }],
            },
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "mixed",
            "query": "answer contract alpha",
            "expected_status": "ok",
            "expected_partition": "systems",
            "expected_evidence_paths": ["wiki/systems/answer-contract.md"],
            "must_contain": ["alpha"],
        },
        run_id="diag-test",
    )

    assert result["failure_layer"] == "boundary_violation"
    assert result["outside_partition_paths"] == ["wiki/operations/wrong-contract.md"]


def test_diagnose_case_carries_query_intent_bucket_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_answer_eval.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [])

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "ok",
            "answer": "alpha",
            "summary": "alpha",
            "claims": [{"id": "c1", "text": "alpha", "support": ["e1"]}],
            "evidence": [{"id": "e1", "path": "wiki/systems/contract.md", "excerpt": "alpha"}],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-bucket",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{
                    "path": "wiki/systems/contract.md",
                    "keep": True,
                    "reason": "kept",
                }],
            },
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "bucket-default",
            "query": "query intent bucket example",
            "expected_status": "ok",
            "expected_evidence_paths": ["wiki/systems/contract.md"],
            "must_contain": ["alpha"],
        },
        run_id="diag-test",
    )

    assert result["query_intent_bucket"] == "unspecified"


def test_summarize_diagnosis_reports_query_intent_bucket_answer_evidence_metrics():
    summary = tm_answer_eval.summarize_diagnosis([
        {
            "passed": True,
            "query_intent_bucket": "workflow_fact",
            "expected_evidence_paths": ["wiki/systems/contract.md"],
            "answer_evidence_rank": 1,
        },
        {
            "passed": False,
            "query_intent_bucket": "topic_locator",
            "expected_evidence_paths": ["wiki/systems/rule.md"],
            "answer_evidence_rank": None,
        },
        {
            "passed": False,
            "query_intent_bucket": "negative",
            "expected_evidence_paths": ["wiki/systems/unknown.md"],
            "answer_evidence_rank": None,
        },
        {
            "passed": True,
            "query_intent_bucket": "unspecified",
            "expected_evidence_paths": [],
            "answer_evidence_rank": None,
        },
    ])

    assert summary["case_count"] == 4
    assert summary["case_count_by_query_intent_bucket"] == {
        "workflow_fact": 1,
        "topic_locator": 1,
        "negative": 1,
        "unspecified": 1,
    }
    assert summary["expected_path_case_count_by_bucket"] == {
        "workflow_fact": 1,
        "topic_locator": 1,
        "negative": 1,
    }
    assert summary["answer_evidence_hit_by_bucket"] == {
        "workflow_fact": 1,
        "topic_locator": 0,
        "negative": 0,
    }


def test_diagnose_surrogate_paths_do_not_change_ground_truth_score(monkeypatch):
    surrogate = "wiki/operations/tmp-artifact-tombstones.md"
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki", lambda *a, **k: [{"path": surrogate}])
    monkeypatch.setattr(tm_answer_eval.tm_core, "search_wiki_hybrid", lambda *a, **k: [{"path": surrogate}])
    monkeypatch.setattr(tm_answer_eval, "tm_llm_wiki_map", _DummyWikiMap([{"path": surrogate}]))

    def fake_memory_answer_core(query: str, **kwargs):
        return {
            "status": "ok",
            "answer": "The temporary research pack has a tombstone summary.",
            "summary": "surrogate summary",
            "claims": [],
            "evidence": [{"id": "e1", "path": surrogate, "excerpt": "P4 and P5 are deferred."}],
            "warnings": [],
            "run_id": kwargs.get("run_id"),
            "trace_id": "trace-surrogate",
            "trace": {
                "calls": [{"primary_scope": "wiki"}],
                "evidence_gate": [{"path": surrogate, "keep": True, "selected": True}],
            },
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)

    result = tm_answer_eval.diagnose_case(
        {
            "id": "tmp-locator",
            "query": "temporary plan location",
            "expected_status": "ok",
            "expected_evidence_paths": [".tmp/private-research-plan.md"],
            "acceptable_surrogate_paths": [surrogate],
            "query_intent_bucket": "topic_locator",
        },
        run_id="diag-surrogate",
    )
    summary = tm_answer_eval.summarize_diagnosis([result])

    assert result["passed"] is False
    assert result["answer_evidence_rank"] is None
    assert result["surrogate_evidence_rank"] == 1
    assert result["surrogate_evidence_hit"] is True
    assert summary["answer_evidence_hit"] == 0
    assert summary["surrogate_path_case_count"] == 1
    assert summary["surrogate_evidence_hit"] == 1


def test_diagnose_compact_redacts_query(tmp_path, monkeypatch, capsys):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({
            "id": "secret-case",
            "query": "do not leak this query",
            "expected_status": "ok",
            "case_source": "real_failure",
        }) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(tm_answer_eval, "diagnose_case", lambda case, **kwargs: {
        "id": case["id"],
        "query": case["query"],
        "passed": False,
        "failure_layer": "answer_synthesis_miss",
        "failure_reasons": ["status_ok"],
        "status": "not_found",
        "expected_status": "ok",
        "case_source": "real_failure",
        "run_id": kwargs.get("run_id"),
    })

    args = type("Args", (), {
        "cases": str(cases),
        "allow_paper_seed_tmp": False,
        "limit": None,
        "run_id": "diag-compact",
        "write_trace": False,
        "top_k_probe": 10,
        "compact": True,
        "json": True,
    })()
    assert tm_answer_eval.cmd_diagnose(args) == 0
    out = capsys.readouterr().out
    assert "do not leak this query" not in out
    report = json.loads(out)
    assert report["run_id"] == "diag-compact"
    assert report["failure_layer_counts"]["answer_synthesis_miss"] == 1


def test_memory_answer_diagnosis_fixture_has_100_unique_cases():
    cases = tm_answer_eval.load_cases(
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_diagnosis_100.jsonl")
    )

    assert len(cases) == 100
    assert len({case["id"] for case in cases}) == 100
    assert {case["diagnosis_category"] for case in cases} == {
        "missing_canonical_knowledge",
        "lexical_wiki_recall",
        "semantic_hybrid_recall",
        "ranking_topk",
        "evidence_expansion_trim",
        "answer_synthesis",
        "freshness_stale_guard",
        "partition_topic_boundary",
        "runtime_service_grounding",
        "actionability_gap",
    }
    tmp_locator = next(case for case in cases if case["id"] == "p35-01-07")
    assert tmp_locator["expected_evidence_paths"] == [
        ".tmp/ai-radar-memory-research-20260609/notes/memory-answer-optimization-plan-v2.md"
    ]
    assert "wiki/operations/tmp-artifact-tombstones.md" in tmp_locator["acceptable_surrogate_paths"]


def test_memory_answer_p310_holdout_fixture_is_independent():
    baseline = tm_answer_eval.load_cases(
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_diagnosis_100.jsonl")
    )
    holdout = tm_answer_eval.load_cases(
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_holdout_p310.jsonl")
    )

    assert len(holdout) == 30
    assert len({case["id"] for case in holdout}) == 30
    assert not ({case["id"] for case in baseline} & {case["id"] for case in holdout})
    assert {case["holdout_domain"] for case in holdout} == {
        "new_agent_tigermemory",
        "new_agent_tigerinvest",
        "chatgpt_ipfb",
        "random_negative",
    }
    assert all(not str(case["id"]).startswith("p35-") for case in holdout)


def test_memory_answer_p315_holdout_fixture_is_independent():
    baseline = tm_answer_eval.load_cases(
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_diagnosis_100.jsonl")
    )
    p310_holdout = tm_answer_eval.load_cases(
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_holdout_p310.jsonl")
    )
    p315_holdout = tm_answer_eval.load_cases(
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_holdout_p315.jsonl")
    )

    p315_ids = {case["id"] for case in p315_holdout}
    assert len(p315_holdout) == 29
    assert len(p315_ids) == 29
    assert not ({case["id"] for case in baseline} & p315_ids)
    assert not ({case["id"] for case in p310_holdout} & p315_ids)
    assert {case["holdout_domain"] for case in p315_holdout} == {
        "new_agent_tigermemory",
        "new_agent_tigerinvest",
        "chatgpt_ipfb",
        "production_dev",
        "memory_answer",
        "random_negative",
    }
    assert {case["query_intent_bucket"] for case in p315_holdout} >= {
        "topic_locator",
        "workflow_fact",
        "tail_detail",
        "negative",
    }
    assert all(not str(case["id"]).startswith(("p35-", "p310-")) for case in p315_holdout)


def test_query_intent_bucket_field_is_present_and_valid_in_answer_fixtures():
    allowed = {
        "topic_locator",
        "tail_detail",
        "workflow_fact",
        "negative",
        "unspecified",
    }
    for fixture in [
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_diagnosis_100.jsonl"),
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_holdout_p310.jsonl"),
        str(REPO_ROOT / "tests" / "fixtures" / "memory_answer_holdout_p315.jsonl"),
    ]:
        cases = tm_answer_eval.load_cases(fixture)
        assert all(case.get("query_intent_bucket") in allowed for case in cases)


def test_load_cases_rejects_invalid_query_intent_bucket(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({
            "id": "bad-bucket",
            "query": "bad bucket",
            "query_intent_bucket": "topic-locator",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    try:
        tm_answer_eval.load_cases(str(cases))
    except ValueError as exc:
        assert "invalid query_intent_bucket" in str(exc)
    else:
        raise AssertionError("expected invalid query_intent_bucket to be rejected")


def test_funnel_compact_summary_includes_query_intent_bucket_metrics():
    report = {
        "case_count": 2,
        "passed": 1,
        "expected_path_case_count": 2,
        "answer_evidence_hit": 1,
        "case_count_by_query_intent_bucket": {"workflow_fact": 1, "topic_locator": 1},
        "expected_path_case_count_by_bucket": {"workflow_fact": 1, "topic_locator": 1},
        "answer_evidence_hit_by_bucket": {"workflow_fact": 1, "topic_locator": 0},
    }

    compact = tm_answer_funnel_compare._compact_summary(report)

    assert compact["case_count_by_query_intent_bucket"] == {"workflow_fact": 1, "topic_locator": 1}
    assert compact["expected_path_case_count_by_bucket"] == {"workflow_fact": 1, "topic_locator": 1}
    assert compact["answer_evidence_hit_by_bucket"] == {"workflow_fact": 1, "topic_locator": 0}


def test_funnel_compare_matrix_env_is_explicit():
    experimental_envs = {
        "TM_EMBED_SUMMARY_WEIGHT",
        "TM_HYBRID_MAP_ARM",
        "TM_ANSWER_WIKI_MAP_BRIDGE",
        "TM_ANSWER_WIKI_MAP",
    }
    for name, env in tm_answer_funnel_compare.MATRICES.items():
        assert set(env) == experimental_envs, name

    assert tm_answer_funnel_compare.MATRICES["summary_off"] == {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    }
    assert tm_answer_funnel_compare.MATRICES["summary_on"] == {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    }
    assert tm_answer_funnel_compare.MATRICES["summary_on_map_arm"] == {
        "TM_EMBED_SUMMARY_WEIGHT": "0.98",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    }
    assert tm_answer_funnel_compare.MATRICES["production"] == {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "0",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    }
    assert tm_answer_funnel_compare.MATRICES["map_arm"] == {
        "TM_EMBED_SUMMARY_WEIGHT": "0",
        "TM_HYBRID_MAP_ARM": "1",
        "TM_ANSWER_WIKI_MAP_BRIDGE": "0",
        "TM_ANSWER_WIKI_MAP": "0",
    }
    assert tm_answer_funnel_compare.MATRICES["bridge"]["TM_ANSWER_WIKI_MAP_BRIDGE"] == "1"


def test_miss_ledger_omits_raw_query_and_groups_decision_bucket():
    report = {
        "run_id": "unit",
        "case_count": 1,
        "expected_path_case_count": 1,
        "answer_evidence_hit": 0,
        "results": [{
            "id": "case-1",
            "query": "do not leak this raw query",
            "case_source": "real_failure",
            "case_source_ref": "trace:1",
            "expected_evidence_paths": ["wiki/systems/a.md"],
            "checks": {"evidence_hit": False},
            "map_bridge_bucket": "map_top10_not_in_gate",
            "evidence_gate_reason_category": "not_in_gate",
            "query_intent_bucket": "workflow_fact",
            "freshness_mode": "current",
            "prompt_budget_truncated": True,
        }],
    }

    ledger = tm_answer_miss_ledger.build_ledger(report)
    dumped = json.dumps(ledger, ensure_ascii=False)

    assert ledger["miss_count"] == 1
    assert ledger["decision_bucket_counts"] == {"candidate_handoff_not_in_gate": 1}
    assert ledger["entries"][0]["query_hash"]
    assert "do not leak this raw query" not in dumped


def test_miss_ledger_supports_compact_eval_failures_without_raw_query():
    report = {
        "run_id": "compact",
        "case_count": 2,
        "expected_evidence_case_count": 2,
        "expected_evidence_hit": 1,
        "failures": [
            {
                "id": "miss-1",
                "query": "also do not leak this compact query",
                "expected_evidence_paths": ["C:/Users/Giant/.codex/skills/delegated-dev-workflow/SKILL.md"],
                "expected_evidence_hit": False,
                "status": "ok",
            },
            {
                "id": "must-only",
                "query": "not an evidence miss",
                "expected_evidence_paths": ["wiki/systems/ok.md"],
                "expected_evidence_hit": True,
                "must_contain_hit": False,
            },
            {
                "id": "wiki-miss",
                "query": "wiki miss without diagnose fields",
                "expected_evidence_paths": ["wiki/systems/missing.md"],
                "expected_evidence_hit": False,
                "status": "not_found",
            },
        ],
    }

    ledger = tm_answer_miss_ledger.build_ledger(report)
    dumped = json.dumps(ledger, ensure_ascii=False)

    assert ledger["miss_count"] == 2
    assert ledger["expected_path_case_count"] == 2
    assert ledger["answer_evidence_hit"] == 1
    assert ledger["decision_bucket_counts"] == {
        "compact_eval_needs_diagnose": 1,
        "source_policy_or_surrogate_needed": 1,
    }
    assert ledger["entries"][0]["case_id"] == "miss-1"
    assert "also do not leak this compact query" not in dumped
    assert "wiki miss without diagnose fields" not in dumped
