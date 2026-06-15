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


def test_memory_answer_core_uses_person_identity_fast_path(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    assert tm_answer._normalize_identity_query_text("虎哥是谁？") == "虎哥是谁"
    assert tm_answer._person_identity_profile_path("使用tigermemory查一下虎哥是谁", "auto") == "wiki/person/tiger.md"

    def fail_search(*_args, **_kwargs):
        raise AssertionError("identity fast path should not call search")

    def fail_llm(*_args, **_kwargs):
        raise AssertionError("identity fast path should not call DeepSeek")

    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", fail_search)
    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fail_llm)

    result = tm_answer.memory_answer_core("虎哥是谁", scope="auto", include_trace=True, run_id="person-fast")

    assert result["status"] == "ok"
    assert "tigermemory 系统的主人" in result["answer"]
    assert "Giant Rao" in result["answer"]
    assert result["evidence"][0]["path"] == "wiki/person/tiger.md"
    assert result["evidence"][0]["source_role"] == "protected_person_profile"
    assert result["trace"]["query_class"] == "identity"
    assert result["trace"]["planner"]["source"] == "person_identity_fast_path"
    assert [call["tool"] for call in result["trace"]["calls"]] == ["read_protected_person_summary"]
    assert result["related_evidence_candidates"] == []
    assert result["trace"]["related_evidence_candidates"]["status"] == "no_selected_evidence"

    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert trace_row["run_id"] == "person-fast"
    assert trace_row["trace"]["planner"]["source"] == "person_identity_fast_path"
    assert "query" not in trace_row


def test_memory_answer_core_person_fast_path_does_not_match_general_tiger_task(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: calls.append("search") or _search_result(),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no evidence should skip LLM")),
    )

    result = tm_answer.memory_answer_core("虎哥今天让我优化什么", scope="wiki", run_id="person-normal")

    assert result["status"] == "not_found"
    assert calls
    assert result["trace"]["query_class"] != "identity"


def test_memory_answer_core_expands_evidence_and_generates_answer(monkeypatch, tmp_path):
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
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
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
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
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {**_empty_map_plan(), "degraded": True, "error": "wiki_map_missing"},
    )
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


def test_rank_manifest_pages_uses_compact_page_signals(monkeypatch):
    monkeypatch.setattr(tm_answer, "_query_planner_manifest_pages", lambda: [
        {
            "path": "wiki/systems/plain-title.md",
            "title": "Plain Title",
            "signals": "记忆问答 自然语言 召回 证据规划",
        },
        {
            "path": "wiki/systems/noise.md",
            "title": "Unrelated",
            "signals": "dashboard runtime canvas",
        },
    ])

    ranked = tm_answer._rank_manifest_pages("为什么记忆问答自然语言召回失败", limit=5)

    assert ranked[0]["path"] == "wiki/systems/plain-title.md"
    assert ranked[0]["score"] > 0


def _empty_map_plan() -> dict:
    return {
        "degraded": False,
        "error": None,
        "candidate_count": 0,
        "top_score": 0.0,
        "top1_top2_margin": 0.0,
        "partitions": [],
        "source_surfaces": [],
        "top_paths_hash": "",
        "queries": [],
        "terms": [],
        "paths": [],
        "candidates": [],
    }


def _map_plan_with_candidate(path: str, *, title: str = "Bridge Target", score: float = 99.0) -> dict:
    return {
        "degraded": False,
        "error": None,
        "candidate_count": 1,
        "top_score": score,
        "top1_top2_margin": score,
        "partitions": ["systems"],
        "source_surfaces": ["wiki"],
        "top_paths_hash": "bridgehash",
        "queries": [path, title],
        "terms": [title],
        "paths": [path],
        "candidates": [{
            "path": path,
            "title": title,
            "partition": "systems",
            "source_surface": "wiki",
            "score": score,
            "map_rank": 1,
        }],
    }


def test_attach_map_candidates_merges_map_terms_into_evidence_query(monkeypatch):
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: _map_plan_with_candidate(
            "wiki/systems/map-arm-target.md",
            title="Map Arm Target",
        ),
    )

    planner = {
        "expanded_queries": ["alpha target"],
        "evidence_terms": ["existing term"],
        "path_hints": [],
        "subquery_roles": [{"index": 0, "role": "primary"}],
    }

    merged = tm_answer._attach_map_candidates("alpha target", planner)

    assert merged["map_candidate_term_count"] == 1
    assert merged["evidence_terms"] == ["existing term", "Map Arm Target"]
    evidence_query = tm_answer._planner_evidence_query("alpha target", merged)
    assert "existing term" in evidence_query
    assert "Map Arm Target" in evidence_query


def test_merge_llm_query_plan_preserves_base_evidence_terms():
    merged, warnings = tm_answer._merge_llm_query_plan(
        {
            "expanded_queries": ["alpha target"],
            "evidence_terms": ["Map Arm Target"],
            "path_hints": [],
        },
        {
            "retrieval_queries": ["alpha natural question"],
            "evidence_terms": ["LLM Stable Term"],
        },
    )

    assert warnings == []
    assert merged["planner_source"] == "llm"
    assert merged["evidence_terms"] == ["Map Arm Target", "LLM Stable Term"]
    evidence_query = tm_answer._planner_evidence_query("alpha target", merged)
    assert "Map Arm Target" in evidence_query
    assert "LLM Stable Term" in evidence_query


def test_memory_answer_core_hybrid_map_arm_widening_is_off_by_default(monkeypatch, tmp_path):
    path = tmp_path / "wiki" / "systems" / "wrong-memory-answer.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Wrong Memory Answer\nalpha unrelated", encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.delenv(tm_answer.HYBRID_MAP_ARM_ENV, raising=False)
    monkeypatch.delenv(tm_answer.WIKI_MAP_BRIDGE_ENV, raising=False)
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")

    def fail_map_candidate_plan(*_args, **_kwargs):
        raise AssertionError("hybrid map arm widening should not probe wiki_map when env is unset")

    monkeypatch.setattr(tm_answer, "_map_candidate_plan", fail_map_candidate_plan)
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/wrong-memory-answer.md",
            "title": "Wrong Memory Answer",
            "snippet": "alpha unrelated",
            "score": 10.0,
        }),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda *_args, **_kwargs: (
            True,
            {
                "status": "ok",
                "answer": "alpha unrelated",
                "summary": "wrong evidence used",
                "claims": [{"id": "c1", "text": "wrong", "support": ["e1"], "confidence": 0.5}],
                "warnings": [],
            },
        ),
    )

    result = tm_answer.memory_answer_core("alpha target", scope="wiki", run_id="map-arm-widening-off")

    widening = result["trace"]["hybrid_map_arm_evidence_widening"]
    assert widening["enabled"] is False
    assert widening["status"] == "disabled"


