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
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

try:
    from zoneinfo import ZoneInfo
    _TZ_CN_IMPL = ZoneInfo("Asia/Shanghai")
except Exception:
    # Windows Python may lack tzdata. Fall back to a fixed +08:00 offset.
    _TZ_CN_IMPL = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")

TZ_CN = _TZ_CN_IMPL
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent  # tools/.. = repo root


# ---------- Enums (AGENTS.md §3, §4) ----------

AGENTS = {"claude-code", "codex", "openclaw", "hermes", "deerflow", "human", "mem0", "linter"}
ACTIONS = {"create", "update", "archive", "lint", "ingest", "compile"}
TOPICS = {"brand", "investment", "operations", "production", "systems", "person", "cross"}

# Partition ownership per AGENTS.md §4. Values are the agents allowed to
# write wiki/<partition>/*.md directly. Anyone else must go via inbox.
PARTITION_OWNERS: dict[str, set[str]] = {
    "brand":      {"openclaw", "claude-code"},
    "investment": {"deerflow", "claude-code"},
    "operations": {"hermes",   "claude-code"},
    "production": {"claude-code"},
    "systems":    {"claude-code", "codex"},
    "person":     {"claude-code"},  # sensitive; claude-code reviews writes
}

# Meta-rule files: only claude-code or human may modify.
META_RULE_PATHS = {"AGENTS.md", "index.md", "log.md"}
META_RULE_PREFIXES = ("schemas/",)
META_RULE_OWNERS = {"claude-code", "human"}

# sources/ is an external-mirror area; no agent should modify it.
# Humans may bypass with `git commit --no-verify`.
SOURCES_PREFIX = "sources/"

# Regex patterns
TITLE_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff _\-]{1,80}")
SLUG_RE = re.compile(r"[a-z0-9\-]+")
COMMIT_MSG_RE = re.compile(
    r"^\[(?P<agent>[a-z0-9\-]+)\]\s+(?P<action>[a-z]+)\s*[:：]\s*(?P<summary>.+)$"
)
INBOX_NAME_RE = re.compile(
    r"^inbox/\d{4}-\d{2}-\d{2}-\d{4}-(?P<agent>[a-z0-9\-]+)-(?P<topic>[a-z]+)\.md$"
)
WIKI_PATH_RE = re.compile(r"^wiki/(?P<partition>[a-z]+)/[^/]+\.md$")
FRONTMATTER_UPDATED_RE = re.compile(r"^updated:\s*(\S+)\s*$", re.MULTILINE)


# ---------- Exceptions ----------

class GitError(RuntimeError):
    """Raised when a git operation fails (push rejected, rebase conflict, etc.)."""


# ---------- Time ----------

def now(fmt: str) -> str:
    """Format current time in Asia/Shanghai TZ."""
    return datetime.datetime.now(TZ_CN).strftime(fmt)


# ---------- Subprocess ----------

