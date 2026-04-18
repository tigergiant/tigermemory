#!/usr/bin/env python3
"""
tm_io.py — tigermemory I/O helper for agents.

Enforces AGENTS.md rules as code, not prose. Agents should call this tool
instead of writing files / running git / calling Mem0 directly.

Usage:
  tm_io.py write-inbox  --agent <name> --topic <topic> --title <title>   # body on stdin
  tm_io.py commit-push  --files <f>... --agent <name> --action <a> --summary <s>
  tm_io.py mem0-write   --agent <name> --topic <topic>                   # text  on stdin
  tm_io.py mem0-search  --query <q> [--size N]
  tm_io.py lint-page    <path>
  tm_io.py guard        --commit-msg-file <path>                         # called by git hook

Exit codes:
  0  success
  2  bad usage / validation failure
  3  git operation failed (push rejected, rebase conflict, etc.)
  4  Mem0 API failure
  5  guard rejected the commit
"""

from __future__ import annotations
import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
try:
    from zoneinfo import ZoneInfo
    _TZ_CN_IMPL = ZoneInfo("Asia/Shanghai")
except Exception:
    # Windows Python may lack tzdata. Fall back to a fixed +08:00 offset.
    _TZ_CN_IMPL = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")

AGENTS = {"claude-code", "codex", "openclaw", "hermes", "deerflow", "human", "mem0"}
ACTIONS = {"create", "update", "archive", "lint", "ingest", "compile"}
TOPICS = {"brand", "investment", "operations", "production", "systems", "person", "cross"}