def test_memory_answer_core_hybrid_map_arm_widens_evidence_candidates(monkeypatch, tmp_path):
    for rel, body in {
        "wiki/systems/wrong-memory-answer.md": "# Wrong Memory Answer\nalpha unrelated",
        "wiki/systems/map-arm-target.md": "# Map Arm Target\nalpha target answer",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    monkeypatch.delenv(tm_answer.WIKI_MAP_BRIDGE_ENV, raising=False)
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: _map_plan_with_candidate(
            "wiki/systems/map-arm-target.md",
            title="Map Arm Target",
            score=30.0,
        ),
    )
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/wrong-memory-answer.md",
            "title": "Wrong Memory Answer",
            "snippet": "alpha unrelated",
            "score": 10.0,
        }),
    )

    captured: dict[str, object] = {}

    def fake_llm(_query: str, evidence: list[dict]):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "alpha target answer",
            "summary": "map arm target used",
            "claims": [{"id": "c1", "text": "map arm target", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("alpha target", scope="wiki", run_id="map-arm-widening")

    assert result["status"] == "ok"
    assert "wiki/systems/map-arm-target.md" in captured["evidence_paths"]
    widening = result["trace"]["hybrid_map_arm_evidence_widening"]
    assert widening["enabled"] is True
    assert widening["added_count"] == 1
    assert result["trace"]["map_to_evidence_bridge"]["enabled"] is False
    gate = result["trace"]["evidence_gate"]
    assert any(
        item["path"] == "wiki/systems/map-arm-target.md"
        and item.get("bridge_source") == "hybrid_map_arm"
        and item.get("map_rank") == 1
        and item.get("map_score") == 30.0
        and item.get("keep") is True
        for item in gate
    )


def test_hybrid_map_arm_widens_more_than_first_four_candidates(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": f"wiki/systems/map-target-{index}.md",
            "title": f"Map Target {index}",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 30.0,
            "map_rank": index,
        }
        for index in range(1, 7)
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "map target",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened = merged["groups"]["wiki"]
    assert trace["added_count"] == 6
    assert len(widened) == 6
    assert widened[-1]["path"] == "wiki/systems/map-target-6.md"
    assert widened[-1]["score_breakdown"] == {"map_score": 30.0, "map_rank": 6}


def test_hybrid_map_arm_skips_development_review_archives(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "sources/internal-analysis/development-reviews/2026-06-15/review.md",
            "title": "Review Archive",
            "partition": "internal-analysis",
            "source_surface": "sources",
            "score": 40.0,
            "map_rank": 1,
        },
        {
            "path": "wiki/systems/canonical-policy.md",
            "title": "Canonical Policy",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 32.0,
            "map_rank": 2,
        },
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "canonical policy",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened = merged["groups"]["wiki"]
    assert trace["skipped_low_priority_count"] == 1
    assert trace["added_count"] == 1
    assert widened[0]["path"] == "wiki/systems/canonical-policy.md"


def test_hybrid_map_arm_accepts_top_rank_margin_candidate_below_strict_score(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "wiki/systems/memory-answer-development-plan.md",
            "title": "Memory Answer Development Plan",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 23.1,
            "map_rank": 1,
        },
        {
            "path": "wiki/systems/weak-peer.md",
            "title": "Weak Peer",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 12.45,
            "map_rank": 2,
        },
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 10.65,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "memory answer diagnosis",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened = merged["groups"]["wiki"]
    assert trace["added_count"] == 1
    assert trace["relaxed_score_count"] == 1
    assert trace["below_min_score_count"] == 1
    assert widened[0]["path"] == "wiki/systems/memory-answer-development-plan.md"
    assert widened[0]["score_breakdown"] == {"map_score": 23.1, "map_rank": 1}


def test_hybrid_map_arm_rejects_deep_low_score_candidate(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "wiki/systems/local-memory-adapter-contract.md",
            "title": "Local Memory Adapter Contract",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 10.65,
            "map_rank": 20,
        }
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 20.0,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "adapter contract",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    assert "wiki" not in merged.get("groups", {})
    assert trace["status"] == "no_new_candidates"
    assert trace["added_count"] == 0
    assert trace["below_min_score_count"] == 1
    assert trace["relaxed_score_count"] == 0


def test_hybrid_map_arm_accepts_typed_mid_score_policy_candidates(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "AGENTS.md",
            "title": "AGENTS",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 16.5,
            "map_rank": 5,
        },
        {
            "path": "wiki/systems/multi-endpoint-mem0.md",
            "title": "Multi Endpoint Mem0",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 17.1,
            "map_rank": 2,
        },
        {
            "path": "wiki/systems/openclaw-investment-routing.md",
            "title": "OpenClaw Investment Routing",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 21.8,
            "map_rank": 29,
        },
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 6.5,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "policy candidates",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened_paths = [item["path"] for item in merged["groups"]["wiki"]]
    assert widened_paths == [
        "AGENTS.md",
        "wiki/systems/multi-endpoint-mem0.md",
        "wiki/systems/openclaw-investment-routing.md",
    ]
    assert trace["added_count"] == 3
    assert trace["relaxed_score_count"] == 3


def test_hybrid_map_arm_accepts_brand_and_investment_top_mid_score_candidates(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "wiki/brand/ipfb-copywriting-guide.md",
            "title": "IPFB 文案撰写准则",
            "partition": "brand",
            "source_surface": "wiki",
            "score": 15.7,
            "map_rank": 1,
        },
        {
            "path": "wiki/investment/miniqmt-integration-status.md",
            "title": "MiniQMT Integration Status",
            "partition": "investment",
            "source_surface": "wiki",
            "score": 18.0,
            "map_rank": 2,
        },
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 1.0,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "IPFB 文案和 MiniQMT 状态",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened_paths = [item["path"] for item in merged["groups"]["wiki"]]
    assert widened_paths == [
        "wiki/brand/ipfb-copywriting-guide.md",
        "wiki/investment/miniqmt-integration-status.md",
    ]
    assert trace["added_count"] == 2
    assert trace["relaxed_score_count"] == 2


def test_hybrid_map_arm_skips_investment_decision_logs_for_generic_queries(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "wiki/investment/decision-log/688981.SH/2026-05-21/risk_debate.md",
            "title": "688981.SH Risk Debate",
            "partition": "investment",
            "source_surface": "wiki",
            "score": 28.5,
            "map_rank": 1,
        },
        {
            "path": "wiki/systems/memory-answer-evidence-policy.md",
            "title": "Memory Answer 证据策略",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 17.5,
            "map_rank": 2,
        },
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 11.0,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "prompt_budget_truncated 代表什么风险",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened_paths = [item["path"] for item in merged["groups"]["wiki"]]
    assert widened_paths == ["wiki/systems/memory-answer-evidence-policy.md"]
    assert trace["skipped_low_priority_count"] == 1
    assert trace["added_count"] == 1


def test_hybrid_map_arm_accepts_systems_exact_code_term_candidate(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "wiki/systems/memory-answer-evidence-policy.md",
            "title": "Memory Answer 证据策略",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 9.8,
            "map_rank": 2,
            "score_breakdown": {
                "matched_terms": ["prompt_budget_truncated", "回答", "风险"],
            },
        }
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 1.0,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "prompt_budget_truncated 代表什么风险",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened = merged["groups"]["wiki"]
    assert widened[0]["path"] == "wiki/systems/memory-answer-evidence-policy.md"
    assert widened[0]["score_breakdown"]["map_matched_terms"] == [
        "prompt_budget_truncated",
        "回答",
        "风险",
    ]
    assert trace["added_count"] == 1
    assert trace["relaxed_score_count"] == 1


def test_hybrid_map_arm_allows_investment_decision_logs_for_decision_queries(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    candidates = [
        {
            "path": "wiki/investment/decision-log/688981.SH/2026-05-21/risk_debate.md",
            "title": "688981.SH Risk Debate",
            "partition": "investment",
            "source_surface": "wiki",
            "score": 28.5,
            "map_rank": 1,
        },
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 20.0,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "688981.SH 的持有决策风险是什么",
        {"primary_results": [], "groups": {}, "warnings": []},
    )

    widened_paths = [item["path"] for item in merged["groups"]["wiki"]]
    assert widened_paths == ["wiki/investment/decision-log/688981.SH/2026-05-21/risk_debate.md"]
    assert trace["skipped_low_priority_count"] == 0
    assert trace["added_count"] == 1


def test_hybrid_map_arm_enriches_existing_candidate_with_map_signal(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    existing_hit = {
        "source": "wiki",
        "path": "AGENTS.md",
        "title": "AGENTS",
        "snippet": "开工规则。",
        "score": 12.0,
    }
    candidates = [
        {
            "path": "AGENTS.md",
            "title": "AGENTS",
            "partition": "systems",
            "source_surface": "wiki",
            "score": 52.0,
            "map_rank": 1,
        }
    ]
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": len(candidates),
            "top1_top2_margin": 20.0,
            "candidates": candidates,
        },
    )

    merged, trace = tm_answer._apply_hybrid_map_arm_evidence_widening(
        "AGENTS 开工规则",
        {"primary_results": [existing_hit], "groups": {}, "warnings": []},
    )

    assert trace["added_count"] == 0
    assert trace["enriched_existing_count"] == 1
    assert merged["primary_results"][0]["bridge_source"] == "hybrid_map_arm"
    assert merged["primary_results"][0]["score_breakdown"] == {"map_score": 52.0, "map_rank": 1}


def test_memory_answer_core_does_not_use_wiki_map_bridge_by_default(monkeypatch, tmp_path):
    target = tmp_path / "wiki" / "systems" / "bridge-target.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Bridge Target\nalpha bridge answer", encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.delenv(tm_answer.WIKI_MAP_BRIDGE_ENV, raising=False)
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("bridge should be disabled")),
    )
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no evidence should skip LLM")),
    )

    result = tm_answer.memory_answer_core("alpha bridge", scope="wiki", run_id="bridge-default-off")

    assert result["status"] == "not_found"
    assert result["trace"]["map_to_evidence_bridge"] == {
        "enabled": False,
        "status": "disabled",
        "candidate_count": 0,
        "added_count": 0,
    }


