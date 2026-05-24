from __future__ import annotations

import importlib
import pathlib
import sys

_PKG_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

import tigermemory_core


def test_detect_repo_root_uses_explicit_env(monkeypatch, tmp_path):
    custom_root = tmp_path / "custom-tigermemory-root"
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(custom_root))

    module = importlib.reload(tigermemory_core)

    assert module.REPO_ROOT == custom_root.resolve()


def test_detect_repo_root_walks_up_from_repo(monkeypatch):
    monkeypatch.delenv("TIGERMEMORY_ROOT", raising=False)

    module = importlib.reload(tigermemory_core)

    assert (module.REPO_ROOT / ".git").is_dir()
    assert (module.REPO_ROOT / "wiki").is_dir()


def test_detect_repo_root_empty_env_falls_back_to_walk_up(monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_ROOT", "")

    module = importlib.reload(tigermemory_core)

    assert (module.REPO_ROOT / ".git").is_dir()
    assert (module.REPO_ROOT / "wiki").is_dir()
