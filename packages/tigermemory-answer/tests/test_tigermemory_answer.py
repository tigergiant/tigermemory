from __future__ import annotations

import datetime
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _pkg_src in (
    REPO_ROOT / "packages" / "tigermemory-core" / "src",
    REPO_ROOT / "packages" / "tigermemory-lessons" / "src",
    REPO_ROOT / "packages" / "tigermemory-persona" / "src",
    REPO_ROOT / "packages" / "tigermemory-answer" / "src",
):
    if str(_pkg_src) not in sys.path:
        sys.path.insert(0, str(_pkg_src))

import tigermemory_answer as answer


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


def _ok_llm(_query: str, _evidence: list[dict]) -> tuple[bool, dict]:
    return True, {
        "status": "ok",
        "answer": "Use the evidence.",
        "summary": "Answered from evidence.",
        "claims": [{"id": "c1", "text": "supported", "support": ["e1"], "confidence": 0.9}],
        "warnings": [],
    }


def test_format_search_hit_minimal_contract():
    hit = answer.format_search_hit("wiki", "wiki/systems/a.md", "A", "snippet", 1.5)

    assert hit == {
        "source": "wiki",
        "path": "wiki/systems/a.md",
        "title": "A",
        "snippet": "snippet",
        "score": 1.5,
    }


def test_format_search_hit_optional_metadata_contract():
    hit = answer.format_search_hit(
        "wiki",
        "wiki/systems/a.md",
        "A",
        "snippet",
        1.0,
        extra={"created_at": "2026-05-25"},
        score_breakdown={"rrf_score": 1.0},
        injection_eligible=True,
        injection_reason="recent",
    )

    assert hit["score_breakdown"] == {"rrf_score": 1.0}
    assert hit["created_at"] == "2026-05-25"
    assert hit["injection_eligible"] is True
    assert hit["injection_reason"] == "recent"


def test_redact_secrets_masks_bearer_tokens():
    assert answer.redact_secrets("Authorization: Bearer abcdefghijklmnopqrstuvwxyz") == "Authorization: [REDACTED]"


def test_redact_secrets_masks_api_key_assignments():
    assert answer.redact_secrets("api_key = abcdefghijklmnop") == "[REDACTED]"


def test_query_hash_is_stable_and_query_sensitive():
    assert answer.query_hash("same") == answer.query_hash("same")
    assert answer.query_hash("same") != answer.query_hash("different")


def test_normalize_run_id_redacts_and_truncates():
    run_id = "token=abcdefghijklmnopqrstuvwxyz" * 10

    normalized = answer.normalize_run_id(run_id)

    assert normalized == "[REDACTED]"


def test_trim_evidence_for_prompt_empty_short_circuit():
    assert answer.trim_evidence_for_prompt([], max_chars=10) == ([], [])


def test_trim_evidence_for_prompt_budget_boundary():
    trimmed, warnings = answer.trim_evidence_for_prompt(
        [{"id": "e1", "excerpt": "abcde"}, {"id": "e2", "excerpt": "fghij"}],
        max_chars=7,
    )

    assert [item["excerpt"] for item in trimmed] == ["abcde", "fg"]
    assert warnings == ["prompt_budget_truncated=true"]


def test_decide_injection_eligibility_wiki_is_evidence_only():
    result = answer.decide_injection_eligibility({"source": "wiki", "path": "wiki/systems/a.md"})

    assert result == {
        "injection_eligible": False,
        "injection_reason": "canonical_wiki_evidence_only",
    }


def test_decide_injection_eligibility_short_onboarding_is_allowed():
    result = answer.decide_injection_eligibility({"source": "onboarding", "title": "Agent Onboarding Snapshot (30s)"})

    assert result["injection_eligible"] is True
    assert result["injection_reason"] == "agent_persona_snapshot"


def test_decide_injection_eligibility_recent_mem0_is_allowed():
    now = datetime.datetime(2026, 5, 25, tzinfo=datetime.timezone.utc)
    result = answer.decide_injection_eligibility(
        {
            "source": "mem0",
            "created_at": "2026-05-24T00:00:00+00:00",
            "score_breakdown": {"route_decision": "mem0"},
        },
        now=now,
    )

    assert result["injection_eligible"] is True
    assert result["injection_reason"] == "recent_atomic_memory"


def test_expand_queries_reads_external_registry(monkeypatch, tmp_path):
    registry = tmp_path / "query_expansions.json"
    registry.write_text(json.dumps([{"patterns": ["unit trigger"], "expansions": ["unit expansion"]}]), encoding="utf-8")
    monkeypatch.setattr(answer, "QUERY_EXPANSION_REGISTRY", registry)

    assert "unit expansion" in answer.expand_queries("please use unit trigger")


def test_expand_queries_builtin_p52_boundary():
    expanded = answer.expand_queries("P5.2 只读 自动下单")

    assert any("不触发 MiniQMT" in item for item in expanded)


def test_scan_conflicts_detects_base_boundary():
    result = answer.scan_conflicts(
        "是否冲突",
        [{"id": "e1", "title": "P5.2", "excerpt": "不自动下单，但也写了真实下单"}],
        "conflict_audit",
    )

    assert result["conflict"] is True
    assert result["conflicts"][0]["evidence_ids"] == ["e1"]


