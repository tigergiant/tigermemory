from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from tigermemory_update import apply_update, get_update_status


def git(repo: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def init_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "initial")
    return path


def clone_with_origin(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    seed = init_repo(tmp_path / "seed")
    git(seed, "branch", "-M", "master")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "master")
    work = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(work))
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "user.name", "Test User")
    return work, seed


def add_remote_commit(seed: pathlib.Path, name: str = "remote.txt") -> None:
    (seed / name).write_text("remote\n", encoding="utf-8")
    git(seed, "add", name)
    git(seed, "commit", "-m", f"add {name}")
    git(seed, "push")


def test_status_reports_non_git_checkout(tmp_path: pathlib.Path):
    status = get_update_status(tmp_path)

    assert status["source_mode"] == "installed_package"
    assert status["safe_to_apply"] is False
    assert status["requires_user_action"] is True
    assert status["reason"] == "not_git_source"


def test_status_reports_clean_git_checkout(tmp_path: pathlib.Path):
    repo = init_repo(tmp_path / "repo")

    status = get_update_status(repo)

    assert status["source_mode"] in {"git_source", "editable_install"}
    assert status["dirty"] is False
    assert status["branch"] in {"master", "main"}
    assert status["head"]


def test_clean_behind_remote_is_safe_to_apply(tmp_path: pathlib.Path):
    work, seed = clone_with_origin(tmp_path)
    add_remote_commit(seed)

    status = get_update_status(work, refresh_remote=True)

    assert status["behind"] == 1
    assert status["update_available"] is True
    assert status["safe_to_apply"] is True


def test_tracked_dirty_file_blocks_apply(tmp_path: pathlib.Path):
    work, seed = clone_with_origin(tmp_path)
    add_remote_commit(seed)
    (work / "README.md").write_text("local edit\n", encoding="utf-8")

    result = apply_update(work, dry_run=True)

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["reason"] == "dirty_worktree"


def test_local_commit_blocks_ff_only_apply(tmp_path: pathlib.Path):
    work, seed = clone_with_origin(tmp_path)
    add_remote_commit(seed)
    (work / "local.txt").write_text("local\n", encoding="utf-8")
    git(work, "add", "local.txt")
    git(work, "commit", "-m", "local commit")

    result = apply_update(work, dry_run=True)

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["reason"] == "local_commits"


def test_apply_dry_run_reports_planned_strategy(tmp_path: pathlib.Path):
    work, seed = clone_with_origin(tmp_path)
    add_remote_commit(seed)

    result = apply_update(work, dry_run=True)

    assert result["ok"] is True
    assert result["applied"] is False
    assert result["reason"] == "dry_run"
    assert result["planned_strategy"] == "ff-only"


def test_fetch_failure_returns_remote_unavailable(tmp_path: pathlib.Path):
    work, _seed = clone_with_origin(tmp_path)
    git(work, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = apply_update(work, dry_run=True)

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["reason"] == "remote_unavailable"


def test_rebase_conflict_aborts_and_reports(tmp_path: pathlib.Path):
    work, seed = clone_with_origin(tmp_path)
    (seed / "README.md").write_text("remote edit\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-m", "remote edit")
    git(seed, "push")
    (work / "README.md").write_text("local edit\n", encoding="utf-8")
    git(work, "add", "README.md")
    git(work, "commit", "-m", "local edit")

    result = apply_update(work, strategy="rebase")

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["reason"] == "conflict"
    assert not (work / ".git" / "rebase-merge").exists()
    assert not (work / ".git" / "rebase-apply").exists()


def test_editable_install_metadata_is_detected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    repo = init_repo(tmp_path / "repo")

    class FakeDist:
        def read_text(self, name: str) -> str | None:
            if name != "direct_url.json":
                return None
            return json.dumps(
                {
                    "url": repo.as_uri(),
                    "dir_info": {"editable": True},
                }
            )

    monkeypatch.setattr(
        "tigermemory_update.importlib_metadata.distributions",
        lambda: [FakeDist()],
    )

    status = get_update_status(repo)

    assert status["source_mode"] == "editable_install"
