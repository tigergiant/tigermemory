#!/usr/bin/env python3
"""Open a generated memory digest in the user's local viewer.
Inputs: CLI/API payloads, inbox or digest markdown, route decisions, proposal metadata, or Mem0 write requests.
Outputs: Rendered markdown, JSON status payloads, routed memory writes, proposal decisions, or review actions.
Depends-on (must-have): tm_core, tm_route/tm_memory_ops helpers, local git-managed files, and configured Mem0/OpenMemory endpoints.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import tigermemory_core as tm_core


def resolve_digest_path(raw: str) -> pathlib.Path:
    path = pathlib.Path(raw)
    if not path.is_absolute():
        path = tm_core.REPO_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(tm_core.REPO_ROOT)
    except ValueError as exc:
        raise ValueError("path must stay inside tigermemory repo") from exc
    return path


def open_path(path: pathlib.Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    subprocess.run(["xdg-open", str(path)], check=False)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: tm_open_digest.py <repo-relative-digest-path>", file=sys.stderr)
        return 2
    try:
        path = resolve_digest_path(args[0])
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        open_path(path)
    except Exception as exc:
        print(f"WARN: viewer launch failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
