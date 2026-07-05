#!/usr/bin/env python3
"""
tools/tm_core.py — shared kernel for tigermemory I/O.

Single source of truth for:
- Agent / topic / partition / action enums (AGENTS.md §3, §4)
- Git helpers (pull-rebase, atomic commit-push with retry)
- Mem0 HTTP client
- Inbox / wiki content generation
- Argument validators (raise ValueError)
- Page linter (returns error list; caller decides how to report)
- Commit-msg guard (returns error list; caller decides exit code)

Both `tm_io.py` (CLI) and `tm_mcp.py` (MCP server) are thin adapters over
this module. Behavior changes should happen HERE, once, and be picked up by
both interfaces automatically.

Errors surface as native Python exceptions (ValueError for bad input,
GitError for git trouble, RuntimeError for Mem0 / environment). Adapters
translate to exit codes (CLI) or JSON-RPC errors (MCP).

Inputs: agent/topic/partition/action 枚举字符串、目标文件相对路径、inbox/wiki 正文、Mem0 API 凭据（环境变量 MEM0_API_KEY/MEM0_USER_ID）、本地 git 仓库工作树状态。
Outputs: 已校验的 commit/inbox/wiki 内容字符串、git 操作结果（commit SHA / push 状态）、Mem0 HTTP 响应 dict、lint 错误列表、commit-msg guard 错误列表。
Depends-on (must-have): Python stdlib (subprocess / urllib / pathlib / zoneinfo)；可选 Mem0 / OpenMemory CE HTTP 端点（由 caller 决定是否调用）；本仓 schemas/PAGE_FORMATS.md 校验规则。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import ipaddress
import pathlib
import re
import shutil
import sqlite3
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .roots import resolve_app_root, resolve_instance_root, subprocess_root_env

__all__ = [
    "ACTIONS",
    "AGENTS",
    "AUTO_GENERATED_DIRS",
    "COMMIT_AGENTS",
    "COMMIT_MSG_RE",
    "DAILY_DIGEST_RE",
    "DATA_SOURCE_AGENTS",
    "DEFAULT_DEEPSEEK_ENDPOINT",
    "DEFAULT_DEEPSEEK_ADMIN_MODEL",
    "DEFAULT_DEEPSEEK_MODEL",
    "EMBEDDING_BATCH_SIZE",
    "EMBEDDING_TIMEOUT",
    "EmbeddingError",
    "FRONTMATTER_UPDATED_RE",
    "GitError",
    "INBOX_NAME_RE",
    "IPFB_COPYWRITING_FILES",
    "LINTER_DASHBOARDS",
    "MEM0_READ_TIMEOUT",
    "MEM0_UUID_RE",
    "MEM0_WRITE_TIMEOUT",
    "META_RULE_OWNERS",
    "META_RULE_PATHS",
    "META_RULE_PREFIXES",
    "MINIMAX_DEFAULT_MAX_TOKENS",
    "MINIMAX_DEFAULT_TIMEOUT",
    "REFINE_DEEPSEEK_ENDPOINT",
    "REFINE_DEEPSEEK_MODEL",
    "REFINE_DEFAULT_TIMEOUT",
    "REFINE_MAX_TEXT_LEN",
    "REFINE_MIN_TEXT_LEN",
    "REFINE_PROMPT_TEMPLATE",
    "REPO_ROOT",
    "SLUG_RE",
    "SOURCES_PREFIX",
    "SOURCES_PROVENANCE_KEYS",
    "SUGGEST_PATCH_DEFAULT_MAX",
    "SUGGEST_PATCH_LLMS",
    "SUGGEST_PATCH_MAX_PAGES_IN_PROMPT",
    "SUGGEST_PATCH_MAX_SUMMARY_CHARS",
    "SUGGEST_PATCH_PROMPT",
    "SUGGEST_PATCH_TYPES",
    "TIGERMEMORY_PROFILE_HYBRID",
    "TIGERMEMORY_PROFILE_LOCAL",
    "TIGERMEMORY_PROFILE_VALUES",
    "TITLE_RE",
    "TOPICS",
    "TZ_CN",
    "WIKI_PATH_RE",
    "answer_from_public_evidence",
    "check_transport_security",
    "configure_stdio",
    "deepseek_admin_model",
    "deepseek_endpoint",
    "deepseek_model",
    "derive_inbox_review_cn",
    "derive_inbox_summary_cn",
    "embed_one",
    "embed_texts",
    "embedding_config",
    "flatten_search_query_terms",
    "git_commit_push",
    "git_pull_rebase",
    "git_remote_blob_url",
    "git_session_status",
    "guard_commit",
    "inbox_rel_path",
    "inbox_review_cn_is_low_quality",
    "ipfb_copywriting_context",
    "is_auto_generated_path",
    "lint_page_errors",
    "lint_repo_scan",
    "mcp_api_key",
    "mem0_base",
    "mem0_delete",
    "mem0_get",
    "mem0_key",
    "mem0_request",
    "mem0_search",
    "mem0_update_content",
    "mem0_user_id",
    "mem0_write",
    "now",
    "primary_search_scope",
    "propose_wiki_admin_page",
    "resolve_app_root",
    "resolve_instance_root",
    "verify_memory_record",
    "subprocess_root_env",
    "refine_from_summary",
    "render_inbox_body",
    "render_wiki_body",
    "run",
    "save_wiki_patches_to_inbox",
    "search_query_term_groups",
    "search_wiki",
    "search_wiki_hybrid",
    "signal_tokens",
    "staged_blob",
    "staged_files",
    "suggest_wiki_patches",
    "tigermemory_profile",
    "validate_action",
    "validate_agent",
    "validate_partition",
    "validate_slug",
    "validate_title",
    "validate_topic",
    "verify_memory_id",
    "write_and_commit_inbox",
    "write_inbox_file",
]

try:
    from zoneinfo import ZoneInfo
    _TZ_CN_IMPL = ZoneInfo("Asia/Shanghai")
except Exception:
    # Windows Python may lack tzdata. Fall back to a fixed +08:00 offset.
    _TZ_CN_IMPL = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")

TZ_CN = _TZ_CN_IMPL


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_INSTANCE_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    legacy = os.environ.get("TIGERMEMORY_ROOT")
    if legacy:
        return pathlib.Path(legacy).resolve()
    cwd = pathlib.Path.cwd().resolve()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / "wiki").is_dir() and ((ancestor / "tools").is_dir() or (ancestor / "runtime").is_dir()):
            return ancestor
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (ancestor / "wiki").is_dir() and (
            (ancestor / ".git").is_dir()
            or (ancestor / "tools").is_dir()
            or (ancestor / "pyproject.toml").is_file()
        ):
            return ancestor
    return here.parent.parent.parent.parent


REPO_ROOT = _detect_repo_root()


# ---------- Enums (AGENTS.md §3, §4) ----------

# Regular agents who may author commits and own pages (AGENTS.md §3).
COMMIT_AGENTS = {
    "claude-code", "cascade", "codex", "chatgpt", "openclaw",
    "hermes", "deerflow", "human", "linter", "kimi", "gemini", "trae",
}
# Special data-source identities: appear only in inbox `source` / Mem0
# `metadata.source` fields. They MUST NOT be used as commit prefix or page
# owner. AGENTS.md §3 is the policy source.
DATA_SOURCE_AGENTS = {"mem0", "tigermemory-ce", "dsa-cron"}
# Full enum: union of both categories. validate_agent / inbox filename regex /
# Mem0 metadata.source acceptors fall back to this set. Commit-prefix and page
# owner validators must use COMMIT_AGENTS instead.
AGENTS = COMMIT_AGENTS | DATA_SOURCE_AGENTS
ACTIONS = {"create", "update", "archive", "lint", "ingest", "compile", "fix"}
# Topic enum used by inbox filenames + Mem0 metadata.topic.
# Note: file-name regex INBOX_NAME_RE allows only [a-z]+ (no hyphens), so the
# `self-evolution` partition uses topic key `selfevolution` (no hyphen). The
# topic-to-partition mapping is documented in AGENTS.md §5.4.
TOPICS = {"brand", "investment", "operations", "production", "systems", "person", "selfevolution", "cross"}

# Partition ownership per AGENTS.md §4.
# 2026-05-04 policy change (虎哥 directive): every agent write is human-authorized,
# multi-agent identity gating only spammed inbox. All regular agents may now
# write to all partitions directly, gated only by L2 review score (>=30) and
# the wiki/person/ PII restriction. The PARTITION_OWNERS map is retained so
# downstream guards (tm_guard_ci, lint partition-mismatch) keep working without
# code churn — we just expanded the owner sets.
_ALL_REGULAR_AGENTS: set[str] = {
    "claude-code", "cascade", "codex", "chatgpt", "openclaw", "hermes", "deerflow", "kimi", "gemini", "trae",
}
PARTITION_OWNERS: dict[str, set[str]] = {
    "brand":          set(_ALL_REGULAR_AGENTS),
    "investment":     set(_ALL_REGULAR_AGENTS),
    "operations":     set(_ALL_REGULAR_AGENTS),
    "production":     set(_ALL_REGULAR_AGENTS),
    "systems":        set(_ALL_REGULAR_AGENTS),
    "person":         {"claude-code"},  # sensitive; PII black-list, claude-code only
    "self-evolution": set(_ALL_REGULAR_AGENTS),
}

# Meta-rule files: only claude-code / cascade / human may modify.
# 2026-05-24 虎哥 directive: cascade joined the meta-rule owner set so it can
# draft AGENTS.md / schemas/ edits directly instead of always routing through
# claude-code. claude-code remains the canonical owner for log.md compile.
META_RULE_PATHS = {"AGENTS.md", "index.md", "log.md"}
META_RULE_PREFIXES = ("schemas/",)
META_RULE_OWNERS = {"claude-code", "cascade", "human"}

# sources/ is an external-mirror area for original materials.
# Agent commits to sources/ require scrape-provenance frontmatter
# (source_url + fetched_at + fetched_by, all non-empty). [human] commits
# bypass this check (remain the canonical writer for non-scraped imports).
# Policy change 2026-05-04: see lessons/2026-05-04-sources-provenance-relaxation.md
SOURCES_PREFIX = "sources/"
SOURCES_PROVENANCE_KEYS = ("source_url", "fetched_at", "fetched_by")

# Linter-owned dashboards: auto-generated by `[linter] lint`, overwrite-only.
# These are the only pages in wiki/ where owner:linter is expected; lint_repo
# exempts them from the partition-ownership check.
LINTER_DASHBOARDS = {
    "wiki/brand/index-by-subtopic.md",
    "wiki/investment/index-by-subtopic.md",
    "wiki/operations/lint-dashboard.md",
    "wiki/operations/inbox-triage.md",
    "wiki/operations/backlinks-dashboard.md",
    "wiki/operations/index-by-subtopic.md",
    "wiki/production/index-by-subtopic.md",
    "wiki/self-evolution/index-by-subtopic.md",
}

AUTO_GENERATED_DIRS = {
    "wiki/investment/decision-log",
}


def is_auto_generated_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").strip("/")
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in AUTO_GENERATED_DIRS
    )

# Regex patterns
TITLE_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff _\-]{1,80}")
SLUG_RE = re.compile(r"[a-z0-9\-]+")
COMMIT_MSG_RE = re.compile(
    r"^\[(?P<agent>[a-z0-9\-]+)\]\s+(?P<action>[a-z]+)\s*[:：]\s*(?P<summary>.+)$"
)
INBOX_NAME_RE = re.compile(
    r"^inbox/\d{4}-\d{2}-\d{2}-\d{4}-(?P<agent>[a-z0-9\-]+)-(?P<topic>[a-z]+)\.md$"
)
DAILY_DIGEST_RE = re.compile(r"^inbox/daily/\d{4}-\d{2}-\d{2}\.md$")
WIKI_PATH_RE = re.compile(r"^wiki/(?P<partition>[a-z]+)/[^/]+\.md$")
FRONTMATTER_UPDATED_RE = re.compile(r"^updated:\s*(\S+)\s*$", re.MULTILINE)


# ---------- Exceptions ----------

class GitError(RuntimeError):
    """Raised when a git operation fails (push rejected, rebase conflict, etc.)."""


# ---------- Time ----------

def now(fmt: str) -> str:
    """Format current time in Asia/Shanghai TZ."""
    return datetime.datetime.now(TZ_CN).strftime(fmt)


# ---------- LLM observability ----------

def _log_llm_call(
    model: str,
    purpose: str,
    duration_ms: float,
    ok: bool,
    **extra: Any,
) -> None:
    """Emit a JSON line to stderr capturing one LLM API call's metadata.

    2026-04-30: surfaces latency regressions (e.g. DeepSeek thinking mode
    silently re-enabling, or MiniMax overload climbing from 8s to 60s).
    Picked up by systemd journal for both tm-http and tm-mcp services.
    Best-effort: never raises, never blocks the caller.
    """
    try:
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": "info" if ok else "warn",
            "kind": "llm_call",
            "model": model,
            "purpose": purpose,
            "duration_ms": round(duration_ms, 1),
            "ok": ok,
            **{k: v for k, v in extra.items() if v is not None},
        }
        print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)
    except Exception:
        pass


# ---------- Subprocess ----------

def run(cmd: list[str], check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a command in REPO_ROOT, capturing output. Raises GitError if check=True and rc!=0,
    or if timeout expires.

    2026-07-04: On Unix, when timeout is set, wrap cmd with `timeout -k 5 {t}`
    (coreutils). This kills the entire process group including grandchildren
    (git-remote-https), which subprocess.run's timeout cannot reach. Without
    this, git fetch hanging on a dead proxy blocks write_memory indefinitely
    even with subprocess timeout. On Windows, subprocess.run timeout is
    sufficient (no grandchild-forking issue for git there).
    """
    use_timeout_wrapper = (
        timeout is not None
        and sys.platform != "win32"
        and shutil.which("timeout") is not None
    )
    if use_timeout_wrapper:
        # `timeout -k 5 {t}` : send SIGTERM at t, SIGKILL 5s later if still alive.
        # Without --foreground, timeout creates a new process group and kills
        # the entire group on timeout — this is what we want (kills
        # git-remote-https grandchildren). subprocess.run uses capture_output
        # (pipes), not TTY, so no --foreground needed.
        wrapped = ["timeout", "-k", "5", str(int(timeout))] + cmd
        try:
            r = subprocess.run(wrapped, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise GitError(f"cmd timed out after {timeout}s: {' '.join(cmd)}")
        # `timeout` returns 124 on timeout, 137 on SIGKILL.
        if r.returncode in (124, 137):
            raise GitError(f"cmd timed out after {timeout}s (coreutils timeout): {' '.join(cmd)}")
    else:
        try:
            r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise GitError(f"cmd timed out after {timeout}s: {' '.join(cmd)}")
    if check and r.returncode != 0:
        raise GitError(
            f"cmd failed: {' '.join(cmd)}\nstderr: {r.stderr.strip()}\nstdout: {r.stdout.strip()}"
        )
    return r


# ---------- Git ----------

def git_pull_rebase() -> None:
    """pull --rebase; on conflict/failure/timeout, abort and raise GitError (AGENTS.md §5.1).

    2026-07-04: added --autostash to match git_commit_push entry pull. Without
    it, push-retry path fails on dirty working trees (e.g. WSL has foreign
    dirty from dashboard rebuild). --autostash stashes dirty before rebase
    and pops after; rebase conflicts still abort per AGENTS.md §5.1.

    2026-07-04: added http.lowSpeedLimit/lowSpeedTime + subprocess timeout=30s.
    WSL git proxy sometimes hangs on fetch; subprocess timeout alone doesn't
    kill grandchild (git-remote-https), so git's own lowSpeed timeout is the
    primary mechanism (10s < 1000 bytes/sec → git aborts fetch). subprocess
    timeout=30 is the fallback.
    """
    cmd = [
        "git",
        "-c", "http.lowSpeedLimit=1000",
        "-c", "http.lowSpeedTime=10",
        "pull", "--rebase", "--autostash", "origin", "master",
    ]
    try:
        r = run(cmd, check=False, timeout=30)
    except GitError as e:
        run(["git", "rebase", "--abort"], check=False)
        raise GitError(f"git pull --rebase timed out after 30s; rebase aborted. stderr: {e}")
    if r.returncode != 0:
        run(["git", "rebase", "--abort"], check=False)
        raise GitError(
            f"git pull --rebase failed; rebase aborted. stderr: {r.stderr.strip()}"
        )


def git_commit_push(files: list[str], msg: str, *, force_add: bool = False) -> str:
    """pull --rebase → add → commit → push (retry 1x). Returns short SHA.

    Raises GitError on commit failure (memory NOT persisted). On push failure
    after retry, does NOT raise: commit is already in local git history, so
    memory is persisted; returns sha and emits a warn event. Push self-heals
    on next operation's entry pull, or via manual `git push`.

    Rationale (2026-07-04): write_memory's contract is "persist memory", not
    "sync to remote". Blocking writes on push/rebase state caused repeated
    write_memory failures when D:\\ had untracked files or dirty working tree.
    Commit success = memory persisted; push is best-effort sync.

    2026-05-03: pull --rebase at entry to self-heal cross-worktree drift
    (WSL MCP writes vs D:\\ human edits). Without this, the F2 pre-commit
    drift guard rejects every write after the peer worktree pushes, forcing
    manual `git pull` on the MCP host. The target files are written to disk
    but untracked at this point, so rebase cannot conflict on them.

    2026-07-03: force_add=True bypasses .gitignore for paths that are
    intentionally ignored but still tracked via signed commits (inbox/).
    See .gitignore line 34: inbox/ is ignored, but pre-commit hook
    requires `routed_by: tigermemory` signature, so inbox writes must
    use `git add -f`.
    """
    start = time.monotonic()

    def runtime_git_event(
        *,
        ok: bool,
        outcome: str,
        severity: str | None = None,
        error: str | None = None,
        commit_sha: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        try:
            from . import runtime_events as tm_runtime_events

            target_ref = {"commit_sha": commit_sha} if commit_sha else {}
            tm_runtime_events.record_event(
                event_type="git_commit_push",
                service="tigermemory-core",
                component="git",
                ok=ok,
                severity=severity or ("info" if ok else "error"),
                duration_ms=round((time.monotonic() - start) * 1000, 1),
                outcome=outcome,
                target_ref=target_ref,
                error=error,
                extra={
                    "files": list(files),
                    "file_count": len(files),
                    "message": msg[:160],
                    **(extra or {}),
                },
            )
        except Exception:
            pass

    # Self-heal: if origin is ahead (peer worktree pushed), pull first.
    # Skip silently if offline / no upstream / proxy hangs; the hook will handle it.
    # 2026-07-04: added timeout=30s. WSL git proxy (172.31.64.1:7890) sometimes
    # hangs on fetch; without timeout, write_memory blocks indefinitely.
    # Timeout → don't raise; commit+push proceed (local commit needs no network).
    try:
        pull_r = run(
            [
                "git",
                "-c", "http.lowSpeedLimit=1000",
                "-c", "http.lowSpeedTime=10",
                "pull", "--rebase", "--autostash", "origin", "master",
            ],
            check=False,
            timeout=30,
        )
    except GitError as e:
        # Timeout. Abort any partial rebase, then continue to commit+push.
        run(["git", "rebase", "--abort"], check=False)
        runtime_git_event(
            ok=False,
            outcome="autopull_timeout_continue",
            severity="warn",
            error=str(e),
        )
        pull_r = None
    if pull_r is not None and pull_r.returncode != 0:
        run(["git", "rebase", "--abort"], check=False)
        runtime_git_event(
            ok=False,
            outcome="autopull_failed_continue",
            severity="warn",
            error=pull_r.stderr.strip() or pull_r.stdout.strip(),
        )
        # Don't raise: maybe offline. Let commit+push attempt proceed;
        # the pre-commit hook will surface a clean error if truly stale.
    add_cmd = ["git", "add"]
    if force_add:
        add_cmd.append("-f")
    add_cmd.append("--")
    add_cmd += files
    run(add_cmd)
    commit_r = run(["git", "commit", "-m", msg], check=False)
    if commit_r.returncode != 0:
        # Unstage what we just added so failed commits don't pollute the index
        # and trip subsequent unrelated commits (pre-commit hooks scan
        # `git diff --cached`, so stale staged-add entries survive across
        # caller's on-disk cleanup).
        run(["git", "restore", "--staged", "--"] + files, check=False)
        runtime_git_event(
            ok=False,
            outcome="commit_failed",
            error=commit_r.stderr.strip() or commit_r.stdout.strip(),
        )
        raise GitError(
            f"git commit failed: {commit_r.stderr.strip() or commit_r.stdout.strip()}"
        )

    sha = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    push_cmd = [
        "git",
        "-c", "http.lowSpeedLimit=1000",
        "-c", "http.lowSpeedTime=10",
        "push",
    ]
    push_retried = False
    try:
        push_r = run(push_cmd, check=False, timeout=60)
        push_ok = push_r.returncode == 0
        push_err = push_r.stderr.strip() or push_r.stdout.strip()
    except GitError as e:
        # Push timed out (coreutils timeout killed whole process group).
        # Don't raise: commit is already in local git history, memory persisted.
        push_ok = False
        push_err = str(e)
    if not push_ok:
        push_retried = True
        try:
            git_pull_rebase()
            push2 = run(push_cmd, check=False, timeout=60)
            push_ok = push2.returncode == 0
            push_err = push2.stderr.strip() or push2.stdout.strip()
        except GitError as e:
            # Rebase failed (e.g. untracked file blocks checkout, or real
            # conflict). Don't raise: commit is already in local git history,
            # memory is persisted. Next write's entry pull --rebase --autostash
            # will self-heal, or a manual `git push` will sync. Raising here
            # would unlink the inbox file and lose the memory, which is the
            # opposite of what write_memory should do.
            # See 2026-07-04 lesson: write_memory must not fail on git sync state.
            push_ok = False
            push_err = str(e)

    if not push_ok:
        runtime_git_event(
            ok=False,
            outcome="commit_ok_push_failed",
            severity="warn",
            commit_sha=sha,
            error=push_err,
            extra={"push_retry": push_retried},
        )
        # Memory is persisted locally; return sha so caller treats write as
        # successful. Push self-heals on next operation.
        return sha

    runtime_git_event(
        ok=True,
        outcome="success",
        commit_sha=sha,
        extra={"push_retry": push_retried},
    )
    return sha


def git_session_status(strict_clean: bool = False) -> dict[str, Any]:
    """Return a read-only session preflight snapshot for agent start/end checks.

    Self-scope discipline (added 2026-05-24, lessons/2026-05-24-self-scope-discipline.md):
    foreign dirty paths are no longer a default blocker. An agent owns only the
    files it itself staged + committed; another agent's in-flight edits do not
    stop it from working or closing. dirty_count / paths / staged_count /
    unstaged_count / untracked_count are still reported as informational so
    callers (and humans) can see what's outstanding. Pass strict_clean=True
    to restore the legacy behaviour (sweeps, archive moves, release checks).

    Phantom protection (added 2026-05-16, lessons/2026-05-16-close-session-stat-cache-phantom.md)
    catches two classes of false-positive dirty entries:

    - **stat-cache phantom**: cross-fs (WSL 9P, Windows mount) mtime drift makes
      `git status` flag a file as ' M' when it's byte-for-byte equal to HEAD.
    - **EOL phantom**: Windows-side editors (PowerShell, Obsidian, VSCode) save
      CRLF; WSL git default `core.autocrlf=false` sees the working-tree CRLF vs
      LF in index and reports diff. Windows-side Git-for-Windows default
      `autocrlf=true` silently round-trips, hiding the diff. Result: same file,
      different verdicts depending on which git binary asks.

    Detection pipeline:

    1. Run `git update-index --refresh` to let git CLI itself reset the in-index
       stat cache where possible (fast, no-op when not needed).
    2. For every ' M' / 'M ' / 'MM' entry, run `git diff --quiet HEAD -- <path>`.
       Exit 0 → byte-for-byte equal to HEAD → stat-cache phantom.
    3. Otherwise, run `git diff --quiet --ignore-cr-at-eol HEAD -- <path>`.
       Exit 0 → only CRLF↔LF differs → EOL phantom.
    4. Phantoms are excluded from `dirty_count`, `paths`, and the dirty-worktree
       blocker. They surface in `phantom_count` / `phantom_paths` for transparency.

    Untracked ('??') and staged-only changes ('A ', 'D ', 'R ', etc.) are NOT
    subject to phantom detection — they reflect real index/worktree state.
    """
    # Step 1: ask git to refresh its own stat cache. This is a no-op for files
    # that are genuinely modified, but clears mtime-only drift on touched-but-
    # unchanged files. Suppress output via check=False; we don't care about its
    # stdout.
    run(["git", "update-index", "--refresh"], check=False)

    status_r = run(["git", "status", "--porcelain=v1"], check=True)
    raw_lines = [line for line in status_r.stdout.splitlines() if line]

    # Step 2 + 3: two-pass phantom detection. Only ' M' / 'M ' / 'MM' rows can
    # be phantom; rename/delete/typechange always reflect real changes.
    phantom_paths: list[str] = []
    lines: list[str] = []
    for line in raw_lines:
        xy = line[:2]
        path = line[3:].strip()
        if xy in (" M", "M ", "MM") and path:
            # Pass 2: byte-for-byte equality (catches stat-cache phantom).
            byte_eq = run(["git", "diff", "--quiet", "HEAD", "--", path], check=False)
            if byte_eq.returncode == 0:
                phantom_paths.append(line)
                continue
            # Pass 3: EOL-equivalence (catches CRLF↔LF phantom). --ignore-cr-at-eol
            # treats trailing CR as whitespace, so a file that differs only in
            # line endings reports rc=0 here while still rc=1 in pass 2.
            eol_eq = run(["git", "diff", "--quiet", "--ignore-cr-at-eol", "HEAD", "--", path], check=False)
            if eol_eq.returncode == 0:
                phantom_paths.append(line)
                continue
        lines.append(line)

    staged = 0
    unstaged = 0
    untracked = 0
    for line in lines:
        xy = line[:2]
        if xy == "??":
            untracked += 1
            continue
        if xy[0] != " ":
            staged += 1
        if xy[1] != " ":
            unstaged += 1

    unmerged_r = run(["git", "diff", "--name-only", "--diff-filter=U"], check=False)
    unmerged = [line for line in unmerged_r.stdout.splitlines() if line]

    branch_r = run(["git", "branch", "--show-current"], check=False)
    branch = branch_r.stdout.strip() if branch_r.returncode == 0 else ""
    head_r = run(["git", "rev-parse", "--verify", "HEAD"], check=False)
    head = head_r.stdout.strip() if head_r.returncode == 0 else ""

    upstream_r = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        check=False,
    )
    upstream = upstream_r.stdout.strip() if upstream_r.returncode == 0 else ""
    ahead = 0
    behind = 0
    if upstream:
        ab_r = run(["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"], check=False)
        if ab_r.returncode == 0:
            parts = ab_r.stdout.split()
            if len(parts) == 2:
                ahead = int(parts[0])
                behind = int(parts[1])

    hooks_r = run(["git", "config", "--get", "core.hooksPath"], check=False)
    hooks_path = hooks_r.stdout.strip() if hooks_r.returncode == 0 else ""
    hooks_dir = REPO_ROOT / hooks_path if hooks_path else None
    required_hooks = ["pre-commit", "commit-msg", "post-commit"]
    hooks_installed = bool(
        hooks_dir
        and hooks_path.replace("\\", "/") == ".githooks"
        and all((hooks_dir / name).exists() for name in required_hooks)
    )

    blockers: list[str] = []
    if not branch:
        blockers.append("detached HEAD")
    if unmerged:
        blockers.append(f"unmerged paths: {len(unmerged)}")
    # Self-scope discipline (2026-05-24 虎哥 directive): foreign dirty paths
    # don't block an agent's own work. strict_clean=True is for sweep tasks
    # (archive moves, release verification) that genuinely need a clean tree.
    if lines and strict_clean:
        blockers.append(f"dirty worktree: {len(lines)}")
    if ahead:
        blockers.append(f"unpushed commits: {ahead}")
    if behind:
        blockers.append(f"local branch behind upstream: {behind}")
    if not hooks_installed:
        blockers.append("git hooks not installed via core.hooksPath=.githooks")

    return {
        "ok": not blockers,
        "branch": branch or None,
        "detached": not bool(branch),
        "head": head or None,
        "upstream": upstream or None,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(lines),
        "staged_count": staged,
        "unstaged_count": unstaged,
        "untracked_count": untracked,
        "unmerged_count": len(unmerged),
        "hooks_path": hooks_path or None,
        "hooks_installed": hooks_installed,
        "blockers": blockers,
        "paths": lines,
        # Stat cache phantom protection: paths that git status reported as
        # modified but git diff --quiet HEAD verified as byte-for-byte equal to
        # HEAD. These are excluded from dirty_count and blockers.
        "phantom_count": len(phantom_paths),
        "phantom_paths": phantom_paths,
    }


def git_remote_blob_url(rel_path: str, branch: str = "master") -> str:
    """Best-effort GitHub blob URL for rel_path. Returns '' if remote isn't GitHub-style."""
    try:
        remote = run(["git", "config", "--get", "remote.origin.url"]).stdout.strip()
    except GitError:
        return ""
    if remote.startswith("git@"):
        remote = remote.replace(":", "/").replace("git@", "https://").replace(".git", "")
    elif remote.startswith("https://"):
        remote = remote.replace(".git", "")
    else:
        return ""
    return f"{remote}/blob/{branch}/{rel_path}"


def staged_files() -> list[tuple[str, str]]:
    """Return [(status, path)] for staged changes. status: A/M/D/R/C..."""
    r = run(["git", "diff", "--cached", "--name-status", "-z"], check=True)
    entries: list[tuple[str, str]] = []
    parts = r.stdout.split("\x00")
    i = 0
    while i < len(parts) - 1:
        status = parts[i]
        if not status:
            i += 1
            continue
        if status.startswith("R") or status.startswith("C"):
            if i + 2 >= len(parts):
                break
            entries.append((status[0], parts[i + 2]))
            i += 3
        else:
            entries.append((status[0], parts[i + 1]))
            i += 2
    return entries


def staged_blob(path: str) -> str | None:
    """Return staged content of path, or None if deleted / unreadable."""
    r = subprocess.run(
        ["git", "show", f":{path}"], cwd=REPO_ROOT, capture_output=True
    )
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------- Mem0 ----------

def _openmemory_env_path() -> Path:
    override = os.environ.get("TIGERMEMORY_OPENMEMORY_ENV", "").strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return REPO_ROOT / "runtime" / "openmemory" / ".env"


def _env_value(key: str) -> str:
    """Read KEY=value from runtime/openmemory/.env. Raises RuntimeError if missing."""
    env_path = _openmemory_env_path()
    if not env_path.exists():
        raise RuntimeError(f"missing {env_path} — configure {key} first")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"{key} not found in {env_path}")


