from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-answer" / "src"))

import tm_answer  # type: ignore[import-not-found]
import tigermemory_answer.eval as tm_answer_eval  # type: ignore[import-not-found]


def _search_result(hit: dict | None = None) -> dict:
    hits = [hit] if hit else []
    return {
        "query": "q",
        "scope": "wiki",
        "strategy": "grouped-intent-budget-v1",
        "primary_scope": "wiki",
        "primary_results": hits,
        "groups": {"wiki": hits},
        "warnings": [],
    }


def test_memory_answer_core_expands_evidence_and_generates_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/agent-write-toolkit.md",
            "title": "Agent 写入工具 tm_io",
            "snippet": "tm_io write_memory",
            "score": 10.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Use write_memory through the toolkit.",
            "summary": "Answered from toolkit evidence.",
            "claims": [{"id": "c1", "text": "write_memory is documented.", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core("verify_memory_id write_memory toolkit", scope="wiki", run_id="unit-run-1")

    assert result["status"] == "ok"
    assert result["run_id"] == "unit-run-1"
    assert result["claims"][0]["support"] == ["e1"]
    assert result["evidence"][0]["id"] == "e1"
    assert result["evidence"][0]["authority"] >= 90.0
    assert result["evidence"][0]["source_role"] == "canonical_wiki"
    assert result["trace_id"]
    assert result["trace"]["run_id"] == "unit-run-1"
    assert result["trace"]["query_class"] == "recall"
    assert result["trace"]["planner"]["intent"] == "recall"
    assert result["trace"]["planner"]["freshness_mode"] == "not_applicable"
    assert [item["role"] for item in result["trace"]["planner"]["subquery_roles"]] == ["primary", "expansion"]
    assert len(result["trace"]["expanded_queries"]) == 2
    assert [call["tool"] for call in result["trace"]["calls"]] == ["search_tigermemory", "search_tigermemory", "DeepSeek"]
    assert (tmp_path / "trace.jsonl").exists()
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert trace_row["run_id"] == "unit-run-1"
    assert trace_row["trace"]["planner"]["intent"] == "recall"
    assert trace_row["trace"]["planner"]["source_budgets"]["wiki"] == 3


def test_memory_answer_core_not_found_skips_llm(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("no such memory", scope="wiki", include_trace=False, run_id="unit-run-hidden")

    assert result["status"] == "not_found"
    assert result["evidence"] == []
    assert result["trace"] is None
    assert calls == []
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert trace_row["run_id"] == "unit-run-hidden"
    assert "query" not in trace_row
    assert trace_row["query_hash"]
    assert trace_row["trace"]["run_id"] == "unit-run-hidden"
    assert trace_row["trace"]["query_class"] == "recall"
    assert trace_row["trace"]["planner"]["intent"] == "recall"
    assert trace_row["trace"]["planner"]["freshness_mode"] == "not_applicable"
    assert trace_row["trace"]["planner"]["source_budgets"]["wiki"] == 3
    assert "expanded_queries" not in trace_row["trace"]
    assert trace_row["trace"]["expanded_query_hashes"]
    assert "query" not in trace_row["trace"]["calls"][0]
    assert trace_row["trace"]["calls"][0]["query_hash"]


def test_decide_injection_eligibility_table():
    now = tm_answer.datetime.datetime(2026, 5, 20, tzinfo=tm_answer.datetime.timezone.utc)

    assert tm_answer.decide_injection_eligibility(
        {"source": "wiki", "path": "wiki/systems/page.md"},
        now=now,
    ) == {
        "injection_eligible": False,
        "injection_reason": "canonical_wiki_evidence_only",
    }
    assert tm_answer.decide_injection_eligibility(
        {"source": "onboarding", "title": "Agent Onboarding Snapshot (30s)"},
        now=now,
    )["injection_eligible"] is True
    assert tm_answer.decide_injection_eligibility(
        {"source": "onboarding", "title": "Agent Onboarding Snapshot (full)"},
        now=now,
    )["injection_eligible"] is False
    assert tm_answer.decide_injection_eligibility(
        {
            "source": "mem0",
            "created_at": "2026-05-01T00:00:00+00:00",
            "score_breakdown": {"route_decision": "mem0"},
        },
        now=now,
    ) == {
        "injection_eligible": True,
        "injection_reason": "recent_atomic_memory",
    }
    assert tm_answer.decide_injection_eligibility(
        {
            "source": "mem0",
            "created_at": "2025-12-01T00:00:00+00:00",
            "score_breakdown": {"route_decision": "mem0"},
        },
        now=now,
    ) == {
        "injection_eligible": False,
        "injection_reason": "low_quality_or_stale",
    }


def test_trim_evidence_for_prompt_enforces_total_excerpt_budget():
    evidence = [
        {"id": "e1", "excerpt": "abcde"},
        {"id": "e2", "excerpt": "fghij"},
    ]

    trimmed, warnings, metrics = tm_answer.trim_evidence_for_prompt(
        evidence,
        max_chars=7,
        query="abc ghi",
        return_metrics=True,
    )

    assert [item["excerpt"] for item in trimmed] == ["abcde", "fg"]
    assert warnings == ["prompt_budget_truncated=true"]
    assert metrics["chars_before"] == 10
    assert metrics["chars_after"] == 7
    assert metrics["truncated_evidence_ids"] == ["e2"]
    assert metrics["retained_evidence_ids"] == ["e1", "e2"]
    assert metrics["key_term_retention"]["terms"] == ["abc", "ghi"]
    assert metrics["key_term_retention"]["retained_terms"] == ["abc"]
    assert metrics["key_term_retention"]["missing_terms"] == ["ghi"]
    assert metrics["key_term_retention"]["retention_rate"] == 0.5


def test_memory_answer_core_can_disable_trace_write(monkeypatch, tmp_path):
    calls = []
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(tm_answer, "TRACE_LOG", trace_path)
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("sensitive query", scope="wiki", write_trace=False)

    assert result["status"] == "not_found"
    assert not trace_path.exists()
    assert calls == []


def test_write_result_trace_sanitizes_full_row_and_trace_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    canary = "canaryrawquerytoken_summary_only_20260609"
    secret = "bearer abcdefghijklmnopqrstuvwxyz"
    result = {
        "status": "ok",
        "answer": f"{secret} answer {canary}",
        "summary": f"safe summary {canary}",
        "warnings": [f"warning echoed {canary}"],
        "run_id": "trace-sanitize",
        "trace_id": "trace-1",
        "claims": [{"id": "c1", "text": f"secret claim {canary}", "support": ["e1"], "confidence": 0.9}],
        "evidence": [{
            "id": "e1",
            "source": "mem0",
            "path": "mem0:secret",
            "title": "systems / codex",
            "excerpt": f"{secret} evidence {canary}",
            "matched_terms": ["bearer", "abcdefghijklmnopqrstuvwxyz", canary],
        }],
    }
    trace = {
        "run_id": "trace-sanitize",
        "evidence_gate": [{
            "candidate_id": "cand1",
            "snippet": f"{secret} snippet {canary}",
            "excerpt": f"{secret} excerpt {canary}",
            "trace_snippet": f"{secret} trace snippet {canary}",
            "trace_excerpt": f"{secret} trace excerpt {canary}",
            "trace_content": f"{secret} trace content {canary}",
            "trace_text": f"{secret} trace text {canary}",
            "trace_answer": f"{secret} trace answer {canary}",
            "matched_terms": ["bearer", "abcdefghijklmnopqrstuvwxyz", canary],
        }],
    }

    tm_answer._write_result_trace(result, trace, f"{secret} {canary}")

    row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    payload = json.dumps(row, ensure_ascii=False)
    assert "bearer" not in payload
    assert "abcdefghijklmnopqrstuvwxyz" not in payload
    assert canary not in payload
    assert "matched_terms" not in payload
    assert "summary" not in row
    assert "warnings" not in row
    assert row["summary_chars"] > 0
    assert row["summary_hash"]
    assert row["warning_count"] == 1
    assert row["warning_chars"] > 0
    assert row["warning_hashes"]
    assert "excerpt" not in row["evidence"][0]
    gate = row["trace"]["evidence_gate"][0]
    assert gate["snippet_chars"] > 0
    assert gate["excerpt_chars"] > 0
    assert gate["trace_snippet_chars"] > 0
    assert gate["trace_excerpt_chars"] > 0
    assert gate["trace_content_chars"] > 0
    assert gate["trace_text_chars"] > 0
    assert gate["trace_answer_chars"] > 0
    assert "trace_snippet" not in gate
    assert "trace_excerpt" not in gate
    assert "trace_content" not in gate
    assert "trace_text" not in gate
    assert "trace_answer" not in gate
    assert gate["matched_term_count"] == 3
    assert gate["matched_term_hashes"]


def test_memory_answer_core_trims_evidence_before_llm(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:long",
            "title": "systems / codex",
            "snippet": "## Heading\n\n" + ("needle " * 100),
            "created_at": "2026-05-01T00:00:00+00:00",
            "updated_at": "2026-05-02T00:00:00+00:00",
            "score": 1.0,
        }),
    )

    def fake_llm(_query, evidence):
        captured["evidence"] = evidence
        return True, {
            "status": "ok",
            "answer": "Trimmed answer.",
            "summary": "Trimmed.",
            "claims": [{"id": "c1", "text": "trimmed", "support": ["e1"], "confidence": 0.8}],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core(
        "needle",
        scope="mem0",
        evidence_char_budget=20,
        run_id="budget-test",
    )

    evidence = captured["evidence"][0]
    assert evidence["id"] == "e1"
    assert evidence["source"] == "mem0"
    assert evidence["path"] == "mem0:long"
    assert evidence["title"] == "systems / codex"
    assert evidence["created_at"] == "2026-05-01T00:00:00+00:00"
    assert evidence["updated_at"] == "2026-05-02T00:00:00+00:00"
    assert evidence["matched_terms"] == ["needle"]
    assert evidence["validity"] == "current"
    assert len(evidence["excerpt"]) <= 20
    assert result["trace"]["prompt_budget_truncated"] is True
    assert result["trace"]["trim_metrics"]["chars_before"] > result["trace"]["trim_metrics"]["chars_after"]
    assert result["trace"]["trim_metrics"]["truncated_evidence_ids"] == ["e1"]
    assert result["trace"]["trim_metrics"]["retained_evidence_ids"] == ["e1"]
    retention = result["trace"]["trim_metrics"]["key_term_retention"]
    assert "terms" not in retention
    assert "retained_terms" not in retention
    assert "missing_terms" not in retention
    assert retention["term_count"] == 1
    assert retention["retained_count"] == 1
    assert retention["retention_rate"] == 1.0
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    stored_payload = json.dumps(trace_row, ensure_ascii=False)
    assert "needle" not in stored_payload
    assert "matched_terms" not in stored_payload
    assert "excerpt" not in trace_row["evidence"][0]
    assert "matched_term_hashes" in stored_payload
    assert "prompt_budget_truncated=true" in result["warnings"]


def test_memory_answer_conflict_scan_uses_untrimmed_evidence(monkeypatch, tmp_path):
    hits = [
        {
            "source": "mem0",
            "path": "mem0:passed",
            "title": "runtime status",
            "snippet": "service " + ("x" * 40) + " passed",
            "score": 1.0,
        },
        {
            "source": "mem0",
            "path": "mem0:unavailable",
            "title": "runtime status",
            "snippet": "service " + ("y" * 40) + " unavailable",
            "score": 1.0,
        },
    ]
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: {
            "query": "service conflict",
            "scope": "mem0",
            "strategy": "grouped-intent-budget-v1",
            "primary_scope": "mem0",
            "primary_results": hits,
            "groups": {"mem0": hits},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda *_args: (_ for _ in ()).throw(AssertionError("conflict path must not call LLM")),
    )

    result = tm_answer.memory_answer_core(
        "service conflict",
        scope="mem0",
        evidence_char_budget=10,
        run_id="conflict-budget-test",
    )

    assert result["status"] == "conflict"
    assert result["trace"]["prompt_budget_truncated"] is True
    assert result["evidence"][0]["excerpt"].endswith("passed")
    assert result["evidence"][1]["excerpt"].endswith("unavailable")


def test_expand_queries_reads_registry(monkeypatch, tmp_path):
    registry = tmp_path / "query_expansions.json"
    registry.write_text(
        json.dumps([{
            "id": "unit",
            "patterns": ["unit trigger"],
            "expansions": ["unit expanded target"],
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_answer, "QUERY_EXPANSION_REGISTRY", registry)

    assert "unit expanded target" in tm_answer.expand_queries("please use unit trigger")


def test_query_planner_llm_uses_budgeted_manifest_context(monkeypatch):
    target = {
        "path": "wiki/systems/memory-answer-development-plan.md",
        "title": "Memory Answer 开发计划",
        "aliases": "记忆问答 自然语言 检索规划",
    }
    noise = [
        {"path": f"wiki/systems/noise-{index}.md", "title": f"Noise {index}"}
        for index in range(120)
    ]
    monkeypatch.setattr(tm_answer, "_query_planner_manifest_pages", lambda: noise + [target])
    captured = {}

    def fake_deepseek(system_prompt, user_msg, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["payload"] = json.loads(user_msg)
        captured["kwargs"] = kwargs
        return True, {
            "retrieval_queries": ["memory answer natural language retrieval"],
            "evidence_terms": ["Memory Answer"],
            "path_hints": ["wiki/systems/memory-answer-development-plan.md"],
        }

    monkeypatch.setattr(tm_answer.tm_core, "_call_deepseek_json", fake_deepseek)

    ok, parsed = tm_answer._call_memory_query_planner_llm(
        "为什么记忆问答自然语言问题搜不到资料，应该看哪个开发计划",
        {
            "intent": "synthesis",
            "query_class": "synthesis",
            "freshness_mode": "not_applicable",
            "expanded_queries": ["为什么记忆问答自然语言问题搜不到资料，应该看哪个开发计划"],
            "source_budgets": {"wiki": 2},
        },
    )

    assert ok is True
    assert parsed["path_hints"] == ["wiki/systems/memory-answer-development-plan.md"]
    assert captured["kwargs"]["purpose"] == "memory_query_plan"
    manifest = captured["payload"]["manifest"]
    assert "candidate_pages" in manifest
    assert "pages" not in manifest
    assert manifest["page_count"] == 121
    assert len(manifest["candidate_pages"]) <= tm_answer.QUERY_PLANNER_CONTEXT_MAX_ITEMS
    assert manifest["candidate_pages"][0]["path"] == "wiki/systems/memory-answer-development-plan.md"
    manifest_payload = json.dumps(manifest, ensure_ascii=False).lower()
    assert "tests/fixtures" not in manifest_payload
    assert "expected_evidence_paths" not in manifest_payload
    assert "answer_key" not in manifest_payload


def test_plan_query_uses_deepseek_planner_for_general_rewrite(monkeypatch):
    query = "请帮我判断记忆问答系统为什么自然语言问题找不到对应资料"
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setattr(tm_answer, "_manifest_candidate_plan", lambda *_args, **_kwargs: ([], [], []))

    def fake_planner(actual_query, base_plan):
        assert actual_query == query
        assert base_plan["expanded_queries"][0] == query
        return True, {
            "intent": "synthesis",
            "retrieval_queries": ["memory answer natural recall"],
            "evidence_terms": ["memory_answer", "natural recall"],
            "path_hints": ["wiki/systems/memory-answer-development-plan.md"],
            "warnings": ["planner used metadata only"],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_query_planner_llm", fake_planner)

    plan = tm_answer.plan_query(query)

    assert plan["planner_source"] == "llm"
    assert plan["expanded_queries"][0] == query
    assert "memory answer natural recall" in plan["expanded_queries"]
    assert "wiki/systems/memory-answer-development-plan.md" in plan["expanded_queries"]
    assert plan["evidence_terms"] == ["memory_answer", "natural recall"]
    assert plan["planner_call"]["ok"] is True
    assert plan["planner_warnings"] == ["planner used metadata only"]


def test_plan_query_falls_back_when_deepseek_planner_fails(monkeypatch):
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setattr(
        tm_answer,
        "_manifest_candidate_plan",
        lambda *_args, **_kwargs: (["wiki/systems/fallback-plan.md"], ["Fallback Plan"], ["wiki/systems/fallback-plan.md"]),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_query_planner_llm",
        lambda *_args: (False, "offline"),
    )

    plan = tm_answer.plan_query("为什么自然语言问题找不到对应资料")

    assert plan["planner_source"] == "deterministic"
    assert plan["planner_call"]["ok"] is False
    assert plan["expanded_queries"][:2] == ["为什么自然语言问题找不到对应资料", "wiki/systems/fallback-plan.md"]
    assert plan["evidence_terms"] == ["Fallback Plan"]
    assert any("memory query planner failed" in warning for warning in plan["planner_warnings"])


def test_plan_query_prioritizes_manifest_candidate_before_generic_llm_probe(monkeypatch):
    query = "为什么自然语言问题应该先看记忆问答开发计划"
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setattr(
        tm_answer,
        "_manifest_candidate_plan",
        lambda *_args, **_kwargs: (
            ["wiki/systems/memory-answer-development-plan.md", "Memory Answer 开发计划"],
            ["Memory Answer 开发计划"],
            ["wiki/systems/memory-answer-development-plan.md"],
        ),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_query_planner_llm",
        lambda *_args: (True, {
            "retrieval_queries": ["tigermemory product vision"],
            "evidence_terms": ["产品愿景"],
            "path_hints": ["wiki/systems/tigermemory-product-vision.md"],
        }),
    )

    plan = tm_answer.plan_query(query)

    assert plan["planner_source"] == "llm"
    assert plan["expanded_queries"][:3] == [
        query,
        "wiki/systems/memory-answer-development-plan.md",
        "Memory Answer 开发计划",
    ]
    assert "tigermemory product vision" in plan["expanded_queries"]
    assert "Memory Answer 开发计划" in plan["evidence_terms"]


def test_memory_answer_core_uses_llm_planner_queries_and_terms(monkeypatch, tmp_path):
    query = "请用自然语言问一下系统资料为什么找不到"
    search_calls = []
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_manifest_candidate_plan", lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_query_planner_llm",
        lambda *_args: (True, {
            "retrieval_queries": ["planner target page"],
            "evidence_terms": ["semanticneedle"],
            "warnings": [],
        }),
    )

    def fake_search(actual_query, *_args, **_kwargs):
        search_calls.append(actual_query)
        if actual_query == "planner target page":
            return _search_result({
                "source": "wiki",
                "path": "wiki/systems/planner-target.md",
                "title": "Planner Target",
                "snippet": "semanticneedle grounded answer",
                "score": 10.0,
            })
        return _search_result()

    captured = {}

    def fake_answer_llm(_query, evidence):
        captured["evidence"] = evidence
        return True, {
            "status": "ok",
            "answer": "Planner found evidence.",
            "summary": "Found through planner.",
            "claims": [{"id": "c1", "text": "planner evidence", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", fake_search)
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_answer_llm)

    result = tm_answer.memory_answer_core(query, scope="wiki", run_id="planner-unit")

    assert result["status"] == "ok"
    assert search_calls == [query, "planner target page"]
    assert captured["evidence"][0]["matched_terms"] == ["semanticneedle"]
    assert [call["purpose"] for call in result["trace"]["calls"] if call.get("tool") == "DeepSeek"] == [
        "memory_query_plan",
        "memory_answer",
    ]
    stored_payload = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "semanticneedle" not in stored_payload
    assert "planner target page" not in stored_payload


def test_conflict_scan_reads_registry(monkeypatch, tmp_path):
    registry = tmp_path / "conflict_patterns.json"
    registry.write_text(
        json.dumps([{
            "id": "unit-status",
            "positive": ["unit-done"],
            "negative": ["unit-pending"],
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_answer, "CONFLICT_PATTERN_REGISTRY", registry)

    result = tm_answer.scan_conflicts(
        "unit conflict",
        [{"id": "e1", "title": "status", "excerpt": "unit-done and unit-pending"}],
        "conflict_audit",
    )

    assert result["conflict"] is True
    assert any(item["name"] == "unit-status" for item in result["conflicts"])


def test_best_excerpt_prefers_distinct_query_terms_over_repeats():
    text = "\n\n".join([
        "## P4 状态记录\n\ntrace trace trace trace trace trace trace trace trace",
        "## P2 完成报告\n\nP1 P2 trace replay summary failures 已完成。",
    ])

    excerpt = tm_answer._best_excerpt(text, "Memory Answer 开发计划 P1 P2 trace replay", "")

    assert "P2 完成报告" in excerpt
    assert excerpt.startswith("## P2 完成报告")


def test_memory_answer_core_drops_unsupported_claims(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:abc",
            "title": "systems / codex",
            "snippet": "durable memory record",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "One valid claim remains.",
            "summary": "Unsupported claim is removed.",
            "claims": [
                {"id": "c1", "text": "valid", "support": ["e1"], "confidence": 0.8},
                {"id": "c2", "text": "invalid", "support": ["e99"], "confidence": 0.8},
            ],
        }),
    )

    result = tm_answer.memory_answer_core("memory record", scope="mem0")

    assert result["status"] == "ok"
    assert [claim["id"] for claim in result["claims"]] == ["c1"]
    assert any("dropped unsupported claim" in warning for warning in result["warnings"])


def test_memory_answer_core_llm_failure_preserves_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/agent-write-toolkit.md",
            "title": "Agent 写入工具 tm_io",
            "snippet": "toolkit evidence",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda _q, _e: (False, "offline"))

    result = tm_answer.memory_answer_core("toolkit", scope="wiki")

    assert result["status"] == "error"
    assert result["answer"] == ""
    assert result["evidence"]
    assert any("LLM failed" in warning for warning in result["warnings"])


def test_memory_answer_core_redacts_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:secret",
            "title": "systems / codex",
            "snippet": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Token was Bearer abcdefghijklmnopqrstuvwxyz",
            "summary": "Secret redacted.",
            "claims": [{"id": "c1", "text": "Bearer abcdefghijklmnopqrstuvwxyz", "support": ["e1"], "confidence": 0.5}],
        }),
    )

    result = tm_answer.memory_answer_core("secret", scope="mem0")

    assert "[REDACTED]" in result["answer"]
    assert "[REDACTED]" in result["claims"][0]["text"]
    assert "[REDACTED]" in result["evidence"][0]["excerpt"]


def test_memory_answer_core_filters_weak_evidence_before_llm(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "lessons",
            "path": "wiki/self-evolution/lessons/2026-05-10-retrieval-eval-query-pollution.md",
            "title": "retrieval eval guard",
            "snippet": "generic tigermemory lesson",
            "score": 1.0,
        }),
    )
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("zzzz impossible 7f3b2c9d", scope="lessons")

    assert result["status"] == "not_found"
    assert result["evidence"] == []
    assert calls == []
    assert any("weak-evidence guard" in warning for warning in result["warnings"])


def test_memory_answer_core_current_state_ignores_obsolete_mem0_evidence(monkeypatch, tmp_path):
    captured = {}
    old_hit = {
        "source": "mem0",
        "path": "mem0:old",
        "title": "ops / agent-a",
        "snippet": "old state",
        "score": 1.0,
        "created_at": "2026-05-01T00:00:00+00:00",
    }
    new_hit = {
        "source": "mem0",
        "path": "mem0:new",
        "title": "ops / agent-a",
        "snippet": "new state",
        "score": 1.0,
        "created_at": "2026-05-10T00:00:00+00:00",
    }
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: {
            "query": "current memory state",
            "scope": "mem0",
            "strategy": "grouped-intent-budget-v1",
            "primary_scope": "mem0",
            "primary_results": [old_hit, new_hit],
            "groups": {"mem0": [old_hit, new_hit]},
            "warnings": [],
        },
    )

    def fake_llm(_query, evidence):
        captured["evidence"] = evidence
        return True, {
            "status": "ok",
            "answer": "new state",
            "summary": "current state resolved",
            "claims": [{"id": "c1", "text": "new", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("当前态 memory state", scope="mem0", run_id="freshness-current")

    assert result["status"] == "ok"
    assert result["trace"]["planner"]["freshness_mode"] == "current"
    assert len(captured["evidence"]) == 1
    assert captured["evidence"][0]["path"] == "mem0:new"
    assert captured["evidence"][0]["validity"] == "current"
    assert result["evidence"][0]["path"] == "mem0:new"
    assert result["trace"]["validity"]["state_counts"]["current"] == 1
    assert result["trace"]["validity"]["state_counts"]["obsolete_ignored"] == 1
    assert result["trace"]["stale_guard"]["counterevidence_ids"]
    assert any(item["validity"] == "obsolete_ignored" for item in result["trace"]["evidence_gate"])
    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "current memory state" not in trace_text
    trace_row = json.loads(trace_text.splitlines()[-1])
    assert "query" not in trace_row["trace"]["validity"]
    assert trace_row["trace"]["validity"]["query_hash"]


def test_memory_answer_core_historical_query_keeps_older_mem0_evidence(monkeypatch, tmp_path):
    captured = {}
    old_hit = {
        "source": "mem0",
        "path": "mem0:old",
        "title": "ops / agent-a",
        "snippet": "old state",
        "score": 1.0,
        "created_at": "2026-05-01T00:00:00+00:00",
    }
    new_hit = {
        "source": "mem0",
        "path": "mem0:new",
        "title": "ops / agent-a",
        "snippet": "new state",
        "score": 1.0,
        "created_at": "2026-05-10T00:00:00+00:00",
    }
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: {
            "query": "previous memory state",
            "scope": "mem0",
            "strategy": "grouped-intent-budget-v1",
            "primary_scope": "mem0",
            "primary_results": [old_hit, new_hit],
            "groups": {"mem0": [old_hit, new_hit]},
            "warnings": [],
        },
    )

    def fake_llm(_query, evidence):
        captured["evidence"] = evidence
        return True, {
            "status": "ok",
            "answer": "historical state",
            "summary": "historical state resolved",
            "claims": [{"id": "c1", "text": "history", "support": ["e1", "e2"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("previous memory state", scope="mem0", run_id="freshness-historical")

    assert result["status"] == "ok"
    assert result["trace"]["planner"]["freshness_mode"] == "historical"
    assert len(captured["evidence"]) == 2
    assert {item["path"] for item in captured["evidence"]} == {"mem0:old", "mem0:new"}
    assert all(item["validity"] == "historical" for item in captured["evidence"])
    assert result["trace"]["validity"]["state_counts"]["historical"] == 2
    assert result["trace"]["stale_guard"]["counterevidence_ids"] == []
    assert all(item["validity"] == "historical" for item in result["trace"]["evidence_gate"])


def test_memory_answer_core_current_state_warns_on_missing_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: {
            "query": "current memory state",
            "scope": "mem0",
            "strategy": "grouped-intent-budget-v1",
            "primary_scope": "mem0",
            "primary_results": [{
                "source": "mem0",
                "path": "mem0:unknown",
                "title": "ops / agent-a",
                "snippet": "state without timestamp",
                "score": 1.0,
            }],
            "groups": {"mem0": [{
                "source": "mem0",
                "path": "mem0:unknown",
                "title": "ops / agent-a",
                "snippet": "state without timestamp",
                "score": 1.0,
            }]},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, evidence: (True, {
            "status": "ok",
            "answer": "unknown dated state",
            "summary": "timestamp missing",
            "claims": [{"id": "c1", "text": "unknown", "support": ["e1"], "confidence": 0.8}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core("current memory state", scope="mem0", run_id="freshness-unknown")

    assert result["status"] == "ok"
    assert result["trace"]["planner"]["freshness_mode"] == "current"
    assert result["evidence"][0]["validity"] == "unknown_date"
    assert result["trace"]["validity"]["state_counts"]["unknown_date"] == 1
    assert any("unknown_date" in warning for warning in result["warnings"])
    assert any(item["validity"] == "unknown_date" for item in result["trace"]["evidence_gate"])


def test_memory_answer_core_conflict_scan_short_circuits_llm(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "mem0",
            "path": "mem0:conflict",
            "title": "systems / codex",
            "snippet": "P5.2 小额规则内自动下单 与 P5.2 不自动下单 两种说法冲突",
            "score": 5.0,
        }),
    )
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = tm_answer.memory_answer_core("P5.2 自动下单 冲突", scope="mem0")

    assert result["status"] == "conflict"
    assert result["claims"][0]["support"] == ["e1"]
    assert calls == []
    assert result["trace"]["conflict_scan"]["conflict"] is True


def test_answer_eval_contract_accepts_optional_fields_and_rejects_paper_seed_tmp(tmp_path):
    baseline = tmp_path / "baseline.jsonl"
    baseline.write_text(
        "\n".join([
            json.dumps({
                "id": "case-1",
                "query": "baseline query",
                "case_source": "real_failure",
                "case_source_ref": "trace:abc",
                "eval_dimension": "static_state_recall",
                "freshness_mode": "current",
                "expected_warning": "warn-1",
                "expected_trace_flags": ["planner", "trace_id"],
            }),
        ]),
        encoding="utf-8",
    )

    loaded = tm_answer_eval.load_cases(str(baseline))
    assert loaded[0]["case_source"] == "real_failure"
    assert loaded[0]["expected_warning"] == "warn-1"
    assert loaded[0]["expected_trace_flags"] == ["planner", "trace_id"]

    experimental = tmp_path / "paper_seed_tmp.jsonl"
    experimental.write_text(
        json.dumps({
            "id": "case-2",
            "query": "paper seed",
            "case_source": "paper_seed_tmp",
        }) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="paper_seed_tmp"):
        tm_answer_eval.load_cases(str(experimental))

    loaded_experimental = tm_answer_eval.load_cases(str(experimental), allow_paper_seed_tmp=True)
    assert loaded_experimental[0]["case_source"] == "paper_seed_tmp"


def test_answer_eval_compact_redacts_queries_and_emits_grouped_metrics(tmp_path, monkeypatch, capsys):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "\n".join([
            json.dumps({
                "id": "case-1",
                "query": "leak secret one",
                "expected_status": "ok",
                "expected_evidence_paths": ["wiki/systems/answer-contract.md"],
                "must_contain": ["alpha"],
                "case_source": "real_failure",
                "eval_dimension": "static_state_recall",
                "freshness_mode": "current",
                "expected_warning": ["warn-1"],
                "expected_trace_flags": ["planner"],
            }),
            json.dumps({
                "id": "case-2",
                "query": "stale seed two",
                "expected_status": "ok",
                "expected_evidence_paths": ["wiki/systems/missing.md"],
                "must_contain": ["beta"],
                "case_source": "patrol",
                "eval_dimension": "stale_obsolete",
                "freshness_mode": "stale_sensitive",
            }),
            json.dumps({
                "id": "case-3",
                "query": "trace flag missing three",
                "expected_status": "ok",
                "must_contain": ["gamma"],
                "case_source": "system_contract",
                "eval_dimension": "workflow_knowledge",
                "freshness_mode": "not_applicable",
                "expected_trace_flags": ["planner"],
            }),
        ]),
        encoding="utf-8",
    )

    def fake_memory_answer_core(query, **kwargs):
        if query == "leak secret one":
            return {
                "status": "ok",
                "answer": "alpha answer",
                "summary": "alpha summary",
                "claims": [{"id": "c1", "text": "alpha", "support": ["e1"], "confidence": 0.9}],
                "evidence": [{"id": "e1", "path": "wiki/systems/answer-contract.md", "excerpt": "alpha", "authority": 100.0, "source_role": "canonical_wiki"}],
                "warnings": ["warn-1"],
                "trace_id": "trace-1",
                "trace": {"planner": {"intent": "recall"}, "trace_id": "trace-1"},
                "run_id": kwargs.get("run_id"),
            }
        if query == "trace flag missing three":
            return {
                "status": "ok",
                "answer": "gamma answer",
                "summary": "gamma summary",
                "claims": [],
                "evidence": [],
                "warnings": [],
                "trace_id": "trace-3",
                "trace": {"trace_id": "trace-3"},
                "run_id": kwargs.get("run_id"),
            }
        return {
            "status": "not_found",
            "answer": "",
            "summary": "",
            "claims": [],
            "evidence": [],
            "warnings": [],
            "trace_id": "trace-2",
            "trace": None,
            "run_id": kwargs.get("run_id"),
        }

    monkeypatch.setattr(tm_answer_eval.tm_answer, "memory_answer_core", fake_memory_answer_core)
    args = type("Args", (), {
        "cases": str(cases),
        "json": True,
        "compact": True,
        "run_id": "unit-run",
        "allow_paper_seed_tmp": False,
    })()

    exit_code = tm_answer_eval.cmd_eval(args)
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "leak secret one" not in captured
    report = json.loads(captured)
    assert report["case_count_by_dimension"]["static_state_recall"] == 1
    assert report["case_count_by_dimension"]["stale_obsolete"] == 1
    assert report["case_count_by_source"]["real_failure"] == 1
    assert report["warning_hit_by_dimension"]["static_state_recall"] == 1
    assert report["stale_penalty_count"] == 1
    assert report["action_seed_count"] == 0
    assert {item["id"] for item in report["failures"]} == {"case-2", "case-3"}
    assert any(item["trace_flags_hit"] is False for item in report["failures"])
    assert all("query" not in item for item in report["failures"])
