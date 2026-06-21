#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

import _bootstrap_paths  # noqa: F401

try:
    from tigerledger.review_server import main
except ModuleNotFoundError:
    package_src = pathlib.Path(__file__).resolve().parents[1] / "packages" / "tigerledger" / "src"
    sys.path.insert(0, str(package_src))
    from tigerledger.review_server import main


if __name__ == "__main__":
    raise SystemExit(main())
