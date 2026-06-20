from __future__ import annotations

import importlib
import pathlib


def test_instance_root_prefers_new_env(monkeypatch, tmp_path: pathlib.Path) -> None:
    app_root = tmp_path / "public-core"
    instance_root = tmp_path / "private-instance"
    app_root.mkdir()
    instance_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))
    monkeypatch.setenv("TIGERMEMORY_INSTANCE_ROOT", str(instance_root))
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path / "legacy-root"))

    roots = importlib.import_module("tigermemory_core.roots")

    assert roots.resolve_app_root() == app_root.resolve()
    assert roots.resolve_instance_root() == instance_root.resolve()


def test_instance_root_falls_back_to_legacy_env(monkeypatch, tmp_path: pathlib.Path) -> None:
    legacy_root = tmp_path / "legacy-instance"
    legacy_root.mkdir()
    monkeypatch.delenv("TIGERMEMORY_INSTANCE_ROOT", raising=False)
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(legacy_root))

    roots = importlib.import_module("tigermemory_core.roots")

    assert roots.resolve_instance_root() == legacy_root.resolve()


def test_app_root_prefers_repo_root_over_package_pyproject(monkeypatch, tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "tigermemory"
    package_root = repo_root / "packages" / "tigermemory-core"
    module_file = package_root / "src" / "tigermemory_core" / "roots.py"
    module_file.parent.mkdir(parents=True)
    (repo_root / "tigermemory_cli.py").write_text("", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text("", encoding="utf-8")
    (package_root / "pyproject.toml").write_text("", encoding="utf-8")
    module_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("TIGERMEMORY_APP_ROOT", raising=False)

    roots = importlib.import_module("tigermemory_core.roots")
    monkeypatch.setattr(roots, "__file__", str(module_file))

    assert roots.resolve_app_root() == repo_root.resolve()
