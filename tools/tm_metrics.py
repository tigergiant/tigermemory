"""Compatibility shim for tigermemory_doctor.metrics."""
from __future__ import annotations

try:
    import _bootstrap_paths  # noqa: F401
except ModuleNotFoundError:
    from tools import _bootstrap_paths  # noqa: F401

import sys

import tigermemory_doctor.metrics as _impl

sys.modules[__name__] = _impl

if __name__ == "__main__":
    _impl.main()