def test_memory_answer_core_wiki_map_bridge_adds_candidates_to_evidence_gate(monkeypatch, tmp_path):
    target = tmp_path / "wiki" / "systems" / "bridge-target.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Bridge Target\nalpha bridge answer", encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setenv(tm_answer.WIKI_MAP_BRIDGE_ENV, "1")
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: _map_plan_with_candidate("wiki/systems/bridge-target.md"),
    )
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())

    captured: dict[str, object] = {}

    def fake_llm(query: str, evidence: list[dict]):
        captured["query"] = query
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "alpha bridge answer",
            "summary": "bridge target used",
            "claims": [{"id": "c1", "text": "bridge target", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("alpha bridge", scope="wiki", run_id="bridge-on")

    assert result["status"] == "ok"
    assert captured["evidence_paths"] == ["wiki/systems/bridge-target.md"]
    assert result["trace"]["map_to_evidence_bridge"]["enabled"] is True
    assert result["trace"]["map_to_evidence_bridge"]["added_count"] == 1
    gate = result["trace"]["evidence_gate"]
    assert gate[0]["path"] == "wiki/systems/bridge-target.md"
    assert gate[0]["keep"] is True
    assert gate[0]["selected"] is True
    assert gate[0]["bridge_source"] == "wiki_map"


def test_memory_answer_core_wiki_map_bridge_competes_with_existing_wrong_evidence(monkeypatch, tmp_path):
    for rel, body in {
        "wiki/systems/wrong-one.md": "# Wrong One\nalpha unrelated",
        "wiki/systems/wrong-two.md": "# Wrong Two\nalpha different",
        "wiki/systems/bridge-target.md": "# Bridge Target\nalpha bridge answer",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setenv(tm_answer.WIKI_MAP_BRIDGE_ENV, "1")
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: _map_plan_with_candidate("wiki/systems/bridge-target.md", score=32.0),
    )
    monkeypatch.setattr(
        tm_answer.tm_search,
        "search_tigermemory",
        lambda *_args, **_kwargs: _search_result({
            "source": "wiki",
            "path": "wiki/systems/wrong-one.md",
            "title": "Wrong One",
            "snippet": "alpha unrelated",
            "score": 1.0,
        }) | {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/systems/wrong-one.md",
                    "title": "Wrong One",
                    "snippet": "alpha unrelated",
                    "score": 1.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/wrong-two.md",
                    "title": "Wrong Two",
                    "snippet": "alpha different",
                    "score": 1.0,
                },
            ],
            "groups": {
                "wiki": [
                    {
                        "source": "wiki",
                        "path": "wiki/systems/wrong-one.md",
                        "title": "Wrong One",
                        "snippet": "alpha unrelated",
                        "score": 1.0,
                    },
                    {
                        "source": "wiki",
                        "path": "wiki/systems/wrong-two.md",
                        "title": "Wrong Two",
                        "snippet": "alpha different",
                        "score": 1.0,
                    },
                ],
            },
        },
    )

    captured: dict[str, object] = {}

    def fake_llm(query: str, evidence: list[dict]):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "alpha bridge answer",
            "summary": "bridge target used",
            "claims": [{"id": "c1", "text": "bridge target", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("alpha bridge", scope="wiki", run_id="bridge-wrong-evidence")

    assert result["status"] == "ok"
    assert "wiki/systems/bridge-target.md" in captured["evidence_paths"]
    assert result["trace"]["map_to_evidence_bridge"]["added_count"] == 1
    gate_paths = [item["path"] for item in result["trace"]["evidence_gate"]]
    assert "wiki/systems/bridge-target.md" in gate_paths


def test_memory_answer_core_wiki_map_bridge_filters_low_score_candidates(monkeypatch, tmp_path):
    target = tmp_path / "wiki" / "systems" / "bridge-target.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Bridge Target\nalpha bridge answer", encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setenv(tm_answer.WIKI_MAP_BRIDGE_ENV, "1")
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: _map_plan_with_candidate(
            "wiki/systems/bridge-target.md",
            score=tm_answer.WIKI_MAP_BRIDGE_MIN_SCORE - 0.5,
        ),
    )
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("low-score bridge should not call LLM")),
    )

    result = tm_answer.memory_answer_core("alpha bridge", scope="wiki", run_id="bridge-low-score")

    assert result["status"] == "not_found"
    assert result["trace"]["map_to_evidence_bridge"]["added_count"] == 0
    assert result["trace"]["map_to_evidence_bridge"]["below_min_score_count"] == 1


def test_memory_answer_core_wiki_map_bridge_allows_current_queries_to_reach_gate(monkeypatch, tmp_path):
    target = tmp_path / "wiki" / "systems" / "bridge-target.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nupdated: 2026-06-12\n---\n# Bridge Target\n当前 alpha bridge answer", encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setenv(tm_answer.WIKI_MAP_BRIDGE_ENV, "1")
    monkeypatch.setattr(tm_answer.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: _map_plan_with_candidate("wiki/systems/bridge-target.md", score=36.0),
    )
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result())

    captured: dict[str, object] = {}

    def fake_llm(query: str, evidence: list[dict]):
        captured["query"] = query
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "current bridge answer",
            "summary": "current bridge target used",
            "claims": [{"id": "c1", "text": "bridge target", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("当前 alpha bridge", scope="wiki", run_id="bridge-current")

    assert result["status"] == "ok"
    assert result["trace"]["query_class"] == "temporal_current"
    assert result["trace"]["planner"]["freshness_mode"] == "current"
    assert "wiki/systems/bridge-target.md" in captured["evidence_paths"]
    gate = result["trace"]["evidence_gate"]
    assert gate[0]["path"] == "wiki/systems/bridge-target.md"
    assert gate[0]["freshness_mode"] == "current"
    assert gate[0]["bridge_source"] == "wiki_map"


def test_plan_query_uses_wiki_map_without_deepseek_when_confident(monkeypatch):
    query = "为什么自然语言召回找不到记忆问答开发计划"
    monkeypatch.delenv(tm_answer.QUERY_PLANNER_ENV, raising=False)
    monkeypatch.setenv(tm_answer.WIKI_MAP_ENV, "1")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {
            "degraded": False,
            "error": None,
            "candidate_count": 8,
            "top_score": 30.0,
            "top1_top2_margin": 8.0,
            "partitions": ["systems"],
            "source_surfaces": ["wiki"],
            "top_paths_hash": "abc123",
            "queries": ["wiki/systems/memory-answer-development-plan.md", "Memory Answer 开发计划"],
            "terms": ["记忆问答开发计划", "natural recall"],
            "paths": ["wiki/systems/memory-answer-development-plan.md"],
            "candidates": [],
        },
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_query_planner_llm",
        lambda *_args: pytest.fail("DeepSeek planner should not run for confident map recall"),
    )

    plan = tm_answer.plan_query(query)

    assert plan["planner_source"] == "deterministic+wiki_map"
    assert plan["expanded_queries"][:3] == [
        query,
        "wiki/systems/memory-answer-development-plan.md",
        "Memory Answer 开发计划",
    ]
    assert plan["path_hints"] == ["wiki/systems/memory-answer-development-plan.md"]
    assert plan["map_candidate_count"] == 8
    assert "planner_call" not in plan


def test_query_planner_context_uses_empty_map_instead_of_large_manifest(monkeypatch):
    monkeypatch.setenv(tm_answer.WIKI_MAP_ENV, "1")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(
        tm_answer,
        "_query_planner_manifest_pages",
        lambda: [{"path": f"wiki/systems/noise-{index}.md", "title": "Noise"} for index in range(200)],
    )

    context = tm_answer._query_planner_context("找不到的自然语言问题")

    assert context["indexed_surfaces"] == ["runtime/llm_wiki/wiki_map.jsonl"]
    assert context["map_status"] == "no_candidates"
    assert context["page_count"] == 0
    assert context["candidate_pages"] == []


def test_map_planner_fallback_reason_boundaries(monkeypatch):
    monkeypatch.delenv(tm_answer.QUERY_PLANNER_ENV, raising=False)
    base = {
        "query_class": "recall",
        "map_candidate_count": tm_answer.MAP_MIN_CANDIDATES,
        "map_top_score": tm_answer.MAP_MIN_TOP_SCORE,
        "map_top1_top2_margin": tm_answer.MAP_MIN_TOP_MARGIN,
        "map_partitions": ["systems"],
    }

    assert tm_answer._map_planner_fallback_reasons("短问", dict(base)) == []
    below_count = dict(base, map_candidate_count=tm_answer.MAP_MIN_CANDIDATES - 1)
    assert "map_candidate_count_below_min" in tm_answer._map_planner_fallback_reasons("短问", below_count)
    below_score = dict(base, map_top_score=tm_answer.MAP_MIN_TOP_SCORE - 0.01)
    assert "map_top_score_below_min" in tm_answer._map_planner_fallback_reasons("短问", below_score)
    low_margin = dict(base, map_top1_top2_margin=tm_answer.MAP_MIN_TOP_MARGIN - 0.01)
    reasons = tm_answer._map_planner_fallback_reasons("这是一个比较复杂的自然语言召回问题需要分析", low_margin)
    assert "map_margin_low_for_complex_query" in reasons
    synthesis = dict(base, query_class="synthesis", map_partitions=["systems", "operations", "investment"])
    assert "synthesis_cross_partition" in tm_answer._map_planner_fallback_reasons("综合分析这些系统问题", synthesis)


def test_plan_query_uses_deepseek_planner_for_general_rewrite(monkeypatch):
    query = "请帮我判断记忆问答系统为什么自然语言问题找不到对应资料"
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
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
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {**_empty_map_plan(), "degraded": True, "error": "wiki_map_missing"},
    )
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


