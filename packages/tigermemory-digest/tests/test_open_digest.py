from __future__ import annotations

import pathlib

from tigermemory_digest import open_digest


def test_resolve_digest_path_accepts_relative_path_inside_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(open_digest.tm_core, "REPO_ROOT", tmp_path)

    resolved = open_digest.resolve_digest_path("wiki/operations/daily.md")

    assert resolved == (tmp_path / "wiki" / "operations" / "daily.md").resolve()


def test_resolve_digest_path_rejects_absolute_path_outside_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(open_digest.tm_core, "REPO_ROOT", tmp_path / "repo")

    try:
        open_digest.resolve_digest_path(str(tmp_path / "outside.md"))
    except ValueError as exc:
        assert "inside tigermemory repo" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_main_returns_two_when_argument_is_missing():
    assert open_digest.main([]) == 2


def test_main_returns_two_when_path_leaves_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(open_digest.tm_core, "REPO_ROOT", tmp_path / "repo")

    assert open_digest.main([str(tmp_path / "outside.md")]) == 2


def test_main_returns_zero_when_viewer_launch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(open_digest.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(open_digest, "open_path", lambda _path: (_ for _ in ()).throw(RuntimeError("boom")))

    assert open_digest.main(["wiki/operations/daily.md"]) == 0