def test_scan_conflicts_reads_registry(monkeypatch, tmp_path):
    registry = tmp_path / "conflict_patterns.json"
    registry.write_text(json.dumps([{"id": "unit-status", "positive": ["done"], "negative": ["pending"]}]), encoding="utf-8")
    monkeypatch.setattr(answer, "CONFLICT_PATTERN_REGISTRY", registry)

    result = answer.scan_conflicts(
        "unit conflict",
        [{"id": "e1", "title": "status", "excerpt": "done and pending"}],
        "conflict_audit",
    )

    assert any(item["name"] == "unit-status" for item in result["conflicts"])


def test_expand_evidence_scores_and_limits_candidates():
    search_result = _search_result({
        "source": "wiki",
        "path": "wiki/systems/agent-write-toolkit.md",
        "title": "Agent toolkit",
        "snippet": "toolkit evidence",
        "score": 1.0,
    })

    evidence, gate = answer.expand_evidence("toolkit evidence", search_result, 1, "recall")

    assert gate[0]["keep"] is True
    assert evidence[0]["id"] == "e1"
    assert evidence[0]["authority"] >= 90.0
    assert evidence[0]["source_role"] == "canonical_wiki"


def test_expand_evidence_normalizes_sources_path_even_when_source_is_wiki(monkeypatch):
    monkeypatch.setattr(
        answer,
        "_read_hit_content",
        lambda _path: "# Review Archive\n\nalpha review archive",
    )
    search_result = _search_result({
        "source": "wiki",
        "path": "sources/internal-analysis/development-reviews/2026-06-15/review.md",
        "title": "Review Archive",
        "snippet": "alpha review archive",
        "score": 20.0,
    })

    evidence, gate = answer.expand_evidence("alpha", search_result, 1, "recall")

    assert evidence[0]["source"] == "sources"
    assert evidence[0]["source_role"] == "source_material"
    assert evidence[0]["authority"] == 70.0
    assert gate[0]["source"] == "sources"


def test_read_hit_content_allows_exact_root_wiki_allowlist_only():
    assert answer._read_hit_content("AGENTS.md") is not None
    assert answer._read_hit_content("README.md") is None


def test_expand_evidence_filters_weak_candidates():
    search_result = _search_result({
        "source": "wiki",
        "path": "wiki/systems/unrelated.md",
        "title": "Other",
        "snippet": "nothing relevant",
        "score": 1.0,
    })

    evidence, gate = answer.expand_evidence("needle-only", search_result, 1, "recall")

    assert evidence == []
    assert gate[0]["keep"] is False


def test_search_tigermemory_wiki_smoke(monkeypatch):
    monkeypatch.setattr(answer.tm_core, "primary_search_scope", lambda _q: "wiki")
    monkeypatch.setattr(
        answer.tm_core,
        "search_wiki_hybrid",
        lambda *_args, **_kwargs: [{
            "path": "wiki/systems/a.md",
            "title": "A",
            "snippet": "body",
            "score": 1.0,
            "score_breakdown": {"rrf": 1.0},
        }],
    )

    result = answer.search_tigermemory("query", scope="wiki", dogfood_log=None)

    assert result["primary_results"][0]["path"] == "wiki/systems/a.md"
    assert result["primary_results"][0]["score_breakdown"] == {"rrf": 1.0}


def test_memory_answer_core_normal_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(answer, "TRACE_LOG", tmp_path / "trace.jsonl")
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: _search_result({
        "source": "wiki",
        "path": "wiki/systems/agent-write-toolkit.md",
        "title": "Agent toolkit",
        "snippet": "toolkit evidence",
        "score": 1.0,
    }))
    monkeypatch.setattr(answer, "_call_memory_answer_llm", _ok_llm)

    result = answer.memory_answer_core("toolkit evidence", scope="wiki", run_id="unit")

    assert result["status"] == "ok"
    assert result["claims"][0]["support"] == ["e1"]
    assert result["run_id"] == "unit"
    assert (tmp_path / "trace.jsonl").exists()


def test_memory_answer_core_not_found_skips_llm(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: _search_result())
    monkeypatch.setattr(answer, "_call_memory_answer_llm", lambda *_args: calls.append("llm"))

    result = answer.memory_answer_core("missing", scope="wiki", write_trace=False)

    assert result["status"] == "not_found"
    assert calls == []


def test_memory_answer_core_disable_trace_write(monkeypatch, tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setattr(answer, "TRACE_LOG", trace_path)
    monkeypatch.setattr(answer, "search_tigermemory", lambda *_args, **_kwargs: _search_result())

    result = answer.memory_answer_core("missing", scope="wiki", write_trace=False)

    assert result["status"] == "not_found"
    assert not trace_path.exists()


def test_memory_answer_core_empty_query_raises_value_error():
    try:
        answer.memory_answer_core("")
    except ValueError as exc:
        assert "query must be non-empty" in str(exc)
    else:
        raise AssertionError("expected ValueError")
