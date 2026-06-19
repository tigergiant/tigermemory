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


def test_detect_repo_root_prefers_cwd_snapshot_for_wheel_install(monkeypatch, tmp_path):
    monkeypatch.delenv("TIGERMEMORY_ROOT", raising=False)
    parent = tmp_path / "private-parent"
    snapshot = parent / "snapshot"
    site_package = parent / ".tmp" / "venv" / "Lib" / "site-packages" / "tigermemory_core"
    (parent / "wiki").mkdir(parents=True)
    (parent / "tools").mkdir()
    (snapshot / "wiki").mkdir(parents=True)
    (snapshot / "tools").mkdir()
    site_package.mkdir(parents=True)
    installed_init = site_package / "__init__.py"
    installed_init.write_text("# installed package marker\n", encoding="utf-8")

    monkeypatch.chdir(snapshot)
    monkeypatch.setattr(tigermemory_core, "__file__", str(installed_init))

    assert tigermemory_core._detect_repo_root() == snapshot.resolve()


def test_detect_repo_root_prefers_explicit_instance_root(monkeypatch, tmp_path):
    instance_root = tmp_path / "instance"
    instance_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_INSTANCE_ROOT", str(instance_root))
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path / "legacy"))

    module = importlib.reload(tigermemory_core)

    assert module.REPO_ROOT == instance_root.resolve()
