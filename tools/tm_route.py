#!/usr/bin/env python3
"""Compatibility shim for the standalone tigermemory-route package."""
from __future__ import annotations

import _bootstrap_paths  # noqa: F401

import sys

import tigermemory_route as _impl

sys.modules[__name__] = _impl