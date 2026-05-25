#!/usr/bin/env python3
"""Compatibility shim for tigermemory_eval.stability."""
from __future__ import annotations

import _bootstrap_paths  # noqa: F401

import sys

import tigermemory_eval.stability as _impl

sys.modules[__name__] = _impl
