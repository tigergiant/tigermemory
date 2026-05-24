#!/usr/bin/env python3
"""Compatibility shim for tigerledger.

New code should import from tigerledger directly. This file keeps legacy
tools imports working while the package boundary settles.
"""
from __future__ import annotations

import importlib as _importlib
import pathlib as _pathlib
import sys as _sys

_PKG_SRC = _pathlib.Path(__file__).resolve().parent.parent / "packages" / "tigerledger" / "src"
if _PKG_SRC.is_dir() and str(_PKG_SRC) not in _sys.path:
    _sys.path.insert(0, str(_PKG_SRC))

_mod = _importlib.import_module("tigerledger.import_alipay")
_sys.modules[__name__] = _mod
globals().update({name: value for name, value in vars(_mod).items() if not name.startswith("__")})
__all__ = [name for name in globals() if not name.startswith("_")]



