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
