#!/usr/bin/env python3
"""
tools/tm_mcp.py — tigermemory MCP server (thin adapter over tm_core).

Exposes 24 tools for remote agents (laptop MCP clients):
- check_worktree
- close_session
- write_inbox
- propose_wiki_page
- search_memories
- write_memory
- read_page
- list_partition
- get_agent_onboarding
- lint_page
- ipfb_copywriting
- review_draft
- lint_repo
- list_pending_digests   (P6.3)
- review_digest          (P6.3)
- approve_fact           (P6.3)
- mark_digest_reviewed   (P6.3)
- minimax_vision         (MiniMax CLI)
- minimax_video          (MiniMax CLI)
- minimax_speech         (MiniMax CLI)
- minimax_music          (MiniMax CLI)
- minimax_image          (MiniMax CLI)
- minimax_search         (MiniMax CLI)
- minimax_quota          (MiniMax CLI)

All rule enforcement and side effects live in tm_core.py. This module only
handles MCP tool decoration, HTTP transport (Bearer auth + DNS rebinding
protection), and exception→JSON mapping.

Usage:
  python tools/tm_mcp.py --stdio                    # default for local clients
  python tools/tm_mcp.py --stdio --role=reader      # read-only (DeerFlow / untrusted)
  python tools/tm_mcp.py --http --host 0.0.0.0 --port 9766

HTTP mode requires TM_MCP_API_KEY in runtime/openmemory/.env.
Role controls write tools: 'writer' (default) can call all tools; 'reader' can call read-only tools.
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
import tm_minimax
import tm_persona
import tm_review_tools


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
    # Cloudflare Tunnel public hostnames (phone / remote clients).
    "tm.doodiu.cloud", "tm-api.doodiu.cloud",
]
_allowed_hosts_env = os.environ.get("TM_MCP_ALLOWED_HOSTS", "").strip()
_allowed_hosts = (
    [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]
    if _allowed_hosts_env
    else _DEFAULT_ALLOWED_HOSTS
)

# Role-based access control (P2-11). Default 'writer' can call all tools;
# 'reader' is blocked by _require_writer() on write tools.
_ROLE: str = "writer"

mcp = FastMCP(
    "tigermemory",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        # allowed_origins empty = allow any Origin (no browser-side CORS lock)
    ),
)


def _require_writer() -> None:
    """Raise PermissionError if current role is not 'writer'."""
    if _ROLE != "writer":
        raise PermissionError(f"write tool not allowed for role={_ROLE}")


def _review_for_memory(text: str) -> dict[str, Any]:
    import tm_review
    return tm_review.review_draft(text)


def _review_metadata(review: dict[str, Any], route: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"llm_review_route": route}
    if review.get("review_skipped"):
        metadata["llm_review_skipped"] = True
        if review.get("reason"):
            metadata["llm_review_reason"] = str(review["reason"])[:200]
        return metadata
    if review.get("score") is not None:
        metadata["llm_review_score"] = review["score"]
        metadata["llm_ready_for_compile"] = bool(review.get("ready_for_compile"))
    return metadata


# ---------- Tools ----------

@mcp.tool()
def check_worktree() -> dict[str, Any]:
    """Return a read-only git/worktree preflight snapshot.

    Agents should call this before editing files. The result is based on the
    same kernel as `tm_io.py status/preflight` and includes dirty counts,
    ahead/behind, unmerged paths, and hook installation state.

    Returns:
        {
            "ok": bool,
            "phase": "start",
            "status": {...},
            "blockers": [str],
            "recommended_action": str
        }
    """
    status = tm_core.git_session_status()
    blockers = list(status["blockers"])
    if blockers:
        action = "Report blockers and do not edit files until the owner/human resolves them."
    else:
        action = "Safe to start work."
    return {
        "ok": not blockers,
        "phase": "start",
        "status": status,
        "blockers": blockers,
        "recommended_action": action,
    }


@mcp.tool()
def close_session() -> dict[str, Any]:
    """Return whether this agent session is safe to close.

    This tool is intentionally read-only: it does not commit, stash, pull,
    push, or repair anything. It only reports blockers so agents cannot claim
    completion while local changes, unpushed commits, or branch drift remain.

    Returns:
        {
            "ok": bool,
            "phase": "close",
            "status": {...},
            "blockers": [str],
            "recommended_action": str
        }
    """
    status = tm_core.git_session_status()
    blockers = list(status["blockers"])
    if blockers:
        action = (
            "Do not close the session. Commit/push owned changes or report "
            "unowned blockers explicitly."
        )
    else:
        action = "Safe to close session."
    return {
        "ok": not blockers,
        "phase": "close",
        "status": status,
        "blockers": blockers,
        "recommended_action": action,
    }


@mcp.tool()
def write_inbox(agent: str, topic: str, title: str, body: str, reason: str) -> dict[str, Any]:
    _require_writer()
    """Explicit human-review channel. Always writes to inbox with routed_by signature.

    Args:
        agent: Agent name (claude-code, codex, openclaw, hermes, deerflow, human, mem0, kimi)
        topic: Topic name (brand, investment, operations, production, systems, person, cross)
        title: 1-80 char title (letters/digits/CJK/space/-/_)
        body: Markdown body content
        reason: Why this content needs human review (recorded in frontmatter)

    Returns:
        {"path": "inbox/...", "commit_sha": "...", "url": "https://github.com/..."}
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required for write_inbox")
    fm_extra = {
        "routed_by": "tigermemory",
        "route_decision_reason": reason.strip()[:200],
    }
    rel, sha = tm_core.write_and_commit_inbox(agent, topic, title, body, frontmatter_extra=fm_extra)
    return {
        "path": rel,
        "commit_sha": sha,
        "url": tm_core.git_remote_blob_url(rel),
        "memory_route": "inbox",
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
    _require_writer()
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
        # Inbox filename uses topic key (no hyphens), not partition name.
        topic_key = partition.replace("-", "")
        inbox_rel = f"inbox/{stamp}-{agent}-{topic_key}.md"
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
            "routed_by: tigermemory\n"
            f"route_decision_reason: L2 review score {review['score']} < 30\n"
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
        # Inbox filename uses topic key (no hyphens), not partition name.
        topic_key = partition.replace("-", "")
        inbox_rel = f"inbox/{stamp}-{agent}-{topic_key}.md"
        inbox_path = tm_core.REPO_ROOT / inbox_rel
        if inbox_path.exists():
            raise FileExistsError(f"file already exists: {inbox_rel}")
        inbox_content = (
            "---\n"
            f"owner: {agent}\n"
            "status: draft\n"
            f"updated: {date}\n"
            "routed_by: tigermemory\n"
            f"route_decision_reason: agent {agent} not owner of wiki/{partition}\n"
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
    """[检索 Mem0 事件记忆] Search Mem0 (atomic event-style memories: "X 部署了" "Y 工具不适合审稿").

    Mem0 只存事件型原子记忆，**不存长文**（品牌指南 / IPFB 文案历史 / 系统架构文档 / 个人档案在 wiki/sources 里）。
    **"过去写过什么 / 之前的决策 / 历史文案" 类问题必须同时调 `search_wiki`**，否则只看到一半。
    Web 搜索用 `minimax_search`。

    Args:
        query: Search query text
        size: Number of results to return (default 5)

    Returns:
        Paginated Mem0 response: {"count": int, "next": ..., "previous": ..., "results": [...]}
    """
    return json.loads(tm_core.mem0_search(query, size))


@mcp.tool()
def search_wiki(
    query: str,
    size: int = 5,
    include_sources: bool = True,
    include_inbox: bool = False,
) -> list[dict[str, Any]]:
    """[检索 Wiki / sources 长文知识] File-based search over wiki/ and sources/ markdown/text.

    与 `search_memories` 互补：Mem0 存事件原子记忆，wiki/sources 存长文知识
    （品牌指南、IPFB 历史文案、系统架构、个人档案、研究报告）。
    **"回忆 / 历史 / 过去的 / 之前写过什么" 类问题：两个 search 工具都要调**。

    Args:
        query: 空格分隔 token；每个 token 都必须命中（AND 语义）。CJK 直接子串匹配。
        size: 返回条数（默认 5）。
        include_sources: 是否扫 sources/（IPFB 历史文案等原始材料），默认 True。
        include_inbox: 是否扫 inbox/（未审核草稿），默认 False。

    Returns:
        按命中次数降序 [{path, score, title, snippet}, ...]。
        拿到 path 后用 `read_page` 读全文。
    """
    return tm_core.search_wiki(query, size, include_sources, include_inbox)


@mcp.tool()
def write_memory(agent: str, topic: str, text: str, force_inbox: bool = False) -> dict[str, Any]:
    _require_writer()
    """Single canonical entry for agent memory writes. Server-side LLM routes to mem0 / inbox / discard.

    Args:
        agent: Agent name
        topic: Topic name
        text: Memory text content
        force_inbox: If True, bypass routing and write directly to inbox (agent requests human review)

    Returns:
        {"route": "mem0", "id": "..."} or {"route": "inbox", "path": "...", "commit_sha": "..."}
        or {"route": "discard", "score": int, "issues": [...]}
    """
    import tm_route
    decision = tm_route.route_memory(text, topic, agent)
    if force_inbox:
        decision = tm_route.RouteDecision(
            route="inbox", score=decision.score, topic_inferred=decision.topic_inferred,
            issues=decision.issues, reasons=f"force_inbox override: {decision.reasons}",
            is_transient=decision.is_transient, is_sensitive=decision.is_sensitive,
            needs_human_review=decision.needs_human_review, unreviewed=decision.unreviewed,
        )

    if decision.route == "discard":
        return {
            "route": "discard",
            "score": decision.score,
            "issues": decision.issues,
            "reasons": decision.reasons,
        }

    if decision.route == "mem0":
        data = json.loads(tm_core.mem0_write(
            agent,
            decision.topic_inferred,
            text,
            metadata_extra=decision.as_metadata(),
        ))
        data["route"] = "mem0"
        data["score"] = decision.score
        data["topic_inferred"] = decision.topic_inferred
        data["reasons"] = decision.reasons
        return data

    # route == "inbox"
    fm_extra = decision.as_metadata()
    fm_extra["routed_by"] = "tigermemory"
    fm_extra["route_decision_reason"] = decision.reasons
    rel, sha = tm_core.write_and_commit_inbox(
        agent,
        decision.topic_inferred,
        f"Routed memory {decision.score}",
        text,
        frontmatter_extra=fm_extra,
    )
    return {
        "route": "inbox",
        "path": rel,
        "commit_sha": sha,
        "url": tm_core.git_remote_blob_url(rel),
        "score": decision.score,
        "topic_inferred": decision.topic_inferred,
        "reasons": decision.reasons,
        "unreviewed": decision.unreviewed,
    }


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
def get_agent_onboarding(depth: str = "5min") -> dict[str, Any]:
    """Return a deterministic tigermemory agent onboarding snapshot.

    Args:
        depth: "30s", "5min", or "full"

    Returns:
        {"depth": "...", "content": "...", "sources": [...]}.
    """
    content = tm_persona.compile_snapshot(depth)
    return {
        "depth": depth,
        "content": content,
        "sources": list(tm_persona.SOURCE_PATHS),
    }


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
def ipfb_copywriting(
    task_type: str = "daily_product",
    channel: str = "wechat",
    wave: str | None = None,
    product: str | None = None,
    history_query: str | None = None,
    history_limit: int = 5,
    excerpt_chars: int = 2500,
) -> dict[str, Any]:
    """Return the IPFB copywriting capability bundle.

    This is the MCP-tool version of `wiki/brand/ipfb-copywriting-skill.md`.
    It does not generate final copy by itself; it returns the required
    writing rules, source excerpts, read order, hard constraints, checklist
    contract, and optional historical examples for the caller to draft from.

    Args:
        task_type: daily_product, series_campaign, holiday, preorder, celebrity
        channel: wechat, xiaohongshu, poster, preorder, etc.
        wave: Optional campaign/wave name, e.g. 觉知半夏, 都市游牧
        product: Optional product/category keyword, e.g. 衬衫, 连衣裙
        history_query: Optional keyword search for historical accepted/rejected copy
        history_limit: Number of historical examples to return
        excerpt_chars: Max chars per source excerpt

    Returns:
        Structured IPFB copywriting context and examples.
    """
    return tm_core.ipfb_copywriting_context(
        task_type=task_type,
        channel=channel,
        wave=wave,
        product=product,
        history_query=history_query,
        history_limit=history_limit,
        excerpt_chars=excerpt_chars,
    )


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

    # Orphan pages (not linked from index). Linter-owned dashboards are
    # exempt — they're auto-generated and intentionally not listed in index.
    for partition in tm_core.PARTITION_OWNERS.keys():
        partition_dir = tm_core.REPO_ROOT / "wiki" / partition
        index_path = partition_dir / "index.md"
        if not index_path.exists():
            continue
        index_content = index_path.read_text(encoding="utf-8")
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            rel = f"wiki/{partition}/{page_file.name}"
            if rel in tm_core.LINTER_DASHBOARDS:
                continue
            if page_file.stem not in index_content:
                orphan_pages.append(rel)

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

    # Wiki pages without '## 来源' section + owner/partition mismatch.
    # Linter-owned dashboards are exempt from both checks — they're auto-
    # generated (no human-authored sources) and owner:linter is by design.
    for partition in tm_core.PARTITION_OWNERS.keys():
        partition_dir = tm_core.REPO_ROOT / "wiki" / partition
        if not partition_dir.exists():
            continue
        for page_file in partition_dir.glob("*.md"):
            if page_file.name == "index.md":
                continue
            rel = f"wiki/{partition}/{page_file.name}"
            if rel in tm_core.LINTER_DASHBOARDS:
                continue
            content = page_file.read_text(encoding="utf-8")
            if "## 来源" not in content:
                missing_sources.append(rel)
            m = re.search(r"^owner:\s*(\S+)", content, re.MULTILINE)
            if m:
                owner = m.group(1)
                if owner == "linter":
                    continue
                if owner not in tm_core.PARTITION_OWNERS[partition] and owner != "human":
                    partition_mismatches.append(f"{rel} (owner: {owner})")

    return {
        "orphan_pages": orphan_pages,
        "stale_drafts": stale_drafts,
        "missing_sources": missing_sources,
        "partition_mismatches": partition_mismatches,
    }


# ---------- P6.3 Daily Digest Review Tools ----------

@mcp.tool()
def list_pending_digests() -> dict[str, Any]:
    """
    列出所有未审核的日报（inbox/daily/*.md 中 status != 'reviewed'）。
    返回: {"digests": [{"date": "2026-04-20", "path": "...", "fact_count": 7, "status": "pending"}, ...]}
    """
    try:
        digests = tm_review_tools.list_pending_digests()
        return {"ok": True, "digests": digests, "count": len(digests)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def review_digest(date: str) -> dict[str, Any]:
    """
    读取指定日期的日报，返回结构化 facts 清单供人审。
    参数: date = "2026-04-20"
    返回: {"ok": True, "date": "...", "facts": [{"id": "fact-001", "topic": "systems", "text": "...", "source_type": "mem0", "source_id": "uuid"}, ...]}
    """
    try:
        digest = tm_review_tools.load_digest(date)
        if not digest:
            return {"ok": False, "error": f"Digest not found for date: {date}"}
        return {
            "ok": True,
            "date": date,
            "facts": digest["facts"],
            "frontmatter": digest["frontmatter"],
            "fact_count": len(digest["facts"]),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def approve_fact(
    date: str,
    fact_id: str,
    action: str,  # "keep", "delete", "promote"
    promote_partition: str | None = None,
    promote_slug: str | None = None,
) -> dict[str, Any]:
    _require_writer()
    """
    对单条事实执行审核操作。
    - keep: 标记为已审核保留（记录在 review_log）
    - delete: Mem0 删记录 / inbox 文件 archive 到 archive/deleted/
    - promote: 转 wiki page（走 L2 review），需给 partition + slug
    返回: {"ok": true, "action": "...", "result": {...}}
    """
    try:
        # Load digest to find the fact
        digest = tm_review_tools.load_digest(date)
        if not digest:
            return {"ok": False, "error": f"Digest not found: {date}"}
        
        # Find fact by ID
        fact = None
        for f in digest["facts"]:
            if f.get("id") == fact_id:
                fact = f
                break
        if not fact:
            return {"ok": False, "error": f"Fact not found: {fact_id}"}
        
        # Execute action
        if action == "keep":
            result = {"fact_id": fact_id, "action": "keep", "ok": True}
        elif action == "delete":
            result = tm_review_tools.execute_delete(fact)
        elif action == "promote":
            if not promote_partition or not promote_slug:
                return {"ok": False, "error": "promote requires promote_partition and promote_slug"}
            result = tm_review_tools.execute_promote(fact, promote_partition, promote_slug)
        else:
            return {"ok": False, "error": f"Invalid action: {action} (must be keep/delete/promote)"}
        
        # Append to review log
        log_entry = {
            "fact_id": fact_id,
            "action": action,
            "result": result,
        }
        if action == "promote":
            log_entry["promoted_to"] = f"wiki/{promote_partition}/{promote_slug}.md"
        
        tm_review_tools.append_review_log(date, log_entry)
        
        return {"ok": True, "action": action, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def mark_digest_reviewed(date: str) -> dict[str, Any]:
    _require_writer()
    """
    日报全部 fact 处理完后，把日报 frontmatter status 改为 'reviewed'，并 commit 到 git。
    返回: {"ok": true, "committed": true, "commit_sha": "..."}
    """
    try:
        # Update status
        updated = tm_review_tools.save_digest_with_log(date, {"status": "reviewed"})
        if not updated:
            return {"ok": False, "error": f"Failed to update digest: {date}"}
        
        # Commit
        repo_root = tm_core.REPO_ROOT
        digest_path = f"inbox/daily/{date}.md"
        sha = tm_core.git_commit_push([digest_path], f"[human] update: mark {date} digest as reviewed")
        
        return {"ok": True, "committed": True, "commit_sha": sha}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- MiniMax CLI Tools ----------

@mcp.tool()
def minimax_vision(
    image: str,
    prompt: str = "Describe the image in detail.",
    timeout: int = 120,
) -> dict[str, Any]:
    """[图片理解/视觉 VLM] Describe an image using MiniMax VLM.

    Use for: reading/understanding image content. Accepts local path or URL.

    Args:
        image: Local file path or URL to the image.
        prompt: Question or instruction about the image.
        timeout: Request timeout in seconds (default 120).

    Returns:
        {"ok": true, "description": "...", "image": "...", "prompt": "..."}
    """
    return tm_minimax.vision_describe(image, prompt, timeout)


@mcp.tool()
def minimax_video(
    prompt: str,
    image: str | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """[视频生成] Generate a video with MiniMax Hailuo 2.3.

    Args:
        prompt: Text description for the video.
        image: Optional reference image (path or URL).
        timeout: Request timeout in seconds (default 600).

    Returns:
        mmx JSON response with task_id or download info.
    """
    _require_writer()
    return tm_minimax.video_generate(prompt, image, timeout)


@mcp.tool()
def minimax_speech(
    text: str,
    voice: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """[语音合成/TTS] Synthesize speech from text using MiniMax Speech 2.8.

    Args:
        text: Text to convert to speech.
        voice: Optional voice ID.
        timeout: Request timeout in seconds (default 120).

    Returns:
        mmx JSON response with output file path or audio data.
    """
    _require_writer()
    return tm_minimax.speech_synthesize(text, voice, timeout)


@mcp.tool()
def minimax_music(
    prompt: str,
    timeout: int = 300,
) -> dict[str, Any]:
    """[音乐生成] Generate music using MiniMax Music 2.6.

    Args:
        prompt: Description of the music to generate.
        timeout: Request timeout in seconds (default 300).

    Returns:
        mmx JSON response with output info.
    """
    _require_writer()
    return tm_minimax.music_generate(prompt, timeout)


@mcp.tool()
def minimax_image(
    prompt: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """[图片生成] Generate an image using MiniMax image-01.

    Args:
        prompt: Description of the image to generate.
        timeout: Request timeout in seconds (default 120).

    Returns:
        mmx JSON response with output info.
    """
    _require_writer()
    return tm_minimax.image_generate(prompt, timeout)


@mcp.tool()
def minimax_search(
    query: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """[联网搜索] Web search via MiniMax search API. Not for Mem0 memory search — use search_memories for that.

    Args:
        query: Search query text.
        timeout: Request timeout in seconds (default 30).

    Returns:
        mmx JSON response with search results.
    """
    return tm_minimax.search_query(query, timeout)


@mcp.tool()
def minimax_quota() -> dict[str, Any]:
    """[MiniMax 配额查询] Show current MiniMax Token Plan quota usage.

    Returns:
        {"ok": true, "raw": "<quota table text>"}
    """
    return tm_minimax.quota_show()


# ---------- Entry point ----------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stdio", action="store_true", default=True, help="Use stdio transport (default)")
    ap.add_argument("--http", action="store_true", help="Use HTTP transport")
    ap.add_argument("--host", default="0.0.0.0", help="HTTP host (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=9766, help="HTTP port (default: 9766)")
    ap.add_argument("--role", choices=["writer", "reader"], default="writer",
                    help="Access role: writer (default, all tools) or reader (read-only tools)")
    args = ap.parse_args()

    # Set global role before MCP starts.
    _ROLE = args.role

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
