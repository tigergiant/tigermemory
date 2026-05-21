from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_open_digest  # type: ignore[import-not-found]


def test_resolve_digest_path_accepts_repo_relative_path():
    path = tm_open_digest.resolve_digest_path("wiki/operations/example.md")

    assert path == (REPO_ROOT / "wiki" / "operations" / "example.md").resolve()


def test_resolve_digest_path_rejects_outside_repo(tmp_path):
    outside = tmp_path / "x.md"

    try:
        tm_open_digest.resolve_digest_path(str(outside))
    except ValueError as exc:
        assert "inside tigermemory repo" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_main_returns_two_for_missing_arg():
    assert tm_open_digest.main([]) == 2


def test_main_returns_zero_when_viewer_fails(monkeypatch):
    monkeypatch.setattr(tm_open_digest, "open_path", lambda _path: (_ for _ in ()).throw(RuntimeError("boom")))

    assert tm_open_digest.main(["wiki/operations/example.md"]) == 0
