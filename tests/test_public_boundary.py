from __future__ import annotations

import json
import pathlib

import tigermemory_publish


PUBLIC_BOUNDARY_PAGE = """---
public: true
title: "public boundary page"
updated: 2026-06-18
owner: cascade
status: active
---

# Public boundary page

公开发布快照应只保留公共可发布内容。
"""


HOST_PATH_LEAK_PAGE = """---
public: true
title: "host path leak page"
updated: 2026-06-18
owner: cascade
status: active
---

# Host path leak

Do not publish machine-absolute paths like D:\\tigermemory or C:\\Users\\Giant.
"""


def _write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_snapshot_repo(root: pathlib.Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("placeholder\n", encoding="utf-8")
    _write_text(root / "wiki" / "systems" / "public-boundary.md", PUBLIC_BOUNDARY_PAGE)
    _write_text(root / "wiki" / "person" / "prefs.md", "# private person note\n")
    _write_text(root / "sources" / "internal-analysis" / "note.md", "Sensitive internal analysis should stay private.\n")
    _write_text(root / ".tmp" / "cache" / "artifact.md", "local temp artifact\n")
    _write_text(root / "runtime" / "openmemory" / ".env", "OPENMEMORY_KEY=stub\n")
    _write_text(root / "review-artifacts" / "feedback.md", "private review artifacts\n")
    _write_text(root / "data" / "expense" / "ledger.md", "expense data stays private\n")
    _write_text(
        root / "wiki" / "investment" / "plan.md",
        "---\npublic: true\ntitle: private investment plan\n---\n\n# private investment material\n",
    )

    # keep a public page present so snapshot still has positive content
    _write_text(root / "tools" / "tm_io.py", "# publish smoke helper\n")


def _build_repo_audit_secret(root: pathlib.Path) -> None:
    _build_snapshot_repo(root)
    _write_text(root / "data" / "sensitive.txt", "api_key=sk-1234567890abcdefghijklmnopqrstuvwxyz\n")


def _run_publish(capsys, repo: pathlib.Path, destination: pathlib.Path, args: list[str] | None = None) -> tuple[int, dict]:
    args = ["--dest", str(destination), "--json", *(args or [])]
    tigermemory_publish.REPO_ROOT = repo
    rc = tigermemory_publish.main(args)
    summary = json.loads(capsys.readouterr().out)
    return rc, summary


def test_snapshot_build_excludes_private_boundary_paths(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    _build_snapshot_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    plan = tigermemory_publish.collect_publish_plan(repo)
    snapshot = tmp_path / "snapshot"
    copied = tigermemory_publish.execute_plan(plan, repo, snapshot)

    assert copied > 0
    assert (snapshot / "wiki" / "systems" / "public-boundary.md").is_file()
    assert not (snapshot / "wiki" / "person").exists(), "wiki/person must be blocked for public snapshot"
    assert not (snapshot / "sources" / "internal-analysis").exists(), "sources/internal-analysis should not be shipped"
    assert not (snapshot / ".tmp").exists(), ".tmp must not be shipped"
    assert not (snapshot / "runtime" / "openmemory" / ".env").exists(), "runtime env secrets must not be shipped"
    assert not (snapshot / "review-artifacts").exists(), "review-artifacts must not be shipped"
    assert not (snapshot / "data" / "expense").exists(), "data/expense must not be shipped"
    assert not (snapshot / "wiki" / "investment").exists(), "investment资料 should not be shipped in open snapshot"


def test_snapshot_audit_blocks_local_path_leaks_with_path_and_reason(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    _build_snapshot_repo(repo)
    _write_text(repo / "wiki" / "systems" / "host-path.md", HOST_PATH_LEAK_PAGE)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc, summary = _run_publish(capsys, repo, tmp_path / "snapshot")

    assert rc == 3
    assert summary["ok"] is False
    assert summary["audit_scope"] == "snapshot"
    path_leaks = [
        finding
        for finding in summary["sensitive_findings"]
        if finding["path"] == "wiki/systems/host-path.md" and finding["kind"] == "path_leak"
    ]
    assert path_leaks, "path-leak should return explicit finding path+reason"
    assert path_leaks[0]["severity"] == "high"


def test_repo_audit_scope_does_not_block_snapshot_release(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    _build_repo_audit_secret(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    snapshot_rc, snapshot_summary = _run_publish(capsys, repo, tmp_path / "snapshot")
    repo_rc, repo_summary = _run_publish(
        capsys,
        repo,
        tmp_path / "repo-audit",
        ["--audit-scope", "repo"],
    )

    assert snapshot_rc == 0
    assert snapshot_summary["ok"] is True
    assert snapshot_summary["audit_scope"] == "snapshot"
    assert not snapshot_summary["sensitive_findings"]
    assert repo_rc == 3
    assert repo_summary["ok"] is False
    assert repo_summary["audit_scope"] == "repo"
    assert any(f["path"] == "data/sensitive.txt" and f["kind"] == "api_key" for f in repo_summary["sensitive_findings"])
