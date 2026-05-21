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
  tm_io.py mem0-update-content --id <uuid>                               # content on stdin
  tm_io.py retention-audit [--json]
  tm_io.py discard-audit   [--json]
  tm_io.py cron-apply DATE [--proposal <id>]
  tm_io.py cron-reject DATE --proposal <id> --reason "..."
  tm_io.py cron-status DATE
  tm_io.py cron-rollback COMMIT_SHA
  tm_io.py cron-daily-report [--date YYYY-MM-DD]
  tm_io.py cron-weekly-report [--date YYYY-MM-DD]
  tm_io.py agent-doctor    [--json]
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

    import tm_memory_ops
    import tm_route

    if args.force_inbox:
        fm_extra = {
            "routed_by": "tigermemory",
            "route_decision_reason": "force_inbox CLI",
            "route_requested_topic": args.topic,
            "stored_topic": args.topic,
        }
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
        storage_topic = tm_memory_ops._storage_topic(
            args.topic,
            decision,
            preserve_requested_topic=True,
        )
        try:
            resp = tm_core.mem0_write(
                args.agent,
                storage_topic,
                body,
                metadata_extra=tm_memory_ops._route_metadata(
                    decision,
                    requested_topic=args.topic,
                    storage_topic=storage_topic,
                ),
            )
        except ValueError as e:
            _die(str(e), code=2)
        except RuntimeError as e:
            _die(str(e), code=4)
        print(resp)
        return

    # route == "inbox"
    storage_topic = tm_memory_ops._storage_topic(
        args.topic,
        decision,
        preserve_requested_topic=True,
    )
    fm_extra = tm_memory_ops._route_metadata(
        decision,
        requested_topic=args.topic,
        storage_topic=storage_topic,
    )
    fm_extra["routed_by"] = "tigermemory"
    fm_extra["route_decision_reason"] = decision.reasons
    try:
        rel = tm_core.write_inbox_file(
            args.agent,
            storage_topic,
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
        "topic": storage_topic,
        "topic_inferred": decision.topic_inferred,
        "reasons": decision.reasons,
        "unreviewed": decision.unreviewed,
        "warnings": tm_memory_ops._topic_warnings(args.topic, decision, storage_topic),
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
        resp = tm_core.mem0_search(args.query, args.size, match_mode=args.match_mode)
    except RuntimeError as e:
        _die(str(e), code=4)
    except ValueError as e:
        _die(str(e), code=2)
    print(resp)


def cmd_mem0_verify(args: argparse.Namespace) -> None:
    try:
        result = tm_core.verify_memory_id(args.id, key_terms=args.terms, digest_date=args.digest_date)
    except RuntimeError as e:
        _die(str(e), code=4)
    except ValueError as e:
        _die(str(e), code=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_mem0_update_content(args: argparse.Namespace) -> None:
    content = sys.stdin.read()
    if not content.strip():
        _die("memory_content required on stdin")
    try:
        resp = tm_core.mem0_update_content(args.id, content)
    except RuntimeError as e:
        _die(str(e), code=4)
    except ValueError as e:
        _die(str(e), code=2)
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


def cmd_retention_audit(args: argparse.Namespace) -> None:
    import tm_retention_audit

    code = tm_retention_audit.cmd_audit(args)
    if code:
        sys.exit(code)


def cmd_discard_audit(args: argparse.Namespace) -> None:
    import tm_route_audit

    code = tm_route_audit.cmd_summary(args)
    if code:
        sys.exit(code)


def cmd_cron_apply(args: argparse.Namespace) -> None:
    import tm_cron_apply

    code = _run_cron_apply(tm_cron_apply, tm_cron_apply.cmd_apply, args)
    if code:
        sys.exit(code)


def cmd_cron_reject(args: argparse.Namespace) -> None:
    import tm_cron_apply

    code = _run_cron_apply(tm_cron_apply, tm_cron_apply.cmd_reject, args)
    if code:
        sys.exit(code)


def cmd_cron_status(args: argparse.Namespace) -> None:
    import tm_cron_apply

    code = _run_cron_apply(tm_cron_apply, tm_cron_apply.cmd_status, args)
    if code:
        sys.exit(code)


def cmd_cron_rollback(args: argparse.Namespace) -> None:
    import tm_cron_apply

    code = _run_cron_apply(tm_cron_apply, tm_cron_apply.cmd_rollback, args)
    if code:
        sys.exit(code)


def cmd_cron_daily_report(args: argparse.Namespace) -> None:
    import tm_memory_reflection

    code = tm_memory_reflection.cmd_daily(args)
    if code:
        sys.exit(code)


def cmd_cron_weekly_report(args: argparse.Namespace) -> None:
    import tm_memory_reflection

    code = tm_memory_reflection.cmd_weekly(args)
    if code:
        sys.exit(code)


def _run_cron_apply(tm_cron_apply, func, args: argparse.Namespace) -> int:
    try:
        return int(func(args))
    except tm_cron_apply.CronApplyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def cmd_agent_doctor(args: argparse.Namespace) -> None:
    import tm_agent_doctor

    code = tm_agent_doctor.cmd_doctor(args)
    if code:
        sys.exit(code)


# ---------- guard (called by commit-msg hook) ----------

def cmd_guard(args: argparse.Namespace) -> None:
    errors = tm_core.guard_commit(pathlib.Path(args.commit_msg_file))
    if errors:
        # Best-effort log to .tmp/guard-rejects.jsonl for tm_metrics.
        try:
            from tm_reject_log import log_reject  # type: ignore
            log_reject(
                guard="commit_msg",
                file=str(args.commit_msg_file),
                msg="; ".join(errors)[:300],
            )
        except Exception:
            pass

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

    w = sub.add_parser(
        "write-inbox",
        help="write a new inbox file; put a concise Chinese summary on the first body line",
    )
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
    ms.add_argument("--match-mode", choices=["id_first", "token_and", "substring"], default="id_first")
    ms.set_defaults(func=cmd_mem0_search)

    mv = sub.add_parser("mem0-verify", help="verify a Mem0 memory id via direct readback/search/digest")
    mv.add_argument("--id", required=True)
    mv.add_argument("--terms")
    mv.add_argument("--digest-date")
    mv.set_defaults(func=cmd_mem0_verify)

    mu = sub.add_parser("mem0-update-content", help="PUT replacement content only; metadata changes require delete + recreate")
    mu.add_argument("--id", required=True)
    mu.set_defaults(func=cmd_mem0_update_content)

    lp = sub.add_parser("lint-page", help="validate a Wiki page against PAGE_FORMATS.md")
    lp.add_argument("path")
    lp.set_defaults(func=cmd_lint_page)

    st = sub.add_parser("status", help="print a read-only agent session status snapshot")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    pf = sub.add_parser("preflight", help="fail if the session cannot safely start or end")
    pf.add_argument("--json", action="store_true")
    pf.set_defaults(func=cmd_preflight)

    ra = sub.add_parser("retention-audit", help="read-only Mem0 retention dry-run audit")
    ra.add_argument("--max-items", type=int, default=200)
    ra.add_argument("--page-size", type=int, default=100)
    ra.add_argument("--limit", type=int, default=30, help="markdown rows to print")
    ra.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ra.set_defaults(func=cmd_retention_audit)

    da = sub.add_parser("discard-audit", help="summarize local write_memory discard quarantine")
    da.add_argument("--date")
    da.add_argument("--root")
    da.add_argument("--limit", type=int, default=80)
    da.add_argument("--json", action="store_true")
    da.set_defaults(func=cmd_discard_audit)

    ca = sub.add_parser("cron-apply", help="apply checked cron proposal(s) from a daily memory digest")
    ca.add_argument("date")
    ca.add_argument("--proposal")
    ca.set_defaults(func=cmd_cron_apply)

    cr = sub.add_parser("cron-reject", help="record rejection for cron proposal(s)")
    cr.add_argument("date")
    cr.add_argument("--proposal")
    cr.add_argument("--reason", required=True)
    cr.set_defaults(func=cmd_cron_reject)

    cs = sub.add_parser("cron-status", help="show cron proposal status for a date")
    cs.add_argument("date")
    cs.set_defaults(func=cmd_cron_status)

    cb = sub.add_parser("cron-rollback", help="rollback a cron-apply commit")
    cb.add_argument("commit_sha")
    cb.add_argument("--reason", default="manual request")
    cb.set_defaults(func=cmd_cron_rollback)

    cdr = sub.add_parser("cron-daily-report", help="render the daily memory route reflection report")
    cdr.add_argument("--date")
    cdr.set_defaults(func=cmd_cron_daily_report)

    cwr = sub.add_parser("cron-weekly-report", help="render the weekly memory route reflection report")
    cwr.add_argument("--date")
    cwr.set_defaults(func=cmd_cron_weekly_report)

    ad = sub.add_parser("agent-doctor", help="read-only agent connect / doctor checks")
    ad.add_argument("--query", default="retention dry-run agent doctor connect mem0 audit")
    ad.add_argument("--http-url", default=None)
    ad.add_argument("--skip-l2", action="store_true", help="skip live DeepSeek/L2 probe")
    ad.add_argument("--json", action="store_true")
    ad.set_defaults(func=cmd_agent_doctor)

    ac = sub.add_parser("agent-connect", help="alias for agent-doctor")
    ac.add_argument("--query", default="retention dry-run agent doctor connect mem0 audit")
    ac.add_argument("--http-url", default=None)
    ac.add_argument("--skip-l2", action="store_true", help="skip live DeepSeek/L2 probe")
    ac.add_argument("--json", action="store_true")
    ac.set_defaults(func=cmd_agent_doctor)

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
