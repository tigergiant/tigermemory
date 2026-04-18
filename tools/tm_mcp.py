#!/usr/bin/env python3
"""
tools/tm_mcp.py — tigermemory MCP server (thin facade over tm_io.py).

Enforces AGENTS.md rules as code. Agents call MCP tools instead of
writing files / running git / calling Mem0 directly.

Usage:
  python tools/tm_mcp.py --stdio          # default for local clients
  python tools/tm_mcp.py --http --host 0.0.0.0 --port 9766

HTTP mode requires TM_MCP_API_KEY in runtime/openmemory/.env.
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
from typing import Any

try:
    from zoneinfo import ZoneInfo
    _TZ_CN_IMPL = ZoneInfo("Asia/Shanghai")
except Exception:
    _TZ_CN_IMPL = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")

from mcp.server.fastmcp import FastMCP

AGENTS = {"claude-code", "codex", "openclaw", "hermes", "deerflow", "human", "mem0"}
ACTIONS = {"create", "update", "archive", "lint", "ingest", "compile"}
TOPICS = {"brand", "investment", "operations", "production", "systems", "person", "cross"}

PARTITION_OWNERS = {
    "brand":      {"openclaw", "claude-code"},
    "investment": {"deerflow", "claude-code"},
    "operations": {"hermes",   "claude-code"},
    "production": {"claude-code"},
    "systems":    {"claude-code", "codex"},
    "person":     {"claude-code"},
}

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TZ_CN = _TZ_CN_IMPL


def now(fmt: str) -> str:
    return datetime.datetime.now(TZ_CN).strftime(fmt)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"cmd failed: {' '.join(cmd)}\nstderr: {r.stderr.strip()}\nstdout: {r.stdout.strip()}"
        )
    return r


# ---------- git helpers ----------

def _git_pull_rebase() -> None:
    """pull --rebase; on conflict/failure, abort and raise (AGENTS.md §5.1)."""
    r = run(["git", "pull", "--rebase"], check=False)
    if r.returncode != 0:
        run(["git", "rebase", "--abort"], check=False)
        raise RuntimeError(
            f"git pull --rebase failed; rebase aborted. stderr: {r.stderr.strip()}"
        )


def _commit_push(files: list[str], msg: str) -> str:
    """pull --rebase → add → commit → push (retry 1x). Returns short SHA.

    On rebase conflict at any point, aborts the rebase and raises, per
    AGENTS.md §5.1. Callers handle cleanup of the working-tree file(s)
    if needed.
    """
    _git_pull_rebase()
    run(["git", "add", "--"] + files)
    commit_r = run(["git", "commit", "-m", msg], check=False)
    if commit_r.returncode != 0:
        raise RuntimeError(
            f"git commit failed: {commit_r.stderr.strip() or commit_r.stdout.strip()}"
        )

    push_r = run(["git", "push"], check=False)
    if push_r.returncode != 0:
        _git_pull_rebase()
        push2 = run(["git", "push"], check=False)
        if push2.returncode != 0:
            raise RuntimeError(f"push failed after rebase retry: {push2.stderr.strip()}")

    return run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()


# ---------- Mem0 helpers ----------

def _mem0_key() -> str:
    env_path = REPO_ROOT / "runtime" / "openmemory" / ".env"
    if not env_path.exists():
        raise RuntimeError(f"missing {env_path} — configure MEM0_API_KEY first")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MEM0_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("MEM0_API_KEY not found in runtime/openmemory/.env")


def _mcp_api_key() -> str:
    env_path = REPO_ROOT / "runtime" / "openmemory" / ".env"
    if not env_path.exists():
        raise RuntimeError(f"missing {env_path} — configure TM_MCP_API_KEY first")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("TM_MCP_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("TM_MCP_API_KEY not found in runtime/openmemory/.env")


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
        raise RuntimeError(f"Mem0 HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Mem0 unreachable: {e.reason}")


# ---------- MCP Server ----------

mcp = FastMCP("tigermemory")


@mcp.tool()
def write_inbox(agent: str, topic: str, title: str, body: str) -> dict[str, Any]:
    """Create inbox/YYYY-MM-DD-HHMM-<agent>-<topic>.md and commit-push atomically.
    
    Args:
        agent: Agent name (claude-code, codex, openclaw, hermes, deerflow, human, mem0)
        topic: Topic name (brand, investment, operations, production, systems, person, cross)
        title: 1-80 char title (letters/digits/CJK/space/-/_)
        body: Markdown body content
    
    Returns:
        {"path": "inbox/...", "commit_sha": "...", "url": "https://github.com/..."}
    """
    if agent not in AGENTS:
        raise ValueError(f"invalid agent '{agent}' (allowed: {sorted(AGENTS)})")
    if topic not in TOPICS:
        raise ValueError(f"invalid topic '{topic}' (allowed: {sorted(TOPICS)})")
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff _\-]{1,80}", title):
        raise ValueError("title must be 1-80 chars: letters/digits/CJK/space/-/_")
    if not body.strip():
        raise ValueError("body required")

    date = now("%Y-%m-%d")
    stamp = now("%Y-%m-%d-%H%M")
    rel = f"inbox/{stamp}-{agent}-{topic}.md"
    path = REPO_ROOT / rel
    if path.exists():
        raise FileExistsError(f"file already exists: {rel}")

    content = (
        "---\n"
        f"owner: {agent}\n"
        "status: draft\n"
        f"updated: {date}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")

    try:
        sha = _commit_push([rel], f"[{agent}] create: {title}")
    except Exception:
        # Leave the file on disk would pollute the next commit. Remove it.
        try:
            path.unlink()
        except OSError:
            pass
        raise
    # Construct GitHub URL (assumes origin is GitHub)
    try:
        remote_url = run(["git", "config", "--get", "remote.origin.url"]).stdout.strip()
        # Convert git@github.com:user/repo.git to https://github.com/user/repo
        if remote_url.startswith("git@"):
            remote_url = remote_url.replace(":", "/").replace("git@", "https://").replace(".git", "")
        elif remote_url.startswith("https://"):
            remote_url = remote_url.replace(".git", "")
        url = f"{remote_url}/blob/master/{rel}"
    except Exception:
        url = ""

    return {"path": rel, "commit_sha": sha, "url": url}


@mcp.tool()
def propose_wiki_page(
    agent: str,
    partition: str,
    slug: str,
    frontmatter: str,
    body: str,
    action: str = "create",
) -> dict[str, Any]:
    """Write a wiki page if agent owns the partition, otherwise write to inbox.

    Args:
        agent: Agent name
        partition: Wiki partition (brand, investment, operations, production, systems, person)
        slug: Page filename without .md
        frontmatter: YAML frontmatter (without --- delimiters; do NOT include 'updated:')
        body: Markdown body content
        action: "create" (default; fails if page exists) or "update" (required to overwrite)

    Returns:
        On owner path:    {"path": "wiki/...", "committed": true, "commit_sha": "..."}
        On fallback path: {"path": "inbox/...", "committed": true, "commit_sha": "...",
                           "fallback_reason": "..."}
    """
    if agent not in AGENTS:
        raise ValueError(f"invalid agent '{agent}'")
    if partition not in PARTITION_OWNERS:
        raise ValueError(f"invalid partition '{partition}'")
    if not re.fullmatch(r"[a-z0-9\-]+", slug):
        raise ValueError("slug must be lowercase letters/digits/hyphens")
    if action not in {"create", "update"}:
        raise ValueError("action must be 'create' or 'update'")

    date = now("%Y-%m-%d")
    owners = PARTITION_OWNERS[partition]

    if agent not in owners:
        # Fallback: write + commit inbox proposal (atomically, not a dirty working tree).
        stamp = now("%Y-%m-%d-%H%M")
        inbox_rel = f"inbox/{stamp}-{agent}-{partition}.md"
        inbox_path = REPO_ROOT / inbox_rel
        if inbox_path.exists():
            raise FileExistsError(f"file already exists: {inbox_rel}")
        inbox_content = (
            "---\n"
            f"owner: {agent}\n"
            "status: draft\n"
            f"updated: {date}\n"
            "---\n\n"
            f"# Proposal: wiki/{partition}/{slug}.md\n\n"
            f"## Frontmatter\n\n```yaml\n{frontmatter}\n```\n\n"
            f"## Body\n\n{body}\n"
        )
        inbox_path.write_text(inbox_content, encoding="utf-8")
        try:
            sha = _commit_push(
                [inbox_rel],
                f"[{agent}] create: propose wiki/{partition}/{slug}.md",
            )
        except Exception:
            try:
                inbox_path.unlink()
            except OSError:
                pass
            raise
        return {
            "path": inbox_rel,
            "committed": True,
            "commit_sha": sha,
            "fallback_reason": (
                f"agent '{agent}' is not an owner of wiki/{partition}/ "
                f"(owners: {sorted(owners)})"
            ),
        }

    # Owner path.
    wiki_rel = f"wiki/{partition}/{slug}.md"
    wiki_path = REPO_ROOT / wiki_rel
    if wiki_path.exists() and action == "create":
        raise FileExistsError(
            f"{wiki_rel} already exists; pass action='update' to overwrite"
        )
    if not wiki_path.exists() and action == "update":
        raise FileNotFoundError(
            f"{wiki_rel} does not exist; pass action='create' (default) to create it"
        )

    # Strip any caller-supplied 'updated:' line to avoid duplicates.
    fm_clean = "\n".join(
        line for line in frontmatter.splitlines() if not re.match(r"^\s*updated\s*:", line)
    ).strip()
    wiki_content = (
        "---\n"
        f"{fm_clean}\n"
        f"updated: {date}\n"
        "---\n\n"
        f"{body}\n"
    )

    # Snapshot originals so we can roll back on git failure.
    prior_wiki = wiki_path.read_text(encoding="utf-8") if wiki_path.exists() else None
    wiki_path.write_text(wiki_content, encoding="utf-8")

    index_path = REPO_ROOT / f"wiki/{partition}/index.md"
    prior_index: str | None = None
    files_to_add = [wiki_rel]
    if index_path.exists():
        index_content = index_path.read_text(encoding="utf-8")
        if f"({slug}.md)" not in index_content:
            prior_index = index_content
            if not index_content.endswith("\n"):
                index_content += "\n"
            index_content += f"- [{slug}]({slug}.md)\n"
            index_path.write_text(index_content, encoding="utf-8")
            files_to_add.append(f"wiki/{partition}/index.md")

    try:
        sha = _commit_push(files_to_add, f"[{agent}] {action}: {wiki_rel}")
    except Exception:
        # Roll back disk changes so working tree stays clean.
        if prior_wiki is None:
            try:
                wiki_path.unlink()
            except OSError:
                pass
        else:
            wiki_path.write_text(prior_wiki, encoding="utf-8")
        if prior_index is not None:
            index_path.write_text(prior_index, encoding="utf-8")
        raise

    return {"path": wiki_rel, "committed": True, "commit_sha": sha}


@mcp.tool()
def search_memories(query: str, size: int = 5) -> dict[str, Any]:
    """Search Mem0 memories by query.

    Args:
        query: Search query text
        size: Number of results to return (default 5)

    Returns:
        Paginated Mem0 response: {"count": int, "next": ..., "previous": ..., "results": [...]}.
    """
    params = urllib.parse.urlencode(
        {"user_id": "tiger", "query": query, "page": 1, "size": size}
    )
    resp = _mem0_request(f"{_mem0_base()}/api/v1/memories/?{params}")
    return json.loads(resp)


@mcp.tool()
def write_memory(agent: str, topic: str, text: str) -> dict[str, Any]:
    """Write a memory to Mem0 with enforced metadata.
    
    Args:
        agent: Agent name
        topic: Topic name
        text: Memory text content
    
    Returns:
        {"id": "..."} or the full response from Mem0 API
    """
    if agent not in AGENTS:
        raise ValueError(f"invalid agent '{agent}'")
    if topic not in TOPICS:
        raise ValueError(f"invalid topic '{topic}'")
    if not text.strip():
        raise ValueError("text required")

    payload = json.dumps(
        {
            "user_id": "tiger",
            "text": text,
            "metadata": {"source": agent, "topic": topic},
        }
    ).encode("utf-8")
    resp = _mem0_request(f"{_mem0_base()}/api/v1/memories/", data=payload)
    return json.loads(resp)


@mcp.tool()
def read_page(path: str) -> str:
    """Read a wiki page or inbox file content.
    
    Args:
        path: Relative path from repo root (e.g., "wiki/systems/agent-write-toolkit.md")
    
    Returns:
        File content as string
    """
    full_path = REPO_ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"not found: {path}")
    if not full_path.is_file():
        raise ValueError(f"not a file: {path}")
    return full_path.read_text(encoding="utf-8")


@mcp.tool()
def list_partition(partition: str) -> list[str]:
    """List all page slugs in a wiki partition.
    
    Args:
        partition: Wiki partition name
    
    Returns:
        List of page slugs (filenames without .md)
    """
    if partition not in PARTITION_OWNERS:
        raise ValueError(f"invalid partition '{partition}'")
    partition_dir = REPO_ROOT / "wiki" / partition
    if not partition_dir.exists():
        return []
    slugs = []
    for f in partition_dir.glob("*.md"):
        if f.name != "index.md":
            slugs.append(f.stem)
    return sorted(slugs)


@mcp.tool()
def lint_page(path: str) -> dict[str, Any]:
    """Validate a wiki page against PAGE_FORMATS.md.
    
    Args:
        path: Relative path to the page
    
    Returns:
        {"ok": true, "errors": []} on success
        {"ok": false, "errors": ["error1", "error2"]} on failure
    """
    full_path = REPO_ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"not found: {path}")

    text = full_path.read_text(encoding="utf-8")
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

    return {"ok": len(errors) == 0, "errors": errors}


@mcp.tool()
def lint_repo() -> dict[str, Any]:
    """Scan entire repository for governance issues.
    
    Returns:
        {
            "orphan_pages": ["wiki/..."],
            "stale_drafts": ["inbox/..."],
            "missing_sources": ["wiki/..."],
            "partition_mismatches": ["wiki/..."]
        }
    """
    orphan_pages: list[str] = []
    stale_drafts: list[str] = []
    missing_sources: list[str] = []
    partition_mismatches: list[str] = []

    # Check for orphan pages (not linked from index)
    for partition in PARTITION_OWNERS.keys():
        partition_dir = REPO_ROOT / "wiki" / partition
        index_path = partition_dir / "index.md"
        if not index_path.exists():
            continue
        
        index_content = index_path.read_text(encoding="utf-8")
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            if page_file.stem not in index_content:
                orphan_pages.append(f"wiki/{partition}/{page_file.name}")

    # Check for stale inbox drafts (>7 days old)
    seven_days_ago = datetime.datetime.now(TZ_CN) - datetime.timedelta(days=7)
    for inbox_file in (REPO_ROOT / "inbox").glob("*.md"):
        if inbox_file.name == ".gitkeep":
            continue
        try:
            stat = inbox_file.stat()
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=TZ_CN)
            if mtime < seven_days_ago:
                stale_drafts.append(f"inbox/{inbox_file.name}")
        except Exception:
            pass

    # Check for wiki pages without 来源 section
    for partition in PARTITION_OWNERS.keys():
        partition_dir = REPO_ROOT / "wiki" / partition
        if not partition_dir.exists():
            continue
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            content = page_file.read_text(encoding="utf-8")
            if "## 来源" not in content:
                missing_sources.append(f"wiki/{partition}/{page_file.name}")

    # Check for partition ownership mismatches
    for partition in PARTITION_OWNERS.keys():
        partition_dir = REPO_ROOT / "wiki" / partition
        if not partition_dir.exists():
            continue
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            content = page_file.read_text(encoding="utf-8")
            m = re.search(r"^owner:\s*(\S+)", content, re.MULTILINE)
            if m:
                owner = m.group(1)
                if owner not in PARTITION_OWNERS[partition] and owner != "human":
                    partition_mismatches.append(f"wiki/{partition}/{page_file.name} (owner: {owner})")

    return {
        "orphan_pages": orphan_pages,
        "stale_drafts": stale_drafts,
        "missing_sources": missing_sources,
        "partition_mismatches": partition_mismatches,
    }


# ---------- Entry point ----------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stdio", action="store_true", default=True, help="Use stdio transport (default)")
    ap.add_argument("--http", action="store_true", help="Use HTTP transport")
    ap.add_argument("--host", default="0.0.0.0", help="HTTP host (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=9766, help="HTTP port (default: 9766)")
    args = ap.parse_args()

    if args.http:
        # HTTP mode: require API key to be present in env (rudimentary
        # deployment check; real Bearer enforcement is TODO — see
        # deploy/mcp/README.md "HTTP security").
        try:
            _mcp_api_key()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        # Bind to requested host/port. FastMCP reads from .settings at run().
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