def test_plan_query_does_not_use_manifest_when_empty_map_and_deepseek_fails(monkeypatch):
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setenv(tm_answer.WIKI_MAP_ENV, "1")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(
        tm_answer,
        "_manifest_candidate_plan",
        lambda *_args, **_kwargs: pytest.fail("manifest fallback must not run when map is available"),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_query_planner_llm",
        lambda *_args: (False, "offline"),
    )

    plan = tm_answer.plan_query("为什么自然语言问题找不到对应资料")

    assert plan["planner_source"] == "deterministic"
    assert plan["planner_call"]["ok"] is False
    assert plan["expanded_queries"] == ["为什么自然语言问题找不到对应资料"]
    assert plan["evidence_terms"] == []
    assert plan["path_hints"] == []


def test_plan_query_does_not_use_manifest_when_empty_map_and_deepseek_succeeds(monkeypatch):
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setenv(tm_answer.WIKI_MAP_ENV, "1")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(
        tm_answer,
        "_manifest_candidate_plan",
        lambda *_args, **_kwargs: pytest.fail("manifest fallback must not run when map is available"),
    )
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_query_planner_llm",
        lambda *_args: (True, {
            "retrieval_queries": ["planner-only query"],
            "evidence_terms": ["planner-only term"],
            "path_hints": ["wiki/systems/planner-only.md"],
        }),
    )

    plan = tm_answer.plan_query("为什么自然语言问题找不到对应资料")

    assert plan["planner_source"] == "llm"
    assert "planner-only query" in plan["expanded_queries"]
    assert "wiki/systems/planner-only.md" in plan["expanded_queries"]
    assert plan["evidence_terms"] == ["planner-only term"]


def test_plan_query_prioritizes_manifest_candidate_before_generic_llm_probe(monkeypatch):
    query = "为什么自然语言问题应该先看记忆问答开发计划"
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "1")
    monkeypatch.setattr(
        tm_answer,
        "_map_candidate_plan",
        lambda *_args, **_kwargs: {**_empty_map_plan(), "degraded": True, "error": "wiki_map_missing"},
    )
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
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
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


def test_planner_expansion_terms_keep_api_endpoint_excerpt(monkeypatch):
    query = "怎么调用记忆检索接口"
    planner = {
        "expanded_queries": [
            query,
            "/search_memories /memory/answer /read_wiki search_tigermemory",
        ],
        "evidence_terms": [],
    }
    evidence_query = tm_answer._planner_evidence_query(query, planner)
    assert "/search_memories" in evidence_query

    content = "\n\n".join([
        "# tm_http 端点契约",
        "## 摘要\n\ntigermemory 的 FastAPI 包装层对外暴露 HTTP 端点。",
        "#### `POST /search_memories`\n\n请求：`{ \"query\": \"string\", \"limit\": 5 }`。用于在 Mem0 检索记忆。",
    ])

    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda _path: content)
    evidence, _gate = tm_answer.expand_evidence(
        evidence_query,
        _search_result({
            "source": "wiki",
            "path": "wiki/systems/tm_http-endpoints.md",
            "title": "tm_http 端点契约",
            "snippet": "# tm_http 端点契约",
            "score": 1.0,
        }),
        max_evidence=1,
        query_class="synthesis",
    )

    assert evidence
    assert "/search_memories" in evidence[0]["excerpt"]


def test_high_confidence_map_hit_can_pass_weak_evidence_gate(monkeypatch):
    monkeypatch.setattr(
        tm_answer,
        "_read_hit_content",
        lambda _path: "# AGENTS.md\n\n本页是开工规则。",
    )

    evidence, gate = tm_answer.expand_evidence(
        "紫色火山如何清洗月亮",
        _search_result({
            "source": "wiki",
            "path": "wiki/systems/startup-rules.md",
            "title": "Startup Rules",
            "snippet": "本页是开工规则。",
            "score": 0.02,
            "score_breakdown": {"map_score": 42.0, "map_rank": 1},
        }),
        max_evidence=1,
        query_class="recall",
    )

    assert evidence
    assert evidence[0]["path"] == "wiki/systems/startup-rules.md"
    assert evidence[0]["match_count"] == 0
    assert gate[0]["reason"] == "high authority fallback"
    assert gate[0]["selected"] is True


def test_high_confidence_map_signal_breaks_same_authority_and_relevance_tie(monkeypatch):
    bodies = {
        "wiki/systems/lexical-peer.md": "# Lexical Peer\n\nalpha surface match.",
        "wiki/systems/map-target.md": "# Map Target\n\nalpha canonical routed answer.",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        "alpha",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/systems/lexical-peer.md",
                    "title": "Lexical Peer",
                    "snippet": "alpha surface match.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/map-target.md",
                    "title": "Map Target",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 42.0, "map_rank": 1},
                },
            ],
            "groups": {},
        },
        max_evidence=1,
        query_class="recall",
    )

    assert evidence[0]["path"] == "wiki/systems/map-target.md"
    assert gate[1]["keep"] is True
    assert gate[1]["selected"] is True


