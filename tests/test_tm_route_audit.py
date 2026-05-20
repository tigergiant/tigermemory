from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_memory_ops  # type: ignore[import-not-found]
import tm_route  # type: ignore[import-not-found]
import tm_route_audit  # type: ignore[import-not-found]


def _decision(route: str = "discard", score: int = 82) -> tm_route.RouteDecision:
    return tm_route.RouteDecision(
        route=route,
        score=score,
        topic_inferred="systems",
        issues=["test"],
        reasons="test decision",
        is_transient=(route == "discard"),
        is_sensitive=False,
        needs_human_review=False,
    )


def test_discard_event_is_redacted_and_bucketed(tmp_path):
    decision = _decision()
    now = dt.datetime(2026, 5, 21, 23, 55, tzinfo=tm_route_audit.tm_core.TZ_CN)

    audit = tm_route_audit.record_discard_event(
        agent="codex",
        requested_topic="systems",
        text="discard this Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        decision=decision,
        audit_root=tmp_path,
        now=now,
    )

    assert audit["ok"] is True
    path = tmp_path / "2026-05-21" / "discard" / "events.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["route"] == "discard"
    assert row["score"] == 82
    assert "[REDACTED:bearer_token]" in row["text_excerpt"]
    assert "abcdefghijklmnopqrstuvwxyz" not in row["text_excerpt"]


def test_discard_summary_flags_high_score_discards(tmp_path):
    decision = _decision(score=91)
    tm_route_audit.record_discard_event(
        agent="codex",
        requested_topic="systems",
        text="high quality but rejected",
        decision=decision,
        audit_root=tmp_path,
        now=dt.datetime(2026, 5, 21, 1, 0, tzinfo=tm_route_audit.tm_core.TZ_CN),
    )

    summary = tm_route_audit.run_summary(date="2026-05-21", audit_root=tmp_path)

    assert summary["event_count"] == 1
    assert summary["route_counts"] == {"discard": 1}
    assert summary["review_flags"][0]["reason"] == "high_score_discard"


def test_write_memory_records_discard_quarantine_only(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_route_audit, "DEFAULT_AUDIT_ROOT", tmp_path)
    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: _decision("discard", 20))

    result = tm_memory_ops.write_memory_with_review("codex", "systems", "discard me")

    assert result["route"] == "discard"
    assert result["discard_audit"]["ok"] is True
    assert (tmp_path / result["discard_audit"]["date"] / "discard" / "events.jsonl").exists()


def test_write_memory_mem0_does_not_duplicate_temp_audit(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tm_route_audit, "DEFAULT_AUDIT_ROOT", tmp_path)
    monkeypatch.setattr(tm_route_audit, "record_discard_event", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(tm_memory_ops.tm_route, "route_memory", lambda *_args, **_kwargs: _decision("mem0", 90))
    monkeypatch.setattr(
        tm_memory_ops.tm_core,
        "mem0_write",
        lambda *_args, **_kwargs: json.dumps({"id": "11111111-1111-4111-8111-111111111111"}),
    )
    monkeypatch.setattr(tm_memory_ops, "schedule_digest_refresh", lambda: None)

    result = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        "store me",
        include_readback=False,
    )

    assert result["route"] == "mem0"
    assert "discard_audit" not in result
    assert calls == []
