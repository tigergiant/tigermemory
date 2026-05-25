#!/usr/bin/env python3
"""Compatibility shim for tigermemory_doctor.retention."""
from __future__ import annotations

try:
    import _bootstrap_paths  # noqa: F401
except ModuleNotFoundError:
    from tools import _bootstrap_paths  # noqa: F401

import sys

import tigermemory_doctor.retention as _impl

sys.modules[__name__] = _impl

if __name__ == "__main__":
    _impl.main()
