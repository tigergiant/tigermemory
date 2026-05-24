#!/usr/bin/env python3
"""Check deploy/openclaw-ce/openclaw.plugin.json against runtime copy.
Inputs: Local repo state, service health endpoints, lessons/wiki pages, Mem0 records, or dashboard preference files.
Outputs: Doctor/audit/onboarding/metrics reports, local UI helper effects, or JSON diagnostics.
Depends-on (must-have): tm_core helpers, local filesystem/git state, and configured local services when the command asks for live checks.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "deploy/openclaw-ce/openclaw.plugin.json"
RUNTIME = "/home/giant/.openclaw/extensions/tigermemory-ce/openclaw.plugin.json"

def _read(path: str | Path) -> str:
    p = Path(path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    r = subprocess.run(["wsl", "cat", str(path)], capture_output=True, text=True)
    return r.stdout

def _norm(text: str) -> str:
    return json.dumps(json.loads(text), sort_keys=True, indent=2)

def main() -> int:
    run_text = _read(RUNTIME)
    if not run_text:
        print(f"MISSING runtime: {RUNTIME}", file=sys.stderr)
        return 1
    src_text = _norm(_read(SRC))
    run_text = _norm(run_text)
    if src_text == run_text:
        print("OK: plugin.json sync")
        return 0
    import difflib
    diff = difflib.unified_diff(src_text.splitlines(), run_text.splitlines(),
                                fromfile=str(SRC), tofile=RUNTIME, lineterm="")
    sys.stderr.writelines(line + "\n" for line in diff)
    return 1

if __name__ == "__main__":
    sys.exit(main())