def test_hybrid_map_arm_reserves_selected_slot_for_high_confidence_map_signal(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    bodies = {
        "wiki/systems/current-peer-a.md": "---\nupdated: 2026-06-15\n---\n# Peer A\n\n当前 alpha beta gamma.",
        "wiki/systems/current-peer-b.md": "---\nupdated: 2026-06-15\n---\n# Peer B\n\n当前 alpha beta delta.",
        "AGENTS.md": "# AGENTS\n\n端口纪律和开工规则。",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        "当前 alpha 端口纪律",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/systems/current-peer-a.md",
                    "title": "Peer A",
                    "snippet": "当前 alpha beta gamma.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/current-peer-b.md",
                    "title": "Peer B",
                    "snippet": "当前 alpha beta delta.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "AGENTS.md",
                    "title": "AGENTS",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 38.0, "map_rank": 1},
                },
            ],
            "groups": {},
        },
        max_evidence=2,
        query_class="recall",
    )

    paths = [item["path"] for item in evidence]
    assert "AGENTS.md" in paths
    agents_gate = next(item for item in gate if item["path"] == "AGENTS.md")
    assert agents_gate["keep"] is True
    assert agents_gate["selected"] is True


def test_hybrid_map_arm_reserves_systems_rank5_mid_score_map_signal(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    bodies = {
        "wiki/systems/current-peer-a.md": "# Peer A\n\n当前 alpha beta gamma.",
        "wiki/systems/current-peer-b.md": "# Peer B\n\n当前 alpha beta delta.",
        "wiki/systems/evidence-policy.md": "# Evidence Policy\n\n.tmp 和 wiki_map 不进入长期事实面。",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        ".tmp wiki_map alpha",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/systems/current-peer-a.md",
                    "title": "Peer A",
                    "snippet": "当前 alpha beta gamma.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/current-peer-b.md",
                    "title": "Peer B",
                    "snippet": "当前 alpha beta delta.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/evidence-policy.md",
                    "title": "Evidence Policy",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 23.9, "map_rank": 1},
                },
            ],
            "groups": {},
        },
        max_evidence=2,
        query_class="recall",
    )

    paths = [item["path"] for item in evidence]
    assert "wiki/systems/evidence-policy.md" in paths
    policy_gate = next(item for item in gate if item["path"] == "wiki/systems/evidence-policy.md")
    assert policy_gate["keep"] is True
    assert policy_gate["selected"] is True


def test_memory_answer_llm_retries_transient_connection_error(monkeypatch):
    calls: list[dict] = []

    def fake_deepseek(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return False, "unreachable: [WinError 10061] target machine actively refused it"
        return True, {
            "status": "ok",
            "answer": "retried",
            "summary": "retried",
            "claims": [],
        }

    monkeypatch.setattr(tm_answer.tm_core, "_call_deepseek_json", fake_deepseek)
    monkeypatch.setattr(tm_answer.time, "sleep", lambda _seconds: None)

    ok, parsed = tm_answer._call_memory_answer_llm("query", [])

    assert ok is True
    assert parsed["status"] == "ok"
    assert len(calls) == 2


def test_memory_answer_llm_does_not_retry_non_transient_error(monkeypatch):
    calls: list[dict] = []

    def fake_deepseek(*_args, **kwargs):
        calls.append(kwargs)
        return False, "HTTP 402 insufficient balance"

    monkeypatch.setattr(tm_answer.tm_core, "_call_deepseek_json", fake_deepseek)
    monkeypatch.setattr(tm_answer.time, "sleep", lambda _seconds: None)

    ok, reason = tm_answer._call_memory_answer_llm("query", [])

    assert ok is False
    assert "402" in reason
    assert len(calls) == 1


def test_hybrid_map_arm_reserves_relaxed_root_policy_candidate(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    bodies = {
        "wiki/systems/topic-peer-a.md": "# Peer A\n\ninvestment report dashboard topic.",
        "wiki/systems/topic-peer-b.md": "# Peer B\n\nportfolio report topic archive.",
        "AGENTS.md": "# AGENTS\n\n投资组合和研报内容归到 investment topic。",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        "投资组合和研报内容应该归到哪个 topic",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/systems/topic-peer-a.md",
                    "title": "Peer A",
                    "snippet": "investment report dashboard topic.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/topic-peer-b.md",
                    "title": "Peer B",
                    "snippet": "portfolio report topic archive.",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "AGENTS.md",
                    "title": "AGENTS",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 16.5, "map_rank": 5},
                    "bridge_source": "hybrid_map_arm",
                },
            ],
            "groups": {},
        },
        max_evidence=2,
        query_class="recall",
    )

    paths = [item["path"] for item in evidence]
    assert "AGENTS.md" in paths
    agents_gate = next(item for item in gate if item["path"] == "AGENTS.md")
    assert agents_gate["keep"] is True
    assert agents_gate["selected"] is True


def test_hybrid_map_arm_reserve_prefers_root_policy_over_higher_scored_lessons(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    bodies = {
        "AGENTS.md": "# AGENTS\n\n端口纪律和开工规则。",
        "wiki/self-evolution/lessons/noisy.md": "# Lesson\n\n端口纪律和开工规则历史事故。",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        "当前 端口纪律 开工规则",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/self-evolution/lessons/noisy.md",
                    "title": "Noisy Lesson",
                    "snippet": "端口纪律和开工规则历史事故。",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 120.0, "map_rank": 1},
                },
                {
                    "source": "wiki",
                    "path": "AGENTS.md",
                    "title": "AGENTS",
                    "snippet": "端口纪律和开工规则。",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 52.0, "map_rank": 2},
                },
            ],
            "groups": {},
        },
        max_evidence=1,
        query_class="recall",
    )

    assert evidence[0]["path"] == "AGENTS.md"
    agents_gate = next(item for item in gate if item["path"] == "AGENTS.md")
    assert agents_gate["selected"] is True


def test_hybrid_map_arm_reserve_keeps_more_relevant_lesson_over_weak_root_policy(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    bodies = {
        "AGENTS.md": "# AGENTS\n\n开工入口。",
        "wiki/self-evolution/lessons/llm-gate.md": "# Lesson\n\ninbox LLM route gate 低质量写入直接文件绕过。",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        "inbox LLM route gate 低质量写入",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "AGENTS.md",
                    "title": "AGENTS",
                    "snippet": "开工入口。",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 35.0, "map_rank": 1},
                },
                {
                    "source": "wiki",
                    "path": "wiki/self-evolution/lessons/llm-gate.md",
                    "title": "LLM Gate Lesson",
                    "snippet": "inbox LLM route gate 低质量写入直接文件绕过。",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 27.0, "map_rank": 3},
                },
            ],
            "groups": {},
        },
        max_evidence=1,
        query_class="recall",
    )

    assert evidence[0]["path"] == "wiki/self-evolution/lessons/llm-gate.md"
    lesson_gate = next(item for item in gate if item["path"] == "wiki/self-evolution/lessons/llm-gate.md")
    assert lesson_gate["selected"] is True


def test_hybrid_map_arm_reserve_keeps_specific_lesson_over_generic_system_page(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    bodies = {
        "AGENTS.md": "# AGENTS\n\ninbox routed_by tigermemory LLM 路由 禁止直接写。",
        "wiki/systems/generic-route.md": "# Generic Route\n\ninbox routed_by tigermemory LLM 路由 endpoint。",
        "wiki/self-evolution/lessons/llm-gate.md": "# Lesson\n\ninbox routed_by tigermemory LLM 路由 禁止直接写事故。",
        "wiki/systems/peer-a.md": "# Peer A\n\ninbox routed_by endpoint。",
        "wiki/systems/peer-b.md": "# Peer B\n\nLLM 路由 endpoint。",
        "wiki/systems/peer-c.md": "# Peer C\n\ntigermemory endpoint。",
        "wiki/operations/daily-health/noisy.md": "# Daily\n\ninbox routed_by LLM route.",
    }
    monkeypatch.setattr(tm_answer, "_read_hit_content", lambda path: bodies[path])

    evidence, gate = tm_answer.expand_evidence(
        "inbox routed_by tigermemory LLM 路由 禁止直接写",
        {
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "AGENTS.md",
                    "title": "AGENTS",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 35.5, "map_rank": 1},
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/generic-route.md",
                    "title": "Generic Route",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 20.7, "map_rank": 14},
                },
                {
                    "source": "wiki",
                    "path": "wiki/self-evolution/lessons/llm-gate.md",
                    "title": "LLM Gate Lesson",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 27.0, "map_rank": 3},
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/peer-a.md",
                    "title": "Peer A",
                    "snippet": "inbox routed_by endpoint。",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/peer-b.md",
                    "title": "Peer B",
                    "snippet": "LLM 路由 endpoint。",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/peer-c.md",
                    "title": "Peer C",
                    "snippet": "tigermemory endpoint。",
                    "score": 20.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/operations/daily-health/noisy.md",
                    "title": "Daily",
                    "snippet": "inbox routed_by LLM route.",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 26.0, "map_rank": 4},
                },
            ],
            "groups": {},
        },
        max_evidence=6,
        query_class="recall",
    )

    paths = [item["path"] for item in evidence]
    assert "wiki/self-evolution/lessons/llm-gate.md" in paths
    lesson_gate = next(item for item in gate if item["path"] == "wiki/self-evolution/lessons/llm-gate.md")
    assert lesson_gate["keep"] is True
    assert lesson_gate["selected"] is True


