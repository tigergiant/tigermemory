"""tigermemory-core extracted as standalone package.

New code should `from tigermemory_core import ...`. This file keeps
`import tm_core` callers working until P3-A2 follow-up consolidates
the re-export layer.

Inputs: re-exports all public names from tigermemory_core
Outputs: same module surface as the original tm_core.py
Depends-on: tigermemory_core (installed via `pip install -e ./packages/tigermemory-core`)
"""
from __future__ import annotations

import pathlib as _pathlib
import sys as _sys

_HERE = _pathlib.Path(__file__).resolve()
_PKG_SRC = _HERE.parent.parent / "packages" / "tigermemory-core" / "src"
if _PKG_SRC.is_dir() and str(_PKG_SRC) not in _sys.path:
    _sys.path.insert(0, str(_PKG_SRC))

from tigermemory_core import *  # noqa: F401, F403, E402
from tigermemory_core import (  # noqa: F401, E402
    _EMBED_BREAKER,
    _env_value,
    _log_llm_call,
)
