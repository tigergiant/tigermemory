from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_agent_doctor  # type: ignore[import-not-found]


def test_agent_doctor_aggregates_ok_warn_and_fail(monkeypatch):
    monkeypatch.setattr(tm_agent_doctor, "check_worktree", lambda: {"name": "worktree", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_tm_http", lambda _url=None: {"name": "tm_http", "status": "warn", "ok": False})
    monkeypatch.setattr(tm_agent_doctor, "check_mem0", lambda: {"name": "mem0_api", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "search_lessons", lambda _query: {"name": "lessons", "status": "fail", "ok": False, "hit_count": 0})
    monkeypatch.setattr(tm_agent_doctor, "recent_lessons_log", lambda: {"name": "lessons_log", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_retention", lambda: {"name": "retention_audit", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_l2_review", lambda: {"name": "l2_review", "status": "ok", "ok": True})

    report = tm_agent_doctor.run_agent_doctor(query="x")

    assert report["status"] == "fail"
    assert report["ok"] is False
    assert report["summary"] == {"fail_count": 1, "warn_count": 1, "ok_count": 5}
    assert "Resolve failing checks" in report["recommended_action"]


def test_agent_doctor_can_skip_l2(monkeypatch):
    called = []
    monkeypatch.setattr(tm_agent_doctor, "check_worktree", lambda: {"name": "worktree", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_tm_http", lambda _url=None: {"name": "tm_http", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_mem0", lambda: {"name": "mem0_api", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "search_lessons", lambda _query: {"name": "lessons", "status": "ok", "ok": True, "hit_count": 2})
    monkeypatch.setattr(tm_agent_doctor, "recent_lessons_log", lambda: {"name": "lessons_log", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_retention", lambda: {"name": "retention_audit", "status": "ok", "ok": True})
    monkeypatch.setattr(tm_agent_doctor, "check_l2_review", lambda: called.append("l2"))

    report = tm_agent_doctor.run_agent_doctor(query="x", include_l2=False)

    assert report["status"] == "ok"
    assert called == []
    assert [check["name"] for check in report["checks"]] == [
        "worktree",
        "tm_http",
        "mem0_api",
        "lessons",
        "lessons_log",
        "retention_audit",
    ]


def test_agent_doctor_markdown_includes_evidence():
    report = {
        "generated_at": "2026-05-21T10:00:00+08:00",
        "status": "warn",
        "recommended_action": "inspect warn checks",
        "checks": [
            {"name": "worktree", "status": "ok", "head": "abc", "dirty_count": 0},
            {"name": "tm_http", "status": "warn", "error": "offline | refused"},
            {"name": "retention_audit", "status": "ok", "item_count": 3, "offline_only": True},
        ],
    }

    markdown = tm_agent_doctor.render_markdown(report)

    assert "# Tigermemory Agent Doctor" in markdown
    assert "offline \\| refused" in markdown
    assert "offline_only=True" in markdown


def test_agent_doctor_retention_check_runs_real_offline_sample():
    # Test that real check_retention runs offline and returns correct schema
    res = tm_agent_doctor.check_retention()
    assert res["name"] == "retention_audit"
    assert res["status"] == "ok"
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert res["offline_only"] is True
    assert "item_count" in res
    assert "action_counts" in res


def test_check_mem0_uses_lightweight_memories_probe(monkeypatch):
    calls = {}

    def fake_request(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return '{"items":[],"total":0}'

    monkeypatch.setattr(tm_agent_doctor.tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_agent_doctor.tm_core, "mem0_user_id", lambda: "tiger")
    monkeypatch.setattr(tm_agent_doctor.tm_core, "mem0_request", fake_request)

    res = tm_agent_doctor.check_mem0(timeout=4)

    assert res["status"] == "ok"
    assert calls["url"] == "http://localhost:8765/api/v1/memories/?user_id=tiger&page=1&size=1&match_mode=id_first"
    assert calls["kwargs"]["timeout"] == 4
