#!/usr/bin/env python3
"""
tm_io.py — tigermemory I/O CLI adapter (thin shell over tm_core).

All business logic lives in tm_core.py. This module only handles:
- argparse CLI surface
- stdin/stdout I/O
- exit code mapping for shell consumers (git hooks, bash scripts)

Usage:
  tm_io.py write-inbox  --agent <name> --topic <topic> --title <title>   # body on stdin
  tm_io.py commit-push  --files <f>... --agent <name> --action <a> --summary <s>
  tm_io.py mem0-write   --agent <name> --topic <topic>                   # text  on stdin
  tm_io.py mem0-search  --query <q> [--size N]
  tm_io.py lint-page    <path>
  tm_io.py status       [--json]
  tm_io.py preflight    [--json]
  tm_io.py guard        --commit-msg-file <path>                         # called by git hook

Exit codes:
  0  success
  1  lint-page: page has errors
  2  bad usage / validation failure (ValueError)
  3  git operation failed (GitError)
  4  Mem0 API failure (RuntimeError from Mem0)
  5  guard rejected the commit
  6  preflight blocked
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import tm_core


def _die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------- write-inbox ----------

def cmd_write_inbox(args: argparse.Namespace) -> None:
    body = sys.stdin.read().strip()
    if not body:
        _die("body required on stdin", code=2)

    import tm_route

    if args.force_inbox:
        fm_extra = {"routed_by": "tigermemory", "route_decision_reason": "force_inbox CLI"}
        try:
            rel = tm_core.write_inbox_file(args.agent, args.topic, args.title, body, frontmatter_extra=fm_extra)
        except ValueError as e:
            _die(str(e), code=2)
        except FileExistsError as e:
            _die(str(e), code=2)
        print(json.dumps({"route": "inbox", "path": rel}))
        return

    decision = tm_route.route_memory(body, args.topic, args.agent)

    if decision.route == "discard":
        print(json.dumps({
            "route": "discard",
            "score": decision.score,
            "issues": decision.issues,
            "reasons": decision.reasons,
        }))
        return

    if decision.route == "mem0":
        try:
            resp = tm_core.mem0_write(
                args.agent,
                decision.topic_inferred,
                body,
                metadata_extra=decision.as_metadata(),
            )
        except ValueError as e:
            _die(str(e), code=2)
        except RuntimeError as e:
            _die(str(e), code=4)
        print(resp)
        return

    # route == "inbox"
    fm_extra = decision.as_metadata()
    fm_extra["routed_by"] = "tigermemory"
    fm_extra["route_decision_reason"] = decision.reasons
    try:
        rel = tm_core.write_inbox_file(
            args.agent,
            decision.topic_inferred,
            args.title,
            body,
            frontmatter_extra=fm_extra,
        )
    except ValueError as e:
        _die(str(e), code=2)
    except FileExistsError as e:
        _die(str(e), code=2)
    print(json.dumps({
        "route": "inbox",
        "path": rel,
        "score": decision.score,
        "reasons": decision.reasons,
        "unreviewed": decision.unreviewed,
    }))


# ---------- commit-push ----------

def cmd_commit_push(args: argparse.Namespace) -> None:
    try:
        tm_core.validate_agent(args.agent)
        tm_core.validate_action(args.action)
    except ValueError as e:
        _die(str(e), code=2)

    if not args.summary or len(args.summary) > 120:
        _die("summary must be 1-120 chars")

    # Forbid agents from touching shared append files
    forbidden_for_agent = {"log.md"}
    for f in args.files:
        if f in forbidden_for_agent and args.agent != "claude-code":
            _die(f"'{f}' is compiled by claude-code; agents must not write it directly")
        p = (tm_core.REPO_ROOT / f).resolve()
        if not p.exists():
            _die(f"file not found: {f}")
        try:
            p.relative_to(tm_core.REPO_ROOT)
        except ValueError:
            _die(f"file outside repo: {f}")

    msg = f"[{args.agent}] {args.action}: {args.summary}"
    try:
        sha = tm_core.git_commit_push(list(args.files), msg)
    except tm_core.GitError as e:
        _die(str(e), code=3)
    print(sha)


# ---------- mem0 ----------

def cmd_mem0_write(args: argparse.Namespace) -> None:
    text = sys.stdin.read().strip()
    if not text:
        _die("text required on stdin")
    try:
        resp = tm_core.mem0_write(args.agent, args.topic, text)
    except ValueError as e:
        _die(str(e), code=2)
    except RuntimeError as e:
        _die(str(e), code=4)
    print(resp)


def cmd_mem0_search(args: argparse.Namespace) -> None:
    try:
        resp = tm_core.mem0_search(args.query, args.size)
    except RuntimeError as e:
        _die(str(e), code=4)
    print(resp)


# ---------- lint-page ----------

def cmd_lint_page(args: argparse.Namespace) -> None:
    path = pathlib.Path(args.path)
    if not path.is_absolute():
        path = tm_core.REPO_ROOT / path
    if not path.exists():
        _die(f"not found: {path}")

    text = path.read_text(encoding="utf-8")
    errors = tm_core.lint_page_errors(text)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print("OK")


# ---------- session status / preflight ----------

def _print_status(status: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    print(f"ok: {status['ok']}")
    print(f"branch: {status['branch'] or '(detached)'}")
    print(f"upstream: {status['upstream'] or '(none)'}")
    print(f"head: {status['head'] or '(none)'}")
    print(f"ahead: {status['ahead']}")
    print(f"behind: {status['behind']}")
    print(f"dirty_count: {status['dirty_count']}")
    print(f"staged_count: {status['staged_count']}")
    print(f"unstaged_count: {status['unstaged_count']}")
    print(f"untracked_count: {status['untracked_count']}")
    print(f"unmerged_count: {status['unmerged_count']}")
    print(f"hooks_path: {status['hooks_path'] or '(unset)'}")
    print(f"hooks_installed: {status['hooks_installed']}")
    if status["blockers"]:
        print("blockers:")
        for item in status["blockers"]:
            print(f"  - {item}")
    if status["paths"]:
        print("paths:")
        for item in status["paths"]:
            print(f"  - {item}")


def cmd_status(args: argparse.Namespace) -> None:
    try:
        status = tm_core.git_session_status()
    except tm_core.GitError as e:
        _die(str(e), code=3)
    _print_status(status, args.json)


def cmd_preflight(args: argparse.Namespace) -> None:
    try:
        status = tm_core.git_session_status()
    except tm_core.GitError as e:
        _die(str(e), code=3)
    _print_status(status, args.json)
    if not status["ok"]:
        sys.exit(6)


# ---------- guard (called by commit-msg hook) ----------

def cmd_guard(args: argparse.Namespace) -> None:
    errors = tm_core.guard_commit(pathlib.Path(args.commit_msg_file))
    if errors:
        print("tigermemory guard rejected this commit:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nHow to fix:\n"
            "  - Re-read AGENTS.md §4 (partition ownership) and §5 (write rules).\n"
            "  - Prefer writing to inbox/ via `tm_io.py write-inbox`.\n"
            "  - If you really need to override (human only), use `git commit --no-verify`.",
            file=sys.stderr,
        )
        sys.exit(5)


# ---------- entrypoint ----------

def main() -> None:
    p = argparse.ArgumentParser(prog="tm_io.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write-inbox", help="write a new inbox file (routed via tm_route by default)")
    w.add_argument("--agent", required=True)
    w.add_argument("--topic", required=True)
    w.add_argument("--title", required=True)
    w.add_argument("--force-inbox", action="store_true", help="skip routing, write directly to inbox")
    w.set_defaults(func=cmd_write_inbox)

    c = sub.add_parser("commit-push", help="atomic git add/commit/push with rules")
    c.add_argument("--files", nargs="+", required=True)
    c.add_argument("--agent", required=True)
    c.add_argument("--action", required=True)
    c.add_argument("--summary", required=True)
    c.set_defaults(func=cmd_commit_push)

    mw = sub.add_parser("mem0-write", help="POST a memory with enforced metadata")
    mw.add_argument("--agent", required=True)
    mw.add_argument("--topic", required=True)
    mw.set_defaults(func=cmd_mem0_write)

    ms = sub.add_parser("mem0-search", help="GET memories by query")
    ms.add_argument("--query", required=True)
    ms.add_argument("--size", type=int, default=5)
    ms.set_defaults(func=cmd_mem0_search)

    lp = sub.add_parser("lint-page", help="validate a Wiki page against PAGE_FORMATS.md")
    lp.add_argument("path")
    lp.set_defaults(func=cmd_lint_page)

    st = sub.add_parser("status", help="print a read-only agent session status snapshot")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    pf = sub.add_parser("preflight", help="fail if the session cannot safely start or end")
    pf.add_argument("--json", action="store_true")
    pf.set_defaults(func=cmd_preflight)

    g = sub.add_parser(
        "guard",
        help="enforce AGENTS.md rules on a pending commit (called by commit-msg hook)",
    )
    g.add_argument("--commit-msg-file", required=True)
    g.set_defaults(func=cmd_guard)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
