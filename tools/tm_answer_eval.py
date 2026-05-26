#!/usr/bin/env python3
"""Compatibility shim for tigermemory_answer.eval."""
from __future__ import annotations

import _bootstrap_paths  # noqa: F401

import sys

import tigermemory_answer.eval as _impl

sys.modules[__name__] = _impl


if __name__ == "__main__":
    _impl.main()
