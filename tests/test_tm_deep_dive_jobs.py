from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_deep_dive_jobs  # type: ignore[import-not-found]


class _FakePopen:
    def __init__(self, *_args, **_kwargs):
        self.pid = os.getpid()


def test_start_job_returns_running_status(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(tm_deep_dive_jobs.subprocess, "Popen", _FakePopen)

    result = tm_deep_dive_jobs.start_job("600519.SH", "2026-05-16", profile="fast")

    assert result["ok"] is True
    assert result["status"] == "running"
    assert result["ticker"] == "600519.SH"
    assert result["profile"] == "fast"
    status = tm_deep_dive_jobs.get_status(result["job_id"])
    assert status["status"] == "running"
    assert status["profile"] == "fast"
    assert status["worker_pid"] == os.getpid()


def test_worker_writes_completed_result(tmp_path, monkeypatch):
    jobs_root = tmp_path / "jobs"
    ta_root = tmp_path / "TradingAgents"
    tools_dir = ta_root / "tools"
    tools_dir.mkdir(parents=True)
    adapter = tools_dir / "tm_adapter.py"
    payload = {
        "ok": True,
        "ticker": "600519.SH",
        "trade_date": "2026-05-16",
        "rating": "Hold",
        "profile": "fast",
        "processed_signal": "Hold",
        "warnings": [],
        "report_paths": {"final_decision": "/tmp/final.md"},
        "cost_estimate_usd": 0.0,
    }
    adapter.write_text(
        "import json\n"
        f"print(json.dumps({payload!r}, ensure_ascii=False, sort_keys=True))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADINGAGENTS_JOB_ROOT", str(jobs_root))
    monkeypatch.setenv("TRADINGAGENTS_ROOT", str(ta_root))
    monkeypatch.setenv("TRADINGAGENTS_PYTHON", sys.executable)

    job_id = "ta-20260519T010203Z-abcdef12"
    rc = tm_deep_dive_jobs.run_worker(job_id, "600519.SH", "2026-05-16", profile="fast")

    assert rc == 0
    status = tm_deep_dive_jobs.get_status(job_id)
    assert status["status"] == "completed"
    assert status["rating"] == "Hold"
    assert status["profile"] == "fast"
    result = tm_deep_dive_jobs.fetch_result(job_id)
    assert result["ok"] is True
    assert result["job_id"] == job_id
    assert result["rating"] == "Hold"
    assert result["profile"] == "fast"


def test_archive_decision_log_outputs_commits_only_generated_paths(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    report = repo / "wiki" / "investment" / "decision-log" / "600519.SH" / "2026-05-16" / "final_decision.md"
    report.parent.mkdir(parents=True)
    report.write_text("final decision\n", encoding="utf-8")
    unrelated = repo / "unrelated.txt"
    unrelated.write_text("leave me unstaged\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=repo, check=True)
    monkeypatch.setenv("TRADINGAGENTS_DECISION_LOG_AUTO_PUSH", "0")
    monkeypatch.setenv("TM_AGENT", "codex")

    result = tm_deep_dive_jobs.archive_decision_log_outputs(
        {
            "trade_date": "2026-05-16",
            "report_paths": {"final_decision": str(report)},
        }
    )

    assert result["ok"] is True
    assert result["commits"][0]["committed"] is True
    tracked = subprocess.run(
        ["git", "ls-files", "--", "wiki/investment/decision-log/600519.SH/2026-05-16/final_decision.md"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "final_decision.md" in tracked.stdout
    scoped_status = subprocess.run(
        ["git", "status", "--porcelain", "--", "wiki/investment/decision-log"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert scoped_status.stdout == ""
    full_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "?? unrelated.txt" in full_status.stdout
    subject = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert subject.stdout.strip() == "[codex] create: archive TradingAgents decision logs 2026-05-16"


def test_pid_alive_for_self_does_not_kill_caller():
    """Regression: on Windows, _pid_alive(os.getpid()) used to call
    os.kill(self, 0) which equals TerminateProcess(self, 0), leaving
    pytest in an inconsistent state that hangs the next
    subprocess.run(capture_output=True). Must return True without harm.
    """
    assert tm_deep_dive_jobs._pid_alive(os.getpid()) is True


def test_pid_alive_for_none_and_zero_returns_none():
    assert tm_deep_dive_jobs._pid_alive(None) is None
    assert tm_deep_dive_jobs._pid_alive(0) is None
    assert tm_deep_dive_jobs._pid_alive(-1) is None


def test_pid_alive_for_unlikely_pid_is_not_true():
    # Very large pid unlikely to exist on either platform. Accept False or
    # None (permission edge cases), but never True.
    result = tm_deep_dive_jobs._pid_alive(2**30)
    assert result is not True


def test_fetch_result_before_completion_returns_status(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_JOB_ROOT", str(tmp_path / "jobs"))
    job_id = "ta-20260519T010203Z-12345678"
    directory = tm_deep_dive_jobs.job_dir(job_id)
    directory.mkdir(parents=True)
    tm_deep_dive_jobs.status_path(job_id).write_text(
        json.dumps(
            {
                "ok": True,
                "job_id": job_id,
                "ticker": "600519.SH",
                "trade_date": "2026-05-16",
                "status": "running",
                "worker_pid": None,
            }
        ),
        encoding="utf-8",
    )

    result = tm_deep_dive_jobs.fetch_result(job_id)

    assert result["ok"] is False
    assert result["status"] == "running"
    assert result["status_detail"]["job_id"] == job_id
