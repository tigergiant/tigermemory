#!/usr/bin/env python3
"""Compatibility CLI for tigermemory-config's read-only config explainer."""
from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE_SRC = _REPO_ROOT / "packages" / "tigermemory-config" / "src"
if _PACKAGE_SRC.is_dir():
    sys.path.insert(0, str(_PACKAGE_SRC))

import tigermemory_config as _impl  # noqa: E402

if __name__ == "__main__":
    sys.exit(_impl.main())
else:
    sys.modules[__name__] = _impl