def test_hybrid_map_arm_selection_reserve_does_not_bypass_gate(monkeypatch):
    monkeypatch.setenv(tm_answer.HYBRID_MAP_ARM_ENV, "1")
    monkeypatch.setattr(
        tm_answer,
        "_read_hit_content",
        lambda _path: "# Source Archive\n\nunrelated archive text.",
    )

    evidence, gate = tm_answer.expand_evidence(
        "当前 alpha",
        {
            "primary_results": [
                {
                    "source": "sources",
                    "path": "sources/reviews/noisy.md",
                    "title": "Noisy Archive",
                    "snippet": "",
                    "score": 20.0,
                    "score_breakdown": {"map_score": 45.0, "map_rank": 1},
                },
            ],
            "groups": {},
        },
        max_evidence=1,
        query_class="recall",
    )

    assert evidence == []
    assert gate[0]["keep"] is False
    assert "selected" not in gate[0]


def test_low_confidence_map_hit_still_fails_weak_evidence_gate(monkeypatch):
    monkeypatch.setattr(
        tm_answer,
        "_read_hit_content",
        lambda _path: "# AGENTS.md\n\n本页是开工规则。",
    )

    evidence, gate = tm_answer.expand_evidence(
        "紫色火山如何清洗月亮",
        _search_result({
            "source": "wiki",
            "path": "wiki/systems/startup-rules.md",
            "title": "Startup Rules",
            "snippet": "本页是开工规则。",
            "score": 0.02,
            "score_breakdown": {"map_score": 23.0, "map_rank": 1},
        }),
        max_evidence=1,
        query_class="recall",
    )

    assert evidence == []
    assert gate[0]["keep"] is False
    assert gate[0]["validity"] == "weak_filtered"


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


def _write_related_map(path: pathlib.Path, edges: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(edge, ensure_ascii=False) for edge in edges) + "\n",
        encoding="utf-8",
    )


def _recommendation_search_hit() -> dict:
    return {
        "source": "wiki",
        "path": "wiki/systems/agent-write-toolkit.md",
        "title": "Agent write toolkit",
        "snippet": "toolkit evidence for write_memory",
        "score": 10.0,
    }


