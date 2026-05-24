#!/usr/bin/env python3
"""Compatibility shim for the standalone tigermemory-index package."""
from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE_SRC = _REPO_ROOT / "packages" / "tigermemory-index" / "src"
if _PACKAGE_SRC.is_dir():
    sys.path.insert(0, str(_PACKAGE_SRC))

import tigermemory_index as _impl  # noqa: E402

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
