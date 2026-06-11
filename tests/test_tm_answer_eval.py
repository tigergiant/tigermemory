from __future__ import annotations

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_answer_eval  # type: ignore[import-not-found]


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