def test_memory_answer_core_returns_related_evidence_sidecar_without_changing_llm_input(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/related-sidecar-no-boost.md",
        "score": 0.0,
        "reasons": ["markdown_link:wiki/systems/related-sidecar-no-boost.md", "shared_keyword:session-handoff"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Related sidecar no boost",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["markdown_links", "keywords"],
        "text_hash": "hash1",
    }])
    captured = {}
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))

    def fake_llm(_query, evidence):
        captured["evidence"] = evidence
        return True, {
            "status": "ok",
            "answer": "Use toolkit evidence.",
            "summary": "Answered from original evidence.",
            "claims": [{"id": "c1", "text": "toolkit", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("toolkit write_memory", scope="wiki", run_id="related-sidecar")

    assert result["status"] == "ok"
    assert [item["path"] for item in result["evidence"]] == ["wiki/systems/agent-write-toolkit.md"]
    assert [item["path"] for item in captured["evidence"]] == ["wiki/systems/agent-write-toolkit.md"]
    assert result["trace"]["recommendation_boosted_candidates"]["candidate_count"] == 1
    assert result["trace"]["recommendation_boosted_candidates"]["rejected_count"] == 1
    assert result["trace"]["recommendation_boosted_candidates"]["accepted_count"] == 0
    assert "related_evidence_candidates" in result
    assert result["related_evidence_candidates"] == [{
        "path": "wiki/systems/related-sidecar-no-boost.md",
        "title": "Related sidecar no boost",
        "score": 0.0,
        "reasons": ["markdown_link", "shared_keyword"],
        "use_hint": "read_next",
        "source_evidence_id": "e1",
        "source_evidence_path": "wiki/systems/agent-write-toolkit.md",
    }]
    assert result["trace"]["related_evidence_candidates"]["candidate_count"] == 1
    assert result["trace"]["related_evidence_candidates"]["candidates"][0] == {
        "path": "wiki/systems/related-sidecar-no-boost.md",
        "score_bucket": "none",
        "reason_categories": ["markdown_link", "shared_keyword"],
        "use_hint": "read_next",
        "source_evidence_id": "e1",
        "source_evidence_path": "wiki/systems/agent-write-toolkit.md",
    }


def test_memory_answer_related_trace_uses_safe_metadata_only(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    raw_query_canary = "raw-query-canary-20260611"
    reason_canary = "candidateexcerptcanary20260611"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/memory-answer-p38-recommendation-plan.md",
        "score": 6.5,
        "reasons": [f"shared_summary_token:{reason_canary}"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Candidate title should stay out of trace",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary"],
        "text_hash": "hash2",
    }])
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Answer.",
            "summary": "Summary.",
            "claims": [{"id": "c1", "text": "claim", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core(f"toolkit {raw_query_canary}", scope="wiki", run_id="related-trace")

    assert result["related_evidence_candidates"][0]["reasons"] == ["shared_summary_token"]
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    stored_payload = json.dumps(trace_row, ensure_ascii=False)
    assert raw_query_canary not in stored_payload
    assert reason_canary not in stored_payload
    assert "Candidate title should stay out of trace" not in stored_payload
    related_trace = trace_row["trace"]["related_evidence_candidates"]
    assert related_trace["status"] == "ok"
    assert related_trace["candidates"][0]["reason_categories"] == ["shared_summary_token"]
    assert related_trace["candidates"][0]["use_hint"] == "candidate_for_evidence"
    assert "query" not in related_trace
    assert "excerpt" not in stored_payload


def test_memory_answer_missing_related_map_returns_empty_sidecar(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", tmp_path / "missing.jsonl")
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))

    def fake_llm(_query, evidence):
        captured["evidence"] = evidence
        return True, {
            "status": "ok",
            "answer": "Answer.",
            "summary": "Summary.",
            "claims": [{"id": "c1", "text": "claim", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("toolkit", scope="wiki", run_id="related-missing")

    assert result["status"] == "ok"
    assert captured["evidence"]
    assert result["related_evidence_candidates"] == []
    assert result["trace"]["related_evidence_candidates"]["status"] == "missing"
    assert result["trace"]["related_evidence_candidates"]["candidate_count"] == 0


def test_memory_answer_invalid_related_map_returns_empty_sidecar(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    related_map.write_text("{not-json}\n", encoding="utf-8")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Answer.",
            "summary": "Summary.",
            "claims": [{"id": "c1", "text": "claim", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core("toolkit", scope="wiki", run_id="related-invalid")

    assert result["status"] == "ok"
    assert result["related_evidence_candidates"] == []
    assert result["trace"]["related_evidence_candidates"]["status"] == "invalid"
    assert result["trace"]["related_evidence_candidates"]["warning"] == "related_map_invalid"


def test_memory_answer_filters_forbidden_related_targets(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    _write_related_map(related_map, [
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "wiki/person/tiger.md",
            "score": 99.0,
            "reasons": ["markdown_link:wiki/person/tiger.md"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "Tiger",
            "target_status": "active",
            "sensitivity": "person",
            "built_from": ["markdown_links"],
            "text_hash": "hash-forbidden",
        },
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "runtime/memory_recommendation/related_map.jsonl",
            "score": 98.0,
            "reasons": ["same_directory:runtime/memory_recommendation"],
            "source_surface": "runtime",
            "target_surface": "runtime",
            "target_title": "Runtime map",
            "target_status": "active",
            "sensitivity": "normal",
            "built_from": ["directory"],
            "text_hash": "hash-runtime",
        },
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "D:/tigermemory/wiki/person/tiger.md",
            "score": 97.0,
            "reasons": ["markdown_link:D:/tigermemory/wiki/person/tiger.md"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "Absolute Person",
            "target_status": "active",
            "sensitivity": "person",
            "built_from": ["markdown_links"],
            "text_hash": "hash-absolute",
        },
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "wiki/systems/../person/tiger.md",
            "score": 96.0,
            "reasons": ["markdown_link:wiki/systems/../person/tiger.md"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "Traversal Person",
            "target_status": "active",
            "sensitivity": "person",
            "built_from": ["markdown_links"],
            "text_hash": "hash-traversal",
        },
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "wiki/systems/memory-answer-p38-recommendation-plan.md",
            "score": 5.0,
            "reasons": ["same_directory:wiki/systems"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "P3.8 recommendation plan",
            "target_status": "active",
            "sensitivity": "normal",
            "built_from": ["directory"],
            "text_hash": "hash-allowed",
        },
    ])
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Answer.",
            "summary": "Summary.",
            "claims": [{"id": "c1", "text": "claim", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core("toolkit", scope="wiki", run_id="related-filter")

    paths = [item["path"] for item in result["related_evidence_candidates"]]
    assert paths == ["wiki/systems/memory-answer-p38-recommendation-plan.md"]
    assert not any(path.startswith(("wiki/person/", "runtime/")) for path in paths)
    assert all(".." not in path and not path.lower().startswith("d:/") for path in paths)
    assert result["related_evidence_candidates"][0]["use_hint"] == "background_only"


def test_memory_answer_core_boosts_related_evidence_into_llm_input(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/toolkit-session-handoff-protocol.md",
        "score": 19.5,
        "reasons": ["shared_keyword:toolkit", "markdown_link:wiki/systems/toolkit-session-handoff-protocol.md"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Toolkit handoff protocol",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary", "markdown_links"],
        "text_hash": "boost-ok",
    }])
    captured = {}
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))

    def fake_llm(_query, evidence):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "Answer with boost.",
            "summary": "Boosted summary.",
            "claims": [
                {
                    "id": "c1",
                    "text": "boosted",
                    "support": ["e1", "e2"],
                    "confidence": 0.9,
                },
            ],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("toolkit write_memory", scope="wiki", run_id="related-boost-on")

    assert result["status"] == "ok"
    assert captured["evidence_paths"] == [
        "wiki/systems/agent-write-toolkit.md",
        "wiki/systems/toolkit-session-handoff-protocol.md",
    ]
    assert [item["id"] for item in result["evidence"]] == ["e1", "e2"]
    boost_trace = result["trace"]["recommendation_boosted_candidates"]
    assert boost_trace["status"] == "ok"
    assert boost_trace["accepted_count"] == 1
    assert boost_trace["rejected_count"] == 0
    assert boost_trace["candidate_count"] == 1
    assert boost_trace["candidates"][0]["action"] == "accepted_to_evidence"
    assert boost_trace["candidates"][0]["path"] == "wiki/systems/toolkit-session-handoff-protocol.md"
    assert boost_trace["candidates"][0]["gate_outcome"] == "evidence_gate_passed"
    candidate_ids = [item["candidate_id"] for item in result["trace"]["evidence_gate"]]
    assert len(candidate_ids) == len(set(candidate_ids))


def test_memory_answer_core_no_boost_when_selected_evidence_is_not_thin(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/toolkit-session-handoff-protocol.md",
        "score": 19.5,
        "reasons": ["shared_keyword:toolkit"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Toolkit handoff protocol",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary"],
        "text_hash": "boost-ok",
    }])
    captured = {}
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())

    def two_hit_result():
        return {
            "query": "q",
            "scope": "wiki",
            "strategy": "grouped-intent-budget-v1",
            "primary_scope": "wiki",
            "primary_results": [
                {
                    "source": "wiki",
                    "path": "wiki/systems/agent-write-toolkit.md",
                    "title": "Agent write toolkit",
                    "snippet": "toolkit evidence for write_memory",
                    "score": 10.0,
                },
                {
                    "source": "wiki",
                    "path": "wiki/systems/toolkit-session-write_memory-reference.md",
                    "title": "Tooling reference",
                    "snippet": "toolkit write_memory reference",
                    "score": 9.0,
                },
            ],
            "groups": {
                "wiki": [
                    {
                        "source": "wiki",
                        "path": "wiki/systems/agent-write-toolkit.md",
                        "title": "Agent write toolkit",
                        "snippet": "toolkit evidence for write_memory",
                        "score": 10.0,
                    },
                    {
                        "source": "wiki",
                        "path": "wiki/systems/toolkit-session-write_memory-reference.md",
                        "title": "Tooling reference",
                        "snippet": "toolkit write_memory reference",
                        "score": 9.0,
                    },
                ],
            },
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: two_hit_result())
    def fake_llm(_query, evidence):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "Answer without boost.",
            "summary": "No boost summary.",
            "claims": [{"id": "c1", "text": "base", "support": ["e1", "e2"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("toolkit write_memory", scope="wiki", run_id="related-boost-off-many")

    assert result["status"] == "ok"
    assert captured["evidence_paths"] == [
        "wiki/systems/agent-write-toolkit.md",
        "wiki/systems/toolkit-session-write_memory-reference.md",
    ]
    assert [item["id"] for item in result["evidence"]] == ["e1", "e2"]
    boost_trace = result["trace"]["recommendation_boosted_candidates"]
    assert boost_trace["candidate_count"] == 0
    assert boost_trace["accepted_count"] == 0


def test_memory_answer_core_no_boost_for_current_freshness(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/toolkit-session-handoff-protocol.md",
        "score": 19.5,
        "reasons": ["shared_keyword:toolkit"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Toolkit handoff protocol",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary"],
        "text_hash": "boost-ok",
    }])
    captured = {}
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer, "classify_query", lambda *_args, **_kwargs: "recall")
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))

    def fake_llm(_query, evidence):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "Current freshness answer.",
            "summary": "Current summary.",
            "claims": [{"id": "c1", "text": "current", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("today toolkit", scope="wiki", run_id="related-no-current")

    assert result["status"] == "ok"
    assert captured["evidence_paths"] == ["wiki/systems/agent-write-toolkit.md"]
    assert result["trace"]["planner"]["freshness_mode"] == "current"
    boost_trace = result["trace"]["recommendation_boosted_candidates"]
    assert boost_trace["candidate_count"] == 0


def test_memory_answer_core_no_boost_for_private_or_forbidden_related_targets(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    _write_related_map(related_map, [
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "wiki/person/tiger.md",
            "score": 19.5,
            "reasons": ["markdown_link:wiki/person/tiger.md"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "Tiger",
            "target_status": "active",
            "sensitivity": "person",
            "built_from": ["markdown_links"],
            "text_hash": "forbidden-boost",
        },
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": "wiki/systems/toolkit-session-handoff-protocol.md",
            "score": 19.5,
            "reasons": ["shared_keyword:toolkit", "markdown_link:wiki/systems/toolkit-session-handoff-protocol.md"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "Toolkit handoff protocol",
            "target_status": "active",
            "sensitivity": "normal",
            "built_from": ["summary"],
            "text_hash": "boost-ok",
        },
    ])
    captured = {}
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))

    def fake_llm(_query, evidence):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "Private sensitive answer.",
            "summary": "Private summary.",
            "claims": [{"id": "c1", "text": "private", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("private toolkit", scope="wiki", run_id="related-no-private")

    assert result["status"] == "ok"
    assert captured["evidence_paths"] == ["wiki/systems/agent-write-toolkit.md"]
    assert result["trace"]["recommendation_boosted_candidates"]["candidate_count"] == 0
    assert all(
        item["path"] != "wiki/person/tiger.md"
        for item in result["trace"]["recommendation_boosted_candidates"].get("candidates", [])
    )


def test_memory_answer_core_related_boost_rejects_by_gate_and_records_block(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    reason_canary = "boost-gate-reason-canary-20260611"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/unrelated-reference-note.md",
        "score": 1.0,
        "reasons": ["shared_keyword:unrelated"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Unrelated",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary"],
        "text_hash": "boost-reject",
    }])
    captured = {}
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))
    original_gate = tm_answer._passes_evidence_gate

    def _passes_evidence_gate_with_canary(item: dict, query_class: str) -> tuple[bool, str]:
        if str(item.get("path", "")) == "wiki/systems/unrelated-reference-note.md":
            return False, f"weak evidence: {reason_canary}"
        return original_gate(item, query_class)

    monkeypatch.setattr(tm_answer, "_passes_evidence_gate", _passes_evidence_gate_with_canary)

    def fake_llm(_query, evidence):
        captured["evidence_paths"] = [item["path"] for item in evidence]
        return True, {
            "status": "ok",
            "answer": "Rejected by gate.",
            "summary": "Rejected summary.",
            "claims": [{"id": "c1", "text": "rejected", "support": ["e1"], "confidence": 0.9}],
            "warnings": [],
        }

    monkeypatch.setattr(tm_answer, "_call_memory_answer_llm", fake_llm)

    result = tm_answer.memory_answer_core("toolkit write_memory", scope="wiki", run_id="related-gate-reject")

    assert result["status"] == "ok"
    assert captured["evidence_paths"] == ["wiki/systems/agent-write-toolkit.md"]
    boost_trace = result["trace"]["recommendation_boosted_candidates"]
    assert boost_trace["candidate_count"] == 1
    assert boost_trace["accepted_count"] == 0
    assert boost_trace["rejected_count"] == 1
    assert boost_trace["candidates"][0]["action"] == "rejected_by_gate"
    assert boost_trace["candidates"][0]["path"] == "wiki/systems/unrelated-reference-note.md"
    assert boost_trace["candidates"][0]["gate_outcome"].startswith("evidence_gate_rejected")
    assert "reason" not in boost_trace["candidates"][0]
    assert boost_trace["candidates"][0]["reason_category"] == "unknown" or boost_trace["candidates"][0]["reason_category"]
    assert "reason_hash" in boost_trace["candidates"][0]
    assert "wiki/systems/unrelated-reference-note.md" not in [item["path"] for item in result["evidence"]]
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    stored_payload = json.dumps(trace_row, ensure_ascii=False)
    assert reason_canary not in stored_payload


