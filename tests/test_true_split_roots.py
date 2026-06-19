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
