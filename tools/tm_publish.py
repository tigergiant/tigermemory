#!/usr/bin/env python3
"""Compatibility shim for the standalone tigermemory-publish package."""
from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE_SRC = _REPO_ROOT / "packages" / "tigermemory-publish" / "src"
if _PACKAGE_SRC.is_dir() and str(_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_SRC))

import tigermemory_publish as _impl  # noqa: E402

if __name__ == "__main__":
    sys.exit(_impl.main())
else:
    sys.modules[__name__] = _impl