def test_memory_answer_core_boost_filters_forbidden_targets_from_boosting(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    forbidden_targets = [
        "wiki/tmp/toolkit-tmp-note.md",
        "sources/tmp/toolkit-temp-note.md",
        "wiki/tests/toolkit-test-note.md",
        "sources/tests/toolkit-test-note.md",
        "wiki/review-artifacts/toolkit-review.md",
        "sources/review-artifacts/toolkit-review.md",
        "runtime/memory_recommendation/forbidden.jsonl",
    ]
    _write_related_map(related_map, [
        {
            "source_path": "wiki/systems/agent-write-toolkit.md",
            "target_path": path,
            "score": 20.0,
            "reasons": ["shared_keyword:toolkit"],
            "source_surface": "wiki",
            "target_surface": "wiki",
            "target_title": "Forbidden boost target",
            "target_status": "active",
            "sensitivity": "normal",
            "built_from": ["summary"],
            "text_hash": "boost-forbidden",
        }
        for path in forbidden_targets
    ] + [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/toolkit-session-handoff-protocol.md",
        "score": 20.0,
        "reasons": ["shared_keyword:toolkit", "markdown_link:wiki/systems/toolkit-session-handoff-protocol.md"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": "Allowed boost target",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary", "markdown_links"],
        "text_hash": "boost-ok",
    }])
    call_count = {"n": 0}

    def fake_search(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _search_result(_recommendation_search_hit())
        return {
            "query": "toolkit write_memory",
            "scope": "wiki",
            "strategy": "grouped-intent-budget-v1",
            "primary_scope": "wiki",
            "primary_results": [
                {
                    "source": "wiki",
                    "path": path,
                    "title": path,
                    "snippet": "toolkit write_memory",
                    "score": 10.0,
                }
                for path in forbidden_targets
            ] + [{
                "source": "wiki",
                "path": "wiki/systems/toolkit-session-handoff-protocol.md",
                "title": "Toolkit handoff protocol",
                "snippet": "toolkit write_memory",
                "score": 10.0,
            }],
            "groups": {},
            "warnings": [],
        }

    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", fake_search)
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Forbidden target filtered.",
            "summary": "Filtered summary.",
            "claims": [{"id": "c1", "text": "filtered", "support": ["e1", "e2"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core("toolkit write_memory", scope="wiki", run_id="related-boost-forbidden")
    captured["evidence_paths"] = [item["path"] for item in result["evidence"]]
    assert captured["evidence_paths"] == [
        "wiki/systems/agent-write-toolkit.md",
        "wiki/systems/toolkit-session-handoff-protocol.md",
    ]
    assert result["trace"]["recommendation_boosted_candidates"]["candidate_count"] == 1
    assert [
        item["path"]
        for item in result["trace"]["recommendation_boosted_candidates"]["candidates"]
    ] == ["wiki/systems/toolkit-session-handoff-protocol.md"]
    trace_gate_paths = [entry["path"] for entry in result["trace"]["evidence_gate"]]
    for forbidden_path in forbidden_targets:
        assert forbidden_path not in trace_gate_paths


def test_memory_answer_core_boost_trace_excludes_raw_query_and_candidate_excerpt_fields(monkeypatch, tmp_path):
    related_map = tmp_path / "related_map.jsonl"
    raw_query_canary = "raw-query-canary-boost-20260611"
    reason_canary = "reason-canary-boost-20260611"
    _write_related_map(related_map, [{
        "source_path": "wiki/systems/agent-write-toolkit.md",
        "target_path": "wiki/systems/toolkit-session-handoff-protocol.md",
        "score": 19.5,
        "reasons": [f"shared_summary_token:{reason_canary}"],
        "source_surface": "wiki",
        "target_surface": "wiki",
        "target_title": f"Title with {raw_query_canary}",
        "target_status": "active",
        "sensitivity": "normal",
        "built_from": ["summary"],
        "text_hash": "boost-ok",
    }])
    monkeypatch.setattr(tm_answer, "RELATED_MAP_PATH", related_map)
    monkeypatch.setattr(tm_answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setenv(tm_answer.QUERY_PLANNER_ENV, "0")
    monkeypatch.setattr(tm_answer, "_map_candidate_plan", lambda *_args, **_kwargs: _empty_map_plan())
    monkeypatch.setattr(tm_answer.tm_search, "search_tigermemory", lambda *_args, **_kwargs: _search_result(_recommendation_search_hit()))
    monkeypatch.setattr(
        tm_answer,
        "_call_memory_answer_llm",
        lambda _q, _e: (True, {
            "status": "ok",
            "answer": "Trace safe answer.",
            "summary": "Trace safe summary.",
            "claims": [{"id": "c1", "text": "claim", "support": ["e1", "e2"], "confidence": 0.9}],
            "warnings": [],
        }),
    )

    result = tm_answer.memory_answer_core(f"toolkit {raw_query_canary}", scope="wiki", run_id="related-boost-trace")

    assert result["trace"]["recommendation_boosted_candidates"]["candidate_count"] == 1
    boost_trace = result["trace"]["recommendation_boosted_candidates"]["candidates"][0]
    assert "title" not in boost_trace
    assert "excerpt" not in boost_trace
    trace_row = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    stored_payload = json.dumps(trace_row, ensure_ascii=False)
    assert raw_query_canary not in stored_payload
    assert reason_canary not in stored_payload
    assert "Title with" not in stored_payload
    assert boost_trace["reason_categories"] == ["shared_summary_token"]


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
