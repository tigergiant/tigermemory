#!/usr/bin/env python3
"""Auto-inject all ``packages/*/src`` directories into ``sys.path``.

Single source of truth for tigermemory's package path resolution.
Any ``tools/*.py`` entry-point that imports a ``tigermemory_*`` or
``tigerledger`` package **must** ``import _bootstrap_paths`` as the
very first import after ``from __future__`` and the stdlib block,
before importing any tigermemory package.

Why this module exists
----------------------
Before this module landed, launcher scripts (``deploy/mcp/*.sh``) and
systemd units (``deploy/mcp/*.service``) each hard-coded
``packages/tigermemory-core/src`` in ``PYTHONPATH``. Every time codex added
a new ``packages/<name>/`` (tigerledger, tigermemory-minimax,
tigermemory-persona, tigermemory-publish, tigermemory-index, etc.), all
7 launchers had to be updated in lockstep, or MCP would crash on startup
with ``ModuleNotFoundError`` — exactly the symptom that produced
``connection closed: initialize response`` for codex CLI sessions on
2026-05-25.

This module replaces all 7 scattered ``PYTHONPATH`` settings with a
single auto-scan. Adding a new package now requires zero launcher
changes — only ``packages/<new-pkg>/src/<module>/`` needs to exist.

How to use
----------
In every ``tools/*.py`` entry-point launched as ``python tools/X.py``::

    #!/usr/bin/env python3
    \"\"\"...docstring...\"\"\"
    from __future__ import annotations

    import _bootstrap_paths  # noqa: F401  -- must be first non-stdlib import

    import tigermemory_core as tm_core
    import tigerledger
    # ... etc

Idempotent
----------
Safe to import multiple times; sys.path entries are deduped.

Side effects
------------
``sys.path`` is mutated at import time. ``packages/*/src`` directories
are prepended (highest priority) in alphabetical order.

Discovery
---------
Newly added ``packages/<name>/src`` directories are auto-discovered;
no edit to this file is needed when codex extracts a new package.
"""
from __future__ import annotations

import pathlib as _pathlib
import sys as _sys

_REPO_ROOT = _pathlib.Path(__file__).resolve().parent.parent
_PACKAGES_DIR = _REPO_ROOT / "packages"


def _bootstrap() -> list[str]:
    """Scan packages/*/src and prepend each to sys.path. Returns list of
    paths actually added (excluding duplicates already present).

    Exposed primarily for the regression test in
    ``tests/test_bootstrap_paths.py``; entry-point code does not need to
    call it explicitly (the module's import-time side effect already runs).
    """
    added: list[str] = []
    if not _PACKAGES_DIR.is_dir():
        return added
    for pkg_dir in sorted(_PACKAGES_DIR.iterdir()):
        if not pkg_dir.is_dir():
            continue
        src = pkg_dir / "src"
        if not src.is_dir():
            continue
        src_str = str(src)
        if src_str not in _sys.path:
            _sys.path.insert(0, src_str)
            added.append(src_str)
    return added


# Import-time side effect: idempotent, safe to import multiple times.
_bootstrap()