def _deepseek_env_value(key: str) -> str:
    """Read DeepSeek config from shell env first, then the local runtime env file."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    return _env_value(key)


def mem0_key() -> str:
    return _env_value("MEM0_API_KEY")


def mcp_api_key() -> str:
    return _env_value("TM_MCP_API_KEY")


def mem0_base() -> str:
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        return "local:disabled"
    return _env_value("MEM0_URL")


def mem0_user_id() -> str:
    """Return the configured Mem0 user_id.

    Reads MEM0_USER_ID via _env_value(). Falls back to "tiger" for
    backward compatibility with existing tigermemory deploys. Override
    by setting MEM0_USER_ID in the runtime config file.
    """
    try:
        return _env_value("MEM0_USER_ID")
    except RuntimeError:
        return "tiger"


TIGERMEMORY_PROFILE_LOCAL = "local"
TIGERMEMORY_PROFILE_HYBRID = "hybrid"
TIGERMEMORY_PROFILE_VALUES = (TIGERMEMORY_PROFILE_LOCAL, TIGERMEMORY_PROFILE_HYBRID)


def _runtime_profile_file_value() -> str | None:
    """Return TIGERMEMORY_PROFILE from runtime/tigermemory/profile.env if present."""
    path = REPO_ROOT / "runtime" / "tigermemory" / "profile.env"
    if not path.is_file():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "TIGERMEMORY_PROFILE":
                return value.strip()
    except OSError:
        return None
    return None


def tigermemory_profile() -> str:
    """Return the active tigermemory runtime profile.

    Default to hybrid so existing WSL/Mem0-backed deployments keep current
    behavior unless a caller explicitly opts into the local-only PoC profile.
    """
    val = os.environ.get("TIGERMEMORY_PROFILE")
    if val is None:
        val = _runtime_profile_file_value()
    if val is None:
        try:
            val = _env_value("TIGERMEMORY_PROFILE")
        except (KeyError, RuntimeError):
            return TIGERMEMORY_PROFILE_HYBRID
    val = val.strip().lower()
    if val not in TIGERMEMORY_PROFILE_VALUES:
        return TIGERMEMORY_PROFILE_HYBRID
    return val


DEFAULT_DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_ADMIN_MODEL = "deepseek-v4-pro"


def deepseek_endpoint() -> str:
    """Return the configured DeepSeek chat-completions endpoint."""
    try:
        return _deepseek_env_value("DEEPSEEK_BASE_URL")
    except RuntimeError:
        return DEFAULT_DEEPSEEK_ENDPOINT


def deepseek_model() -> str:
    """Return the configured DeepSeek model id."""
    try:
        return _deepseek_env_value("DEEPSEEK_MODEL")
    except RuntimeError:
        return DEFAULT_DEEPSEEK_MODEL


def deepseek_admin_model() -> str:
    """Return the configured DeepSeek model id for durable Wiki Admin drafting."""
    try:
        return _deepseek_env_value("DEEPSEEK_ADMIN_MODEL")
    except RuntimeError:
        return DEFAULT_DEEPSEEK_ADMIN_MODEL


# 2026-04-30: tiered Mem0 timeouts. Hardcoded 30s used to mask slow LLM calls
# inside Mem0 (fact-extract + categorize). With DeepSeek thinking disabled,
# normal latency is 1-3s; 10/15s caps surface regressions early and let callers
# fall back to inbox without holding the request open.
MEM0_READ_TIMEOUT = 10   # search / list
MEM0_WRITE_TIMEOUT = 15  # POST /memories — slowest path (Mem0 internal LLM)
MEM0_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_LOCAL_DB_DEFAULT_REL_PATH = pathlib.Path("data") / "tigermemory" / "memory.sqlite"
_LOCAL_MEMORY_SCHEMA_VERSION = 3
_SHADOW_SEARCH_ENV = "TM_SHADOW_SEARCH_ENABLED"
_LOCAL_DUAL_WRITE_ENV = "TM_LOCAL_DUAL_WRITE"
_SHADOW_SEARCH_LOG_REL_PATH = pathlib.Path(".tmp") / "search-shadow"
_LOCAL_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_LOCAL_LATIN_TERM_RE = re.compile(r"[a-z0-9][a-z0-9._:/\\-]*", re.IGNORECASE)
_LOCAL_CJK_STOP_TERMS = {
    "是谁",
    "是什么",
    "什么",
    "怎么",
    "如何",
    "一下",
    "帮我",
    "请问",
    "查询",
    "搜索",
    "看看",
    "关于",
    "是否",
    "是",
    "需要",
}


def _local_db_path() -> pathlib.Path:
    """Resolve local sqlite DB path for the current process.

    Priority:
    1) TIGERMEMORY_LOCAL_DB (absolute / relative path, relative to REPO_ROOT)
    2) default local path `data/tigermemory/memory.sqlite`
    """
    value = os.environ.get("TIGERMEMORY_LOCAL_DB")
    if value:
        path = pathlib.Path(value).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
    else:
        path = REPO_ROOT / _LOCAL_DB_DEFAULT_REL_PATH
    return path


def _local_db_conn() -> sqlite3.Connection:
    path = _local_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _shadow_search_enabled() -> bool:
    return _env_truthy(_SHADOW_SEARCH_ENV)


def _local_dual_write_enabled() -> bool:
    if os.environ.get(_LOCAL_DUAL_WRITE_ENV) is not None:
        return _env_truthy(_LOCAL_DUAL_WRITE_ENV)
    try:
        raw = _env_value(_LOCAL_DUAL_WRITE_ENV)
    except RuntimeError:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _shadow_search_log_path() -> pathlib.Path:
    today = datetime.datetime.now(TZ_CN).strftime("%Y-%m-%d")
    return REPO_ROOT / _SHADOW_SEARCH_LOG_REL_PATH / f"{today}.jsonl"


def _readonly_local_db_conn(path: pathlib.Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _maybe_log_mem0_shadow_search(query: str, old_response_body: str, *, size: int) -> None:
    """Best-effort Phase 0 comparison for Mem0 search vs local SQLite search.

    The hook must never change the online return value and must not create or
    migrate the local SQLite DB. All failures are captured in the shadow log or
    swallowed if even logging is unavailable.
    """
    if not _shadow_search_enabled():
        return
    try:
        old_ids = _mem0_item_ids(old_response_body)
        db_path = _local_db_path()
        warnings: list[str] = []
        local_ids: list[str] = []
        local_t0 = time.monotonic()
        if not db_path.exists():
            warnings.append(f"local_db_missing: {db_path}")
        else:
            try:
                conn = _readonly_local_db_conn(db_path)
                try:
                    local_ids = [
                        str(item.get("id"))
                        for item in _local_search_local_memory(conn, query, size=size)
                        if isinstance(item, dict) and item.get("id")
                    ]
                finally:
                    conn.close()
            except Exception as exc:
                warnings.append(f"local_search_exception: {exc}")
        local_latency_ms = (time.monotonic() - local_t0) * 1000.0
        intersection = set(old_ids) & set(local_ids)
        record = {
            "timestamp": datetime.datetime.now(TZ_CN).isoformat(),
            "query": query,
            "size": size,
            "old_ids": old_ids,
            "local_ids": local_ids,
            "intersection_count": len(intersection),
            "old_count": len(old_ids),
            "local_count": len(local_ids),
            "old_latency_ms": 0.0,
            "local_latency_ms": round(local_latency_ms, 2),
            "warnings": warnings,
        }
        log_path = _shadow_search_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
    except Exception:
        pass


def _local_schema_ddl() -> str:
    return """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        topic TEXT NOT NULL,
        source_agent TEXT NOT NULL,
        route_decision TEXT NOT NULL,
        route_score INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        content_sha256 TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'active',
        backend_origin TEXT NOT NULL DEFAULT 'local',
        vector_status TEXT NOT NULL DEFAULT 'fts5_only',
        legacy_mem0_id TEXT,
        shadow_state TEXT,
        verified_at INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_memories_content_sha_topic
        ON memories(content_sha256, topic);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_legacy_mem0_id
        ON memories(legacy_mem0_id) WHERE legacy_mem0_id IS NOT NULL;
    CREATE TABLE IF NOT EXISTS migration_audit (
        legacy_mem0_id TEXT PRIMARY KEY,
        new_id TEXT,
        content_sha256 TEXT NOT NULL,
        disposition TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        memory_id TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        done_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_outbox_pending
        ON outbox(status, next_attempt_at);
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        id UNINDEXED,
        content
    );
    CREATE TRIGGER IF NOT EXISTS memories_fts_ai
    AFTER INSERT ON memories
    BEGIN
        INSERT INTO memories_fts(id, content) VALUES (new.id, new.content);
    END;
    CREATE TRIGGER IF NOT EXISTS memories_fts_ad
    AFTER DELETE ON memories
    BEGIN
        DELETE FROM memories_fts WHERE id = old.id;
    END;
    CREATE TRIGGER IF NOT EXISTS memories_fts_au
    AFTER UPDATE ON memories
    BEGIN
        DELETE FROM memories_fts WHERE id = old.id;
        INSERT INTO memories_fts(id, content) VALUES (new.id, new.content);
    END;
    """


def _ensure_local_memory_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_local_schema_ddl())
    existing_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    for column_name, column_ddl in (
        ("content_sha256", "ALTER TABLE memories ADD COLUMN content_sha256 TEXT"),
        ("legacy_mem0_id", "ALTER TABLE memories ADD COLUMN legacy_mem0_id TEXT"),
        ("shadow_state", "ALTER TABLE memories ADD COLUMN shadow_state TEXT"),
        ("verified_at", "ALTER TABLE memories ADD COLUMN verified_at INTEGER"),
    ):
        if column_name not in existing_columns:
            conn.execute(column_ddl)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_content_sha_topic
        ON memories(content_sha256, topic)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_legacy_mem0_id
        ON memories(legacy_mem0_id) WHERE legacy_mem0_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_audit (
            legacy_mem0_id TEXT PRIMARY KEY,
            new_id TEXT,
            content_sha256 TEXT NOT NULL,
            disposition TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            memory_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            done_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_outbox_pending
        ON outbox(status, next_attempt_at)
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_meta (key, value, updated_at)
        VALUES ('schema_version', ?, ?)
        """,
        (str(_LOCAL_MEMORY_SCHEMA_VERSION), datetime.datetime.now(TZ_CN).isoformat()),
    )
    conn.commit()


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def _ensure_local_metadata_json(raw: Any) -> tuple[dict[str, Any], bool]:
    if raw is None:
        return ({}, False)
    if isinstance(raw, dict):
        return (dict(raw), False)
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, dict):
            return (dict(parsed), False)
    except Exception:
        pass
    return ({"_raw_metadata": str(raw)}, True)


