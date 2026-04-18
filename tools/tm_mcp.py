#!/usr/bin/env python3
"""
tools/tm_mcp.py — tigermemory MCP server (thin adapter over tm_core).

Exposes 8 tools for remote agents (laptop MCP clients):
- write_inbox
- propose_wiki_page
- search_memories
- write_memory
- read_page
- list_partition
- lint_page
- lint_repo

All rule enforcement and side effects live in tm_core.py. This module only
handles MCP tool decoration, HTTP transport (Bearer auth + DNS rebinding
protection), and exception→JSON mapping.

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
import re
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import tm_core


# ---------- MCP Server ----------

# Allowed Host header values for HTTP transport. Defaults cover WSL loopback
# plus the tigermemory-wsl Tailscale node (clients connect directly, no host
# portproxy). Override via TM_MCP_ALLOWED_HOSTS (comma-separated) for other
# topologies. DNS rebinding protection stays on.
_DEFAULT_ALLOWED_HOSTS = [
    "localhost", "localhost:*",
    "127.0.0.1", "127.0.0.1:*",
    # WSL's own Tailscale identity (direct peer, no portproxy).
    "tigermemory-wsl", "tigermemory-wsl:*",
    "100.113.108.21", "100.113.108.21:*",
]
_allowed_hosts_env = os.environ.get("TM_MCP_ALLOWED_HOSTS", "").strip()
_allowed_hosts = (
    [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]
    if _allowed_hosts_env
    else _DEFAULT_ALLOWED_HOSTS
)

mcp = FastMCP(
    "tigermemory",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        # allowed_origins empty = allow any Origin (no browser-side CORS lock)
    ),
)


# ---------- Tools ----------

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
    rel, sha = tm_core.write_and_commit_inbox(agent, topic, title, body)
    return {
        "path": rel,
        "commit_sha": sha,
        "url": tm_core.git_remote_blob_url(rel),
    }


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
    tm_core.validate_agent(agent)
    tm_core.validate_partition(partition)
    tm_core.validate_slug(slug)
    if action not in {"create", "update"}:
        raise ValueError("action must be 'create' or 'update'")

    # L2 content review. Hard-block if score < 30; otherwise continue with
    # existing owner/fallback flow. Score info is returned to caller so the
    # agent sees issues/suggestions.
    import tm_review
    review = tm_review.review_draft(body)
    commit_suffix = " [unreviewed]" if review.get("review_skipped") else ""
    if review.get("score") is not None and review["score"] < 30:
        # Hard block: force draft into inbox with review result embedded,
        # regardless of whether agent owns the partition.
        stamp = tm_core.now("%Y-%m-%d-%H%M")
        date = tm_core.now("%Y-%m-%d")
        inbox_rel = f"inbox/{stamp}-{agent}-{partition}.md"
        inbox_path = tm_core.REPO_ROOT / inbox_rel
        if inbox_path.exists():
            raise FileExistsError(f"file already exists: {inbox_rel}")
        issues_md = "\n".join(f"- {i}" for i in review.get("issues", [])) or "- (none)"
        suggestions_md = "\n".join(f"- {s}" for s in review.get("suggestions", [])) or "- (none)"
        inbox_content = (
            "---\n"
            f"owner: {agent}\n"
            "status: draft\n"
            f"updated: {date}\n"
            "---\n\n"
            f"# L2-blocked draft: wiki/{partition}/{slug}.md\n\n"
            f"## L2 review (score {review['score']}/100)\n\n"
            f"### Issues\n\n{issues_md}\n\n"
            f"### Suggestions\n\n{suggestions_md}\n\n"
            f"## Original frontmatter\n\n```yaml\n{frontmatter}\n```\n\n"
            f"## Original body\n\n{body}\n"
        )
        inbox_path.write_text(inbox_content, encoding="utf-8")
        try:
            sha = tm_core.git_commit_push(
                [inbox_rel],
                f"[{agent}] create: L2-block propose wiki/{partition}/{slug}.md",
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
            "fallback_reason": f"L2 review score {review['score']} < 30",
            "review": review,
        }

    owners = tm_core.PARTITION_OWNERS[partition]

    # Fallback path: non-owner → inbox proposal (committed atomically).
    if agent not in owners:
        stamp = tm_core.now("%Y-%m-%d-%H%M")
        date = tm_core.now("%Y-%m-%d")
        inbox_rel = f"inbox/{stamp}-{agent}-{partition}.md"
        inbox_path = tm_core.REPO_ROOT / inbox_rel
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
            sha = tm_core.git_commit_push(
                [inbox_rel],
                f"[{agent}] create: propose wiki/{partition}/{slug}.md" + commit_suffix,
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
            "review": review,
        }

    # Owner path.
    wiki_rel = f"wiki/{partition}/{slug}.md"
    wiki_path = tm_core.REPO_ROOT / wiki_rel
    if wiki_path.exists() and action == "create":
        raise FileExistsError(
            f"{wiki_rel} already exists; pass action='update' to overwrite"
        )
    if not wiki_path.exists() and action == "update":
        raise FileNotFoundError(
            f"{wiki_rel} does not exist; pass action='create' (default) to create it"
        )

    wiki_content = tm_core.render_wiki_body(frontmatter, body)

    # Snapshot originals so we can roll back on git failure.
    prior_wiki = wiki_path.read_text(encoding="utf-8") if wiki_path.exists() else None
    wiki_path.write_text(wiki_content, encoding="utf-8")

    index_path = tm_core.REPO_ROOT / f"wiki/{partition}/index.md"
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
        sha = tm_core.git_commit_push(files_to_add, f"[{agent}] {action}: {wiki_rel}" + commit_suffix)
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

    return {"path": wiki_rel, "committed": True, "commit_sha": sha, "review": review}


@mcp.tool()
def search_memories(query: str, size: int = 5) -> dict[str, Any]:
    """Search Mem0 memories by query.

    Args:
        query: Search query text
        size: Number of results to return (default 5)

    Returns:
        Paginated Mem0 response: {"count": int, "next": ..., "previous": ..., "results": [...]}
    """
    return json.loads(tm_core.mem0_search(query, size))


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
    return json.loads(tm_core.mem0_write(agent, topic, text))


@mcp.tool()
def read_page(path: str) -> str:
    """Read a wiki page or inbox file content.

    Args:
        path: Relative path from repo root (e.g., "wiki/systems/agent-write-toolkit.md")

    Returns:
        File content as string
    """
    full_path = tm_core.REPO_ROOT / path
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
    tm_core.validate_partition(partition)
    partition_dir = tm_core.REPO_ROOT / "wiki" / partition
    if not partition_dir.exists():
        return []
    return sorted(f.stem for f in partition_dir.glob("*.md") if f.name != "index.md")


@mcp.tool()
def lint_page(path: str) -> dict[str, Any]:
    """Validate a wiki page against PAGE_FORMATS.md.

    Args:
        path: Relative path to the page

    Returns:
        {"ok": true, "errors": []} on success
        {"ok": false, "errors": ["error1", "error2"]} on failure
    """
    full_path = tm_core.REPO_ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"not found: {path}")

    errors = tm_core.lint_page_errors(full_path.read_text(encoding="utf-8"))
    return {"ok": len(errors) == 0, "errors": errors}


@mcp.tool()
def review_draft(body: str) -> dict[str, Any]:
    """Score a draft body for content quality (L2 pre-review).

    Agents can call this proactively before `propose_wiki_page` to check if
    their draft is worth submitting. Uses DeepSeek API; fails open on error.

    Args:
        body: Markdown body to review

    Returns:
        {"score": int 0-100 | None, "issues": [str], "suggestions": [str],
         "ready_for_compile": bool, "review_skipped": bool,
         "reason": str (only if review_skipped)}
    """
    import tm_review
    return tm_review.review_draft(body)


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

    # Orphan pages (not linked from index)
    for partition in tm_core.PARTITION_OWNERS.keys():
        partition_dir = tm_core.REPO_ROOT / "wiki" / partition
        index_path = partition_dir / "index.md"
        if not index_path.exists():
            continue
        index_content = index_path.read_text(encoding="utf-8")
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            if page_file.stem not in index_content:
                orphan_pages.append(f"wiki/{partition}/{page_file.name}")

    # Stale inbox drafts (>7 days old by mtime)
    seven_days_ago = datetime.datetime.now(tm_core.TZ_CN) - datetime.timedelta(days=7)
    inbox_dir = tm_core.REPO_ROOT / "inbox"
    if inbox_dir.exists():
        for inbox_file in inbox_dir.glob("*.md"):
            if inbox_file.name == ".gitkeep":
                continue
            try:
                mtime = datetime.datetime.fromtimestamp(
                    inbox_file.stat().st_mtime, tz=tm_core.TZ_CN
                )
                if mtime < seven_days_ago:
                    stale_drafts.append(f"inbox/{inbox_file.name}")
            except Exception:
                pass

    # Wiki pages without '## 来源' section + owner/partition mismatch
    for partition in tm_core.PARTITION_OWNERS.keys():
        partition_dir = tm_core.REPO_ROOT / "wiki" / partition
        if not partition_dir.exists():
            continue
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            content = page_file.read_text(encoding="utf-8")
            if "## 来源" not in content:
                missing_sources.append(f"wiki/{partition}/{page_file.name}")
            m = re.search(r"^owner:\s*(\S+)", content, re.MULTILINE)
            if m:
                owner = m.group(1)
                if owner not in tm_core.PARTITION_OWNERS[partition] and owner != "human":
                    partition_mismatches.append(
                        f"wiki/{partition}/{page_file.name} (owner: {owner})"
                    )

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
        # HTTP mode: load API key and wrap FastMCP's Starlette app with a
        # simple Bearer middleware. We bypass FastMCP's OAuth/AuthSettings
        # path and enforce a single shared token matching TM_MCP_API_KEY.
        try:
            expected_key = tm_core.mcp_api_key()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        import uvicorn
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        class BearerAuth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if request.url.path == "/healthz":
                    return JSONResponse({"ok": True})
                auth = request.headers.get("authorization", "")
                if not auth.startswith("Bearer "):
                    return JSONResponse({"error": "missing Bearer token"}, status_code=401)
                if auth[7:].strip() != expected_key:
                    return JSONResponse({"error": "invalid token"}, status_code=403)
                return await call_next(request)

        app = mcp.streamable_http_app()
        app.add_middleware(BearerAuth)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        mcp.run()
