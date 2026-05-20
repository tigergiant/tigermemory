from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_route  # type: ignore[import-not-found]
import tm_route_audit  # type: ignore[import-not-found]
import tm_route_replay  # type: ignore[import-not-found]


def test_route_replay_matrix_and_severe_flips():
    cases = [
        tm_route_replay.ReplayCase("a", "mem0", "a", "systems", "codex", "mem0", 90, "old"),
        tm_route_replay.ReplayCase("b", "discard", "b", "systems", "codex", "discard", 20, "old"),
        tm_route_replay.ReplayCase("c", "inbox", "c", "systems", "codex", "inbox", 50, "old"),
    ]

    def judge(case: tm_route_replay.ReplayCase, _prompt: str) -> tm_route_replay.ReplayDecision:
        mapping = {"a": "discard", "b": "mem0", "c": "inbox"}
        return tm_route_replay.ReplayDecision(route=mapping[case.case_id], score=case.original_score, reason="mock")

    result = tm_route_replay.replay_cases(
        cases,
        date="2026-05-21",
        new_prompt="new",
        proposal_id="proposal-2026-05-21-001",
        judge=judge,
    )

    assert result["matrix"]["mem0"]["discard"] == 1
    assert result["matrix"]["discard"]["mem0"] == 1
    assert result["matrix"]["inbox"]["inbox"] == 1
    assert result["severe_count"] == 2
    assert result["recommendation"] == "apply"
    assert len(result["severe_cases"]) == 2


def test_route_replay_rejects_when_many_severe_flips():
    cases = [
        tm_route_replay.ReplayCase(f"case-{i}", "mem0", "x", "systems", "codex", "mem0", 90, "old")
        for i in range(6)
    ]

    result = tm_route_replay.replay_cases(
        cases,
        date="2026-05-21",
        new_prompt="new",
        proposal_id="proposal-2026-05-21-001",
        judge=lambda case, _prompt: tm_route_replay.ReplayDecision(route="discard", reason="mock"),
    )

    assert result["severe_count"] == 6
    assert result["recommendation"] == "reject-by-default"


def test_collect_discard_cases_from_quarantine(tmp_path):
    decision = tm_route.RouteDecision(
        route="discard",
        score=25,
        topic_inferred="systems",
        issues=[],
        reasons="low quality",
        is_transient=True,
        is_sensitive=False,
        needs_human_review=False,
    )
    tm_route_audit.record_discard_event(
        agent="codex",
        requested_topic="systems",
        text="discard me",
        decision=decision,
        audit_root=tmp_path,
        now=dt.datetime(2026, 5, 21, tzinfo=tm_route_audit.tm_core.TZ_CN),
    )

    cases = tm_route_replay.collect_discard_cases(dates={"2026-05-21"}, audit_root=tmp_path)

    assert len(cases) == 1
    assert cases[0].source == "discard"
    assert cases[0].original_route == "discard"
    assert cases[0].topic == "systems"


def test_collect_inbox_cases_reads_frontmatter(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "2026-05-21-1200-codex-systems.md").write_text(
        """---
source: codex
route_requested_topic: systems
stored_topic: systems
route_score: 48
route_decision_reason: medium score
---
body text
""",
        encoding="utf-8",
    )

    cases = tm_route_replay.collect_inbox_cases(dates={"2026-05-21"}, inbox_dir=inbox)

    assert len(cases) == 1
    assert cases[0].source == "inbox"
    assert cases[0].agent == "codex"
    assert cases[0].original_score == 48


def test_run_replay_writes_result_with_judgment_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_route_replay, "PROPOSAL_ROOT", tmp_path / "proposals")
    monkeypatch.setattr(
        tm_route_replay,
        "collect_cases",
        lambda **_kwargs: [
            tm_route_replay.ReplayCase("case-1", "discard", "x", "systems", "codex", "discard", 20, "old")
        ],
    )
    judgments = tmp_path / "judgments.json"
    judgments.write_text(json.dumps({"case-1": {"route": "mem0", "score": 80, "reason": "mock"}}), encoding="utf-8")

    result = tm_route_replay.run_replay(
        date="2026-05-21",
        proposal_id="proposal-2026-05-21-001",
        new_prompt="new prompt",
        judgment_file=judgments,
    )

    assert result["matrix"]["discard"]["mem0"] == 1
    out = tmp_path / "proposals" / "2026-05-21" / "proposal-2026-05-21-001" / "replay-result.json"
    assert out.exists()
