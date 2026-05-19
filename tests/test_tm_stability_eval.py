from __future__ import annotations

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_stability_eval  # type: ignore[import-not-found]


def _result(rating: str, thesis: str, price_target: float | None = 10.0) -> dict:
    return {
        "ok": True,
        "job_id": f"ta-20260519T010203Z-{abs(hash((rating, thesis))) % 10**8:08d}",
        "status": "completed",
        "rating": rating,
        "price_target": price_target,
        "decision_markdown": f"**Rating**: {rating}\n\n**Investment Thesis**: {thesis}\n\n**Price Target**: {price_target}",
        "provider_trace": {"by_provider": {"openuu-gpt:ok": 3, "openuu-gpt:error": 0}},
    }


def test_summarize_results_marks_stable_when_rating_and_thesis_match():
    thesis = "cash flow margin valuation trend catalyst"

    summary = tm_stability_eval.summarize_results(
        [_result("Hold", thesis), _result("Hold", thesis), _result("Hold", thesis)]
    )

    assert summary["stability"] == "stable"
    assert summary["consensus_rating"] == "Hold"
    assert summary["jaccard_mean"] == 1.0


def test_summarize_results_marks_edge_for_two_to_one_rating_split():
    shared = "cash flow margin valuation trend catalyst"
    variant = "cash flow margin valuation demand catalyst"

    summary = tm_stability_eval.summarize_results(
        [_result("Hold", shared), _result("Hold", variant), _result("Underweight", shared)]
    )

    assert summary["stability"] == "edge"
    assert summary["consensus_rating"] == "Hold"


def test_summarize_results_marks_unstable_for_low_thesis_overlap():
    summary = tm_stability_eval.summarize_results(
        [
            _result("Hold", "alpha beta gamma delta"),
            _result("Hold", "inventory leverage liquidity debt"),
            _result("Hold", "export tariff policy battery"),
        ]
    )

    assert summary["stability"] == "unstable"
    assert summary["jaccard_mean"] < 0.4


def test_summarize_results_marks_single_for_one_run():
    summary = tm_stability_eval.summarize_results([_result("Hold", "cash flow margin valuation")])

    assert summary["stability"] == "single"
    assert summary["consensus_rating"] == "Hold"


def test_summarize_results_marks_failed_single_run_unstable():
    summary = tm_stability_eval.summarize_results(
        [{"ok": False, "job_id": "ta-20260519T010203Z-00000000", "status": "failed"}]
    )

    assert summary["stability"] == "unstable"
    assert summary["failed_count"] == 1


def test_start_stability_eval_fans_out_and_writes_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_stability_eval.tm_core, "REPO_ROOT", tmp_path)
    started: list[str] = []

    def fake_start_job(ticker: str, trade_date: str, profile: str = "deep") -> dict:
        job_id = f"ta-20260519T010203Z-{len(started) + 1:08x}"
        started.append(job_id)
        return {"ok": True, "job_id": job_id, "status": "running", "ticker": ticker, "trade_date": trade_date, "profile": profile}

    def fake_get_status(job_id: str) -> dict:
        return {"ok": True, "job_id": job_id, "status": "completed"}

    def fake_fetch_result(job_id: str) -> dict:
        return _result("Hold", "cash flow margin valuation trend catalyst")

    monkeypatch.setattr(tm_stability_eval.tm_deep_dive_jobs, "start_job", fake_start_job)
    monkeypatch.setattr(tm_stability_eval.tm_deep_dive_jobs, "get_status", fake_get_status)
    monkeypatch.setattr(tm_stability_eval.tm_deep_dive_jobs, "fetch_result", fake_fetch_result)

    result = tm_stability_eval.start_stability_eval(
        "600519.SH", "2026-05-16", profile="deep", n=3, poll_interval_sec=0.01, timeout_sec=5
    )

    assert result["ok"] is True
    assert result["stability"] == "stable"
    assert result["job_ids"] == started
    assert (tmp_path / "wiki" / "investment" / "decision-log" / "600519-2026-05-16-stability-eval.md").exists()
    monthly_log = tmp_path / "wiki" / "investment" / "decision-log" / "600519.SH-2026-05.md"
    assert "stability_label=`stable`" in monthly_log.read_text(encoding="utf-8")
