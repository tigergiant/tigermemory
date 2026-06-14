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


def test_acceptance_rejects_api_test_review_as_formal_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(accept, "REPO_ROOT", tmp_path)
    review = tmp_path / "sources" / "api-review.md"
    review.parent.mkdir(parents=True)
    review.write_text("---\nchannel: claude-api-test\n---\n\n# draft\n", encoding="utf-8")

    result = accept.validate_acceptance(
        stage="p1b",
        summary="api draft should not release",
        evidence=[f"review:{review}"],
    )

    assert result["ok"] is False
    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 1
    assert result["evidence"][0]["channel"] == "claude-api-test"
    assert "draft evidence" in result["evidence"][0]["reason"]


def test_acceptance_records_api_test_review_as_draft_only(monkeypatch, tmp_path):
    monkeypatch.setattr(accept, "REPO_ROOT", tmp_path)
    review = tmp_path / "sources" / "api-review.md"
    review.parent.mkdir(parents=True)
    review.write_text("---\nchannel: claude-api-test\n---\n\n# draft\n", encoding="utf-8")

    result = accept.validate_acceptance(
        stage="p1b",
        summary="api draft is auxiliary",
        evidence=[f"draft_review:{review}"],
    )

    assert result["ok"] is False
    assert result["accepted_count"] == 0
    assert result["auxiliary_count"] == 1
    assert result["auxiliary_kinds"] == ["draft_review"]


def test_acceptance_accepts_official_review_as_formal_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(accept, "REPO_ROOT", tmp_path)
    review = tmp_path / "sources" / "official-review.md"
    review.parent.mkdir(parents=True)
    review.write_text("---\nchannel: claude-official-review\n---\n\n# review\n", encoding="utf-8")

    result = accept.validate_acceptance(
        stage="p1b",
        summary="official review can release",
        evidence=[f"review:{review}"],
    )

    assert result["ok"] is True
    assert result["accepted_count"] == 1
    assert result["accepted_kinds"] == ["review"]