def _local_write_memory_record(
    agent: str,
    topic: str,
    text: str,
    metadata_extra: dict[str, Any] | None = None,
    *,
    route_decision: str | None = None,
    route_score: int | None = None,
    backend_origin: str = TIGERMEMORY_PROFILE_LOCAL,
    legacy_mem0_id: str | None = None,
    shadow_state: str | None = None,
) -> dict[str, Any]:
    conn = _local_db_conn()
    try:
        _ensure_local_memory_schema(conn)
        memory_id = str(uuid.uuid4())
        now_ts = int(time.time())
        metadata_payload, _ = _ensure_local_metadata_json(metadata_extra)
        metadata_payload.setdefault("source", agent)
        metadata_payload.setdefault("topic", topic)
        route_decision_value = str(
            route_decision or metadata_payload.get("route_decision") or "mem0"
        ).strip() or "mem0"
        try:
            raw_score = route_score if route_score is not None else metadata_payload.get("route_score")
            route_score_value = int(raw_score) if raw_score is not None else 0
        except (TypeError, ValueError):
            route_score_value = 0

        if legacy_mem0_id:
            metadata_payload["legacy_mem0_id"] = legacy_mem0_id
        if shadow_state:
            metadata_payload["shadow_state"] = shadow_state
        metadata_payload["routed_from"] = "mem0_write"
        metadata_payload["source_agent"] = metadata_payload.get("source_agent", agent)

        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        legacy_mem0_id_value = str(metadata_payload.get("legacy_mem0_id") or "").strip() or None
        shadow_state_value = str(metadata_payload.get("shadow_state") or "").strip() or None
        conn.execute(
            """
            INSERT INTO memories(
                id, content, topic, source_agent, route_decision, route_score,
                metadata_json, content_sha256, created_at, updated_at, state,
                backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                text,
                topic,
                agent,
                route_decision_value,
                route_score_value,
                json.dumps(metadata_payload, ensure_ascii=False),
                content_sha256,
                now_ts,
                now_ts,
                "active",
                backend_origin,
                "fts5_only",
                legacy_mem0_id_value,
                shadow_state_value,
                now_ts,
            ),
        )
        conn.commit()
        return {
            "ok": True,
            "id": memory_id,
            "route": "local",
            "route_info": {
                "backend": backend_origin,
                "backend_origin": backend_origin,
                "vector_status": "fts5_only",
                "route_decision": route_decision_value,
                "route_score": route_score_value,
            },
        }
    finally:
        conn.close()


def _record_local_dual_write_failure(
    *,
    agent: str,
    topic: str,
    remote_id: str,
    error: Exception,
) -> None:
    try:
        from . import runtime_events

        runtime_events.record_event(
            event_type="memory_local_dual_write",
            service="tigermemory-core",
            component="mem0_write",
            ok=False,
            severity="warning",
            agent=agent,
            route="local-shadow",
            outcome="shadow_write_failed",
            target_ref={"legacy_mem0_id": remote_id, "topic": topic},
            error=str(error),
            extra={"source_agent": agent},
        )
    except Exception:
        pass


def _local_fts_query(query: str) -> str:
    terms = flatten_search_query_terms(search_query_term_groups(query))
    if not terms:
        terms = [t for t in query.split() if t.strip()]
    terms = [term.strip().replace('"', '""') for term in terms if term.strip()]
    if not terms:
        return ""
    return " AND ".join(f'"{term}"' for term in terms)


def _local_cjk_query_terms(query: str, *, max_terms: int = 48) -> list[str]:
    """Return lightweight CJK/Latin bridge terms for local sqlite fallback search.

    SQLite FTS5's default tokenizer is not a reliable Chinese segmenter.  This
    app-level bridge keeps the schema stable while making short Chinese natural
    questions usable in local/basic mode.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        normalized = term.strip().lower()
        if not normalized or normalized in seen or normalized in _LOCAL_CJK_STOP_TERMS:
            return
        if _LOCAL_CJK_RUN_RE.fullmatch(normalized):
            if len(normalized) < 2:
                return
        elif len(normalized) < 2:
            return
        seen.add(normalized)
        terms.append(normalized)

    q = (query or "").strip()
    if not q:
        return []

    for group in search_query_term_groups(q):
        for item in group:
            add(item)

    for item in _LOCAL_LATIN_TERM_RE.findall(q):
        add(item)

    for run in _LOCAL_CJK_RUN_RE.findall(q):
        cleaned = run
        for stop in sorted(_LOCAL_CJK_STOP_TERMS, key=len, reverse=True):
            cleaned = cleaned.replace(stop, "")
        add(cleaned)
        if len(cleaned) >= 3:
            for width in (4, 3, 2):
                if len(cleaned) < width:
                    continue
                for idx in range(0, len(cleaned) - width + 1):
                    add(cleaned[idx : idx + width])

    return terms[:max_terms]


def _local_memory_fallback_rows(conn: sqlite3.Connection, query: str, size: int) -> list[sqlite3.Row]:
    terms = _local_cjk_query_terms(query)
    if not terms:
        return []
    rows = conn.execute(
        """
        SELECT id, content, topic, source_agent, route_decision, route_score,
               metadata_json, content_sha256, created_at, updated_at, state,
               backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
        FROM memories
        WHERE state = 'active'
        ORDER BY created_at DESC
        LIMIT 500
        """
    ).fetchall()
    q_lower = (query or "").strip().lower()
    query_has_cjk = bool(_LOCAL_CJK_RUN_RE.search(query or ""))
    scored: list[tuple[int, int, sqlite3.Row]] = []
    for row in rows:
        text = "\n".join(
            str(row[key] or "")
            for key in ("content", "topic", "source_agent", "metadata_json")
        ).lower()
        score = 0
        if q_lower and q_lower in text:
            score += 20
        matched_terms = 0
        for term in terms:
            if term in text:
                matched_terms += 1
                score += 4 if _LOCAL_CJK_RUN_RE.search(term) and len(term) >= 3 else 2
        if not query_has_cjk and len(terms) >= 2 and matched_terms < 2 and q_lower not in text:
            continue
        if score > 0:
            scored.append((score, int(row["created_at"]), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _score, _created_at, row in scored[: max(1, int(size))]]


def _local_memory_row_to_item(row: sqlite3.Row, *, include_route_info: bool = True) -> dict[str, Any]:
    meta, _meta_warn = _ensure_local_metadata_json(row["metadata_json"])
    route_decision = row["route_decision"]
    route_score = row["route_score"]
    item: dict[str, Any] = {
        "id": row["id"],
        "text": row["content"],
        "content": row["content"],
        "topic": row["topic"],
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source_agent": row["source_agent"],
        "source": row["source_agent"],
        "metadata_": meta,
        "metadata": meta,
        "backend_origin": row["backend_origin"],
        "vector_status": row["vector_status"],
        "content_sha256": _row_value(row, "content_sha256"),
        "legacy_mem0_id": _row_value(row, "legacy_mem0_id"),
        "shadow_state": _row_value(row, "shadow_state"),
        "verified_at": _row_value(row, "verified_at"),
    }
    if include_route_info:
        item["route_decision"] = route_decision
        item["route_score"] = route_score
        item["route_info"] = {
            "backend": row["backend_origin"],
            "route_decision": route_decision,
            "route_score": route_score,
            "vector_status": row["vector_status"],
        }
    return item


def _local_read_memory_by_id(conn: sqlite3.Connection, memory_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, content, topic, source_agent, route_decision, route_score,
               metadata_json, content_sha256, created_at, updated_at, state,
               backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
        FROM memories
        WHERE id = ?
        """,
        (memory_id,),
    ).fetchone()
    if not row:
        return None
    return _local_memory_row_to_item(row, include_route_info=True)


def _local_read_memory_by_legacy_id(conn: sqlite3.Connection, legacy_mem0_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, content, topic, source_agent, route_decision, route_score,
               metadata_json, content_sha256, created_at, updated_at, state,
               backend_origin, vector_status, legacy_mem0_id, shadow_state, verified_at
        FROM memories
        WHERE legacy_mem0_id = ?
        """,
        (legacy_mem0_id,),
    ).fetchone()
    if not row:
        return None
    return _local_memory_row_to_item(row, include_route_info=True)


def _local_search_local_memory(conn: sqlite3.Connection, query: str, size: int = 5) -> list[dict[str, Any]]:
    limit = max(1, int(size))
    q = query.strip()
    if MEM0_UUID_RE.fullmatch(q):
        row = _local_read_memory_by_id(conn, q)
        return [row] if row else []
    fts_query = _local_fts_query(q)
    rows: list[sqlite3.Row] = []
    if fts_query:
        try:
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.topic, m.source_agent, m.route_decision, m.route_score,
                       m.metadata_json, m.content_sha256, m.created_at, m.updated_at, m.state,
                       m.backend_origin, m.vector_status, m.legacy_mem0_id, m.shadow_state, m.verified_at
                FROM memories AS m
                WHERE m.id IN (
                    SELECT id FROM memories_fts WHERE memories_fts MATCH ?
                )
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.Error:
            rows = []
    seen = {str(row["id"]) for row in rows}
    if len(rows) < limit:
        for row in _local_memory_fallback_rows(conn, q, limit):
            if str(row["id"]) in seen:
                continue
            rows.append(row)
            seen.add(str(row["id"]))
            if len(rows) >= limit:
                break
    return [_local_memory_row_to_item(row, include_route_info=True) for row in rows]


def configure_stdio() -> None:
    """
    [终端编码鲁棒性配置器]
    避免在 Windows 控制台环境下输出中文字符或 Emoji 时发生 UnicodeEncodeError (GBK/cp936 等本地编码限制)。
    对 sys.stdout 和 sys.stderr 强行重配置为 utf-8 编码，对非法字节进行替换。
    """
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    stream.reconfigure(errors="backslashreplace")
                except Exception:
                    pass


def _is_private_ip(host: str) -> bool:
    """
    [传输安全阻断器单兵辅助函数]
    精准检测 IP 是否属于安全的回环网络 (127.0.0.0/8, ::1)、
    RFC 1918 私有局域网网段 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    或者 Tailscale 虚拟安全专网 IP (100.64.0.0/10)。
    必须是一个标准的数字 IP 字符串，不支持伪装域名。
    """
    if not host:
        return False
    # 去除 IPv6 的中括号，例如 [::1] -> ::1
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private:
            return True
        if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
    except ValueError:
        pass
    return False


def check_transport_security(url: str) -> None:
    """
    [传输安全阻断器 - Network Transports Bearer Guard]
    精准识别安全的回环地址 (localhost)、HTTPS 加密传输、局域网私有网段以及 Tailscale 虚拟安全专网 IP。
    防止零基础或初学者在不安全的外网环境中明文传输 API 凭证。
    """
    # 检查是否配置了强制豁免环境变量
    if os.environ.get("TM_ALLOW_UNSECURE_HTTP") == "1":
        return

    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname

    # 1. 允许安全的加密连接
    if scheme == "https":
        return

    # 2. 允许安全的主机名或本地回环
    if host in ("localhost", "[::1]", "::1"):
        return

    # 3. 允许私有 IP 或 Tailscale/CGNAT 安全专网
    if host and _is_private_ip(host):
        return

    # 若不满足任何安全通道条件，则抛出详细的中文指引阻断请求（ASCII/GBK 安全字符）
    block_msg = (
        "\n"
        "[Guard] Tigermemory 传输安全阻断警报 - Bearer Guard\n"
        "============================================================\n"
        "[Warning] 安全阻断：未加密的外网明文 HTTP 请求可能导致 API 密钥在传输中泄露！\n"
        f" -> 目标地址: {url}\n\n"
        "[Info] 为什么被拦截？\n"
        "   为避免初学者在外部网络明文发送 Bearer 令牌，本拦截器默认仅信任本地回环、HTTPS、局域网或 Tailscale 私有内网。\n\n"
        "[Fix] 如何解决？（满足以下任意一项即可自动放行）：\n"
        "   1. 升级为 HTTPS (例如 https://your-domain.com)；\n"
        "   2. 本地部署请确保使用的是 localhost 或 127.0.0.1 端口；\n"
        "   3. 如果两端都在外网，推荐免费安装并使用 Tailscale 异地组网，利用其提供的 100.x.y.z 网段 IP 即可自动放行；\n"
        "   4. 局域网部署 (家用 NAS / 公司内网) 也会自动放行：192.168.x.x / 10.x.x.x / 172.16-31.x.x。\n\n"
        "[Emergency] 临时豁免方式 (仅限安全隔离的开发调试环境)：\n"
        "   - Windows PowerShell 执行: $env:TM_ALLOW_UNSECURE_HTTP=\"1\"\n"
        "   - WSL2 / Linux / macOS 执行: export TM_ALLOW_UNSECURE_HTTP=1\n"
        "============================================================\n"
    )
    raise RuntimeError(block_msg)



def mem0_request(
    url: str,
    data: bytes | None = None,
    *,
    timeout: int = MEM0_READ_TIMEOUT,
    method: str | None = None,
) -> str:
    """GET (data=None) or POST to Mem0. Raises RuntimeError with HTTP code / reason on failure.

    Raises RuntimeError("Mem0 timeout: ...") specifically on socket timeout so
    callers can distinguish transient slowness from hard failures and degrade.
    """
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        raise RuntimeError("local profile: mem0_request blocked")
    check_transport_security(url)
    key = mem0_key()
    headers = {"Authorization": f"Bearer {key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, headers=headers, method=(method or ("POST" if data else "GET"))
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mem0 HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        # socket.timeout is wrapped as URLError(reason=socket.timeout(...))
        reason = e.reason
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            raise RuntimeError(f"Mem0 timeout: {reason} (limit={timeout}s)")
        raise RuntimeError(f"Mem0 unreachable: {reason}")


def mem0_write(
    agent: str,
    topic: str,
    text: str,
    metadata_extra: dict[str, Any] | None = None,
    *,
    route_decision: str | None = None,
    route_score: int | None = None,
    route_topic_inferred: str | None = None,
    timeout: int = MEM0_WRITE_TIMEOUT,
    infer: bool = False,
) -> str:
    """POST a memory with enforced metadata. Returns raw response body.

    `timeout` defaults to `MEM0_WRITE_TIMEOUT` but callers (e.g. tm_http
    `_write_memory_with_review`) may pass a smaller value when they have a
    tight remaining budget for the overall request.

    `infer` defaults to False (2026-04-30, P5): tigermemory already runs
    `tm_route.route_memory` to do LLM-based fact evaluation before this
    point, so Mem0's internal fact-extract LLM is redundant AND has been
    observed to silently drop technical content (returning {"results":[]}
    when its "Personal Information Organizer" prompt judges the text as
    non-personal). Setting infer=False stores text verbatim, ~1-2s latency,
    no data loss. Requires the OpenMemory router to forward the field
    (deploy/openmemory/patches/app/routers/memories.py P5 patch).
    """
    validate_agent(agent)
    validate_topic(topic)
    if not text.strip():
        raise ValueError("text required")
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        return json.dumps(
            _local_write_memory_record(
                agent,
                topic,
                text,
                metadata_extra,
                route_decision=route_decision,
                route_score=route_score,
                backend_origin=TIGERMEMORY_PROFILE_LOCAL,
            )
        )
    metadata: dict[str, Any] = {"source": agent, "topic": topic}
    if route_decision is not None:
        metadata["route_decision"] = route_decision
    if route_score is not None:
        metadata["route_score"] = route_score
    if route_topic_inferred is not None:
        metadata["route_topic_inferred"] = route_topic_inferred
    if metadata_extra:
        metadata.update(metadata_extra)
    payload = json.dumps({
        "user_id": mem0_user_id(),
        "text": text,
        "metadata": metadata,
        "infer": infer,
    }).encode("utf-8")
    raw_response = mem0_request(
        f"{mem0_base()}/api/v1/memories/",
        data=payload,
        timeout=timeout,
    )
    if _local_dual_write_enabled():
        remote_id = ""
        try:
            remote_payload = json.loads(raw_response)
            remote_id = str(remote_payload.get("id") or "").strip() if isinstance(remote_payload, dict) else ""
            if MEM0_UUID_RE.fullmatch(remote_id):
                _local_write_memory_record(
                    agent,
                    topic,
                    text,
                    metadata,
                    route_decision=route_decision,
                    route_score=route_score,
                    backend_origin="local-shadow",
                    legacy_mem0_id=remote_id,
                    shadow_state="pending",
                )
        except Exception as exc:
            _record_local_dual_write_failure(
                agent=agent,
                topic=topic,
                remote_id=remote_id,
                error=exc,
            )
    return raw_response


def mem0_get(memory_id: str) -> str:
    """GET a Mem0 memory by exact UUID. Returns raw response body."""
    if not MEM0_UUID_RE.fullmatch(memory_id.strip()):
        raise ValueError("memory_id must be a full UUID")
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        raise ValueError("mem0_get unavailable in local profile")
    mem_id = urllib.parse.quote(memory_id.strip())
    return mem0_request(
        f"{mem0_base().rstrip('/')}/api/v1/memories/{mem_id}",
        timeout=MEM0_READ_TIMEOUT,
    )


def mem0_delete(memory_ids: list[str]) -> str:
    """DELETE Mem0 memories by UUID via OpenMemory's collection endpoint."""
    ids = [mid.strip() for mid in memory_ids if mid and mid.strip()]
    if not ids:
        raise ValueError("memory_ids required")
    bad = [mid for mid in ids if not MEM0_UUID_RE.fullmatch(mid)]
    if bad:
        raise ValueError(f"invalid memory UUID(s): {', '.join(bad)}")
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        return json.dumps({"ok": False, "deleted": 0, "reason": "local profile"})
    payload = json.dumps({"user_id": mem0_user_id(), "memory_ids": ids}).encode("utf-8")
    return mem0_request(
        f"{mem0_base().rstrip('/')}/api/v1/memories/",
        data=payload,
        timeout=MEM0_READ_TIMEOUT,
        method="DELETE",
    )


def mem0_update_content(memory_id: str, memory_content: str) -> str:
    """PUT replacement content for a Mem0 memory.

    OpenMemory CE ignores metadata on PUT, so this wrapper intentionally
    exposes content-only updates. To change metadata, delete + recreate.
    """
    mem_id = memory_id.strip()
    if not MEM0_UUID_RE.fullmatch(mem_id):
        raise ValueError("memory_id must be a full UUID")
    if not memory_content.strip():
        raise ValueError("memory_content required")
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        return json.dumps({"ok": False, "reason": "local profile"})
    payload = json.dumps({
        "user_id": mem0_user_id(),
        "memory_content": memory_content,
    }).encode("utf-8")
    return mem0_request(
        f"{mem0_base().rstrip('/')}/api/v1/memories/{urllib.parse.quote(mem_id)}",
        data=payload,
        timeout=MEM0_WRITE_TIMEOUT,
        method="PUT",
    )


def mem0_search(
    query: str,
    size: int = 5,
    match_mode: str = "id_first",
    *,
    explain: bool = False,
) -> str:
    """GET memories by query. Returns raw response body."""
    if match_mode not in {"id_first", "token_and", "substring"}:
        raise ValueError("match_mode must be one of: id_first, token_and, substring")
    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        conn = _local_db_conn()
        try:
            _ensure_local_memory_schema(conn)
            results = _local_search_local_memory(conn, query, size=size)
            payload = {
                "count": len(results),
                "results": results,
                "items": results,
                "warnings": [],
                "search_backend": TIGERMEMORY_PROFILE_LOCAL,
            }
            return json.dumps(payload)
        finally:
            conn.close()
    params = urllib.parse.urlencode(
        # OpenMemory's patched GET /api/v1/memories/ filters on search_query.
        # Older tigermemory docs and clients used query=; the router keeps that
        # as a compatibility alias, but tm_core should call the canonical param.
        {
            "user_id": mem0_user_id(),
            "search_query": query,
            "page": 1,
            "size": size,
            "match_mode": match_mode,
        }
    )
    raw = mem0_request(
        f"{mem0_base()}/api/v1/memories/?{params}",
        timeout=MEM0_READ_TIMEOUT,
    )
    _maybe_log_mem0_shadow_search(query, raw, size=size)
    return raw


def _mem0_item_text(item: dict[str, Any]) -> str:
    return str(item.get("text") or item.get("content") or item.get("memory") or "")


def _mem0_item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata_") or item.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _mem0_item_ids(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data.get("items") or data.get("results") or []
    if not isinstance(items, list):
        return []
    return [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]


def _mem0_created_at_local(created_at: Any) -> tuple[datetime.datetime | None, str | None]:
    if isinstance(created_at, (int, float)):
        dt = datetime.datetime.fromtimestamp(created_at, TZ_CN)
        return dt, dt.isoformat()
    if isinstance(created_at, str) and created_at.strip():
        raw = created_at.strip()
        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            dt = dt.astimezone(TZ_CN)
            return dt, dt.isoformat()
        except ValueError:
            return None, raw
    return None, None


def _digest_window(date_str: str) -> tuple[datetime.datetime, datetime.datetime]:
    day = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=TZ_CN)
    end = datetime.datetime.combine(day, datetime.time.max, tzinfo=TZ_CN)
    return start, end


def _digest_status_for_memory(memory_id: str, created_dt: datetime.datetime | None, digest_date: str | None) -> dict[str, Any]:
    if not digest_date:
        return {
            "digest_date": None,
            "digest_path": None,
            "digest_contains": None,
            "digest_inclusion_reason": "no digest date available",
        }
    rel = f"inbox/daily/{digest_date}.md"
    path = REPO_ROOT / rel
    start, end = _digest_window(digest_date)
    if created_dt is not None and not (start <= created_dt <= end):
        return {
            "digest_date": digest_date,
            "digest_path": rel,
            "digest_contains": False,
            "digest_inclusion_reason": (
                f"created_at_local={created_dt.isoformat()} outside digest window {digest_date}"
            ),
        }
    if not path.exists():
        return {
            "digest_date": digest_date,
            "digest_path": rel,
            "digest_contains": False,
            "digest_inclusion_reason": "digest file missing",
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    contains = memory_id in text
    return {
        "digest_date": digest_date,
        "digest_path": rel,
        "digest_contains": contains,
        "digest_inclusion_reason": "included in digest" if contains else "inside digest window but id not found in digest",
    }


def verify_memory_id(
    memory_id: str,
    key_terms: str | None = None,
    digest_date: str | None = None,
) -> dict[str, Any]:
    return verify_memory_record(memory_id, key_terms=key_terms, digest_date=digest_date)


def verify_memory_record(
    memory_id: str,
    key_terms: str | None = None,
    digest_date: str | None = None,
) -> dict[str, Any]:
    """Verify a Mem0 id against direct storage, search, and daily digest visibility."""
    mem_id = memory_id.strip()
    if not MEM0_UUID_RE.fullmatch(mem_id):
        raise ValueError("memory_id must be a full UUID")

    if tigermemory_profile() == TIGERMEMORY_PROFILE_LOCAL:
        conn = _local_db_conn()
        try:
            _ensure_local_memory_schema(conn)
            data = _local_read_memory_by_id(conn, mem_id)
            if not data:
                data = _local_read_memory_by_legacy_id(conn, mem_id)
            if not data:
                return {
                    "id": mem_id,
                    "queried_id": mem_id,
                    "resolved_id": None,
                    "legacy_mem0_id": None,
                    "status": "not_found",
                    "exists": False,
                    "direct_readback_ok": False,
                    "state": None,
                    "metadata": {},
                    "created_at": None,
                    "created_at_local": None,
                    "text_len": 0,
                    "text_sha256_12": None,
                    "text_preview": "",
                    "search_by_id_self_hit": False,
                    "search_by_id_count": 0,
                    "search_by_id_ids": [],
                    "search_by_terms_self_hit": None,
                    "search_by_terms_count": None,
                    "search_by_terms_ids": [],
                    "backend_origin": TIGERMEMORY_PROFILE_LOCAL,
                    "warnings": [],
                }

            text = str(data.get("content") or "")
            resolved_id = str(data.get("id") or mem_id)
            legacy_mem0_id = data.get("legacy_mem0_id")
            created_dt, created_local = _mem0_created_at_local(data.get("created_at"))
            effective_digest_date = digest_date or (created_dt.strftime("%Y-%m-%d") if created_dt else None)

            result: dict[str, Any] = {
                "id": resolved_id,
                "queried_id": mem_id,
                "resolved_id": resolved_id,
                "legacy_mem0_id": legacy_mem0_id,
                "status": "exists_active" if str(data.get("state") or "") == "active" else "exists_inactive",
                "exists": True,
                "direct_readback_ok": True,
                "state": data.get("state"),
                "metadata": data.get("metadata_") or {},
                "created_at": data.get("created_at"),
                "created_at_local": created_local,
                "text_len": len(text),
                "text_sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
                "text_preview": text[:300],
                "search_by_id_self_hit": False,
                "search_by_id_count": None,
                "search_by_id_ids": [],
                "search_by_terms_self_hit": None,
                "search_by_terms_count": None,
                "search_by_terms_ids": [],
                "backend_origin": data.get("backend_origin"),
                "vector_status": data.get("vector_status"),
                "content_sha256": data.get("content_sha256"),
                "shadow_state": data.get("shadow_state"),
                "verified_at": data.get("verified_at"),
                "warnings": [],
                "digest_date": None,
                "digest_path": None,
                "digest_contains": None,
                "digest_inclusion_reason": "n/a: local backend MVP does not write digest",
            }

            try:
                ids = [item.get("id") for item in _local_search_local_memory(conn, resolved_id, size=20)]
                ids = [item_id for item_id in ids if isinstance(item_id, str)]
                result["search_by_id_ids"] = ids
                result["search_by_id_count"] = len(ids)
                result["search_by_id_self_hit"] = resolved_id in ids
            except Exception as exc:
                result["search_by_id_self_hit"] = False
                result["warnings"].append(f"search_by_id failed: {exc}")

            terms = (key_terms or "").strip()
            if terms:
                try:
                    ids = [item.get("id") for item in _local_search_local_memory(conn, terms, size=20)]
                    ids = [item_id for item_id in ids if isinstance(item_id, str)]
                    result["search_by_terms_ids"] = ids
                    result["search_by_terms_count"] = len(ids)
                    result["search_by_terms_self_hit"] = resolved_id in ids
                except Exception as exc:
                    result["search_by_terms_self_hit"] = False
                    result["warnings"].append(f"search_by_terms failed: {exc}")

            return result
        finally:
            conn.close()

    result = {
        "id": mem_id,
        "status": "unknown",
        "exists": False,
        "direct_readback_ok": False,
        "state": None,
        "metadata": {},
        "created_at": None,
        "created_at_local": None,
        "text_len": 0,
        "text_sha256_12": None,
        "text_preview": "",
        "search_by_id_self_hit": None,
        "search_by_id_count": None,
        "search_by_id_ids": [],
        "search_by_terms_self_hit": None,
        "search_by_terms_count": None,
        "search_by_terms_ids": [],
        "warnings": [],
    }

    try:
        direct_raw = mem0_get(mem_id)
    except RuntimeError as exc:
        err = str(exc)
        if err.startswith("Mem0 HTTP 404:"):
            result["status"] = "not_found"
            result["error"] = err
            return result
        result["status"] = "mem0_unreachable"
        result["error"] = err
        return result

    try:
        data = json.loads(direct_raw)
    except json.JSONDecodeError as exc:
        result["status"] = "mem0_unreachable"
        result["error"] = f"Mem0 returned invalid JSON: {exc}"
        return result
    if not isinstance(data, dict):
        result["status"] = "mem0_unreachable"
        result["error"] = "Mem0 returned a non-object response"
        return result
    text = _mem0_item_text(data)
    meta = _mem0_item_metadata(data)
    state = str(data.get("state") or "")
    created_dt, created_local = _mem0_created_at_local(data.get("created_at"))
    effective_digest_date = digest_date or (created_dt.strftime("%Y-%m-%d") if created_dt else None)

    result.update({
        "status": "exists_active" if state == "active" else "exists_inactive",
        "exists": True,
        "direct_readback_ok": True,
        "state": state or None,
        "metadata": meta,
        "created_at": data.get("created_at"),
        "created_at_local": created_local,
        "text_len": len(text),
        "text_sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "text_preview": text[:300],
    })

    try:
        ids = _mem0_item_ids(mem0_search(mem_id, size=20, match_mode="id_first"))
        result["search_by_id_ids"] = ids
        result["search_by_id_count"] = len(ids)
        result["search_by_id_self_hit"] = mem_id in ids
    except Exception as exc:
        result["search_by_id_self_hit"] = False
        result["warnings"].append(f"search_by_id failed: {exc}")

    terms = (key_terms or "").strip()
    if terms:
        try:
            ids = _mem0_item_ids(mem0_search(terms, size=20, match_mode="id_first"))
            result["search_by_terms_ids"] = ids
            result["search_by_terms_count"] = len(ids)
            result["search_by_terms_self_hit"] = mem_id in ids
        except Exception as exc:
            result["search_by_terms_self_hit"] = False
            result["warnings"].append(f"search_by_terms failed: {exc}")

    result.update(_digest_status_for_memory(mem_id, created_dt, effective_digest_date))
    return result


# ---------- Embedding client (OpenAI-compatible) ----------
#
# Used by tm_memory_eval rerank and any future LLM Wiki embedding integration.
# Does NOT auto-fall-back on failure: callers must degrade (e.g. skip rerank,
# fall back to lexical-only) explicitly. Hiding embedding failures behind a
# silent lexical fallback would mask regressions that the eval harness exists
# to catch.
#
# Env contract (read at call time so .env edits take effect without restart):
#   EMBEDDING_BASE_URL   e.g. https://ark.cn-beijing.volces.com/api/coding/v3
#                        or   http://localhost:19190/v1  (封存路径: 本地 Qwen)
#   EMBEDDING_MODEL      e.g. doubao-embedding-vision  or  qwen3-embedding
#   EMBEDDING_API_KEY    fallback to OPENAI_API_KEY (ARK key lives there)
#   EMBEDDING_DIMENSIONS optional; pass-through if server supports (ARK: 2048)
#   EMBEDDING_TIMEOUT    optional int seconds, default 30

def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


EMBEDDING_TIMEOUT = _env_positive_int("EMBEDDING_TIMEOUT", 30)
# ARK (Volcengine) /embeddings limits `input` to 10 entries per request. We
# keep the cap configurable for self-hosted backends (e.g. local vLLM accepts
# larger batches) but default to the tightest known limit so primary-path
# callers succeed without feature-detection.
EMBEDDING_BATCH_SIZE = _env_positive_int("EMBEDDING_BATCH_SIZE", 10)

# ---- Stability layer (OpenViking-inspired retry + circuit breaker) ----
#
# Phase 5b-0 audit (`wiki/systems/openviking-upstream-grounding.md` §6, §9.1)
# identified that tm_core embed had no transient/permanent classification, no
# backoff, and no circuit breaker — Phase 5 first-run 50% HTTP 500 was the
# symptom. This block is the minimum-viable port of that engineering baseline.
# It does NOT change retrieval ranking, model, or index dimension. It does NOT
# add sparse / hybrid (Coding Plan endpoint lacks /multimodal_embeddings).

# Patterns adapted from openviking/utils/model_retry.py.
_EMBED_TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_EMBED_PERMANENT_HTTP_STATUSES = {400, 401, 403, 404, 422}
_EMBED_TRANSIENT_TEXT_PATTERNS = (
    "timeout", "timed out", "rate limit", "ratelimit", "toomanyrequests",
    "requestbursttoofast", "connection refused", "connection reset",
    "connectionerror", "temporarily unavailable", "server overloaded",
)
_EMBED_PERMANENT_TEXT_PATTERNS = (
    "accountoverdue", "invalidendpointormodel", "model or endpoint",
    "does not exist", "do not have access", "unauthorized", "forbidden",
    "shape mismatch", "dimension mismatch",
)


class EmbeddingError(RuntimeError):
    """Embedding failure with transient/permanent classification.

    Subclass of RuntimeError so existing `except RuntimeError` / `except Exception`
    callers keep working unchanged. Adds `.kind` ('permanent'|'transient'|'unknown')
    and `.status` (HTTP status int or None) for retry/breaker logic.
    """

    def __init__(self, message: str, *, kind: str = "unknown", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def _classify_embedding_failure(status: int | None, body: str) -> str:
    """Return 'permanent' | 'transient' | 'unknown' based on status + body text.

    Status code wins if it is in either list. Otherwise scan body text. Default
    is 'unknown' which is treated as non-retryable (fail-fast, like OpenViking).
    """
    if status is not None:
        if status in _EMBED_PERMANENT_HTTP_STATUSES:
            return "permanent"
        if status in _EMBED_TRANSIENT_HTTP_STATUSES:
            return "transient"
    text = (body or "").lower()
    for pat in _EMBED_PERMANENT_TEXT_PATTERNS:
        if pat in text:
            return "permanent"
    for pat in _EMBED_TRANSIENT_TEXT_PATTERNS:
        if pat in text:
            return "transient"
    return "unknown"


def _embed_retry_config() -> dict[str, Any]:
    """Read retry / breaker tunables from env at call time (so .env edits stick).

    All envs optional. Defaults are conservative per OpenViking baseline.
    """
    def _f(name: str, default: float) -> float:
        v = os.environ.get(name, "").strip()
        if not v:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def _i(name: str, default: int) -> int:
        v = os.environ.get(name, "").strip()
        if not v:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    jitter_raw = os.environ.get("EMBEDDING_RETRY_JITTER", "1").strip().lower()
    jitter = jitter_raw not in {"0", "false", "no", "off"}

    return {
        "max_retries": max(0, _i("EMBEDDING_MAX_RETRIES", 3)),
        "base_delay": max(0.0, _f("EMBEDDING_BASE_DELAY", 0.5)),
        "max_delay": max(0.0, _f("EMBEDDING_MAX_DELAY", 8.0)),
        "jitter": jitter,
        "breaker_threshold": max(1, _i("EMBEDDING_BREAKER_THRESHOLD", 5)),
        "breaker_reset": max(0.0, _f("EMBEDDING_BREAKER_RESET", 60.0)),
    }


class _EmbeddingBreaker:
    """Single-process circuit breaker for embed_texts.

    State machine adapted from openviking/utils/circuit_breaker.py, simplified
    (no half-open exponential backoff cascade — tigermemory builds are short).

    - CLOSED: allow all calls.
    - OPEN: fast-fail with EmbeddingError(kind='transient') until reset_timeout
      elapses, then transition to HALF_OPEN on next check.
    - HALF_OPEN: allow exactly one probe call. Success -> CLOSED. Failure -> OPEN.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self) -> None:
        self.state = self.CLOSED
        self.failure_count = 0
        self.opened_at: float | None = None

    def reset(self) -> None:
        self.state = self.CLOSED
        self.failure_count = 0
        self.opened_at = None

    def check(self, *, threshold: int, reset_timeout: float) -> None:
        """Raise if breaker is OPEN and reset window has not elapsed."""
        if self.state == self.OPEN:
            assert self.opened_at is not None
            elapsed = time.monotonic() - self.opened_at
            if elapsed >= reset_timeout:
                self.state = self.HALF_OPEN
                _embed_log(
                    f"[embed-breaker] OPEN -> HALF_OPEN (reset={reset_timeout:.0f}s elapsed); allowing one probe"
                )
                return
            raise EmbeddingError(
                f"Embedding circuit breaker OPEN ({self.failure_count} consecutive transient failures); "
                f"retry in {reset_timeout - elapsed:.0f}s",
                kind="transient",
            )

    def record_success(self) -> None:
        if self.state == self.HALF_OPEN:
            _embed_log("[embed-breaker] HALF_OPEN -> CLOSED (probe succeeded)")
        self.state = self.CLOSED
        self.failure_count = 0
        self.opened_at = None

    def record_failure(self, err: EmbeddingError, *, threshold: int) -> None:
        if err.kind != "transient":
            # Permanent / unknown failures don't trip the breaker — they fail
            # fast on the caller side and won't repeat from a network blip.
            return
        if self.state == self.HALF_OPEN:
            self.state = self.OPEN
            self.opened_at = time.monotonic()
            _embed_log("[embed-breaker] HALF_OPEN -> OPEN (probe failed)")
            return
        self.failure_count += 1
        if self.failure_count >= threshold and self.state != self.OPEN:
            self.state = self.OPEN
            self.opened_at = time.monotonic()
            _embed_log(
                f"[embed-breaker] CLOSED -> OPEN ({self.failure_count} consecutive transient failures)"
            )


# Single in-process breaker. Tests can call _EMBED_BREAKER.reset().
_EMBED_BREAKER = _EmbeddingBreaker()


def _embed_log(msg: str) -> None:
    """stderr log helper, never logs api_key (callers must not pass it in)."""
    try:
        sys.stderr.write(msg.rstrip() + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _embed_sleep(seconds: float) -> None:
    """Indirection so tests can monkey-patch this to a no-op."""
    if seconds > 0:
        time.sleep(seconds)


def _embed_backoff_delay(attempt: int, *, base_delay: float, max_delay: float, jitter: bool) -> float:
    """Exponential backoff with optional jitter (OpenViking compute_delay)."""
    import random
    delay = min(base_delay * (2 ** attempt), max_delay)
    if jitter:
        delay += random.uniform(0.0, min(base_delay, delay))
    return delay


def embedding_config() -> dict[str, Any]:
    base = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
    model = os.environ.get("EMBEDDING_MODEL", "")
    api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not base or not model or not api_key:
        raise RuntimeError(
            "Embedding not configured: set EMBEDDING_BASE_URL, EMBEDDING_MODEL, "
            "and EMBEDDING_API_KEY (or OPENAI_API_KEY). "
            "Reference runtime/openmemory/.env for the live values."
        )
    dim_raw = os.environ.get("EMBEDDING_DIMENSIONS", "").strip()
    dim = int(dim_raw) if dim_raw else None
    return {"base": base, "model": model, "api_key": api_key, "dim": dim}


def _embed_batch_once(
    batch: list[str],
    cfg: dict[str, Any],
    effective_timeout: int,
) -> list[list[float]]:
    """Single embedding HTTP call. Raises EmbeddingError on any failure.

    Caller (`_embed_batch`) is responsible for retry / breaker / classification
    re-routing. This function does no retry of its own.
    """
    body: dict[str, Any] = {
        "model": cfg["model"],
        "input": batch,
        "encoding_format": "float",
    }
    if cfg["dim"]:
        body["dimensions"] = cfg["dim"]
    payload = json.dumps(body).encode("utf-8")
    check_transport_security(cfg["base"])
    req = urllib.request.Request(
        f"{cfg['base']}/embeddings",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=effective_timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")[:300]
        kind = _classify_embedding_failure(e.code, body_err)
        raise EmbeddingError(
            f"Embedding HTTP {e.code}: {body_err}",
            kind=kind,
            status=e.code,
        )
    except urllib.error.URLError as e:
        reason = e.reason
        msg = str(reason)
        if isinstance(reason, socket.timeout) or "timed out" in msg.lower():
            raise EmbeddingError(
                f"Embedding timeout: {reason} (limit={effective_timeout}s)",
                kind="transient",
            )
        kind = _classify_embedding_failure(None, msg)
        # URLError without explicit timeout but with "connection reset/refused"
        # falls through classify; default unknown -> treat as transient because
        # network-layer URLError is almost always a retryable blip.
        if kind == "unknown":
            kind = "transient"
        raise EmbeddingError(f"Embedding unreachable: {reason}", kind=kind)
    except (TimeoutError, socket.timeout) as e:
        raise EmbeddingError(
            f"Embedding timeout: {e} (limit={effective_timeout}s)",
            kind="transient",
        ) from e
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise EmbeddingError(f"Embedding response not JSON: {e}", kind="unknown")
    data = d.get("data") or []
    if len(data) != len(batch):
        # Shape mismatch is a server-side or model-side problem, not a network
        # blip. Mark permanent so retry doesn't loop on the same broken call.
        raise EmbeddingError(
            f"Embedding shape mismatch: expected {len(batch)} vectors, got {len(data)}",
            kind="permanent",
        )
    return [item["embedding"] for item in data]


def _embed_batch(
    batch: list[str],
    cfg: dict[str, Any],
    effective_timeout: int,
) -> list[list[float]]:
    """Wrap `_embed_batch_once` with retry + circuit breaker.

    Behavior:
    - Permanent errors (400/401/403/404/422, "model not found", shape mismatch)
      raise immediately without retry.
    - Transient errors (408/429/5xx, timeout, connection reset) retry with
      exponential backoff + jitter, up to `EMBEDDING_MAX_RETRIES` times.
    - Unknown errors raise immediately (fail-fast, matches OpenViking default).
    - Consecutive transient failures (across calls) trip a circuit breaker that
      fast-fails subsequent calls until `EMBEDDING_BREAKER_RESET` elapses.

    Tunables: EMBEDDING_MAX_RETRIES, EMBEDDING_BASE_DELAY, EMBEDDING_MAX_DELAY,
    EMBEDDING_RETRY_JITTER, EMBEDDING_BREAKER_THRESHOLD, EMBEDDING_BREAKER_RESET.
    Local Qwen path (no transient errors) is unaffected: first attempt succeeds,
    no sleep, no breaker.
    """
    rcfg = _embed_retry_config()
    _EMBED_BREAKER.check(
        threshold=rcfg["breaker_threshold"],
        reset_timeout=rcfg["breaker_reset"],
    )

    attempt = 0
    while True:
        try:
            result = _embed_batch_once(batch, cfg, effective_timeout)
        except EmbeddingError as err:
            if err.kind != "transient":
                # Permanent / unknown — record breaker (no-op for non-transient)
                # and re-raise immediately.
                _EMBED_BREAKER.record_failure(err, threshold=rcfg["breaker_threshold"])
                raise
            # Transient: maybe retry.
            if attempt >= rcfg["max_retries"]:
                _EMBED_BREAKER.record_failure(err, threshold=rcfg["breaker_threshold"])
                raise
            delay = _embed_backoff_delay(
                attempt,
                base_delay=rcfg["base_delay"],
                max_delay=rcfg["max_delay"],
                jitter=rcfg["jitter"],
            )
            status_str = f"status={err.status}" if err.status else "status=-"
            _embed_log(
                f"[embed-retry] transient {status_str} attempt {attempt + 1}/{rcfg['max_retries']}; "
                f"sleeping {delay:.2f}s"
            )
            _embed_sleep(delay)
            attempt += 1
            continue
        # Success path.
        _EMBED_BREAKER.record_success()
        return result


def embed_texts(
    texts: list[str],
    *,
    timeout: int | None = None,
    batch_size: int | None = None,
) -> list[list[float]]:
    """POST /embeddings (OpenAI-compatible). Returns [vec, ...] aligned with input.

    Splits into batches of `batch_size` (default EMBEDDING_BATCH_SIZE=10) because
    ARK caps `input` to 10 per request. Local vLLM backends tolerate larger
    batches; callers on those backends can pass a higher cap.

    Raises RuntimeError on config error, HTTP error, timeout, or shape mismatch.
    Callers must catch and degrade.
    """
    if not texts:
        return []
    cfg = embedding_config()
    effective_timeout = timeout if timeout is not None else EMBEDDING_TIMEOUT
    effective_batch = batch_size if batch_size is not None else EMBEDDING_BATCH_SIZE
    vectors: list[list[float]] = []
    for start in range(0, len(texts), effective_batch):
        vectors.extend(
            _embed_batch(texts[start:start + effective_batch], cfg, effective_timeout)
        )
    return vectors


def embed_one(text: str, *, timeout: int | None = None) -> list[float]:
    """Single-text convenience wrapper around `embed_texts`."""
    return embed_texts([text], timeout=timeout)[0]


# ---------- Wiki search (file-based) ----------

_SEARCH_ROOTS = {
    "wiki": ("wiki", "AGENTS.md"),
    "sources": ("sources",),
    "inbox": ("inbox",),
}
_SEARCH_EXTS = {".md", ".txt"}
_SEARCH_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SEARCH_ALIASES_INLINE_RE = re.compile(r'^aliases:\s*\[(.+?)\]\s*$', re.MULTILINE)
_SEARCH_ALIAS_ITEM_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
_SEARCH_SCALAR_RE_TEMPLATE = r"^{field}:\s*(.+?)\s*$"
_SEARCH_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SEARCH_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SEARCH_AGGREGATE_REPORT_PATHS = {
    "wiki/systems/memory-retrieval-eval.md",
}
_SEARCH_CJK_STOP_TERMS = {
    "是谁",
    "是什么",
    "什么",
    "如何",
    "怎么",
    "怎么办",
    "一下",
    "帮我",
    "请问",
    "查询",
    "搜索",
    "看看",
    "关于",
    "是否",
    "需要",
    "方法",
    "步骤",
    "能力",
    "这样做",
    "的",
    "吗",
    "呢",
}
_SEARCH_CJK_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("提交", ("提交", "commit")),
    ("推送", ("推送", "push")),
    ("钩子", ("钩子", "hook")),
    ("绕过", ("绕过", "bypass", "no-verify", "no verify")),
    ("记忆库", ("记忆库", "mem0", "openmemory")),
    ("备份", ("备份", "backup")),
    ("策略", ("策略", "retention", "恢复")),
    ("虎哥", ("虎哥", "tiger", "giant")),
    ("个人资料", ("个人资料", "profile", "person")),
    ("豆豆", ("豆豆", "doodiu")),
    ("品牌", ("品牌", "brand")),
    ("定位", ("定位", "positioning")),
    ("编码", ("编码", "coding")),
    ("极简", ("极简", "simplicity", "最小")),
    ("原则", ("原则", "principles")),
    ("变基", ("变基", "rebase")),
    ("冲突", ("冲突", "conflict")),
    ("流式", ("流式", "streaming", "stream")),
    ("语音", ("语音", "voice")),
    ("图像", ("图像", "image")),
    ("识别", ("识别", "recognition", "analyze", "describe")),
    ("插件", ("插件", "plugin")),
    ("发布", ("发布", "publish", "publishing")),
    ("上线", ("上线", "publish", "publishing")),
    ("调试", ("调试", "debug")),
    ("安装", ("安装", "install", "installation")),
    ("总结", ("总结", "summarization", "summary", "summarize")),
    ("集成", ("集成", "integration", "integrate")),
    ("供应链", ("供应链", "production", "supplier")),
    ("工厂", ("工厂", "factory")),
    ("列表", ("列表", "index")),
)


def _search_bridge_groups_from_cjk_token(token: str, *, max_terms_per_group: int = 8) -> list[list[str]]:
    groups: list[list[str]] = []

    def clean_term(term: str) -> str:
        value = term.strip().lower()
        if not value or value in _SEARCH_CJK_STOP_TERMS:
            return ""
        if _LOCAL_CJK_RUN_RE.fullmatch(value) and len(value) < 2:
            return ""
        return value

    def group_from(candidates: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            value = clean_term(candidate)
            if not value or value in seen:
                continue
            out.append(value)
            seen.add(value)
            if len(out) >= max_terms_per_group:
                break
        return out

    stop_terms = sorted(_SEARCH_CJK_STOP_TERMS | _LOCAL_CJK_STOP_TERMS, key=len, reverse=True)
    for run in _LOCAL_CJK_RUN_RE.findall(token):
        cleaned = run
        for stop in stop_terms:
            cleaned = cleaned.replace(stop, "")
        cleaned = clean_term(cleaned)
        if not cleaned:
            continue
        if len(cleaned) <= 4:
            candidates = [cleaned]
            for width in (4, 3, 2):
                if len(cleaned) < width:
                    continue
                for idx in range(0, len(cleaned) - width + 1):
                    candidates.append(cleaned[idx : idx + width])
            group = group_from(candidates)
            if group:
                groups.append(group)
            continue
        leading = group_from([cleaned, cleaned[:4], cleaned[:3], cleaned[:2]])
        trailing = group_from([cleaned, cleaned[-4:], cleaned[-3:], cleaned[-2:]])
        if leading:
            groups.append(leading)
        if trailing and trailing != leading:
            groups.append(trailing)
    return groups


def search_query_term_groups(query: str) -> list[list[str]]:
    """Return AND groups with OR alternatives for lightweight lexical recall.

    The wiki search remains deterministic lexical search. This helper only
    expands compact CJK task phrases into stable domain terms and English
    aliases, so pages do not need to copy full eval/user queries into aliases.
    """
    groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in re.split(r"\s+", (query or "").strip().lower()):
        token = raw.strip().strip("，。！？；：,.!?;:()[]{}<>《》“”\"'")
        if not token:
            continue
        token_groups: list[list[str]] = []
        latin_terms = _LOCAL_LATIN_TERM_RE.findall(token)
        for latin in latin_terms:
            token_groups.append([latin.lower()])
        if _SEARCH_CJK_RE.search(token):
            cjk_synonym_count = 0
            for cjk_term, alternatives in _SEARCH_CJK_SYNONYMS:
                if cjk_term in token:
                    token_groups.append([alt.lower() for alt in alternatives if alt])
                    cjk_synonym_count += 1
            for stop_term in sorted(_SEARCH_CJK_STOP_TERMS, key=len, reverse=True):
                token = token.replace(stop_term, "")
            if cjk_synonym_count == 0:
                token_groups.extend(_search_bridge_groups_from_cjk_token(token))
        if not token_groups and token:
            token_groups.append([token])
        for group in token_groups:
            key = tuple(dict.fromkeys(item for item in group if item))
            if key and key not in seen:
                groups.append(list(key))
                seen.add(key)
    return groups


def flatten_search_query_terms(term_groups: list[list[str]]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for group in term_groups:
        for item in group:
            if item and item not in seen:
                terms.append(item)
                seen.add(item)
    return terms


def _score_file_for_query(text_lower: str, term_groups: list[list[str]]) -> int:
    """Return occurrence score. 0 if any required term group is missing."""
    total = 0
    for group in term_groups:
        group_score = sum(text_lower.count(term) for term in group if term)
        if group_score == 0:
            return 0
        total += group_score
    return total


def _slug_search_text(rel: str) -> str:
    """Expand path separators so slug words can satisfy token search."""
    return re.sub(r"[^a-z0-9_]+", " ", rel.lower())


def _extract_search_frontmatter(text: str) -> str:
    fm = _SEARCH_FRONTMATTER_RE.match(text)
    if not fm:
        return ""
    return fm.group(1)


def _dedupe_search_strings(items: list[str], *, limit: int = 16) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = re.sub(r"\s+", " ", str(item or "").strip())
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        out.append(value[:200])
        seen.add(key)
        if len(out) >= limit:
            break
    return out


def _parse_inline_search_list(value: str) -> list[str]:
    raw = str(value or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    quoted = [match.group(1) or match.group(2) or "" for match in _SEARCH_ALIAS_ITEM_RE.finditer(raw)]
    if quoted:
        return _dedupe_search_strings(quoted)
    return _dedupe_search_strings([part.strip() for part in raw.split(",")])


def _extract_frontmatter_list(fm: str, field: str, *, limit: int = 16) -> list[str]:
    """Extract simple YAML-ish scalar, inline list, or block-list values."""
    if not fm:
        return []
    lines = fm.splitlines()
    items: list[str] = []
    field_prefix = f"{field}:"
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(field_prefix):
            continue
        value = stripped[len(field_prefix):].strip()
        if value:
            items.extend(_parse_inline_search_list(value))
            break
        for child in lines[idx + 1:]:
            if child and not child.startswith((" ", "\t", "-")):
                break
            child_value = child.strip()
            if child_value.startswith("-"):
                items.extend(_parse_inline_search_list(child_value[1:].strip()))
        break
    return _dedupe_search_strings(items, limit=limit)


def _extract_frontmatter_scalar(fm: str, field: str, *, limit: int = 260) -> str:
    if not fm:
        return ""
    pattern = re.compile(_SEARCH_SCALAR_RE_TEMPLATE.format(field=re.escape(field)), re.MULTILINE)
    match = pattern.search(fm)
    if not match:
        return ""
    value = match.group(1).strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", value)[:limit]


def _extract_markdown_section(text: str, heading_names: tuple[str, ...], *, limit: int = 500) -> str:
    matches = list(_SEARCH_HEADING_RE.finditer(text))
    wanted = {name.lower() for name in heading_names}
    for idx, match in enumerate(matches):
        heading = match.group(1).strip().lower()
        if heading not in wanted:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = re.sub(r"\s+", " ", text[start:end].strip())
        return body[:limit]
    return ""


def _extract_search_metadata(text: str) -> dict[str, Any]:
    """Extract public Wiki metadata used by lexical search and answer evidence."""
    fm = _extract_search_frontmatter(text)
    aliases = _extract_frontmatter_list(fm, "aliases")
    tags = _extract_frontmatter_list(fm, "tags", limit=12)
    key_facts = _extract_frontmatter_list(fm, "key_facts", limit=8)
    summary = _extract_frontmatter_scalar(fm, "summary")
    if not summary:
        summary = _extract_markdown_section(text, ("摘要", "summary"), limit=320)
    if not key_facts:
        facts_text = _extract_markdown_section(text, ("关键事实", "key facts", "facts"), limit=500)
        key_facts = _dedupe_search_strings(
            [re.sub(r"^\s*[-*]\s*", "", line).strip() for line in facts_text.split("。")],
            limit=6,
        )
    return {
        "aliases": aliases,
        "tags": tags,
        "summary": summary,
        "key_facts": key_facts,
    }


def _extract_search_aliases(text: str) -> list[str]:
    """Extract frontmatter aliases for lexical ranking."""
    return list(_extract_search_metadata(text).get("aliases") or [])


def primary_search_scope(query: str) -> str:
    """Return the primary grouped-search scope for a free-form query."""
    q = query.lower()
    onboarding_triggers = (
        "git pull", "ff-only", "preflight", "tm_lessons.py", "top-3",
        "selfevolution", "write_memory", "write_inbox", "routed_by",
    )
    lesson_triggers = (
        "commit push", "worktree", "writefile", "no verify", "no-verify",
        "powershell", "mojibake", "gbk", "hook reject", "llm gate", "bypass",
    )
    mem0_wiki_triggers = ("promotion", "lifecycle", "duplicate", "compilation", "wiki")
    if any(trigger in q for trigger in lesson_triggers):
        return "lessons"
    if ("提交" in q and "推送" in q) or "绕过钩子" in q or "绕过 hook" in q:
        return "lessons"
    if any(trigger in q for trigger in onboarding_triggers):
        return "onboarding"
    if "mem0" in q and any(trigger in q for trigger in mem0_wiki_triggers):
        return "wiki"
    if "mem0" in q:
        return "mem0"
    return "wiki"


_GENERIC_SIGNAL_TOKENS = {
    "a", "an", "and", "answer", "api", "case", "how", "memory", "policy",
    "query", "search", "the", "tigermemory", "what", "why",
    "怎么", "如何", "什么", "记忆", "检索", "接口",
}


def signal_tokens(query: str) -> list[str]:
    """Return non-generic query tokens useful for relevance expansion."""
    tokens: list[str] = []
    for token in re.split(r"[\s,，。;；:：/\\|()\[\]{}\"'`]+", str(query or "").strip()):
        clean = token.strip().lower()
        if not clean or clean in _GENERIC_SIGNAL_TOKENS:
            continue
        if len(clean) < 2 and not re.search(r"[\u4e00-\u9fff]", clean):
            continue
        tokens.append(clean)
    return tokens


def _group_in_text(group: list[str], text: str) -> bool:
    return any(term in text for term in group if term)


def _group_in_slug(group: list[str], slug_words: set[str]) -> bool:
    return any(term in slug_words for term in group if term)


def _is_person_profile_query(term_groups: list[list[str]]) -> bool:
    has_person = any(
        "个人资料" in group or "profile" in group or "person" in group
        for group in term_groups
    )
    has_tiger = any(
        "虎哥" in group or "tiger" in group or "giant" in group
        for group in term_groups
    )
    return has_person and has_tiger


def _rank_search_hit(
    raw_score: int,
    rel: str,
    title: str,
    term_groups: list[list[str]],
    aliases: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> float:
    """Rank exact pages above aggregate pages without changing the AND contract."""
    score = float(raw_score)
    title_lower = title.lower()
    slug_words = set(_slug_search_text(rel).split())
    alias_text = " ".join(aliases or []).lower()
    meta = metadata or {}
    tag_text = " ".join(meta.get("tags") or []).lower()
    summary_text = str(meta.get("summary") or "").lower()
    facts_text = " ".join(meta.get("key_facts") or []).lower()
    query_phrase = " ".join(group[0] for group in term_groups if group and group[0]).strip()

    # Slug/title/alias hits are strong signals for canonical pages. Body-only
    # counts otherwise let indexes and overview pages dominate through repeats.
    for group in term_groups:
        if _group_in_slug(group, slug_words):
            score += 30
        if _group_in_text(group, title_lower):
            score += 15
        if _group_in_text(group, alias_text):
            score += 20
        if _group_in_text(group, tag_text):
            score += 18
        if _group_in_text(group, summary_text):
            score += 12
        if _group_in_text(group, facts_text):
            score += 16
        if any(_SEARCH_CJK_RE.search(term) and len(term) >= 4 and term in summary_text for term in group):
            score += 55
        if any(_SEARCH_CJK_RE.search(term) and len(term) >= 4 and term in facts_text for term in group):
            score += 60
    if title_lower and all(_group_in_text(group, title_lower) for group in term_groups):
        score += 80
        title_token_count = len([token for token in re.split(r"\W+", title_lower) if token])
        if title_token_count <= len(term_groups) + 2:
            score += 120
    if alias_text and all(_group_in_text(group, alias_text) for group in term_groups):
        score += 40
    if tag_text and all(_group_in_text(group, tag_text) for group in term_groups):
        score += 35
    if summary_text and all(_group_in_text(group, summary_text) for group in term_groups):
        score += 30
    if facts_text and all(_group_in_text(group, facts_text) for group in term_groups):
        score += 35
    if query_phrase:
        if query_phrase in title_lower:
            score += 120
        if query_phrase in alias_text:
            score += 120
    if _is_person_profile_query(term_groups):
        if rel.startswith("wiki/person/") and not rel.endswith("/index.md"):
            score += 1000
        else:
            score *= 0.02

    if rel in _SEARCH_AGGREGATE_REPORT_PATHS:
        score *= 0.05
    if rel.endswith("/index.md"):
        score *= 0.2
    if rel.startswith("wiki/operations/") and "dashboard" in rel:
        score *= 0.2
    return score


def _best_snippet(text: str, tokens: list[str], width: int = 200) -> str:
    """Return a ~width-char window around the earliest token hit."""
    lower = text.lower()
    hits = [lower.find(t) for t in tokens if lower.find(t) >= 0]
    if not hits:
        return text[:width].strip()
    start = max(0, min(hits) - 40)
    end = min(len(text), start + width)
    snippet = text[start:end].replace("\n", " ").strip()
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


def search_wiki(
    query: str,
    size: int = 5,
    include_sources: bool = True,
    include_inbox: bool = False,
    *,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """File-based search over wiki/ (+ optional sources/, inbox/) markdown/text.

    Complements mem0_search: Mem0 stores atomic event-style memories; wiki
    stores long-form knowledge (brand guides, IPFB copy history, system docs,
    person profile). When an agent asks "what did we write/decide about X",
    both stores should be consulted.

    Args:
        query: Whitespace-separated tokens. CJK handled as substring.
        size: Max results to return.
        include_sources: Also scan sources/ (IPFB copy history, brand docs).
        include_inbox: Also scan inbox/ (raw unreviewed drafts; default off).

    Returns:
        List of {path, score, title, snippet}, sorted by score desc.
    """
    q = (query or "").strip()
    if not q:
        return []
    term_groups = search_query_term_groups(q)
    if not term_groups:
        return []
    snippet_terms = flatten_search_query_terms(term_groups)

    roots: list[str] = list(_SEARCH_ROOTS["wiki"])
    if include_sources:
        roots.extend(_SEARCH_ROOTS["sources"])
    if include_inbox:
        roots.extend(_SEARCH_ROOTS["inbox"])

    results: list[dict[str, Any]] = []
    for root in roots:
        root_path = REPO_ROOT / root
        if not root_path.exists():
            continue
        paths = [root_path] if root_path.is_file() else root_path.rglob("*")
        for p in paths:
            if not p.is_file() or p.suffix.lower() not in _SEARCH_EXTS:
                continue
            rel = p.relative_to(REPO_ROOT).as_posix()
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Title: first markdown H1 or first non-empty line stripped.
            title = ""
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("# "):
                    title = s[2:].strip()
                    break
                if s and not s.startswith("---") and ":" not in s[:20]:
                    title = s[:80]
                    break
            metadata = _extract_search_metadata(text)
            aliases = list(metadata.get("aliases") or [])
            metadata_text = " ".join([
                " ".join(aliases),
                " ".join(metadata.get("tags") or []),
                str(metadata.get("summary") or ""),
                " ".join(metadata.get("key_facts") or []),
            ])
            searchable = f"{rel} {_slug_search_text(rel)} {title} {metadata_text}\n{text}"
            raw_score = _score_file_for_query(searchable.lower(), term_groups)
            if raw_score == 0:
                continue
            score = _rank_search_hit(raw_score, rel, title, term_groups, aliases, metadata)
            hit = {
                "path": rel,
                "score": score,
                "title": title,
                "snippet": _best_snippet(text, snippet_terms),
            }
            if aliases:
                hit["aliases"] = aliases
            if metadata.get("tags"):
                hit["tags"] = metadata["tags"]
            if metadata.get("summary"):
                hit["summary"] = metadata["summary"]
            if metadata.get("key_facts"):
                hit["key_facts"] = metadata["key_facts"]
            if explain:
                alias_text = " ".join(aliases).lower()
                tag_text = " ".join(metadata.get("tags") or []).lower()
                summary_text = str(metadata.get("summary") or "").lower()
                facts_text = " ".join(metadata.get("key_facts") or []).lower()
                hit["score_breakdown"] = {
                    "lexical_score": score,
                    "lexical_rank": None,
                    "vector_score": None,
                    "vector_rank": None,
                    "alias_match": bool(
                        alias_text
                        and any(_group_in_text(group, alias_text) for group in term_groups)
                    ),
                    "tag_match": bool(
                        tag_text
                        and any(_group_in_text(group, tag_text) for group in term_groups)
                    ),
                    "summary_match": bool(
                        summary_text
                        and any(_group_in_text(group, summary_text) for group in term_groups)
                    ),
                    "key_fact_match": bool(
                        facts_text
                        and any(_group_in_text(group, facts_text) for group in term_groups)
                    ),
                    "rrf_score": None,
                    "lexical_anchor": False,
                    "final_score": score,
                }
            results.append(hit)

    results.sort(key=lambda r: (-r["score"], r["path"]))
    limited = results[:max(1, size)]
    if explain:
        for rank, hit in enumerate(limited, 1):
            breakdown = hit.get("score_breakdown")
            if isinstance(breakdown, dict):
                breakdown["lexical_rank"] = rank
    return limited


# ---------- Hybrid lexical + embedding recall ----------

# Standard RRF constant (Cormack et al., 2009). k=10..100 all behave similarly;
# 60 is the canonical default and what most production hybrid retrievers use.
_RRF_K = 60
_HYBRID_MAP_ARM_ENV = "TM_HYBRID_MAP_ARM"
_HYBRID_MAP_MIN_SCORE = 24.0
_HYBRID_LEXICAL_ANCHOR_COUNT = 2
_HYBRID_LEXICAL_ANCHOR_MIN_SCORE = 100.0
_HYBRID_LEXICAL_FIRST_MIN_SCORE = 150.0
_HYBRID_LEXICAL_FIRST_RATIO = 1.25
_HYBRID_LEXICAL_FIRST_SKIP_SUFFIXES = (
    "wiki/systems/memory-retrieval-eval.md",
)


def _skip_hybrid_lexical_first(path: str) -> bool:
    return path.endswith("/index.md") or path in _HYBRID_LEXICAL_FIRST_SKIP_SUFFIXES


_HYBRID_MAP_RECORDS_CACHE: list[dict[str, Any]] | None = None


def _hybrid_map_arm_enabled() -> bool:
    return str(os.environ.get(_HYBRID_MAP_ARM_ENV) or "").strip().lower() in {
        "1",
        "true",
        "on",
        "enabled",
        "yes",
        "force",
    }


def _hybrid_map_stub(path: str, title: str) -> dict[str, Any]:
    snippet = ""
    try:
        body = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        snippet = " ".join(body[:600].split())[:300]
    except OSError:
        pass
    return {
        "path": path,
        "title": title,
        "score": 0.0,
        "snippet": snippet,
    }


def _hybrid_map_recall(query: str, *, limit: int) -> list[dict[str, Any]]:
    global _HYBRID_MAP_RECORDS_CACHE
    tools_dir = str(REPO_ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import tm_llm_wiki_map  # type: ignore[import-not-found]

    if _HYBRID_MAP_RECORDS_CACHE is None:
        _HYBRID_MAP_RECORDS_CACHE = tm_llm_wiki_map.load_map()
    return [
        hit
        for hit in tm_llm_wiki_map.map_recall(query, limit=limit, records=_HYBRID_MAP_RECORDS_CACHE)
        if float(hit.get("score") or 0.0) >= _HYBRID_MAP_MIN_SCORE
    ]


def search_wiki_hybrid(
    query: str,
    size: int = 5,
    include_sources: bool = True,
    include_inbox: bool = False,
    *,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """Reciprocal-rank-fusion of lexical, embedding, and optional wiki-map recall.

    Why this exists: Phase 2e (2026-05-06) showed lexical AND search returns
    0 candidates for many CN / synonym / cross-lingual queries; Phase 2f
    showed RRF-fusing lexical + embedding lifts hit@3 from 50/81 (61.7%) to
    71/81 (87.6%) on the canonical eval set, while keeping lexical's strength
    on exact-token English canonical hits.

    Graceful degradation: if the embedding index is missing or the embedding
    backend is unreachable, this falls back to lexical-only — callers never
    see a hard failure from this path.

    `include_inbox=True` only feeds inbox into the lexical branch; the
    embedding index intentionally does not cover inbox/ (those are unreviewed
    drafts).
    """
    pool_k = max(size * 4, 12)
    lex_hits = search_wiki(
        query,
        size=pool_k,
        include_sources=include_sources,
        include_inbox=include_inbox,
        explain=explain,
    )

    def degraded_lexical_hits() -> list[dict[str, Any]]:
        hits = [dict(hit) for hit in lex_hits[:max(1, size)]]
        if explain:
            for rank, hit in enumerate(hits, 1):
                breakdown = dict(hit.get("score_breakdown") or {})
                breakdown.update({
                    "lexical_rank": rank,
                    "vector_score": None,
                    "vector_rank": None,
                    "rrf_score": None,
                    "lexical_anchor": False,
                    "degraded": True,
                    "final_score": hit.get("score"),
                })
                hit["score_breakdown"] = breakdown
        return hits

    emb_hits: list[dict[str, Any]] = []
    emb_degraded = False
    try:
        # Lazy import: tm_core is imported by lots of modules; tm_embed_index
        # depends on tm_core, so importing at module top would create a cycle.
        import tm_embed_index  # type: ignore[import-not-found]
        emb_hits = tm_embed_index.search(query, scope="wiki", k=pool_k)
    except RuntimeError:
        # Index empty / not built / embedding service unreachable.
        # Degrade silently to lexical-only — caller already gets useful results.
        emb_degraded = True
    except Exception:
        emb_degraded = True

    map_hits: list[dict[str, Any]] = []
    map_degraded = False
    if _hybrid_map_arm_enabled():
        try:
            map_hits = _hybrid_map_recall(query, limit=pool_k)
        except Exception:
            map_degraded = True

    if emb_degraded and not map_hits:
        return degraded_lexical_hits()

    if not include_sources:
        emb_hits = [h for h in emb_hits if not h["path"].startswith("sources/")]
        map_hits = [h for h in map_hits if not h["path"].startswith("sources/")]

    fused: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(lex_hits, 1):
        path = hit["path"]
        fused.setdefault(path, {"hit": hit, "score": 0.0})
        fused[path]["score"] += 1.0 / (_RRF_K + rank)
    for rank, hit in enumerate(emb_hits, 1):
        path = hit["path"]
        if path not in fused:
            # Emb-only hit: build the same shape lexical returns. Snippet is
            # generated cheaply from the leading body so the agent has a
            # preview without re-reading the file.
            snippet = ""
            try:
                body = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
                snippet = " ".join(body[:600].split())[:300]
            except OSError:
                pass
            stub = {
                "path": path,
                "title": hit.get("title", ""),
                "score": 0.0,
                "snippet": snippet,
            }
            fused[path] = {"hit": stub, "score": 0.0}
        fused[path]["score"] += 1.0 / (_RRF_K + rank)
    for rank, hit in enumerate(map_hits, 1):
        path = str(hit.get("path") or "")
        if not path:
            continue
        if path not in fused:
            fused[path] = {"hit": _hybrid_map_stub(path, str(hit.get("title") or "")), "score": 0.0}
        fused[path]["score"] += 1.0 / (_RRF_K + rank)

    ordered = sorted(fused.values(), key=lambda v: -v["score"])
    lex_score_by_path: dict[str, float] = {}
    lex_rank_by_path: dict[str, int] = {}
    lex_alias_match_by_path: dict[str, bool] = {}
    lexical_first_candidate: tuple[str, float] | None = None
    anchor_paths: list[str] = []
    for rank, hit in enumerate(lex_hits, 1):
        try:
            lex_score = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            lex_score = 0.0
        path = str(hit.get("path") or "")
        if not path:
            continue
        lex_score_by_path[path] = lex_score
        lex_rank_by_path[path] = rank
        breakdown = hit.get("score_breakdown")
        if isinstance(breakdown, dict):
            lex_alias_match_by_path[path] = bool(breakdown.get("alias_match"))
        if (
            lexical_first_candidate is None
            and lex_score >= _HYBRID_LEXICAL_FIRST_MIN_SCORE
            and not _skip_hybrid_lexical_first(path)
        ):
            lexical_first_candidate = (path, lex_score)
        if (
            len(anchor_paths) < _HYBRID_LEXICAL_ANCHOR_COUNT
            and lex_score >= _HYBRID_LEXICAL_ANCHOR_MIN_SCORE
            and path not in anchor_paths
        ):
            anchor_paths.append(path)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, size)
    emb_score_by_path: dict[str, float] = {}
    emb_rank_by_path: dict[str, int] = {}
    for rank, hit in enumerate(emb_hits, 1):
        path = str(hit.get("path") or "")
        if not path:
            continue
        emb_rank_by_path[path] = rank
        try:
            emb_score_by_path[path] = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            emb_score_by_path[path] = 0.0
    map_score_by_path: dict[str, float] = {}
    map_rank_by_path: dict[str, int] = {}
    for rank, hit in enumerate(map_hits, 1):
        path = str(hit.get("path") or "")
        if not path:
            continue
        map_rank_by_path[path] = rank
        try:
            map_score_by_path[path] = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            map_score_by_path[path] = 0.0

    def add_entry(entry: dict[str, Any]) -> None:
        if len(out) >= limit:
            return
        merged = dict(entry["hit"])
        merged["score"] = round(entry["score"], 6)
        path = str(merged.get("path") or "")
        if not path or path in seen:
            return
        if explain:
            merged["score_breakdown"] = {
                "lexical_score": lex_score_by_path.get(path),
                "lexical_rank": lex_rank_by_path.get(path),
                "vector_score": emb_score_by_path.get(path),
                "vector_rank": emb_rank_by_path.get(path),
                "map_score": map_score_by_path.get(path),
                "map_rank": map_rank_by_path.get(path),
                "alias_match": lex_alias_match_by_path.get(path, False),
                "rrf_score": merged["score"],
                "lexical_anchor": path in anchor_paths or (
                    lexical_first_candidate is not None
                    and path == lexical_first_candidate[0]
                ),
                "degraded": bool(emb_degraded or map_degraded),
                "final_score": merged["score"],
            }
        seen.add(path)
        out.append(merged)

    promoted_first = False
    if ordered and lexical_first_candidate:
        top_path = str(ordered[0]["hit"].get("path") or "")
        top_lex_score = lex_score_by_path.get(top_path, 0.0)
        candidate_path, candidate_score = lexical_first_candidate
        if candidate_path != top_path and candidate_score >= max(
            _HYBRID_LEXICAL_FIRST_MIN_SCORE,
            top_lex_score * _HYBRID_LEXICAL_FIRST_RATIO,
        ):
            entry = fused.get(candidate_path)
            if entry:
                add_entry(entry)
                promoted_first = True
    if ordered and not promoted_first:
        add_entry(ordered[0])
    for path in anchor_paths:
        entry = fused.get(path)
        if entry:
            add_entry(entry)
    for entry in ordered[1:]:
        if len(out) >= limit:
            break
        add_entry(entry)
    return out


# ---------- Public evidence-grounded answer ----------

PUBLIC_ASK_PROMPT = """你是 TigerMemory 的公开版 Wiki Admin 问答助手。

任务：只根据用户给出的 evidence 回答问题，并附来源。

硬边界：
1. 只能使用 evidence 中的信息；不要使用常识补全、不要猜测。
2. 如果证据不足，answer 要明确说“当前证据不足”，insufficient_evidence=true。
3. 每个关键结论都要引用 citation id，例如 W1、M1。
4. 不要输出 markdown 代码块，不要输出思考过程，只输出 JSON 对象。

输出 JSON：
{
  "answer": "面向普通用户的简洁中文回答，包含引用标记",
  "claims": [{"text": "结论", "citation_ids": ["W1"]}],
  "citations": [{"id": "W1", "reason": "为什么引用它"}],
  "confidence": 0-100,
  "insufficient_evidence": false
}
"""


def _public_evidence_label(item: dict[str, Any], index_by_source: dict[str, int]) -> str:
    source = str(item.get("source") or "evidence").lower()
    prefix = "W" if source == "wiki" else "M" if source == "memory" else "E"
    index_by_source[prefix] = index_by_source.get(prefix, 0) + 1
    return f"{prefix}{index_by_source[prefix]}"


def _public_evidence_text(item: dict[str, Any], label: str) -> str:
    source = str(item.get("source") or "evidence")
    lines = [f"[{label}] source={source}"]
    if item.get("path"):
        lines.append(f"path={item.get('path')}")
    if item.get("id"):
        lines.append(f"id={item.get('id')}")
    if item.get("title"):
        lines.append(f"title={item.get('title')}")
    if item.get("topic"):
        lines.append(f"topic={item.get('topic')}")
    if item.get("tags"):
        lines.append(f"tags={', '.join(str(x) for x in item.get('tags') or [])}")
    if item.get("summary"):
        lines.append(f"summary={str(item.get('summary'))[:420]}")
    if item.get("key_facts"):
        facts = "; ".join(str(x) for x in item.get("key_facts") or [])
        lines.append(f"key_facts={facts[:600]}")
    snippet = str(item.get("snippet") or "")
    if snippet:
        lines.append(f"snippet={snippet[:700]}")
    return "\n".join(lines)


def answer_from_public_evidence(
    query: str,
    evidence: list[dict[str, Any]],
    *,
    timeout: int = 15,
    max_evidence: int = 8,
) -> dict[str, Any]:
    """Generate a source-grounded public answer from already-selected evidence."""
    q = str(query or "").strip()
    if not q:
        raise ValueError("query is required")
    selected = [item for item in evidence if isinstance(item, dict)][:max(1, max_evidence)]
    if not selected:
        return {
            "schema": "tigermemory-public-answer-v1",
            "query": q,
            "answer": "当前证据不足，未找到可以回答这个问题的本地资料。",
            "claims": [],
            "citations": [],
            "confidence": 0,
            "insufficient_evidence": True,
            "model": None,
        }

    index_by_source: dict[str, int] = {}
    labelled: list[tuple[str, dict[str, Any]]] = []
    evidence_blocks: list[str] = []
    for item in selected:
        label = _public_evidence_label(item, index_by_source)
        labelled.append((label, item))
        evidence_blocks.append(_public_evidence_text(item, label))
    allowed_ids = {label for label, _item in labelled}
    user_msg = (
        f"question:\n{q}\n\n"
        f"evidence:\n\n" + "\n\n".join(evidence_blocks) + "\n\n"
        "请输出 JSON 对象。"
    )
    model = deepseek_admin_model()
    ok, parsed = _call_deepseek_json(
        PUBLIC_ASK_PROMPT,
        user_msg,
        timeout=timeout,
        temperature=0.1,
        max_tokens=1600,
        purpose="public_ask",
        model=model,
    )
    if not ok:
        raise RuntimeError(str(parsed))
    if not isinstance(parsed, dict):
        raise RuntimeError("malformed public answer response")

    raw_claims = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
    claims: list[dict[str, Any]] = []
    cited_ids: set[str] = set()
    for claim in raw_claims:
        if not isinstance(claim, dict):
            continue
        text = re.sub(r"\s+", " ", str(claim.get("text") or "").strip())
        ids = [
            str(cid).strip()
            for cid in (claim.get("citation_ids") or [])
            if str(cid).strip() in allowed_ids
        ]
        if text:
            claims.append({"text": text[:400], "citation_ids": ids})
            cited_ids.update(ids)

    raw_citations = parsed.get("citations") if isinstance(parsed.get("citations"), list) else []
    citation_reason_by_id: dict[str, str] = {}
    for citation in raw_citations:
        if not isinstance(citation, dict):
            continue
        cid = str(citation.get("id") or "").strip()
        if cid in allowed_ids:
            citation_reason_by_id[cid] = re.sub(r"\s+", " ", str(citation.get("reason") or "").strip())[:240]
            cited_ids.add(cid)

    citations: list[dict[str, Any]] = []
    for label, item in labelled:
        if label not in cited_ids:
            continue
        citation = {"id": label, "source": item.get("source")}
        for key in ("path", "id", "title", "topic"):
            if item.get(key):
                citation[key] = item.get(key)
        if citation_reason_by_id.get(label):
            citation["reason"] = citation_reason_by_id[label]
        citations.append(citation)

    answer = re.sub(r"\s+", " ", str(parsed.get("answer") or "").strip())
    insufficient = bool(parsed.get("insufficient_evidence"))
    if not answer:
        answer = "当前证据不足，未能生成可靠回答。"
        insufficient = True
    confidence = _admin_confidence(parsed.get("confidence"))
    if not citations and not insufficient:
        insufficient = True
        confidence = min(confidence, 30)
        answer = f"{answer}（注意：模型没有给出有效来源，建议只作为草稿参考。）"

    return {
        "schema": "tigermemory-public-answer-v1",
        "query": q,
        "answer": answer,
        "claims": claims,
        "citations": citations,
        "confidence": confidence,
        "insufficient_evidence": insufficient,
        "model": model,
        "evidence_used": [label for label, _item in labelled],
    }


# ---------- Validators ----------

def validate_agent(name: str) -> None:
    if name not in AGENTS:
        raise ValueError(f"invalid agent '{name}' (allowed: {sorted(AGENTS)})")


def validate_topic(name: str) -> None:
    if name not in TOPICS:
        raise ValueError(f"invalid topic '{name}' (allowed: {sorted(TOPICS)})")


def validate_partition(name: str) -> None:
    if name not in PARTITION_OWNERS:
        raise ValueError(
            f"invalid partition '{name}' (allowed: {sorted(PARTITION_OWNERS.keys())})"
        )


def validate_action(name: str) -> None:
    if name not in ACTIONS:
        raise ValueError(f"invalid action '{name}' (allowed: {sorted(ACTIONS)})")


def validate_title(s: str) -> None:
    if not TITLE_RE.fullmatch(s):
        raise ValueError("title must be 1-80 chars: letters/digits/CJK/space/-/_")


def validate_slug(s: str) -> None:
    if not SLUG_RE.fullmatch(s):
        raise ValueError("slug must be lowercase letters/digits/hyphens")


# ---------- Content generation ----------

def inbox_rel_path(agent: str, topic: str, stamp: str | None = None) -> str:
    """Compute inbox filename. Caller checks collision."""
    if stamp is None:
        stamp = now("%Y-%m-%d-%H%M")
    return f"inbox/{stamp}-{agent}-{topic}.md"


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SUMMARY_CN_MISSING = "未提供中文摘要：请写入 agent 在正文首行补一句中文概括。"
_INBOX_REVIEW_GENERIC_CN = {"标题", "摘要", "元数据", "原文", "标签", "正文", "内容"}
_INBOX_REVIEW_META_RE = re.compile(r"^[-*]?\s*(文档时间|最近访问|链接|类型|作者|标签|来源)\s*[：:].*")


def _clean_inbox_summary(value: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.lstrip("#").strip()
    text = text.replace(":", "：")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _clean_inbox_preview(value: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.lstrip("#").strip()
    text = text.replace(":", "：")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _strip_inbox_review_label(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = text.lstrip("#").strip()
    text = re.sub(r"^Routed memory\s+\d+\s*", "", text, flags=re.I).strip()
    text = re.sub(r"^(中文标题|中文预览|中文摘要|内容摘要|原文预览|标题|摘要)\s*[：:]\s*", "", text).strip()
    text = re.sub(r"^(标题|摘要)\s+", "", text).strip()
    return text


def inbox_review_cn_is_low_quality(value: str | None) -> bool:
    text = _strip_inbox_review_label(str(value or ""))
    if not text:
        return True
    if text.startswith(_SUMMARY_CN_MISSING):
        return True
    if text in _INBOX_REVIEW_GENERIC_CN:
        return True
    if re.fullmatch(r"Routed memory\s+\d+", text, flags=re.I):
        return True
    return False


def derive_inbox_summary_cn(title: str, body: str) -> tuple[str, str]:
    """Return (summary_cn, source) for inbox review metadata.

    The desired source is the writing agent's own Chinese first line. We only
    fall back to a missing-summary marker instead of guessing a translation.
    """
    for raw in body.splitlines()[:8]:
        clean = _clean_inbox_summary(raw)
        if clean and _CJK_RE.search(clean):
            return clean, "body_first_chinese_line"
    clean_title = _clean_inbox_summary(title)
    if clean_title and _CJK_RE.search(clean_title):
        return clean_title, "title"
    return _SUMMARY_CN_MISSING, "missing"


def derive_inbox_review_cn(title: str, body: str) -> tuple[str, str, str]:
    """Return (title_cn, preview_cn, source) for inbox review UI metadata."""
    title_lines: list[str] = []
    summary_lines: list[str] = []
    in_summary = False
    for raw in body.splitlines()[:48]:
        clean = _clean_inbox_preview(raw)
        if clean and _CJK_RE.search(clean):
            clean = _strip_inbox_review_label(clean)
            if not clean:
                continue
            if clean == "摘要":
                in_summary = True
                continue
            if inbox_review_cn_is_low_quality(clean) or _INBOX_REVIEW_META_RE.match(clean):
                continue
            if in_summary:
                summary_lines.append(clean)
            else:
                title_lines.append(clean)
    candidate_lines = title_lines or summary_lines
    if candidate_lines:
        title_cn = _clean_inbox_summary(candidate_lines[0], limit=42)
        preview_source = summary_lines or candidate_lines
        preview_cn = _clean_inbox_preview(" ".join(preview_source[:4]), limit=200)
        return title_cn, preview_cn, "body_chinese_lines"
    clean_title = _clean_inbox_summary(_strip_inbox_review_label(title), limit=42)
    if clean_title and _CJK_RE.search(clean_title):
        return clean_title, clean_title, "title"
    return _SUMMARY_CN_MISSING, _SUMMARY_CN_MISSING, "missing"


def render_inbox_body(
    agent: str,
    title: str,
    body: str,
    date: str | None = None,
    frontmatter_extra: dict[str, Any] | None = None,
) -> str:
    """Render standard inbox frontmatter + body."""
    if date is None:
        date = now("%Y-%m-%d")
    extra_lines = ""
    extra: dict[str, Any] = dict(frontmatter_extra or {})
    title_cn, preview_cn, review_source = derive_inbox_review_cn(title, body)
    if inbox_review_cn_is_low_quality(extra.get("title_cn")):
        extra["title_cn"] = title_cn
        extra["review_cn_source"] = review_source
    if inbox_review_cn_is_low_quality(extra.get("preview_cn")):
        extra["preview_cn"] = preview_cn
        extra["review_cn_source"] = review_source
    extra.setdefault("review_cn_source", review_source)
    if inbox_review_cn_is_low_quality(extra.get("summary_cn")):
        if not inbox_review_cn_is_low_quality(extra.get("title_cn")):
            extra["summary_cn"] = extra["title_cn"]
            extra["summary_cn_source"] = "title_cn"
        else:
            summary_cn, source = derive_inbox_summary_cn(title, body)
            extra["summary_cn"] = summary_cn
            extra["summary_cn_source"] = source
    if extra:
        for k, v in extra.items():
            if isinstance(v, bool):
                extra_lines += f"{k}: {'true' if v else 'false'}\n"
            else:
                extra_lines += f"{k}: {v}\n"
    return (
        "---\n"
        f"owner: {agent}\n"
        "status: draft\n"
        f"updated: {date}\n"
        f"{extra_lines}"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def render_wiki_body(frontmatter: str, body: str, date: str | None = None) -> str:
    """Render wiki page. Strips any caller-supplied 'updated:' to prevent dupes."""
    if date is None:
        date = now("%Y-%m-%d")
    fm_clean = "\n".join(
        line for line in frontmatter.splitlines() if not re.match(r"^\s*updated\s*:", line)
    ).strip()
    return (
        "---\n"
        f"{fm_clean}\n"
        f"updated: {date}\n"
        "---\n\n"
        f"{body}\n"
    )


# ---------- Lint ----------

def lint_page_errors(text: str) -> list[str]:
    """Return list of lint errors for a wiki page's full text. Empty list = clean."""
    text = text.removeprefix("\ufeff")
    errors: list[str] = []

    if not text.startswith("---\n"):
        errors.append("missing frontmatter opener")
    else:
        fm_end = text.find("\n---\n", 4)
        if fm_end < 0:
            errors.append("unclosed frontmatter")
        else:
            fm = text[4:fm_end]
            for field in ("owner:", "status:", "updated:"):
                if not re.search(rf"^{re.escape(field)}", fm, re.MULTILINE):
                    errors.append(f"frontmatter missing '{field}'")
            m = re.search(r"^updated:\s*(\S+)", fm, re.MULTILINE)
            if m and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group(1)):
                errors.append(f"updated '{m.group(1)}' not YYYY-MM-DD")
            # Optional `public` field (P2 stage2-2 publish gate).
            pm = re.search(r"^public:\s*(\S+)", fm, re.MULTILINE)
            if pm and pm.group(1).lower() not in ("true", "false"):
                errors.append(f"public '{pm.group(1)}' must be 'true' or 'false'")

    if "\n## 摘要" not in text:
        errors.append("missing '## 摘要' section")
    if "\n## 来源" not in text:
        errors.append("missing '## 来源' section")

    return errors


def lint_repo_scan() -> dict[str, list[str]]:
    """Repo-wide lint: orphan pages, stale inbox drafts, missing sources, partition mismatches.

    Pure helper shared by tm_mcp.lint_repo (MCP tool) and tm_io.cmd_lint_repo
    (CLI). Linter-owned dashboards (LINTER_DASHBOARDS) and auto-generated
    paths (is_auto_generated_path) are exempted from all four checks.
    """
    orphan_pages: list[str] = []
    stale_drafts: list[str] = []
    missing_sources: list[str] = []
    partition_mismatches: list[str] = []

    # Orphan pages (not linked from index).
    for partition in PARTITION_OWNERS.keys():
        partition_dir = REPO_ROOT / "wiki" / partition
        index_path = partition_dir / "index.md"
        if not index_path.exists():
            continue
        index_content = index_path.read_text(encoding="utf-8")
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            rel = f"wiki/{partition}/{page_file.name}"
            if rel in LINTER_DASHBOARDS or is_auto_generated_path(rel):
                continue
            if page_file.stem not in index_content:
                orphan_pages.append(rel)

    # Stale inbox drafts (>7 days old by mtime).
    seven_days_ago = datetime.datetime.now(TZ_CN) - datetime.timedelta(days=7)
    inbox_dir = REPO_ROOT / "inbox"
    if inbox_dir.exists():
        for inbox_file in inbox_dir.glob("*.md"):
            if inbox_file.name == ".gitkeep":
                continue
            try:
                mtime = datetime.datetime.fromtimestamp(
                    inbox_file.stat().st_mtime, tz=TZ_CN
                )
                if mtime < seven_days_ago:
                    stale_drafts.append(f"inbox/{inbox_file.name}")
            except Exception:
                pass

    # Wiki pages without '## 来源' + owner/partition mismatch.
    for partition in PARTITION_OWNERS.keys():
        partition_dir = REPO_ROOT / "wiki" / partition
        if not partition_dir.exists():
            continue
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            rel = f"wiki/{partition}/{page_file.name}"
            if rel in LINTER_DASHBOARDS or is_auto_generated_path(rel):
                continue
            content = page_file.read_text(encoding="utf-8")
            if "## 来源" not in content:
                missing_sources.append(rel)
            m = re.search(r"^owner:\s*(\S+)", content, re.MULTILINE)
            if m:
                owner = m.group(1)
                if owner == "linter":
                    continue
                if owner not in PARTITION_OWNERS[partition] and owner != "human":
                    partition_mismatches.append(f"{rel} (owner: {owner})")

    return {
        "orphan_pages": orphan_pages,
        "stale_drafts": stale_drafts,
        "missing_sources": missing_sources,
        "partition_mismatches": partition_mismatches,
    }


# ---------- IPFB copywriting capability ----------

IPFB_COPYWRITING_FILES = {
    "skill": "wiki/brand/ipfb-copywriting-skill.md",
    "guide": "wiki/brand/ipfb-copywriting-guide.md",
    "brand_guide": "wiki/brand/ipfb-brand-guide.md",
    "design_plan": "wiki/brand/ipfb-26-summer-design-plan.md",
    "product_plan": "wiki/brand/ipfb-26-summer-product-plan.md",
    "history": "sources/documents/brand/IPFB历史发文文案.txt",
}


def _read_repo_text(rel: str) -> str:
    path = REPO_ROOT / rel
    if not path.exists():
        raise FileNotFoundError(f"not found: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def _source_entry(rel: str, limit: int) -> dict[str, Any]:
    path = REPO_ROOT / rel
    if not path.exists():
        return {"path": rel, "exists": False, "excerpt": ""}
    return {"path": rel, "exists": True, "excerpt": _excerpt_text(_read_repo_text(rel), limit)}


def _excerpt_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]..."


def _history_examples(query: str | None, limit: int) -> list[dict[str, str | int]]:
    if limit <= 0:
        return []
    if not (REPO_ROOT / IPFB_COPYWRITING_FILES["history"]).exists():
        return []
    text = _read_repo_text(IPFB_COPYWRITING_FILES["history"])
    lines = text.splitlines()
    if not query:
        examples: list[dict[str, str | int]] = []
        for i, line in enumerate(lines):
            if "IPFB" not in line:
                continue
            block = "\n".join(lines[i : min(i + 7, len(lines))]).strip()
            if block:
                examples.append({"line": i + 1, "text": block})
            if len(examples) >= limit:
                break
        return examples

    tokens = [t.strip() for t in re.split(r"[\s,，/、]+", query) if t.strip()]
    examples = []
    seen: set[int] = set()
    for i, line in enumerate(lines):
        if i in seen or not any(token in line for token in tokens):
            continue
        start = max(0, i - 2)
        end = min(len(lines), i + 7)
        block = "\n".join(lines[start:end]).strip()
        if block:
            examples.append({"line": start + 1, "text": block})
            seen.update(range(start, end))
        if len(examples) >= limit:
            break
    return examples


def _mem0_recent_feedback(days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
    """Query Mem0 for recent IPFB/brand feedback. Fail-open: returns [] on error.

    Skips promotion markers (content contains '已固化于'): those are accounting
    entries pointing to guide content, not feedback to inject into撰稿 context.
    Otherwise the撰稿人 sees the marker text as if it were a new instruction.
    """
    queries = ["IPFB 文案 辉总 反馈 审稿", "IPFB 禁用 短语 风格 偏好"]
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for q in queries:
        try:
            raw = mem0_search(q, size=limit)
            data = json.loads(raw)
            items = data.get("items") or data.get("results") or []
            for item in items:
                mid = item.get("id", "")
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                meta = item.get("metadata_") or item.get("metadata") or {}
                topic = meta.get("topic", "")
                if topic and topic not in ("brand", "person", "cross", "production"):
                    continue
                content = item.get("content") or item.get("memory") or ""
                if "已固化于" in content:
                    continue  # promotion marker — already in guide, do not re-inject
                results.append({
                    "id": mid,
                    "text": content[:300],
                    "topic": topic,
                    "created_at": item.get("created_at", ""),
                    "source": meta.get("source", "unknown"),
                })
        except Exception:
            pass
    return results[:limit]


def ipfb_copywriting_context(
    task_type: str = "daily_product",
    channel: str = "wechat",
    wave: str | None = None,
    product: str | None = None,
    history_query: str | None = None,
    history_limit: int = 5,
    excerpt_chars: int = 2500,
) -> dict[str, Any]:
    """Return the IPFB copywriting capability bundle for MCP callers."""
    task_map = {
        "daily_product": "朋友圈 / 小红书单品日常宣发",
        "series_campaign": "系列大片 / 波段上新",
        "holiday": "节日海报",
        "preorder": "订货会倒计时",
        "celebrity": "明星同款",
    }
    if task_type not in task_map:
        raise ValueError(f"invalid task_type '{task_type}' (allowed: {sorted(task_map)})")

    sources: dict[str, dict[str, Any]] = {}
    for key in ("skill", "guide", "brand_guide"):
        rel = IPFB_COPYWRITING_FILES[key]
        sources[key] = _source_entry(rel, excerpt_chars)

    if task_type in {"series_campaign", "daily_product"} or wave:
        for key in ("design_plan", "product_plan"):
            rel = IPFB_COPYWRITING_FILES[key]
            sources[key] = _source_entry(rel, excerpt_chars)

    if history_query is None:
        query_parts = [part for part in (wave, product, task_map[task_type]) if part]
        history_query = " ".join(query_parts) if query_parts else None

    return {
        "ok": True,
        "capability": "ipfb-copywriting",
        "description": "IPFB 品牌宣发文案上下文包；调用者据此起稿，tigermemory 只提供规则、资料和自检。",
        "task": {
            "task_type": task_type,
            "task_type_label": task_map[task_type],
            "channel": channel,
            "wave": wave,
            "product": product,
        },
        "read_order": [
            {
                "path": IPFB_COPYWRITING_FILES["guide"],
                "when": "每次必读",
                "focus": "核心准则、雷区、抬头、标题、正文结构、hashtag、CTA、自检清单",
            },
            {
                "path": IPFB_COPYWRITING_FILES["design_plan"],
                "when": "写系列大片 / 波段主题时必读",
                "focus": "对应波段的核心主题、色彩、情绪关键词、系列概念词",
            },
            {
                "path": IPFB_COPYWRITING_FILES["product_plan"],
                "when": "写单品 / 波段核心品类时必读",
                "focus": "对应波段的场景定位、核心品类、单品卖点",
            },
            {
                "path": IPFB_COPYWRITING_FILES["history"],
                "when": "遇到新场景 / 没把握时检索",
                "focus": "历史通过稿与被拒稿的真实审稿样本",
            },
        ],
        "hard_rules": [
            "系列概念词不可自造，必须来自设计企划。",
            "波段场景不可乱用，必须和商品企划/设计企划对齐。",
            "文案要有画面、感官和留白，不做电商卖点堆砌。",
            "禁用电商词：热卖、爆款、藏肉、显瘦、显高、百搭、承包、必入、神套装、性价比等。",
            "交稿前必须用 ipfb-copywriting-skill.md 的自检清单逐项检查。",
            "起稿前必须阅读本返回值的 recent_feedback 字段，避免重复已被拒的短语或意象。",
        ],
        "output_contract": {
            "draft_count": 2,
            "relationship": "两稿互补，不是同一句话微调。",
            "include_hashtags": True,
            "final_review": "虎哥 / 辉总最终审稿；新反馈应写入 inbox 或更新准则。",
        },
        "sources": sources,
        "history_query": history_query,
        "history_examples": _history_examples(history_query, history_limit),
        "recent_feedback": _mem0_recent_feedback(days=30, limit=10),
        "recent_feedback_note": "Mem0 近 30 天 IPFB/品牌相关记忆。起稿前必须阅读，避免重复犯错。",
        "maintenance_note": "如果辉总/虎哥有新审稿偏好，只追加范例或雷区，不推翻核心规则。",
    }


# ---------- Guard (commit-msg hook) ----------

def guard_commit(commit_msg_path: pathlib.Path) -> list[str]:
    """Return list of guard violations for a pending commit. Empty list = pass.

    Callers translate the return value into an exit code (CLI → 5) or a
    rejection response (MCP).
    """
    errors: list[str] = []

    if not commit_msg_path.exists():
        return [f"commit-msg file not found: {commit_msg_path}"]
    raw = commit_msg_path.read_text(encoding="utf-8")
    first_line = next(
        (ln for ln in raw.splitlines() if ln and not ln.startswith("#")), ""
    )
    m = COMMIT_MSG_RE.match(first_line)
    agent: str | None = None
    action: str | None = None
    if not m:
        errors.append(
            "commit message must start with '[<agent>] <action>: <summary>' "
            f"(got: {first_line[:80]!r})"
        )
    else:
        agent = m.group("agent")
        action = m.group("action")
        if agent not in COMMIT_AGENTS:
            errors.append(
                f"commit prefix agent '{agent}' not in commit-author set "
                f"{sorted(COMMIT_AGENTS)} (data-source identities "
                f"{sorted(DATA_SOURCE_AGENTS)} cannot author commits)"
            )
        if action not in ACTIONS:
            errors.append(
                f"commit action '{action}' not in allowed set {sorted(ACTIONS)}"
            )

    staged = staged_files()
    paths = [p for (st, p) in staged if st != "D"]

    # 3. sources/ provenance check
    # Agents may write sources/ if every staged file has a complete
    # scrape-provenance frontmatter (source_url + fetched_at + fetched_by,
    # all non-empty). [human] commits bypass entirely.
    if agent != "human":
        for p in paths:
            if not p.startswith(SOURCES_PREFIX):
                continue
            try:
                content = (REPO_ROOT / p).read_text(encoding="utf-8")
            except (FileNotFoundError, UnicodeDecodeError) as e:
                errors.append(
                    f"'{p}' under sources/: cannot read for provenance check ({e})"
                )
                continue
            if not content.startswith("---\n"):
                errors.append(
                    f"'{p}' under sources/ missing frontmatter. Agent commits to "
                    f"sources/ require {list(SOURCES_PROVENANCE_KEYS)} (all non-empty)."
                )
                continue
            fm_end = content.find("\n---\n", 4)
            if fm_end < 0:
                errors.append(f"'{p}' under sources/ unclosed frontmatter")
                continue
            fm = content[4:fm_end]
            missing = [
                k for k in SOURCES_PROVENANCE_KEYS
                if not re.search(rf"^{re.escape(k)}:\s*\S", fm, re.MULTILINE)
            ]
            if missing:
                errors.append(
                    f"'{p}' under sources/ frontmatter missing or empty: {missing}. "
                    "Agents writing sources/ must include scrape provenance."
                )

    # 4. Meta-rule files: only claude-code / human
    for p in paths:
        is_meta = p in META_RULE_PATHS or any(p.startswith(pref) for pref in META_RULE_PREFIXES)
        if is_meta and agent not in META_RULE_OWNERS:
            errors.append(
                f"'{p}' is a meta-rule file; only {sorted(META_RULE_OWNERS)} "
                f"may modify it (commit agent: {agent})"
            )

    # 5. log.md is claude-code compile only
    if "log.md" in paths and not (agent == "claude-code" and action == "compile"):
        errors.append("log.md is append-only via [claude-code] compile; agents must not write it")

    # 6. Partition ownership on wiki/
    # 2026-05-24 虎哥 directive: removed the cross-partition atomicity check.
    # It was a treatment preference (force small commits per partition), not a
    # safety rule. After the 2026-05-04 ownership relaxation it protected
    # nothing real (all regular agents already own all partitions except
    # person), and it blocked legitimate cross-partition atomic edits (e.g.
    # subtopic-index PoC across 5 partitions, synchronized policy rewrites).
    # Ownership is still enforced per partition below: every partition a
    # commit touches must be owned by the commit agent.
    # LINTER_DASHBOARDS are auto-generated overwrite-only pages; they have
    # their own check immediately below. Excluding them here lets a pure
    # `[linter] lint` dashboard-refresh commit pass owner check (linter is
    # not in any partition's owner set by design).
    wiki_partitions: set[str] = set()
    for p in paths:
        if p in LINTER_DASHBOARDS:
            continue
        wm = WIKI_PATH_RE.match(p)
        if wm:
            wiki_partitions.add(wm.group("partition"))
    if agent is not None and agent != "human":
        for part in sorted(wiki_partitions):
            owners = PARTITION_OWNERS.get(part, set())
            if agent not in owners:
                errors.append(
                    f"agent '{agent}' is not an owner of wiki/{part}/ "
                    f"(owners: {sorted(owners)}). Write to inbox/ instead."
                )

    # Special: linter-owned dashboards are overwrite-only by [linter] lint.
    for dash in LINTER_DASHBOARDS:
        if dash in paths and not (agent == "linter" and action == "lint"):
            errors.append(
                f"{dash} is overwrite-only by [linter] lint; other agents must not modify it"
            )

    # 7. Inbox filename convention
    for p in paths:
        if p.startswith("inbox/") and p != "inbox/.gitkeep" and p.endswith(".md"):
            if DAILY_DIGEST_RE.match(p) or p == "inbox/index.md":
                continue
            im = INBOX_NAME_RE.match(p)
            if not im:
                errors.append(
                    f"inbox filename '{p}' violates "
                    "inbox/YYYY-MM-DD-HHMM-<agent>-<topic>.md"
                )
                continue
            if agent is not None and im.group("agent") not in AGENTS:
                errors.append(f"inbox '{p}' has unknown agent token")
            if im.group("topic") not in TOPICS:
                errors.append(
                    f"inbox '{p}' has invalid topic '{im.group('topic')}' "
                    f"(allowed: {sorted(TOPICS)})"
                )

    # 8. Frontmatter `updated` must be today (Asia/Shanghai) for added/modified md
    today = now("%Y-%m-%d")
    for st, p in staged:
        if st == "D" or not p.endswith(".md"):
            continue
        # Rename (R) preserves the original `updated` date — moving a file is
        # not a content update. Applies to archive/ moves and inbox filename
        # fixes alike. Genuine edits show up as M (modify) alongside, which
        # will still require today's date.
        if st == "R":
            continue
        blob = staged_blob(p)
        if not blob or not blob.startswith("---"):
            continue
        fm_end = blob.find("\n---", 3)
        if fm_end < 0:
            continue
        fm = blob[:fm_end]
        fmm = FRONTMATTER_UPDATED_RE.search(fm)
        if not fmm:
            continue
        val = fmm.group(1)
        if val != today:
            errors.append(
                f"'{p}' frontmatter updated='{val}' != today '{today}' "
                "(Asia/Shanghai). Refresh it before committing."
            )

    return errors


# ---------- High-level write operations ----------

def write_inbox_file(
    agent: str,
    topic: str,
    title: str,
    body: str,
    frontmatter_extra: dict[str, Any] | None = None,
) -> str:
    """Write inbox file. Returns relative path. Does NOT commit.

    Raises ValueError on bad input, FileExistsError on timestamp collision.
    """
    validate_agent(agent)
    validate_topic(topic)
    validate_title(title)
    if not body.strip():
        raise ValueError("body required")

    rel = inbox_rel_path(agent, topic)
    path = REPO_ROOT / rel
    if path.exists():
        raise FileExistsError(f"file already exists: {rel}")
    path.write_text(render_inbox_body(agent, title, body, frontmatter_extra=frontmatter_extra), encoding="utf-8")
    return rel


def write_and_commit_inbox(
    agent: str,
    topic: str,
    title: str,
    body: str,
    frontmatter_extra: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Atomic: write inbox file + commit-push. Returns (rel_path, short_sha).

    On git failure, moves the on-disk file to .tmp/inbox-recovery/ so the
    working tree stays clean AND the memory content is preserved for retry.
    2026-07-04: previously unlinked the file, losing memory on any commit
    failure (hook reject, mojibake false positive, git internal error).
    """
    rel = write_inbox_file(agent, topic, title, body, frontmatter_extra=frontmatter_extra)
    path = REPO_ROOT / rel
    try:
        sha = git_commit_push([rel], f"[{agent}] create: {title}", force_add=True)
    except Exception:
        _recover_to_tmp(path, "inbox-recovery")
        raise
    return rel, sha


def _recover_to_tmp(path: pathlib.Path, subdir: str) -> None:
    """Move a failed-write file to .tmp/{subdir}/ for recovery instead of unlinking.

    Preserves memory content for manual retry / audit. Best-effort: if move
    fails, falls back to unlink (working-tree cleanliness takes priority).
    """
    if not path.exists():
        return
    recovery_dir = REPO_ROOT / ".tmp" / subdir
    try:
        recovery_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dest = recovery_dir / f"{ts}-{path.name}"
        path.rename(dest)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass


# ---------- P6.1 Fact refinement (DeepSeek) ----------

REFINE_DEEPSEEK_ENDPOINT = DEFAULT_DEEPSEEK_ENDPOINT
REFINE_DEEPSEEK_MODEL = DEFAULT_DEEPSEEK_MODEL
REFINE_DEFAULT_TIMEOUT = 15  # seconds
REFINE_MIN_TEXT_LEN = 20
REFINE_MAX_TEXT_LEN = 500

REFINE_PROMPT_TEMPLATE = """你是 Tiger 的记忆提炼助手。从下面的会话摘要中，提取 1-{max_facts} 条对 Tiger 长期有用的结构化事实。

【输出格式】严格 JSON 数组，每条包含:
- topic: 必须从 [systems, brand, operations, investment, person, production] 选一个
- text: {min_len}-{max_len} 字中文，一句话描述一个具体事实

【规则】
1. 只提炼事实，不提炼情绪/感受/流水账
2. 忽略心跳/巡检/格式化回复/调试日志
3. 如果整场对话无有价值事实，返回空数组 []
4. 不要编号、不要 markdown、只输出纯 JSON 数组

【topic 归类指引】
- systems: 工程、架构、工具链、bug 修复
- brand: Doodiu / IPFB / Tigerland 等品牌决策
- operations: 日常运营、协作流程、团队管理
- investment: 投资、资金、财务决策
- person: 个人偏好、关系、健康
- production: 生产制造、供应链、打样

会话摘要将在用户消息中给出。输出 JSON 对象 {{"facts": [...]}}。"""


def _call_deepseek_json(
    system_prompt: str,
    user_msg: str,
    *,
    timeout: int = REFINE_DEFAULT_TIMEOUT,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    purpose: str = "unknown",
    model: str | None = None,
) -> tuple[bool, Any]:
    """Low-level DeepSeek JSON call. Returns (ok, parsed_or_reason).

    Bypasses ambient HTTP(S)_PROXY env vars (same pattern as tm_review).
    On any failure returns (False, reason_str). On success returns (True, parsed_json).
    Emits one `kind=llm_call` JSON log line to stderr per call (see
    `_log_llm_call`); `purpose` tags the caller for grouping in journal.
    """
    prompt_chars = len(system_prompt) + len(user_msg)
    endpoint = deepseek_endpoint()
    model = model or deepseek_model()
    try:
        key = _deepseek_env_value("DEEPSEEK_API_KEY")
    except RuntimeError as e:
        _log_llm_call(model, purpose, 0.0, False,
                      error=f"no DEEPSEEK_API_KEY: {e}", prompt_chars=prompt_chars)
        return False, f"no DEEPSEEK_API_KEY: {e}"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},  # 2026-04-30: skip reasoning for JSON tasks; see api-docs.deepseek.com/zh-cn/guides/thinking_mode
    }).encode("utf-8")

    check_transport_security(endpoint)
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    t0 = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        dur = (time.monotonic() - t0) * 1000
        _log_llm_call(model, purpose, dur, False,
                      error=f"HTTP {e.code}", prompt_chars=prompt_chars, timeout_s=timeout)
        return False, f"DeepSeek HTTP {e.code}"
    except urllib.error.URLError as e:
        dur = (time.monotonic() - t0) * 1000
        _log_llm_call(model, purpose, dur, False,
                      error=f"unreachable: {e.reason}", prompt_chars=prompt_chars, timeout_s=timeout)
        return False, f"DeepSeek unreachable: {e.reason}"
    except Exception as e:
        dur = (time.monotonic() - t0) * 1000
        _log_llm_call(model, purpose, dur, False,
                      error=str(e), prompt_chars=prompt_chars, timeout_s=timeout)
        return False, f"DeepSeek error: {e}"
    dur = (time.monotonic() - t0) * 1000

    try:
        api_resp = json.loads(raw)
        content = api_resp["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, json.JSONDecodeError, TypeError) as e:
        _log_llm_call(model, purpose, dur, False,
                      error=f"malformed: {e}", prompt_chars=prompt_chars,
                      response_chars=len(raw))
        return False, f"malformed DeepSeek response: {e}"

    usage = api_resp.get("usage") or {}
    _log_llm_call(
        model, purpose, dur, True,
        prompt_chars=prompt_chars,
        response_chars=len(content) if isinstance(content, str) else None,
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
    )
    return True, parsed


def refine_from_summary(
    summary: str,
    max_facts: int = 3,
    *,
    timeout: int = REFINE_DEFAULT_TIMEOUT,
) -> list[dict]:
    """Extract up to max_facts structured facts from a conversation summary.

    Returns a list of {"topic": str, "text": str}. Empty list on:
    - Empty/too-short summary
    - DeepSeek API failure (fail-closed: caller treats as "nothing to refine")
    - All facts failed validation

    Uses DeepSeek chat/completions with response_format=json_object. The model
    is prompted to return a JSON object with a "facts" key holding the array
    (JSON mode requires object at root).
    """
    if not isinstance(summary, str) or len(summary.strip()) < 30:
        return []
    if not isinstance(max_facts, int) or not (1 <= max_facts <= 10):
        max_facts = 3

    system_prompt = REFINE_PROMPT_TEMPLATE.format(
        max_facts=max_facts,
        min_len=REFINE_MIN_TEXT_LEN,
        max_len=REFINE_MAX_TEXT_LEN,
    )
    # DeepSeek json_object mode requires the word "json" in prompts and emits
    # a JSON object at the root. We ask the model to return {"facts": [...]}
    user_msg = (
        "请输出 JSON 对象，结构为 {\"facts\": [...]}，"
        "facts 是事实数组（每条含 topic 和 text 字段）。\n\n"
        f"会话摘要：\n{summary[:8000]}"
    )

    ok, parsed = _call_deepseek_json(
        system_prompt, user_msg, timeout=timeout, temperature=0.2,
        purpose="refine_facts",
    )
    if not ok:
        return []

    # Accept either top-level array, {"facts":[...]}, or {"results":[...]}
    raw_facts: list = []
    if isinstance(parsed, list):
        raw_facts = parsed
    elif isinstance(parsed, dict):
        for key in ("facts", "results", "items", "data"):
            if isinstance(parsed.get(key), list):
                raw_facts = parsed[key]
                break

    valid: list[dict] = []
    for f in raw_facts:
        if not isinstance(f, dict):
            continue
        topic = f.get("topic")
        text = f.get("text")
        if not isinstance(topic, str) or topic not in TOPICS or topic == "cross":
            # cross is not a real partition for facts
            continue
        if not isinstance(text, str):
            continue
        text = text.strip()
        if len(text) < REFINE_MIN_TEXT_LEN or len(text) > REFINE_MAX_TEXT_LEN:
            continue
        valid.append({"topic": topic, "text": text})

    return valid[:max_facts]


# ---------- Phase B1: MiniMax M2 adapter + suggest_wiki_patches ----------

# MiniMax M2 is a reasoning model: responses are wrapped in <think>...</think>
# before the real payload. Its json_object mode is also weak — outputs often
# come fenced in ```json ... ```. Both must be stripped.

MINIMAX_DEFAULT_TIMEOUT = 120  # reasoning is slower than chat
MINIMAX_DEFAULT_MAX_TOKENS = 4096

_MINIMAX_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_MINIMAX_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_minimax_artifacts(raw: str) -> str:
    """Remove <think>...</think> reasoning and ```json ... ``` fences."""
    cleaned = _MINIMAX_THINK_RE.sub("", raw).strip()
    m = _MINIMAX_FENCE_RE.match(cleaned)
    if m:
        cleaned = m.group(1).strip()
    return cleaned


_MINIMAX_RETRYABLE = {429, 500, 502, 503, 504, 529}
_MINIMAX_RETRY_DELAYS = (1, 3)  # seconds; 2 retries total (3 attempts)


def _call_minimax_json(
    system_prompt: str,
    user_msg: str,
    *,
    timeout: int = MINIMAX_DEFAULT_TIMEOUT,
    temperature: float = 0.2,
    max_tokens: int = MINIMAX_DEFAULT_MAX_TOKENS,
    purpose: str = "unknown",
) -> tuple[bool, Any]:
    """Same contract as _call_deepseek_json but against MiniMax M2.

    Reads MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_MODEL from .env.
    Strips reasoning and markdown fences before json.loads.

    Retries on transient upstream errors (HTTP 429/5xx/529 overload) with a
    short exponential backoff. Non-retryable errors (4xx auth, parse
    failures) return immediately. Emits one `kind=llm_call` JSON log line
    per terminal outcome (see `_log_llm_call`); retried attempts are
    consolidated into the final entry's `attempts` field.
    """
    prompt_chars = len(system_prompt) + len(user_msg)
    try:
        key = _env_value("MINIMAX_API_KEY")
        base = _env_value("MINIMAX_BASE_URL").rstrip("/")
        model = _env_value("MINIMAX_MODEL")
    except RuntimeError as e:
        _log_llm_call("minimax", purpose, 0.0, False,
                      error=f"no MiniMax config: {e}", prompt_chars=prompt_chars)
        return False, f"no MiniMax config: {e}"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }).encode("utf-8")

    check_transport_security(base)
    last_err: str = "MiniMax error: exhausted retries"
    t_total0 = time.monotonic()
    attempts_used = 0
    for attempt in range(len(_MINIMAX_RETRY_DELAYS) + 1):
        attempts_used = attempt + 1
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break  # success -> parse below
        except urllib.error.HTTPError as e:
            last_err = f"MiniMax HTTP {e.code}"
            if e.code in _MINIMAX_RETRYABLE and attempt < len(_MINIMAX_RETRY_DELAYS):
                time.sleep(_MINIMAX_RETRY_DELAYS[attempt])
                continue
            dur = (time.monotonic() - t_total0) * 1000
            _log_llm_call(model, purpose, dur, False,
                          error=f"HTTP {e.code}", prompt_chars=prompt_chars,
                          attempts=attempts_used, timeout_s=timeout)
            return False, last_err
        except urllib.error.URLError as e:
            last_err = f"MiniMax unreachable: {e.reason}"
            if attempt < len(_MINIMAX_RETRY_DELAYS):
                time.sleep(_MINIMAX_RETRY_DELAYS[attempt])
                continue
            dur = (time.monotonic() - t_total0) * 1000
            _log_llm_call(model, purpose, dur, False,
                          error=f"unreachable: {e.reason}", prompt_chars=prompt_chars,
                          attempts=attempts_used, timeout_s=timeout)
            return False, last_err
        except Exception as e:
            dur = (time.monotonic() - t_total0) * 1000
            _log_llm_call(model, purpose, dur, False,
                          error=str(e), prompt_chars=prompt_chars,
                          attempts=attempts_used, timeout_s=timeout)
            return False, f"MiniMax error: {e}"
    else:
        dur = (time.monotonic() - t_total0) * 1000
        _log_llm_call(model, purpose, dur, False,
                      error=last_err, prompt_chars=prompt_chars,
                      attempts=attempts_used, timeout_s=timeout)
        return False, last_err
    dur = (time.monotonic() - t_total0) * 1000

    try:
        api_resp = json.loads(raw)
        content = api_resp["choices"][0]["message"]["content"]
        cleaned = _strip_minimax_artifacts(content)
        parsed = json.loads(cleaned)
    except (KeyError, json.JSONDecodeError, TypeError) as e:
        _log_llm_call(model, purpose, dur, False,
                      error=f"malformed: {e}", prompt_chars=prompt_chars,
                      attempts=attempts_used, response_chars=len(raw))
        return False, f"malformed MiniMax response: {e}"

    usage = api_resp.get("usage") or {}
    _log_llm_call(
        model, purpose, dur, True,
        prompt_chars=prompt_chars,
        response_chars=len(content) if isinstance(content, str) else None,
        attempts=attempts_used,
        tokens_in=usage.get("prompt_tokens"),
        tokens_out=usage.get("completion_tokens"),
    )
    return True, parsed


# ---------- suggest_wiki_patches (Phase B1) ----------

SUGGEST_PATCH_MAX_PAGES_IN_PROMPT = 60    # keep prompt bounded
SUGGEST_PATCH_MAX_SUMMARY_CHARS = 6000    # cap input summary
SUGGEST_PATCH_DEFAULT_MAX = 5
SUGGEST_PATCH_TYPES = {"append", "update_section", "new_section"}

WIKI_ADMIN_PUBLIC_PARTITIONS = (
    "projects",
    "areas",
    "resources",
    "decisions",
    "journal",
    "systems",
    "archive",
)

WIKI_ADMIN_ROUTE_SCHEMA = "tigermemory-route-proposal-v1"
WIKI_ADMIN_PRIMARY_ROUTES = {
    "wiki",
    "sources_raw",
    "inbox_proposal",
    "private_lane",
    "discard_reject",
}
WIKI_ADMIN_STABILITY_BY_PARTITION = {
    "projects": "working",
    "areas": "durable",
    "resources": "durable",
    "decisions": "durable",
    "journal": "working",
    "systems": "durable",
    "archive": "durable",
}

WIKI_ADMIN_PROPOSAL_PROMPT = """你是 TigerMemory 公开版的 Wiki Admin。

任务：把用户提供的资料整理成一个“待用户审批”的 Markdown Wiki 页面草案。

硬边界：
1. 只输出 JSON 对象，不要输出 markdown 代码块或解释。
2. 不要声称已经写入长期 Wiki；你只是在生成草案。
3. 不要编造来源。资料里没有来源时，在 sources 里写 "user-provided text"。
4. 不要包含密钥、token、密码、身份证、银行卡、家庭住址等敏感信息。
5. 只面向个人知识库公开分类：projects、areas、resources、decisions、journal、systems、archive。
6. 不要写 person/private、investment、brand、production、operations、self-evolution 页面；涉及个人隐私或投资/医疗/财务敏感建议时 should_write=false。
7. 内容必须区分已验证、推断、待确认；没有证据的结论放入待确认。

分类建议：
- projects：有明确目标、交付物或阶段的项目。
- areas：长期维护的责任领域、习惯、健康、学习、家庭、工作等。
- resources：资料、教程、参考链接、模板、方法库。
- decisions：已做出的决定、取舍、原因和影响。
- journal：日记、周回顾、阶段复盘、时间线记录。
- systems：工具设置、Agent 行为规则、工作流、自动化说明。
- archive：过期、完成或暂不维护但仍需保留的材料。

输出 JSON 格式：
{
  "should_write": true,
  "title": "1-80 字页面标题",
  "slug": "lowercase-ascii-slug",
  "summary": "200 字以内摘要",
  "body_markdown": "不含 frontmatter 和 H1 的正文，必须含 ## 摘要 与 ## 来源",
  "rationale": "为什么值得进长期 Wiki",
  "confidence": 0-100,
  "aliases": ["可选别名"],
  "tags": ["可选标签，短词"],
  "key_facts": ["可选关键事实，每条一句话"],
  "evidence_refs": ["来源或证据"],
  "stability": "ephemeral|working|durable",
  "evidence_quality": "raw|partial|sufficient|conflicting"
}

如果不适合写入 Wiki，输出：
{"should_write": false, "rationale": "...", "confidence": 0, "evidence_refs": []}
"""

SUGGEST_PATCH_PROMPT = """你是 tigermemory wiki 的编辑助手。根据对话摘要和现有 wiki 页目录，判断该对话是否应该更新某些已有 wiki 页。

严格规则：
1. 只能针对 `wiki_catalog` 中**已存在**的页产出 patch。禁止编造页路径。
2. 如果对话涉及**全新主题**（catalog 里没有对应 entity 页），返回空数组 patches=[]。新主题由另一条管道处理。
3. 每个 patch 必须完整包含：
   - page: wiki 页相对路径，必须是 wiki_catalog 中出现过的 page 字段原样拷贝
   - type: "append" | "update_section" | "new_section"
     * append: 在页的末尾追加新事实，无需指定 section
     * update_section: 修正或扩充某个已有 section
     * new_section: 新增一个 section
   - section: 目标 section 标题（如 "已验证现状" / "待确认" / "规划"）。type=append 时填空字符串""
   - content: 要追加或改写的 markdown 片段，简洁精炼，不要含 frontmatter 或 h1
   - rationale: 一句话说明为什么这段对话触发了这个 patch（供人类评审）
4. 至多返回 {max_patches} 个 patch。如果不确定该不该改某页，**宁可不返**。
5. 不要输出任何评论、think 过程、代码块包裹，只输出 json 对象。

输出格式：
```
{{"patches": [{{"page": "...", "type": "...", "section": "...", "content": "...", "rationale": "..."}}]}}
```
"""


SUGGEST_PATCH_LLMS = {"auto", "minimax", "deepseek"}


def suggest_wiki_patches(
    summary: str,
    wiki_catalog: list[dict],
    *,
    max_patches: int = SUGGEST_PATCH_DEFAULT_MAX,
    timeout: int = MINIMAX_DEFAULT_TIMEOUT,
    llm: str = "auto",
) -> list[dict]:
    """Propose patches to existing wiki pages based on a conversation summary.

    Args:
        summary: conversation summary (e.g. OpenClaw autoCompactionSummary).
        wiki_catalog: list of {"page": str (relative path), "summary": str}.
            Typically derived from `wiki/<partition>/index.md` entries.
        max_patches: cap on output.
        timeout: LLM request timeout.
        llm: "auto" (MiniMax first, fallback to DeepSeek on upstream outage),
            "minimax" (MiniMax only), "deepseek" (DeepSeek only).

    Returns:
        List of dicts {"page", "type", "section", "content", "rationale"},
        validated against wiki_catalog's page set. Empty on any failure
        (fail-closed) or if model returns no patches.

    Design notes:
    - Fail-closed: any LLM/parse error -> []. Caller treats as "no suggestions".
    - Ground-truth paths: patches referring to pages not in wiki_catalog are
      dropped. This is the hard rule against hallucinated paths.
    - Empty catalog -> [] immediately (nothing to patch).
    - Auto fallback: MiniMax 529 overload / 5xx / unreachable -> DeepSeek.
      Auth/parse failures on MiniMax do NOT fall through (would likely also
      fail or charge DeepSeek quota pointlessly).
    """
    if not isinstance(summary, str) or len(summary.strip()) < 30:
        return []
    if not isinstance(wiki_catalog, list) or not wiki_catalog:
        return []
    if not isinstance(max_patches, int) or not (1 <= max_patches <= 20):
        max_patches = SUGGEST_PATCH_DEFAULT_MAX
    if llm not in SUGGEST_PATCH_LLMS:
        llm = "auto"

    # Build a bounded prompt view of the catalog
    catalog = wiki_catalog[:SUGGEST_PATCH_MAX_PAGES_IN_PROMPT]
    allowed_pages = {entry["page"] for entry in catalog if isinstance(entry, dict) and "page" in entry}
    if not allowed_pages:
        return []

    catalog_lines = [
        f"- {e['page']} — {e.get('summary', '')}"
        for e in catalog
        if isinstance(e, dict) and "page" in e
    ]
    catalog_text = "\n".join(catalog_lines)
    summary_text = summary.strip()[:SUGGEST_PATCH_MAX_SUMMARY_CHARS]

    system_prompt = SUGGEST_PATCH_PROMPT.format(max_patches=max_patches)
    user_msg = (
        f"wiki_catalog:\n{catalog_text}\n\n"
        f"会话摘要：\n{summary_text}\n\n"
        "请输出 json 对象 {\"patches\": [...]}。"
    )

    # Route: auto tries MiniMax first, falls back to DeepSeek on upstream
    # outage (retryable HTTP or unreachable). Explicit llm= skips fallback.
    ok, parsed = False, None
    if llm in ("auto", "minimax"):
        ok, parsed = _call_minimax_json(
            system_prompt, user_msg, timeout=timeout, temperature=0.1,
            purpose="suggest_wiki_patches",
        )
        if not ok and llm == "auto" and isinstance(parsed, str):
            # Fallback only on transport/overload errors, not auth/parse.
            transient = (
                "unreachable" in parsed
                or any(f"HTTP {c}" in parsed for c in _MINIMAX_RETRYABLE)
            )
            if transient:
                ok, parsed = _call_deepseek_json(
                    system_prompt, user_msg, timeout=timeout, temperature=0.1,
                    purpose="suggest_wiki_patches_fallback",
                )
    elif llm == "deepseek":
        ok, parsed = _call_deepseek_json(
            system_prompt, user_msg, timeout=timeout, temperature=0.1,
            purpose="suggest_wiki_patches",
        )

    if not ok:
        return []

    raw_patches: list = []
    if isinstance(parsed, dict):
        for key in ("patches", "results", "items"):
            if isinstance(parsed.get(key), list):
                raw_patches = parsed[key]
                break
    elif isinstance(parsed, list):
        raw_patches = parsed

    valid: list[dict] = []
    for p in raw_patches:
        if not isinstance(p, dict):
            continue
        page = p.get("page")
        ptype = p.get("type")
        section = p.get("section", "")
        content = p.get("content")
        rationale = p.get("rationale", "")
        if not isinstance(page, str) or page not in allowed_pages:
            continue  # hallucinated path, drop
        if ptype not in SUGGEST_PATCH_TYPES:
            continue
        if ptype in ("update_section", "new_section") and not (isinstance(section, str) and section.strip()):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        valid.append({
            "page": page,
            "type": ptype,
            "section": section.strip() if isinstance(section, str) else "",
            "content": content.strip(),
            "rationale": rationale.strip() if isinstance(rationale, str) else "",
        })

    return valid[:max_patches]


_ADMIN_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ADMIN_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_ADMIN_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}")
_ADMIN_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"]?([^\s'\"`]{12,})"
)
_ADMIN_CN_ID_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}"
    r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
_ADMIN_CN_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _admin_yaml_quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _admin_slug(value: str, *, fallback_seed: str) -> str:
    raw = str(value or "").strip().lower()
    raw = raw.replace("_", "-").replace(" ", "-")
    raw = re.sub(r"[^a-z0-9\-]+", "-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    if raw and SLUG_RE.fullmatch(raw):
        return raw[:80].strip("-") or raw
    tokens = _ADMIN_SLUG_TOKEN_RE.findall(str(fallback_seed or "").lower())
    if tokens:
        return "-".join(tokens)[:80].strip("-") or "wiki-admin-proposal"
    digest = hashlib.sha256(str(fallback_seed or value or "wiki-admin-proposal").encode("utf-8")).hexdigest()[:10]
    return f"wiki-admin-{digest}"


def _admin_clean_title(value: str, fallback: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "").strip()).strip("# ")
    if not title:
        title = fallback
    if len(title) > 80:
        title = title[:79].rstrip() + "…"
    return title or "TigerMemory Wiki Admin Proposal"


def _admin_string_list(value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if text:
            out.append(text[:200])
        if len(out) >= limit:
            break
    return out


def _admin_confidence(value: Any) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


def _admin_choice(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else fallback


def _admin_source_refs(source: str, source_refs: Any = None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(source_refs, list):
        for item in source_refs:
            if not isinstance(item, dict):
                continue
            entry: dict[str, str] = {}
            for key in ("kind", "path", "sha256_12", "url", "label"):
                value = str(item.get(key) or "").strip()
                if value:
                    entry[key] = value[:500]
            if entry:
                out.append(entry)
            if len(out) >= 8:
                break
    if not out:
        text = str(source or "user-provided text").strip() or "user-provided text"
        out.append({"kind": "source_ref", "label": text[:500]})
    return out


def _admin_input_kind(source_refs: list[dict[str, str]], explicit: Any = None) -> str:
    value = str(explicit or "").strip()
    if value in {"chat_note", "meeting_note", "web_clip", "file_excerpt", "manual_note"}:
        return value
    kinds = {entry.get("kind", "") for entry in source_refs}
    if "file" in kinds:
        return "file_excerpt"
    if "url" in kinds or "web" in kinds:
        return "web_clip"
    if "stdin" in kinds:
        return "manual_note"
    return "manual_note"


def _admin_route_payload(
    *,
    partition: str,
    should_write: bool,
    title: str,
    summary: str,
    rationale: str,
    target_path: str | None,
    source_refs: list[dict[str, str]],
    input_kind: str,
    stability: str,
    sensitivity: str,
    evidence_quality: str,
    primary_route: str | None = None,
    rejection_code: str | None = None,
    external_llm_allowed: bool = True,
) -> dict[str, Any]:
    route = primary_route
    if not route:
        route = "wiki" if should_write else "inbox_proposal"
    route = _admin_choice(route, WIKI_ADMIN_PRIMARY_ROUTES, "inbox_proposal")
    proposed_partition = partition if should_write and route == "wiki" else None
    return {
        "schema": WIKI_ADMIN_ROUTE_SCHEMA,
        "input_kind": input_kind,
        "primary_route": route,
        "proposed_partition": proposed_partition,
        "target_path": target_path if route == "wiki" else None,
        "source_refs": source_refs,
        "title": title,
        "summary": summary,
        "reason": rationale[:500],
        "stability": stability,
        "sensitivity": sensitivity,
        "evidence_quality": evidence_quality,
        "external_llm_allowed": external_llm_allowed,
        "redaction_required": sensitivity in {"medium", "high", "restricted"},
        "missing_evidence": [] if evidence_quality in {"sufficient", "partial"} else ["review source evidence"],
        "rejection_code": rejection_code,
        "human_review_required": True,
        "auto_write_allowed": False,
    }


def _admin_body_with_required_sections(
    body: str,
    summary: str,
    evidence_refs: list[str],
    key_facts: list[str] | None = None,
) -> str:
    text = str(body or "").strip()
    if not text:
        text = f"## 摘要\n\n{summary or '待补充摘要。'}\n"
    if "## 摘要" not in text:
        text = f"## 摘要\n\n{summary or '待补充摘要。'}\n\n{text}"
    facts = key_facts or []
    if facts and "## 关键事实" not in text and "## Key Facts" not in text:
        facts_md = "\n".join(f"- {fact}" for fact in facts)
        if "## 来源" in text:
            before, after = text.split("## 来源", 1)
            text = before.rstrip() + "\n\n## 关键事实\n\n" + facts_md + "\n\n## 来源" + after
        else:
            text = text.rstrip() + "\n\n## 关键事实\n\n" + facts_md + "\n"
    if "## 来源" not in text:
        refs = evidence_refs or ["user-provided text"]
        text = text.rstrip() + "\n\n## 来源\n\n" + "\n".join(f"- {ref}" for ref in refs) + "\n"
    return text.rstrip() + "\n"


def _admin_secret_like_reason(text: str) -> str | None:
    if _ADMIN_PRIVATE_KEY_RE.search(text):
        return "private_key"
    if _ADMIN_BEARER_RE.search(text):
        return "bearer_token"
    if _ADMIN_SECRET_ASSIGNMENT_RE.search(text):
        return "secret_assignment"
    if _ADMIN_CN_ID_RE.search(text):
        return "identity_number"
    if _ADMIN_CN_PHONE_RE.search(text):
        return "phone_number"
    return None


def propose_wiki_admin_page(
    text: str,
    *,
    partition: str,
    title: str = "",
    source: str = "user-provided text",
    source_refs: list[dict[str, Any]] | None = None,
    input_kind: str | None = None,
    timeout: int = REFINE_DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Use the configured DeepSeek-compatible LLM to draft a reviewable wiki page proposal.

    The function returns a proposal payload only. It never writes wiki files and
    never commits. Callers must put the payload through an explicit human review
    step before writing the generated markdown to disk.
    """
    if partition not in WIKI_ADMIN_PUBLIC_PARTITIONS:
        raise ValueError(f"partition '{partition}' is not supported by the public Wiki Admin proposal flow")
    source_text = str(text or "").strip()
    if len(source_text) < 20:
        raise ValueError("proposal source text must be at least 20 characters")
    secret_reason = _admin_secret_like_reason(source_text)
    if secret_reason:
        raise ValueError(f"proposal source text appears to contain {secret_reason}; remove secrets or private data before calling an online LLM")

    fallback_title = _admin_clean_title(title, "TigerMemory Wiki Admin Proposal")
    normalized_source_refs = _admin_source_refs(source, source_refs)
    normalized_input_kind = _admin_input_kind(normalized_source_refs, input_kind)
    user_msg = (
        f"target_partition: {partition}\n"
        f"preferred_title: {fallback_title}\n"
        f"source_ref: {source or 'user-provided text'}\n\n"
        f"资料：\n{source_text[:12000]}\n\n"
        "请输出 JSON 对象。"
    )
    ok, parsed = _call_deepseek_json(
        WIKI_ADMIN_PROPOSAL_PROMPT,
        user_msg,
        timeout=timeout,
        temperature=0.1,
        max_tokens=1600,
        purpose="wiki_admin_proposal",
        model=deepseek_admin_model(),
    )
    if not ok:
        raise RuntimeError(str(parsed))
    if not isinstance(parsed, dict):
        raise RuntimeError("malformed Wiki Admin proposal response")
    if parsed.get("should_write") is False:
        rationale = str(parsed.get("rationale") or "model judged this source unsuitable for wiki")
        evidence_refs = _admin_string_list(parsed.get("evidence_refs"))
        evidence_quality = _admin_choice(parsed.get("evidence_quality"), {"raw", "partial", "sufficient", "conflicting"}, "partial")
        stability = _admin_choice(parsed.get("stability"), {"ephemeral", "working", "durable"}, "working")
        route = _admin_route_payload(
            partition=partition,
            should_write=False,
            title=fallback_title,
            summary="",
            rationale=rationale,
            target_path=None,
            source_refs=normalized_source_refs,
            input_kind=normalized_input_kind,
            stability=stability,
            sensitivity="low",
            evidence_quality=evidence_quality,
            primary_route="inbox_proposal",
            rejection_code="model_rejected",
        )
        return {
            "schema": "tigermemory-admin-proposal-v1",
            "should_write": False,
            "partition": partition,
            "title": fallback_title,
            "rationale": rationale[:500],
            "confidence": _admin_confidence(parsed.get("confidence")),
            "source_refs": normalized_source_refs,
            "evidence_refs": evidence_refs,
            "route": route,
            "primary_route": route["primary_route"],
            "sensitivity": route["sensitivity"],
            "stability": route["stability"],
            "evidence_quality": route["evidence_quality"],
            "auto_write_allowed": False,
            "user_review_required": True,
        }

    proposal_title = _admin_clean_title(parsed.get("title"), fallback_title)
    summary = re.sub(r"\s+", " ", str(parsed.get("summary") or "").strip())[:220]
    evidence_refs = _admin_string_list(parsed.get("evidence_refs"))
    aliases = _admin_string_list(parsed.get("aliases"), limit=6)
    tags = _admin_string_list(parsed.get("tags"), limit=8)
    key_facts = _admin_string_list(parsed.get("key_facts"), limit=8)
    slug = _admin_slug(str(parsed.get("slug") or ""), fallback_seed=f"{proposal_title} {source_text[:200]}")
    target_path = f"wiki/{partition}/{slug}.md"
    stability = _admin_choice(
        parsed.get("stability"),
        {"ephemeral", "working", "durable"},
        WIKI_ADMIN_STABILITY_BY_PARTITION.get(partition, "working"),
    )
    evidence_quality = _admin_choice(
        parsed.get("evidence_quality"),
        {"raw", "partial", "sufficient", "conflicting"},
        "sufficient" if evidence_refs else "partial",
    )
    route = _admin_route_payload(
        partition=partition,
        should_write=True,
        title=proposal_title,
        summary=summary,
        rationale=str(parsed.get("rationale") or "").strip(),
        target_path=target_path,
        source_refs=normalized_source_refs,
        input_kind=normalized_input_kind,
        stability=stability,
        sensitivity="low",
        evidence_quality=evidence_quality,
        primary_route="wiki",
    )
    body = _admin_body_with_required_sections(str(parsed.get("body_markdown") or ""), summary, evidence_refs, key_facts)
    fm_lines = [
        "owner: human",
        "status: active",
        f"title: {_admin_yaml_quote(proposal_title)}",
        f"summary: {_admin_yaml_quote(summary)}",
    ]
    if aliases:
        fm_lines.append("aliases:")
        for alias in aliases:
            fm_lines.append(f"  - {_admin_yaml_quote(alias)}")
    if tags:
        fm_lines.append("tags:")
        for tag in tags:
            fm_lines.append(f"  - {_admin_yaml_quote(tag)}")
    if key_facts:
        fm_lines.append("key_facts:")
        for fact in key_facts:
            fm_lines.append(f"  - {_admin_yaml_quote(fact)}")
    wiki_markdown = render_wiki_body("\n".join(fm_lines), body)
    return {
        "schema": "tigermemory-admin-proposal-v1",
        "should_write": True,
        "partition": partition,
        "title": proposal_title,
        "slug": slug,
        "target_path": target_path,
        "action": "create",
        "summary": summary,
        "rationale": str(parsed.get("rationale") or "").strip()[:500],
        "confidence": _admin_confidence(parsed.get("confidence")),
        "aliases": aliases,
        "tags": tags,
        "key_facts": key_facts,
        "source_refs": normalized_source_refs,
        "evidence_refs": evidence_refs or [source or "user-provided text"],
        "route": route,
        "primary_route": route["primary_route"],
        "sensitivity": route["sensitivity"],
        "stability": route["stability"],
        "evidence_quality": route["evidence_quality"],
        "auto_write_allowed": False,
        "wiki_markdown": wiki_markdown,
        "user_review_required": True,
    }


def save_wiki_patches_to_inbox(
    patches: list[dict],
    source: str,
    *,
    summary_excerpt: str = "",
) -> str:
    """Serialize a patch suggestion list into a reviewable inbox file.

    Returns the relative path of the written file. Raises ValueError on bad
    input. Uses Asia/Shanghai time for filename and frontmatter.

    The file is written directly under `inbox/` with topic='cross' because a
    single suggest_wiki_patches call can span multiple partitions.
    """
    validate_agent(source)
    if not isinstance(patches, list) or not patches:
        raise ValueError("patches must be a non-empty list")

    # Filename follows standard inbox convention (AGENTS.md §5.4) to pass the
    # guard: YYYY-MM-DD-HHMM-<agent>-<topic>.md. The wiki-patches nature is
    # declared via `type: wiki-patches` in the frontmatter instead.
    now = datetime.datetime.now(TZ_CN)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    date_str = now.strftime("%Y-%m-%d")
    fname = f"{stamp}-{source}-cross.md"
    rel = f"inbox/{fname}"
    path = REPO_ROOT / rel

    lines: list[str] = [
        "---",
        f"owner: {source}",
        "status: proposal",
        f"updated: {date_str}",
        "type: wiki-patches",
        "---",
        "",
        f"# Wiki Patch Suggestions ({stamp})",
        "",
        f"Generated by `suggest_wiki_patches` from a conversation summary. "
        f"{len(patches)} patch(es) proposed. Review and apply manually or via"
        " a future review tool.",
        "",
    ]
    if summary_excerpt:
        excerpt = summary_excerpt.strip()[:500]
        lines += ["## 源摘要", "", excerpt, ""]

    lines += ["## Patches", ""]
    for i, p in enumerate(patches, 1):
        lines.append(f"### {i}. `{p['page']}` — {p['type']}")
        if p.get("section"):
            lines.append(f"- **section**: {p['section']}")
        lines.append(f"- **rationale**: {p.get('rationale', '')}")
        lines.append("")
        lines.append("```markdown")
        lines.append(p["content"])
        lines.append("```")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return rel
