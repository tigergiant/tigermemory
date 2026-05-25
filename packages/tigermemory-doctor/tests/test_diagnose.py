from __future__ import annotations

import json

from tigermemory_doctor import diagnose
from tigermemory_doctor import retention


def test_check_worktree_reports_dirty_paths_without_blocking_clean_tree(monkeypatch):
    monkeypatch.setattr(diagnose.tm_core, "git_session_status", lambda: {
        "ok": True,
        "head": "abc123",
        "branch": "master",
        "upstream": "origin/master",
        "ahead": 0,
        "behind": 0,
        "dirty_count": 2,
        "blockers": [],
        "paths": ["tools/x.py"],
    })

    result = diagnose.check_worktree()

    assert result["status"] == "ok"
    assert result["dirty_count"] == 2
    assert result["paths"] == ["tools/x.py"]


def test_check_tm_http_returns_unavailable_when_request_fails(monkeypatch):
    def fake_urlopen(_url, timeout):
        raise TimeoutError(f"timeout={timeout}")

    monkeypatch.setattr(diagnose.urllib.request, "urlopen", fake_urlopen)

    result = diagnose.check_tm_http("http://127.0.0.1:8790", timeout=1)

    assert result["status"] == "warn"
    assert result["ok"] is False
    assert "timeout=1" in result["error"]


def test_check_mem0_reports_api_health_payload(monkeypatch):
    calls = []

    def fake_mem0_request(url, timeout):
        calls.append((url, timeout))
        return {"ok": True}

    monkeypatch.setattr(diagnose.tm_core, "mem0_base", lambda: "http://mem0")
    monkeypatch.setattr(diagnose.tm_core, "mem0_request", fake_mem0_request)

    result = diagnose.check_mem0(timeout=4)

    assert result["status"] == "ok"
    assert calls == [("http://mem0/api/v1/memories/categories?user_id=tiger", 4)]


def test_check_l2_review_scores_probe_text(monkeypatch):
    monkeypatch.setattr(diagnose.tm_review, "review_draft", lambda text, timeout: {
        "score": 88,
        "review_skipped": False,
        "reason": "ok",
    })

    result = diagnose.check_l2_review(timeout=2)

    assert result["status"] == "ok"
    assert result["score"] == 88
    assert result["review_skipped"] is False


def test_check_retention_uses_retention_module_functions():
    assert diagnose.score_item is retention.score_item
    assert diagnose.run_retention_audit is retention.run_retention_audit
    assert diagnose.load_mem0_json is retention.load_mem0_json


def test_run_agent_doctor_combines_checks_and_warnings(monkeypatch):
    monkeypatch.setattr(diagnose, "check_worktree", lambda: {"name": "worktree", "status": "ok"})
    monkeypatch.setattr(diagnose, "check_tm_http", lambda _url: {"name": "tm_http", "status": "warn"})
    monkeypatch.setattr(diagnose, "check_mem0", lambda: {"name": "mem0", "status": "ok"})
    monkeypatch.setattr(diagnose, "search_lessons", lambda query: {"name": "lessons", "status": "ok", "query": query})
    monkeypatch.setattr(diagnose, "recent_lessons_log", lambda: {"name": "lessons_log", "status": "ok"})
    monkeypatch.setattr(diagnose, "check_retention", lambda: {"name": "retention", "status": "ok"})
    monkeypatch.setattr(diagnose, "check_l2_review", lambda: {"name": "l2", "status": "ok"})

    report = diagnose.run_agent_doctor(query="doctor query", http_url="http://tm")

    assert report["status"] == "warn"
    assert report["summary"] == {"fail_count": 0, "warn_count": 1, "ok_count": 6}
    assert [check["name"] for check in report["checks"]] == ["worktree", "tm_http", "mem0", "lessons", "lessons_log", "retention", "l2"]
