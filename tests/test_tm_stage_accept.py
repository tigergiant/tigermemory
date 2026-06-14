from __future__ import annotations

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_stage_accept as accept


def test_acceptance_requires_objective_evidence():
    result = accept.validate_acceptance(
        stage="p1b",
        summary="agent says it is done",
        evidence=["agent: I checked it"],
    )

    assert result["ok"] is False
    assert result["accepted_count"] == 0


def test_acceptance_accepts_commit_test_and_review_path(monkeypatch, tmp_path):
    monkeypatch.setattr(accept, "REPO_ROOT", tmp_path)
    review = tmp_path / "sources" / "review.md"
    review.parent.mkdir(parents=True)
    review.write_text("# review\n", encoding="utf-8")

    result = accept.validate_acceptance(
        stage="p1b",
        summary="contract added",
        evidence=[
            "commit:8753fdaf",
            "test: py -m pytest tests/test_tm_stage_accept.py => 2 passed",
            "review:sources/review.md",
        ],
    )

    assert result["ok"] is True
    assert result["accepted_count"] == 3
    assert result["accepted_kinds"] == ["commit", "review", "test"]

