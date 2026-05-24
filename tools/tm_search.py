#!/usr/bin/env python3
"""Compatibility shim for tigermemory_answer.

Historical entry point. Real implementation now lives in the
``tigermemory_answer`` package. This shim re-exports its public API for
backwards compatibility.
"""
from __future__ import annotations

import _bootstrap_paths  # noqa: F401

from tigermemory_answer import *  # noqa: F401,F403