# Partition ownership per AGENTS.md §4. Values are the agents allowed to
# write wiki/<partition>/*.md directly. Anyone else must go via inbox.
PARTITION_OWNERS = {
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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent  # tools/.. = repo root
TZ_CN = _TZ_CN_IMPL


def now(fmt: str) -> str:
    return datetime.datetime.now(TZ_CN).strftime(fmt)


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(
            f"cmd failed: {' '.join(cmd)}\nstderr: {r.stderr.strip()}\nstdout: {r.stdout.strip()}",
            code=3,
        )
    return r


# ---------- write-inbox ----------

def cmd_write_inbox(args: argparse.Namespace) -> None:
    if args.agent not in AGENTS:
        die(f"invalid agent '{args.agent}' (allowed: {sorted(AGENTS)})")
    if args.topic not in TOPICS:
        die(f"invalid topic '{args.topic}' (allowed: {sorted(TOPICS)})")
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff _\-]{1,80}", args.title):
        die("title must be 1-80 chars: letters/digits/CJK/space/-/_")

    body = sys.stdin.read().strip()
    if not body:
        die("body required on stdin")

    date = now("%Y-%m-%d")
    stamp = now("%Y-%m-%d-%H%M")
    rel = f"inbox/{stamp}-{args.agent}-{args.topic}.md"
    path = REPO_ROOT / rel
    if path.exists():
        die(f"file already exists: {rel}")

    content = (
        "---\n"
        f"owner: {args.agent}\n"
        "status: draft\n"
        f"updated: {date}\n"
        "---\n\n"
        f"# {args.title}\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    print(rel)


# ---------- commit-push ----------

def cmd_commit_push(args: argparse.Namespace) -> None:
    if args.agent not in AGENTS:
        die(f"invalid agent '{args.agent}'")
    if args.action not in ACTIONS:
        die(f"invalid action '{args.action}' (allowed: {sorted(ACTIONS)})")
    if not args.summary or len(args.summary) > 120:
        die("summary must be 1-120 chars")

    # forbid agents from touching shared append files
    forbidden_for_agent = {"log.md"}
    for f in args.files:
        if f in forbidden_for_agent and args.agent != "claude-code":
            die(f"'{f}' is compiled by claude-code; agents must not write it directly")
        p = (REPO_ROOT / f).resolve()
        if not p.exists():
            die(f"file not found: {f}")
        try:
            p.relative_to(REPO_ROOT)
        except ValueError:
            die(f"file outside repo: {f}")

    run(["git", "pull", "--rebase"])

    # abort if rebase left us in weird state
    status = run(["git", "status", "--porcelain=v2", "--branch"], check=False)
    if "unmerged" in status.stdout.lower():
        run(["git", "rebase", "--abort"], check=False)
        die("unmerged paths detected; aborted. Retry writing as new inbox file.", code=3)

    run(["git", "add", "--"] + list(args.files))
    msg = f"[{args.agent}] {args.action}: {args.summary}"
    commit_r = run(["git", "commit", "-m", msg], check=False)
    if commit_r.returncode != 0:
        # nothing to commit is fatal for this tool
        die(f"git commit failed: {commit_r.stderr.strip() or commit_r.stdout.strip()}", code=3)

    push_r = run(["git", "push"], check=False)
    if push_r.returncode != 0:
        # retry-once policy: pull --rebase, handle conflict, push again
        pull2 = run(["git", "pull", "--rebase"], check=False)
        if pull2.returncode != 0:
            run(["git", "rebase", "--abort"], check=False)
            die(
                "rebase conflict on retry; aborted. "
                "Re-write as a new inbox file (unique timestamp) and retry.",
                code=3,
            )
        push2 = run(["git", "push"], check=False)
        if push2.returncode != 0:
            die(f"push failed after rebase retry: {push2.stderr.strip()}", code=3)

    h = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    print(h)


# ---------- mem0 ----------

def _mem0_key() -> str:
    env_path = REPO_ROOT / "runtime" / "openmemory" / ".env"
    if not env_path.exists():
        die(f"missing {env_path} — configure MEM0_API_KEY first")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MEM0_API_KEY="):
            return line.split("=", 1)[1].strip()
    die("MEM0_API_KEY not found in runtime/openmemory/.env")


def _mem0_base() -> str:
    return os.getenv("MEM0_URL", "http://tiger-mainmachine:9765")


def _mem0_request(url: str, data: bytes | None = None) -> str:
    key = _mem0_key()
    headers = {"Authorization": f"Bearer {key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        die(f"Mem0 HTTP {e.code}: {body}", code=4)
    except urllib.error.URLError as e:
        die(f"Mem0 unreachable: {e.reason}", code=4)


def cmd_mem0_write(args: argparse.Namespace) -> None:
    if args.agent not in AGENTS:
        die(f"invalid agent '{args.agent}'")
    if args.topic not in TOPICS:
        die(f"invalid topic '{args.topic}'")
    text = sys.stdin.read().strip()
    if not text:
        die("text required on stdin")

    payload = json.dumps(
        {
            "user_id": "tiger",
            "text": text,
            "metadata": {"source": args.agent, "topic": args.topic},
        }
    ).encode("utf-8")
    resp = _mem0_request(f"{_mem0_base()}/api/v1/memories/", data=payload)
    print(resp)


def cmd_mem0_search(args: argparse.Namespace) -> None:
    params = urllib.parse.urlencode(
        {"user_id": "tiger", "query": args.query, "page": 1, "size": args.size}
    )
    resp = _mem0_request(f"{_mem0_base()}/api/v1/memories/?{params}")
    print(resp)


# ---------- lint-page ----------

def cmd_lint_page(args: argparse.Namespace) -> None:
    path = pathlib.Path(args.path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        die(f"not found: {path}")

    text = path.read_text(encoding="utf-8")
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
            # updated must be YYYY-MM-DD
            m = re.search(r"^updated:\s*(\S+)", fm, re.MULTILINE)
            if m and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group(1)):
                errors.append(f"updated '{m.group(1)}' not YYYY-MM-DD")

    if "\n## 摘要" not in text:
        errors.append("missing '## 摘要' section")
    if "\n## 来源" not in text:
        errors.append("missing '## 来源' section")

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print("OK")


# ---------- guard (called by commit-msg hook) ----------

COMMIT_MSG_RE = re.compile(
    r"^\[(?P<agent>[a-z0-9\-]+)\]\s+(?P<action>[a-z]+)\s*[:：]\s*(?P<summary>.+)$"
)
INBOX_NAME_RE = re.compile(
    r"^inbox/\d{4}-\d{2}-\d{2}-\d{4}-(?P<agent>[a-z0-9\-]+)-(?P<topic>[a-z]+)\.md$"
)
WIKI_PATH_RE = re.compile(r"^wiki/(?P<partition>[a-z]+)/[^/]+\.md$")
FRONTMATTER_UPDATED_RE = re.compile(r"^updated:\s*(\S+)\s*$", re.MULTILINE)


def _staged_files() -> list[tuple[str, str]]:
    """Return [(status, path)] for staged changes. status: A/M/D/R<score>/..."""
    r = run(["git", "diff", "--cached", "--name-status", "-z"], check=True)
    out = r.stdout
    entries: list[tuple[str, str]] = []
    parts = out.split("\x00")
    i = 0
    while i < len(parts) - 1:
        status = parts[i]
        if not status:
            i += 1
            continue
        if status.startswith("R") or status.startswith("C"):
            # rename/copy: status, old, new
            if i + 2 >= len(parts):
                break
            entries.append((status[0], parts[i + 2]))
            i += 3
        else:
            entries.append((status[0], parts[i + 1]))
            i += 2
    return entries


def _staged_blob(path: str) -> str | None:
    """Return staged content of path, or None if deleted/unreadable."""
    r = subprocess.run(
        ["git", "show", f":{path}"], cwd=REPO_ROOT, capture_output=True
    )
    if r.returncode != 0:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def cmd_guard(args: argparse.Namespace) -> None:
    errors: list[str] = []

    # 1. Commit message format
    msg_path = pathlib.Path(args.commit_msg_file)
    if not msg_path.exists():
        die(f"commit-msg file not found: {msg_path}")
    raw = msg_path.read_text(encoding="utf-8")
    first_line = next(
        (ln for ln in raw.splitlines() if ln and not ln.startswith("#")), ""
    )
    m = COMMIT_MSG_RE.match(first_line)
    if not m:
        errors.append(
            "commit message must start with '[<agent>] <action>: <summary>' "
            f"(got: {first_line[:80]!r})"
        )
        agent = None
        action = None
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

    # 2. Staged files (empty list is fine; per-file checks become no-ops)
    staged = _staged_files()
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
        blob = _staged_blob(p)
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

    w = sub.add_parser("write-inbox", help="write a new inbox file")
    w.add_argument("--agent", required=True)
    w.add_argument("--topic", required=True)
    w.add_argument("--title", required=True)
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

    g = sub.add_parser("guard", help="enforce AGENTS.md rules on a pending commit (called by commit-msg hook)")
    g.add_argument("--commit-msg-file", required=True)
    g.set_defaults(func=cmd_guard)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
