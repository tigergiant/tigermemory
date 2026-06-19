from __future__ import annotations

import hashlib
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import project_canvas_autocommit_guard as guard  # type: ignore[import-not-found]


def test_extract_diff_lines_ignores_headers() -> None:
    diff = "\n".join(
        [
            "diff --git a/wiki/operations/project-canvas.md b/wiki/operations/project-canvas.md",
            "--- a/wiki/operations/project-canvas.md",
            "+++ b/wiki/operations/project-canvas.md",
            "@@ -1 +1 @@",
            "-old status",
            "+new status",
        ]
    )

    assert guard._extract_added_lines(diff) == ["new status"]
    assert guard._extract_removed_lines(diff) == ["old status"]


def test_safety_allows_negated_no_order_investment_status() -> None:
    lines = [
        (
            "直接含义：当前只是 no-order 观察，不是策略通过，不能下单；"
            "passable_evidence_count=0 / ready_for_strategy_order_count=0 / "
            "trade=false / real=false / prod=false / touch1999=false"
        )
    ]

    assert guard._added_line_safety_issues(lines) == []


def test_safety_blocks_positive_trade_readiness_and_1999() -> None:
    lines = [
        "ETF ready_for_strategy_order_count=1 / trade=false",
        "debug route touch1999=true",
        "release_gate=passed",
    ]

    issues = guard._added_line_safety_issues(lines)

    assert [item["check"] for item in issues] == [
        "positive_trade_readiness_count",
        "dangerous_boolean_true",
        "release_gate_pass_claim",
    ]


def test_hash_source_report_accepts_literal_hash(tmp_path: pathlib.Path) -> None:
    hash_value = "a" * 64
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "artifact.json").write_text(
        f'{{"source_hash": "{hash_value}"}}',
        encoding="utf-8",
    )

    report = guard._hash_source_report([f"hash `{hash_value}`"], tmp_path)

    assert report["ok"] is True
    assert report["missing"] == []
    assert hash_value in report["sources"]


def test_hash_source_report_accepts_uppercase_literal_hash_source(tmp_path: pathlib.Path) -> None:
    hash_value = "a" * 64
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "artifact.md").write_text(
        f"file sha256 `{hash_value.upper()}`\n",
        encoding="utf-8",
    )

    report = guard._hash_source_report([f"file sha256 `{hash_value}`"], tmp_path)

    assert report["ok"] is True
    assert report["missing"] == []
    assert hash_value in report["sources"]


def test_hash_source_report_accepts_literal_hash_without_rg(tmp_path: pathlib.Path, monkeypatch) -> None:
    hash_value = "b" * 64
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "artifact.md").write_text(
        f"payload hash `{hash_value}`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard.shutil, "which", lambda name: None)

    report = guard._hash_source_report([f"payload hash `{hash_value}`"], tmp_path)

    assert report["ok"] is True
    assert report["missing"] == []
    assert report["sources"][hash_value].replace("\\", "/") == "reports/artifact.md"


def test_hash_source_report_accepts_file_sha256(tmp_path: pathlib.Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    artifact = reports / "artifact.json"
    artifact.write_text('{"ok": true, "trade": false}\n', encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    report = guard._hash_source_report([f"artifact hash `{digest}`"], tmp_path)

    assert report["ok"] is True
    assert report["missing"] == []
    assert report["sources"][digest].replace("\\", "/") == "reports/artifact.json"


def test_hash_source_report_blocks_missing_hash(tmp_path: pathlib.Path) -> None:
    (tmp_path / "reports").mkdir()
    missing = "f" * 64

    report = guard._hash_source_report([f"missing hash `{missing}`"], tmp_path)

    assert report["ok"] is False
    assert report["missing"] == [missing]
