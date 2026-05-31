#!/usr/bin/env python3
"""
tools/tm_guard_ci.py — server-side commit validator for CI (GitHub Actions).

Mirrors the L3 commit-msg hook's rules (tm_core.guard_commit) but operates
post-facto on pushed commits. Runs in GitHub Actions on every push to master,
catches any commit that bypassed the local hook (e.g. linter auto-commits,
--no-verify pushes, or token-based direct push).

Usage:
    python3 tools/tm_guard_ci.py <before_sha> <after_sha>

Exit 0 if all commits in the range are clean, exit 1 on any violation.
Inputs: Repository markdown/python files, frontmatter, section text, git diff inputs, or CLI path arguments.
Outputs: Deterministic reports, rewritten generated files, validation errors, or patch proposals.
Depends-on (must-have): Python stdlib plus tm_core/path parsers; no Mem0 write path unless explicitly invoked by caller.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _bootstrap_paths  # noqa: F401  -- expose packages/*/src before tigermemory imports
import tigermemory_core as tm_core

LINTER_DASHBOARDS = {
    "wiki/operations/lint-dashboard.md",
    "wiki/operations/inbox-triage.md",
}
# Tolerate the special [unreviewed] tag suffix on commit messages
UNREVIEWED_TAG = "[unreviewed]"

# Inbox signature: any added inbox/*.md must declare routing provenance.
# Mirror of .githooks/pre-commit; catches commits that bypassed the local hook
# (fresh clones without core.hooksPath, --no-verify pushes, token pushes).
# Excludes inbox/daily/*.md (digest products) — handled by regex below.
INBOX_FILE_RE = re.compile(r"^inbox/[^/]+\.md$")
ROUTED_BY_RE = re.compile(r"^routed_by:\s*(tigermemory|human-direct)\s*$", re.M)


def validate_commit(sha: str) -> list[str]:
    """Return list of violations for a single commit; empty = clean."""
    errors: list[str] = []
    short = sha[:8]

    msg = subprocess.run(
        ["git", "log", "-1", "--pretty=%B", sha],
        capture_output=True, text=True, check=True,
    ).stdout
    first_line = next(
        (ln for ln in msg.splitlines() if ln and not ln.startswith("#")), ""
    )
    # Strip trailing [unreviewed] tag before matching format
    stripped = first_line.replace(UNREVIEWED_TAG, "").rstrip()

    m = tm_core.COMMIT_MSG_RE.match(stripped)
    agent: str | None = None
    action: str | None = None
    if not m:
        errors.append(f"{short}: commit message format invalid: {first_line[:80]!r}")
    else:
        agent = m.group("agent")
        action = m.group("action")
        if agent not in tm_core.AGENTS:
            errors.append(f"{short}: agent '{agent}' not in whitelist {sorted(tm_core.AGENTS)}")
        if action not in tm_core.ACTIONS:
            errors.append(f"{short}: action '{action}' not in whitelist {sorted(tm_core.ACTIONS)}")

    paths_out = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        capture_output=True, text=True, check=True,
    ).stdout
    paths = [p for p in paths_out.splitlines() if p]

    # Inbox routing signature: any added inbox/*.md (excluding inbox/daily/) must
    # carry `routed_by: tigermemory` (LLM-routed) or `routed_by: human-direct`
    # (human emergency edit). Mirrors .githooks/pre-commit so CI catches bypasses.
    added_out = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r",
         "--diff-filter=A", sha],
        capture_output=True, text=True, check=True,
    ).stdout
    added_paths = [p for p in added_out.splitlines() if p]
    for p in added_paths:
        if not INBOX_FILE_RE.match(p):
            continue
        try:
            blob = subprocess.run(
                ["git", "show", f"{sha}:{p}"],
                capture_output=True, text=True, check=True,
            ).stdout
        except subprocess.CalledProcessError:
            errors.append(f"{short}: '{p}' could not be read for signature check")
            continue
        if not ROUTED_BY_RE.search(blob):
            errors.append(
                f"{short}: '{p}' missing 'routed_by: tigermemory' frontmatter "
                "(use tm_io.py write-inbox or MCP write_memory; "
                "direct git add inbox/*.md is forbidden)"
            )

    # sources/ immutability
    for p in paths:
        if p.startswith(tm_core.SOURCES_PREFIX):
            errors.append(f"{short}: '{p}' is under sources/ (immutable by agent)")

    # Meta-rule files: only claude-code or human
    for p in paths:
        is_meta = p in tm_core.META_RULE_PATHS or any(
            p.startswith(pref) for pref in tm_core.META_RULE_PREFIXES
        )
        if is_meta and agent not in tm_core.META_RULE_OWNERS:
            errors.append(
                f"{short}: '{p}' is a meta-rule file; only {sorted(tm_core.META_RULE_OWNERS)} "
                f"may modify (commit agent: {agent})"
            )

    # log.md is [claude-code] compile only
    if "log.md" in paths and not (agent == "claude-code" and action == "compile"):
        errors.append(f"{short}: log.md is append-only via [claude-code] compile")

    # Linter-owned dashboards are overwrite-only by [linter] lint
    for dash in LINTER_DASHBOARDS:
        if dash in paths and not (agent == "linter" and action == "lint"):
            errors.append(f"{short}: {dash} is overwrite-only by [linter] lint")

    # Partition ownership on wiki/<partition>/ (skip dashboards already handled)
    for p in paths:
        if p in LINTER_DASHBOARDS:
            continue
        mp = re.match(r"^wiki/([^/]+)/", p)
        if not mp:
            continue
        partition = mp.group(1)
        owners = tm_core.PARTITION_OWNERS.get(partition)
        if not owners:
            continue
        if agent not in owners and agent != "human":
            errors.append(
                f"{short}: '{p}' — agent '{agent}' not owner of wiki/{partition}/ "
                f"(owners: {sorted(owners)})"
            )

    return errors


def main() -> int:
    before = sys.argv[1] if len(sys.argv) > 1 else ""
    after = sys.argv[2] if len(sys.argv) > 2 else "HEAD"

    # On first push / new branch GitHub sends before=0000...
    if before and set(before) != {"0"}:
        rng = f"{before}..{after}"
    else:
        rng = f"{after}~1..{after}"

    try:
        shas = subprocess.run(
            ["git", "rev-list", rng],
            capture_output=True, text=True, check=True,
        ).stdout.split()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: git rev-list {rng} failed: {e.stderr}")
        return 2

    if not shas:
        print(f"No new commits in range {rng}; nothing to validate")
        return 0

    all_errors: list[str] = []
    for sha in shas:
        all_errors.extend(validate_commit(sha))

    if all_errors:
        print(f"::error::COMMIT GUARD REJECTED {len(shas)} commit(s):")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print(f"Validated {len(shas)} commit(s) in {rng}, all clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