def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command in REPO_ROOT, capturing output. Raises GitError if check=True and rc!=0."""
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GitError(
            f"cmd failed: {' '.join(cmd)}\nstderr: {r.stderr.strip()}\nstdout: {r.stdout.strip()}"
        )
    return r


# ---------- Git ----------

def git_pull_rebase() -> None:
    """pull --rebase; on conflict/failure, abort and raise GitError (AGENTS.md §5.1)."""
    r = run(["git", "pull", "--rebase"], check=False)
    if r.returncode != 0:
        run(["git", "rebase", "--abort"], check=False)
        raise GitError(
            f"git pull --rebase failed; rebase aborted. stderr: {r.stderr.strip()}"
        )


def git_commit_push(files: list[str], msg: str) -> str:
    """pull --rebase → add → commit → push (retry 1x). Returns short SHA.

    Raises GitError on failure. On rebase conflict at any point, aborts the
    rebase first. Callers are responsible for rolling back on-disk changes
    if they want a clean working tree after failure.
    """
    git_pull_rebase()
    run(["git", "add", "--"] + files)
    commit_r = run(["git", "commit", "-m", msg], check=False)
    if commit_r.returncode != 0:
        raise GitError(
            f"git commit failed: {commit_r.stderr.strip() or commit_r.stdout.strip()}"
        )

    push_r = run(["git", "push"], check=False)
    if push_r.returncode != 0:
        git_pull_rebase()
        push2 = run(["git", "push"], check=False)
        if push2.returncode != 0:
            raise GitError(f"push failed after rebase retry: {push2.stderr.strip()}")

    return run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()


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

def _env_value(key: str) -> str:
    """Read KEY=value from runtime/openmemory/.env. Raises RuntimeError if missing."""
    env_path = REPO_ROOT / "runtime" / "openmemory" / ".env"
    if not env_path.exists():
        raise RuntimeError(f"missing {env_path} — configure {key} first")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"{key} not found in {env_path}")


def mem0_key() -> str:
    return _env_value("MEM0_API_KEY")


def mcp_api_key() -> str:
    return _env_value("TM_MCP_API_KEY")


def mem0_base() -> str:
    return os.getenv("MEM0_URL", "http://tiger-mainmachine:9765")


def mem0_request(url: str, data: bytes | None = None) -> str:
    """GET (data=None) or POST to Mem0. Raises RuntimeError with HTTP code / reason on failure."""
    key = mem0_key()
    headers = {"Authorization": f"Bearer {key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, headers=headers, method=("POST" if data else "GET")
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mem0 HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Mem0 unreachable: {e.reason}")


def mem0_write(agent: str, topic: str, text: str) -> str:
    """POST a memory with enforced metadata. Returns raw response body."""
    validate_agent(agent)
    validate_topic(topic)
    if not text.strip():
        raise ValueError("text required")
    payload = json.dumps({
        "user_id": "tiger",
        "text": text,
        "metadata": {"source": agent, "topic": topic},
    }).encode("utf-8")
    return mem0_request(f"{mem0_base()}/api/v1/memories/", data=payload)


def mem0_search(query: str, size: int = 5) -> str:
    """GET memories by query. Returns raw response body."""
    params = urllib.parse.urlencode(
        {"user_id": "tiger", "query": query, "page": 1, "size": size}
    )
    return mem0_request(f"{mem0_base()}/api/v1/memories/?{params}")


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


def render_inbox_body(agent: str, title: str, body: str, date: str | None = None) -> str:
    """Render standard inbox frontmatter + body."""
    if date is None:
        date = now("%Y-%m-%d")
    return (
        "---\n"
        f"owner: {agent}\n"
        "status: draft\n"
        f"updated: {date}\n"
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

    if "\n## 摘要" not in text:
        errors.append("missing '## 摘要' section")
    if "\n## 来源" not in text:
        errors.append("missing '## 来源' section")

    return errors


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
        if agent not in AGENTS:
            errors.append(
                f"commit prefix agent '{agent}' not in allowed set {sorted(AGENTS)}"
            )
        if action not in ACTIONS:
            errors.append(
                f"commit action '{action}' not in allowed set {sorted(ACTIONS)}"
            )

    staged = staged_files()
    paths = [p for (st, p) in staged if st != "D"]

    # 3. sources/ immutability
    for p in paths:
        if p.startswith(SOURCES_PREFIX):
            errors.append(
                f"'{p}' is under sources/ (external mirror, agent-immutable). "
                "Humans may override with --no-verify."
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

    # 6. Partition ownership + atomicity on wiki/
    wiki_partitions: set[str] = set()
    for p in paths:
        wm = WIKI_PATH_RE.match(p)
        if wm:
            wiki_partitions.add(wm.group("partition"))
    if len(wiki_partitions) > 1:
        errors.append(
            f"commit touches multiple wiki partitions {sorted(wiki_partitions)}; "
            "split into one commit per partition"
        )
    elif len(wiki_partitions) == 1 and agent is not None:
        part = next(iter(wiki_partitions))
        owners = PARTITION_OWNERS.get(part, set())
        if agent not in owners and agent != "human":
            errors.append(
                f"agent '{agent}' is not an owner of wiki/{part}/ "
                f"(owners: {sorted(owners)}). Write to inbox/ instead."
            )

    # Special: linter-owned dashboards are overwrite-only by [linter] lint.
    LINTER_DASHBOARDS = {
        "wiki/operations/lint-dashboard.md",
        "wiki/operations/inbox-triage.md",
    }
    for dash in LINTER_DASHBOARDS:
        if dash in paths and not (agent == "linter" and action == "lint"):
            errors.append(
                f"{dash} is overwrite-only by [linter] lint; other agents must not modify it"
            )

    # 7. Inbox filename convention
    for p in paths:
        if p.startswith("inbox/") and p != "inbox/.gitkeep" and p.endswith(".md"):
            im = INBOX_NAME_RE.match(p)
            if not im:
                errors.append(
                    f"inbox filename '{p}' violates "
                    "inbox/YYYY-MM-DD-HHMM-<agent>-<topic>.md"
                )
                continue
            if agent is not None and im.group("agent") not in AGENTS:
                errors.append(f"inbox '{p}' has unknown agent token")

    # 8. Frontmatter `updated` must be today (Asia/Shanghai) for added/modified md
    today = now("%Y-%m-%d")
    for st, p in staged:
        if st == "D" or not p.endswith(".md"):
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

def write_inbox_file(agent: str, topic: str, title: str, body: str) -> str:
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
    path.write_text(render_inbox_body(agent, title, body), encoding="utf-8")
    return rel


def write_and_commit_inbox(agent: str, topic: str, title: str, body: str) -> tuple[str, str]:
    """Atomic: write inbox file + commit-push. Returns (rel_path, short_sha).

    On git failure, removes the on-disk file so working tree stays clean.
    """
    rel = write_inbox_file(agent, topic, title, body)
    path = REPO_ROOT / rel
    try:
        sha = git_commit_push([rel], f"[{agent}] create: {title}")
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return rel, sha
