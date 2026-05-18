from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_answer  # type: ignore[import-not-found]


def _result(status: str = "ok") -> dict:
    return {
        "status": status,
        "answer": "Use the evidence-backed answer path.",
        "summary": "Answered from evidence.",
        "claims": [{"id": "c1", "text": "The answer is supported.", "support": ["e1"], "confidence": 0.9}],
        "evidence": [{
            "id": "e1",
            "source": "wiki",
            "path": "wiki/systems/agent-write-toolkit.md",
            "title": "Agent toolkit",
            "excerpt": "hidden excerpt should not appear in text mode",
            "score": 12.0,
        }],
        "warnings": [],
        "run_id": None,
        "trace_id": "trace-1",
        "trace": None,
    }


def test_cli_answer_json_delegates_to_core(monkeypatch, capsys):
    captured = {}

    def fake_core(query, scope, top_k, max_evidence, include_trace, run_id):
        captured.update({
            "query": query,
            "scope": scope,
            "top_k": top_k,
            "max_evidence": max_evidence,
            "include_trace": include_trace,
            "run_id": run_id,
        })
        return _result()

    monkeypatch.setattr(tm_answer, "memory_answer_core", fake_core)

    rc = tm_answer.main([
        "answer",
        "memory answer cli",
        "--scope",
        "wiki",
        "--top-k",
        "3",
        "--max-evidence",
        "2",
        "--no-trace",
        "--run-id",
        "cli-run-1",
        "--json",
        "--compact",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert captured == {
        "query": "memory answer cli",
        "scope": "wiki",
        "top_k": 3,
        "max_evidence": 2,
        "include_trace": False,
        "run_id": "cli-run-1",
    }


def test_cli_answer_text_prints_evidence_metadata_not_excerpt(monkeypatch, capsys):
    monkeypatch.setattr(tm_answer, "memory_answer_core", lambda *_args, **_kwargs: _result())

    rc = tm_answer.main(["answer", "memory answer cli", "--scope", "wiki"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "status: ok" in out
    assert "wiki/systems/agent-write-toolkit.md" in out
    assert "trace_id: trace-1" in out
    assert "hidden excerpt" not in out


def test_cli_answer_error_returns_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(tm_answer, "memory_answer_core", lambda *_args, **_kwargs: _result(status="error"))

    rc = tm_answer.main(["answer", "memory answer cli"])

    assert rc == 2
    assert "status: error" in capsys.readouterr().out
