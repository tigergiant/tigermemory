#!/usr/bin/env python3
"""Root resolution helpers for TigerMemory true-split layout."""

from __future__ import annotations

import os
import pathlib


def _path_from_env(name: str) -> pathlib.Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return pathlib.Path(value).expanduser().resolve()


def resolve_instance_root(cwd: pathlib.Path | None = None) -> pathlib.Path:
    explicit = _path_from_env("TIGERMEMORY_INSTANCE_ROOT")
    if explicit is not None:
        return explicit
    legacy = _path_from_env("TIGERMEMORY_ROOT")
    if legacy is not None:
        return legacy
    return (cwd or pathlib.Path.cwd()).resolve()


def resolve_app_root(cwd: pathlib.Path | None = None) -> pathlib.Path:
    explicit = _path_from_env("TIGERMEMORY_APP_ROOT")
    if explicit is not None:
        return explicit
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (ancestor / "pyproject.toml").is_file() or (ancestor / "tigermemory_cli.py").is_file():
            return ancestor
    return (cwd or pathlib.Path.cwd()).resolve()


def subprocess_root_env(instance_root: pathlib.Path) -> dict[str, str]:
    root = str(instance_root.resolve())
    return {
        "TIGERMEMORY_INSTANCE_ROOT": root,
        "TIGERMEMORY_ROOT": root,
    }
