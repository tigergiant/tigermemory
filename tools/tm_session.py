#!/usr/bin/env python3
"""
tm_session.py — agent session begin/end guard (AGENTS.md 9.1).

Every agent working on a local tigermemory checkout must bracket its work:

  python3 tools/tm_session.py begin <agent> [--task <hint>]
  # ... do work: reads, edits, commits, pushes ...
  python3 tools/tm_session.py end   <agent>

Purpose: block the three recurring concurrent-write incidents documented in
wiki/systems/session-protocol.md:

  1. agent opens session on top of someone else's staged residue
  2. agent finishes without committing, leaves residue for the next agent
  3. agent finishes committing but forgets to push

State file: .git/tm-session.json (single instance per local checkout,
gitignored by git itself).

Exit codes:
  0  success
  2  bad usage / invalid agent
  3  git operation failed (GitError)
  6  worktree not clean / end checks failed
  7  session state conflict (already active on begin; no active on end)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys

import tm_core

# Agents that run a session. Special data-source identities (mem0,
# tigermemory-ce) never hold a session; they only appear as write sources.
SESSION_AGENTS = tm_core.AGENTS - {"mem0", "tigermemory-ce"}

SESSION_FILE = tm_core.REPO_ROOT / ".git" / "tm-session.json"


# ---------- helpers ----------

def _die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _validate_session_agent(name: str) -> None:
    if name not in SESSION_AGENTS:
        raise ValueError(
            f"invalid session agent '{name}' (allowed: {sorted(SESSION_AGENTS)})"
        )


def _read_session() -> dict | None:
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _die(f"corrupt session file {SESSION_FILE}: {e}", code=7)


def _write_session(data: dict) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _delete_session() -> None:
    try:
        SESSION_FILE.unlink()
    except FileNotFoundError:
        pass


def _head_sha() -> str:
    return tm_core.run(["git", "rev-parse", "HEAD"]).stdout.strip()


def _git_fetch() -> None:
    """Best-effort fetch; network failures are warnings, not fatal."""
    r = tm_core.run(["git", "fetch", "origin", "master"], check=False)
    if r.returncode != 0:
        print(
            f"WARN: git fetch failed (offline?): {r.stderr.strip()}",
            file=sys.stderr,
        )


def _ahead_behind() -> tuple[int, int]:
    """Return (ahead, behind) of local master vs origin/master. (0, 0) if no upstream."""
    r = tm_core.run(
        ["git", "rev-list", "--left-right", "--count", "HEAD...origin/master"],
        check=False,
    )
    if r.returncode != 0:
        return (0, 0)
    parts = r.stdout.strip().split()
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _worktree_status() -> list[tuple[str, str]]:
    """Return [(code, path)] from `git status --porcelain` (both staged and unstaged).

    code is the 2-char porcelain code (e.g. ' M', 'MM', '??', 'A ').
    """
    r = tm_core.run(["git", "status", "--porcelain", "-z"], check=True)
    entries: list[tuple[str, str]] = []
    parts = r.stdout.split("\x00")
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if not chunk:
            i += 1
            continue
        if len(chunk) < 4:
            i += 1
            continue
        code = chunk[:2]
        path = chunk[3:]
        entries.append((code, path))
        # Rename/copy entries have a second path after NUL; skip it.
        if code[0] in ("R", "C"):
            i += 2
        else:
            i += 1
    return entries


def _infer_owner(path: str) -> str:
    """Best-effort owner guess for a residual file, so the operator knows
    whose residue it is. Never authoritative — AGENTS.md is."""
    if path.startswith("wiki/brand/"):
        return "openclaw | claude-code"
    if path.startswith("wiki/investment/"):
        return "deerflow | claude-code"
    if path.startswith("wiki/operations/"):
        return "hermes | claude-code"
    if path.startswith("wiki/production/"):
        return "claude-code"
    if path.startswith("wiki/systems/"):
        return "claude-code | codex"
    if path.startswith("wiki/person/"):
        return "claude-code"
    if path.startswith("inbox/"):
        # inbox/YYYY-MM-DD-HHMM-<source>-<topic>.md
        name = pathlib.PurePosixPath(path).name
        m = tm_core.INBOX_NAME_RE.match(path) if hasattr(tm_core, "INBOX_NAME_RE") else None
        if m:
            try:
                return m.group("source")
            except (IndexError, KeyError):
                pass
        # Fallback: best-effort parse
        stem = name.rsplit(".", 1)[0]
        segs = stem.split("-")
        if len(segs) >= 6:
            return segs[5]
        return "unknown"
    if path.startswith("tools/") or path.startswith(".githooks/"):
        return "claude-code | codex"
    if path.startswith("deploy/") or path.startswith("schemas/"):
        return "claude-code"
    if path == ".codeiumignore" or path == "AGENTS.md" or path.startswith("runtime/"):
        return "claude-code | human"
    return "unknown"


def _print_residue(entries: list[tuple[str, str]]) -> None:
    """Human-readable dump of worktree residue, grouped by staged/unstaged/untracked."""
    staged: list[tuple[str, str]] = []
    unstaged: list[tuple[str, str]] = []
    untracked: list[tuple[str, str]] = []
    for code, path in entries:
        if code == "??":
            untracked.append((code, path))
        elif code[0] != " " and code[0] != "?":
            staged.append((code, path))
        else:
            unstaged.append((code, path))

    def _dump(label: str, group: list[tuple[str, str]]) -> None:
        if not group:
            return
        print(f"  {label} ({len(group)}):", file=sys.stderr)
        # Sort by mtime when available so oldest (most suspicious) comes first.
        def _mtime(p: str) -> float:
            try:
                return (tm_core.REPO_ROOT / p).stat().st_mtime
            except OSError:
                return 0.0
        group_sorted = sorted(group, key=lambda e: _mtime(e[1]))
        for code, path in group_sorted:
            owner = _infer_owner(path)
            print(f"    {code} {path}   [likely: {owner}]", file=sys.stderr)

    _dump("staged (index)", staged)
    _dump("unstaged (worktree)", unstaged)
    _dump("untracked", untracked)


# ---------- subcommands ----------

def cmd_begin(args: argparse.Namespace) -> None:
    try:
        _validate_session_agent(args.agent)
    except ValueError as e:
        _die(str(e), code=2)

    existing = _read_session()
    if existing is not None:
        print(
            f"ERROR: session already in progress (agent={existing.get('agent')}, "
            f"started_at={existing.get('started_at')}).\n"
            f"Resolve with `tm_session.py end {existing.get('agent')}` or "
            f"`tm_session.py abort {existing.get('agent')} --reason ...`.",
            file=sys.stderr,
        )
        sys.exit(7)

    _git_fetch()
    ahead, behind = _ahead_behind()
    if ahead > 0:
        print(
            f"WARN: local master is ahead of origin/master by {ahead} commit(s). "
            f"Previous session may have left unpushed work.",
            file=sys.stderr,
        )
    if behind > 0:
        print(
            f"INFO: local master is behind origin/master by {behind} commit(s). "
            f"`tm_session end` will pull --rebase before push.",
            file=sys.stderr,
        )

    try:
        entries = _worktree_status()
    except tm_core.GitError as e:
        _die(str(e), code=3)
    if entries:
        print(
            f"ERROR: worktree not clean — {len(entries)} residual file(s). "
            f"Cannot begin session.",
            file=sys.stderr,
        )
        _print_residue(entries)
        print(
            "\nHow to fix:\n"
            "  - If the residue is yours: finish it (commit+push) or discard it.\n"
            "  - If it's another agent's: stash with `git stash push -u -m \"pre-session <reason>\"`\n"
            "    and notify that agent, or commit on their behalf only if rules allow\n"
            "    (see AGENTS.md 4 partition ownership).",
            file=sys.stderr,
        )
        sys.exit(6)

    data = {
        "agent": args.agent,
        "task": args.task or "",
        "start_sha": _head_sha(),
        "started_at": datetime.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
    _write_session(data)
    print(f"OK to begin. agent={args.agent} start_sha={data['start_sha'][:7]}")


def cmd_status(args: argparse.Namespace) -> None:
    sess = _read_session()
    if sess is None:
        print("no active session")
        return

    started_at = sess.get("started_at", "")
    try:
        start_dt = datetime.datetime.fromisoformat(started_at)
        dur = datetime.datetime.now(tm_core.TZ_CN) - start_dt
        dur_str = str(dur).split(".", 1)[0]
    except ValueError:
        dur_str = "?"

    head = _head_sha()
    start_sha = sess.get("start_sha", "")
    commits = 0
    if start_sha and start_sha != head:
        r = tm_core.run(
            ["git", "rev-list", "--count", f"{start_sha}..HEAD"], check=False
        )
        if r.returncode == 0:
            try:
                commits = int(r.stdout.strip())
            except ValueError:
                pass

    try:
        residue = len(_worktree_status())
    except tm_core.GitError:
        residue = -1

    ahead, behind = _ahead_behind()
    print(f"agent       : {sess.get('agent')}")
    print(f"task        : {sess.get('task') or '(none)'}")
    print(f"started_at  : {started_at}")
    print(f"duration    : {dur_str}")
    print(f"start_sha   : {start_sha[:7] if start_sha else '?'}")
    print(f"HEAD        : {head[:7]}")
    print(f"commits     : {commits}")
    print(f"worktree    : {residue} residual file(s)")
    print(f"ahead/behind: {ahead}/{behind} vs origin/master")


def cmd_end(args: argparse.Namespace) -> None:
    try:
        _validate_session_agent(args.agent)
    except ValueError as e:
        _die(str(e), code=2)

    sess = _read_session()
    if sess is None:
        print(
            "ERROR: no active session. Did you forget `tm_session.py begin`?",
            file=sys.stderr,
        )
        sys.exit(7)
    if sess.get("agent") != args.agent:
        print(
            f"ERROR: session owned by '{sess.get('agent')}', not '{args.agent}'.\n"
            f"Only the owner can end it (or use `abort`).",
            file=sys.stderr,
        )
        sys.exit(7)

    # 1) worktree must be clean — nothing in index or in the working tree.
    try:
        entries = _worktree_status()
    except tm_core.GitError as e:
        _die(str(e), code=3)
    if entries:
        print(
            f"ERROR: {len(entries)} uncommitted file(s) in worktree. "
            f"Commit or discard before ending session.",
            file=sys.stderr,
        )
        _print_residue(entries)
        sys.exit(6)

    # 2) sync with origin via rebase so any concurrent pushes merge cleanly.
    try:
        tm_core.git_pull_rebase()
    except tm_core.GitError as e:
        _die(f"pull --rebase failed: {e}", code=3)

    # 3) ensure nothing remains unpushed.
    _git_fetch()
    ahead, behind = _ahead_behind()
    if ahead > 0:
        print(
            f"INFO: {ahead} local commit(s) not on origin, pushing...",
            file=sys.stderr,
        )
        push_r = tm_core.run(["git", "push"], check=False)
        if push_r.returncode != 0:
            print(
                f"ERROR: git push failed: {push_r.stderr.strip()}\n"
                f"Resolve manually then re-run `tm_session.py end {args.agent}`.",
                file=sys.stderr,
            )
            sys.exit(3)
        ahead, behind = _ahead_behind()

    if ahead > 0 or behind > 0:
        print(
            f"ERROR: still out of sync with origin (ahead={ahead}, behind={behind}).",
            file=sys.stderr,
        )
        sys.exit(6)

    # 4) success — compute summary and clear session.
    start_sha = sess.get("start_sha", "")
    head = _head_sha()
    commits = 0
    files = 0
    if start_sha and start_sha != head:
        rc = tm_core.run(
            ["git", "rev-list", "--count", f"{start_sha}..HEAD"], check=False
        )
        if rc.returncode == 0:
            try:
                commits = int(rc.stdout.strip())
            except ValueError:
                pass
        rf = tm_core.run(
            ["git", "diff", "--name-only", f"{start_sha}..HEAD"], check=False
        )
        if rf.returncode == 0:
            files = len([l for l in rf.stdout.splitlines() if l.strip()])

    started_at = sess.get("started_at", "")
    dur_str = "?"
    try:
        start_dt = datetime.datetime.fromisoformat(started_at)
        dur = datetime.datetime.now(tm_core.TZ_CN) - start_dt
        dur_str = str(dur).split(".", 1)[0]
    except ValueError:
        pass

    _delete_session()
    print(
        f"OK. agent={args.agent} duration={dur_str} commits={commits} files={files} "
        f"start={start_sha[:7] if start_sha else '?'} head={head[:7]}"
    )


def cmd_abort(args: argparse.Namespace) -> None:
    try:
        _validate_session_agent(args.agent)
    except ValueError as e:
        _die(str(e), code=2)

    sess = _read_session()
    if sess is None:
        print("ERROR: no active session to abort.", file=sys.stderr)
        sys.exit(7)
    if sess.get("agent") != args.agent:
        print(
            f"ERROR: session owned by '{sess.get('agent')}', not '{args.agent}'.",
            file=sys.stderr,
        )
        sys.exit(7)

    reason = args.reason or "no-reason"
    # If there is any residue, stash it so the operator can recover later.
    try:
        entries = _worktree_status()
    except tm_core.GitError as e:
        _die(str(e), code=3)
    if entries:
        ts = tm_core.now("%Y%m%d-%H%M%S")
        stash_msg = f"tm-session-abort {args.agent} {ts} | {reason}"
        r = tm_core.run(
            ["git", "stash", "push", "-u", "-m", stash_msg], check=False
        )
        if r.returncode != 0:
            _die(f"git stash failed: {r.stderr.strip()}", code=3)
        print(
            f"stashed {len(entries)} file(s) as: {stash_msg}\n"
            f"recover with: git stash list | grep '{ts}'",
            file=sys.stderr,
        )

    _delete_session()
    print(f"aborted. agent={args.agent} reason={reason!r}")


# ---------- entrypoint ----------

def main() -> None:
    p = argparse.ArgumentParser(prog="tm_session.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("begin", help="verify clean worktree and open a session")
    b.add_argument("agent")
    b.add_argument("--task", default="", help="one-line hint about what you're doing")
    b.set_defaults(func=cmd_begin)

    s = sub.add_parser("status", help="print current session state (if any)")
    s.set_defaults(func=cmd_status)

    e = sub.add_parser("end", help="verify everything committed+pushed and close session")
    e.add_argument("agent")
    e.set_defaults(func=cmd_end)

    a = sub.add_parser("abort", help="stash residue and close session without push")
    a.add_argument("agent")
    a.add_argument("--reason", default="", help="why aborting")
    a.set_defaults(func=cmd_abort)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
