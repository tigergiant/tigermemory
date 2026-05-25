"""Append a single guard-reject event to .tmp/guard-rejects.jsonl.

Used by pre-commit / commit-msg / mojibake_guard / tm_io.guard right before
they exit non-zero, so tm_metrics.py can count rejects per month.

CLI (called from shell hooks):
    py tools/tm_reject_log.py append --guard mojibake --file wiki/x.md \\
                                     --line 16 --msg "4+ consecutive ?"

Library (called from inside python guards):
    from tm_reject_log import log_reject
    log_reject(guard="mojibake", file="...", line=16, msg="...")

Stdlib only. Best-effort: any failure here MUST NOT block the commit guard
itself from completing its real job (still exit 1). We swallow logging
errors and proceed.
Inputs: CLI arguments, local repository files, or data supplied by the caller.
Outputs: A deterministic stdout report, file rewrite, or helper return value documented by the command.
Depends-on (must-have): Python stdlib and local tigermemory helper modules; external services only when explicitly requested.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / ".tmp"
LOG_FILE = LOG_DIR / "guard-rejects.jsonl"


def log_reject(
    guard: str,
    file: Optional[str] = None,
    line: Optional[int] = None,
    msg: Optional[str] = None,
    purpose: Optional[str] = None,
    ide: Optional[str] = None,
    hook: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    """Append one JSONL line. Never raises; logging is best-effort.

    `purpose` defaults to "real" but should be "test" for manual probes
    (e.g. `tm_reject_log.py append --purpose test`) and "validation"
    for in-session verification by an agent or auditor. tm_metrics.py
    only counts purpose == "real" toward governance metrics.

    `ide` / `hook` / `context` are optional extensions added 2026-05-26
    for unified cross-IDE reject logging. Existing callers that omit them
    produce records with null values for these fields (backwards-compatible).
    """
    try:
        LOG_DIR.mkdir(exist_ok=True)
        record = {
            "ts": datetime.datetime.now(
                datetime.timezone(datetime.timedelta(hours=8))
            ).isoformat(timespec="seconds"),
            "ide": ide,
            "hook": hook,
            "agent": os.environ.get("TM_AGENT", "unknown"),
            "guard": guard,
            "file": file,
            "line": line,
            "msg": msg,
            "context": context,
            "purpose": purpose or "real",
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Never fail the commit guard because of logging trouble.
        pass


def cmd_append(args: argparse.Namespace) -> int:
    log_reject(
        guard=args.guard,
        file=args.file,
        line=args.line,
        msg=args.msg,
        purpose=args.purpose,
        ide=getattr(args, 'ide', None),
        hook=getattr(args, 'hook', None),
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tm_reject_log.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("append", help="append one reject event")
    sp.add_argument("--guard", required=True, help="guard type, e.g. mojibake / drift / routed_by / partition / owner / commit_msg / mem0_topic")
    sp.add_argument("--file", default=None)
    sp.add_argument("--line", type=int, default=None)
    sp.add_argument("--msg", default=None)
    sp.add_argument("--ide", default=None, help="IDE source: windsurf / codex / git")
    sp.add_argument("--hook", default=None, help="hook name: pre_run_command / pre_tool_use_policy / pre-commit / commit-msg")
    sp.add_argument("--purpose", default="real",
                    choices=["real", "test", "validation"],
                    help="real (counted in metrics) / test (manual probe) / validation (in-session audit)")
    sp.set_defaults(func=cmd_append)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
