#!/usr/bin/env python3
"""
tools/tm_mcp.py — tigermemory MCP server (thin adapter over tm_core).

Exposes 26 tools for remote agents (laptop MCP clients):

Read tools (callable in both writer and reader roles):
- check_worktree            — git/worktree preflight snapshot
- agent_doctor              — combined agent connect / health diagnostics
- retention_audit           — read-only Mem0 retention dry-run audit
- close_session             — session-close blocker check
- search_memories           — Mem0 atomic event memory search
- search_wiki               — wiki/sources file-based search
- search_tigermemory        — grouped search across wiki/lessons/onboarding/Mem0
- memory_answer             — evidence-first answer with citations and trace
- read_page                 — read wiki/inbox file content
- list_partition            — list slugs in a wiki partition
- get_agent_onboarding      — onboarding snapshot
- ipfb_copywriting          — IPFB copywriting capability bundle (read-mostly)
- lint_repo                 — repo-wide or single-page lint
- review_digest             — daily digest review (list/read/mark-reviewed)
- minimax_quota             — MiniMax token-plan quota query
- minimax_vision            — image VLM understanding (external)
- minimax_search            — web search (external)
- expense_record            — record expense/income entry (private SQLite) [v1 alias]
- expense_query             — query/aggregate expense entries (private SQLite) [v1 alias]
- expense_write             — unified write: record/update/delete/batch_record (private SQLite)
- expense_read              — unified read: list/aggregate/trend/sql (private SQLite)
- start_deep_dive           — start TradingAgents single-stock deep-dive job
- get_deep_dive_status      — poll TradingAgents deep-dive job state
- fetch_deep_dive_result    — fetch completed TradingAgents deep-dive JSON
- start_stability_eval      — run N TradingAgents deep dives and write a consensus label

Write tools (writer role only):
- propose_wiki_page         — wiki page draft with L2 review + inbox fallback
- write_sources             — sources/<subdir>/<slug>.md ingest with provenance frontmatter
- write_memory              — single canonical memory write (LLM-routed)
- approve_fact              — daily-digest fact approval
- minimax_image / video / speech / music — generative media (external, writer-gated)

All rule enforcement and side effects live in tm_core.py. This module only
handles MCP tool decoration, HTTP transport (Bearer auth + DNS rebinding
protection), and exception→JSON mapping.

Usage:
  python tools/tm_mcp.py --stdio                    # default for local clients
  python tools/tm_mcp.py --stdio --role=reader      # read-only (DeerFlow / untrusted)
  python tools/tm_mcp.py --http --host 0.0.0.0 --port 9766

HTTP mode requires TM_MCP_API_KEY in runtime/openmemory/.env.
Role controls write tools: 'writer' (default) can call all tools; 'reader' can call read-only tools.

Note: OpenClaw 5.2's `tigermemory-ce` plugin uses HTTP endpoints in
`tools/tm_http.py` (port 8790), NOT this MCP server. See
`wiki/systems/tigermemory-agent-access.md` for the full integration map.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import tm_core
import tm_answer
import tm_memory_ops
import tm_minimax
import tm_persona
import tm_review_tools
import tm_expense
import tm_search
import tm_deep_dive_jobs
import tm_stability_eval
import tm_agent_doctor
import tm_retention_audit


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
_SEARCH_DOGFOOD_LOG = tm_core.REPO_ROOT / ".tmp" / "search-tigermemory.jsonl"

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
def agent_doctor(
    query: str = tm_agent_doctor.DEFAULT_QUERY,
    include_l2: bool = True,
    http_url: str | None = None,
) -> dict[str, Any]:
    """Read-only agent connect / doctor checks for Codex, Cascade, web agents, and OpenClaw.

    Combines worktree/preflight state, tm-http health, Mem0 reachability, L2
    review reachability, and lessons-hit evidence into one diagnostic response.
    """
    return tm_agent_doctor.run_agent_doctor(query=query, include_l2=include_l2, http_url=http_url)


@mcp.tool()
def retention_audit(max_items: int = 200, page_size: int = 100) -> dict[str, Any]:
    """Read-only Mem0 retention dry-run audit.

    Returns scored candidates and reasons. It never deletes or updates records.
    """
    return tm_retention_audit.run_retention_audit(max_items=max_items, page_size=page_size)


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
        result = {
            "path": inbox_rel,
            "committed": True,
            "commit_sha": sha,
            "fallback_reason": f"L2 review score {review['score']} < 30",
            "review": review,
        }
        try:
            tm_memory_ops.schedule_digest_refresh()
            result["digest_refresh_scheduled"] = True
        except Exception as exc:
            result["warnings"] = [f"digest refresh scheduling failed: {exc}"]
        return result

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
        result = {
            "path": inbox_rel,
            "committed": True,
            "commit_sha": sha,
            "fallback_reason": (
                f"agent '{agent}' is not an owner of wiki/{partition}/ "
                f"(owners: {sorted(owners)})"
            ),
            "review": review,
        }
        try:
            tm_memory_ops.schedule_digest_refresh()
            result["digest_refresh_scheduled"] = True
        except Exception as exc:
            result["warnings"] = [f"digest refresh scheduling failed: {exc}"]
        return result

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

    result = {"path": wiki_rel, "committed": True, "commit_sha": sha, "review": review}
    try:
        result.update(
            tm_memory_ops.schedule_embed_refresh(
                scope="wiki",
                reason="propose_wiki_page",
                paths=files_to_add,
            )
        )
    except Exception as exc:
        result["warnings"] = [f"embed refresh scheduling failed: {exc}"]
    return result


@mcp.tool()
def write_sources(
    agent: str,
    subdir: str,
    slug: str,
    source_url: str,
    fetched_by: str,
    body: str,
    page_title: str = "",
    status: str = "ok",
    failure_reason: str = "",
    action: str = "create",
) -> dict[str, Any]:
    _require_writer()
    """Write a sources/<subdir>/<slug>.md file with provenance frontmatter.

    Use this when ingesting external materials (scraped official docs, PDF
    extracts, Mem0 dumps, etc.). The commit-guard requires every sources/
    file to carry source_url + fetched_at + fetched_by; this tool fills them
    automatically (fetched_at = server-side now, Asia/Shanghai).

    Args:
        agent: Agent name (validated against AGENTS set)
        subdir: Topic subdir under sources/ (e.g. "huawei-celia"). Lower-case
            letters / digits / hyphens. Required, no flat sources/*.md.
        slug: Filename without .md (e.g. "01-openclaw-mode"). Same charset.
        source_url: Origin URL (must start with http:// or https://)
        fetched_by: Tool/agent identifier (e.g. "openclaw-via-playwright")
        body: Markdown body content (without frontmatter)
        page_title: Optional, original page <title> or H1
        status: "ok" (default) | "partial" | "failed"
        failure_reason: Required if status != "ok"
        action: "create" (default; fails if exists) | "update" (overwrite)

    Returns:
        {"path": "sources/...", "committed": true, "commit_sha": "..."}
    """
    tm_core.validate_agent(agent)
    if not re.fullmatch(r"[a-z0-9\-]+", subdir):
        raise ValueError(f"invalid subdir '{subdir}' (lowercase letters/digits/hyphens)")
    if not re.fullmatch(r"[a-z0-9\-]+", slug):
        raise ValueError(f"invalid slug '{slug}' (lowercase letters/digits/hyphens)")
    if not (source_url.startswith("http://") or source_url.startswith("https://")):
        raise ValueError(f"source_url must start with http(s)://, got: {source_url!r}")
    if not fetched_by.strip():
        raise ValueError("fetched_by is required (non-empty)")
    if action not in {"create", "update"}:
        raise ValueError("action must be 'create' or 'update'")
    if status not in {"ok", "partial", "failed"}:
        raise ValueError("status must be 'ok', 'partial', or 'failed'")
    if status != "ok" and not failure_reason.strip():
        raise ValueError("failure_reason required when status != 'ok'")

    rel = f"sources/{subdir}/{slug}.md"
    full_path = tm_core.REPO_ROOT / rel
    if full_path.exists() and action == "create":
        raise FileExistsError(f"{rel} already exists; pass action='update' to overwrite")
    if not full_path.exists() and action == "update":
        raise FileNotFoundError(f"{rel} does not exist; pass action='create' (default)")

    fetched_at = datetime.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds")
    fm_lines = [
        f"source_url: {source_url}",
        f"fetched_at: {fetched_at}",
        f"fetched_by: {fetched_by}",
    ]
    if page_title:
        fm_lines.append(f"page_title: {page_title}")
    fm_lines.append(f"status: {status}")
    if failure_reason:
        fm_lines.append(f"failure_reason: {failure_reason}")
    content = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body
    if not content.endswith("\n"):
        content += "\n"

    full_path.parent.mkdir(parents=True, exist_ok=True)
    prior = full_path.read_text(encoding="utf-8") if full_path.exists() else None
    full_path.write_text(content, encoding="utf-8")
    try:
        sha = tm_core.git_commit_push([rel], f"[{agent}] ingest: {rel}")
    except Exception:
        if prior is None:
            try:
                full_path.unlink()
            except OSError:
                pass
        else:
            full_path.write_text(prior, encoding="utf-8")
        raise

    result = {"path": rel, "committed": True, "commit_sha": sha}
    try:
        result.update(
            tm_memory_ops.schedule_embed_refresh(
                scope="wiki",
                reason="write_sources",
                paths=[rel],
            )
        )
    except Exception as exc:
        result["warnings"] = [f"embed refresh scheduling failed: {exc}"]
    return result


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
def verify_memory_id(
    memory_id: str,
    key_terms: str | None = None,
    digest_date: str | None = None,
) -> dict[str, Any]:
    """[审计 Mem0 ID] Verify a write_memory-returned Mem0 id by direct readback, search, and digest visibility.

    Args:
        memory_id: Full Mem0 UUID returned by write_memory.
        key_terms: Optional exact terms expected in the memory text, used for a search self-hit check.
        digest_date: Optional YYYY-MM-DD digest date to check. Defaults to the memory's Asia/Shanghai created_at date.

    Returns:
        Status and evidence fields such as status, direct_readback_ok, state,
        created_at_local, text_sha256_12, search_by_id_self_hit,
        search_by_terms_self_hit, and digest_inclusion_reason.
    """
    return tm_core.verify_memory_id(memory_id, key_terms=key_terms, digest_date=digest_date)


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
def search_tigermemory(query: str, scope: str = "auto", top_k: int = 5) -> dict[str, Any]:
    """Grouped search across tigermemory knowledge surfaces.

    One call fans out to existing read paths and returns grouped results. It
    deliberately does not fuse all sources into one normalized ranking because
    eval showed that hurts hit@1.
    """
    return tm_search.search_tigermemory(query, scope=scope, top_k=top_k, role=_ROLE, dogfood_log=_SEARCH_DOGFOOD_LOG)


@mcp.tool()
def memory_answer(
    query: str,
    scope: str = "auto",
    top_k: int = 5,
    max_evidence: int = 6,
    include_trace: bool = True,
    run_id: str | None = None,
    evidence_char_budget: int = 2000,
) -> dict[str, Any]:
    """Evidence-first answer over tigermemory search surfaces.

    Args:
        query: User question to answer from tigermemory evidence.
        scope: auto | all | wiki | lessons | onboarding | mem0.
        top_k: Per-source search limit, clamped to 1..10.
        max_evidence: Evidence items to expand/read, clamped to 1..12.
        include_trace: Include trace details in the response.
        run_id: Optional run id for grouping trace rows from one eval or scan.
        evidence_char_budget: Max total excerpt characters sent to the answer LLM.

    Returns:
        {status, answer, summary, claims, evidence, warnings, run_id, trace_id, trace}.
    """
    return tm_answer.memory_answer_core(
        query,
        scope=scope,
        top_k=top_k,
        max_evidence=max_evidence,
        include_trace=include_trace,
        run_id=run_id,
        evidence_char_budget=evidence_char_budget,
    )


@mcp.tool()
def write_memory(agent: str, topic: str, text: str, force_inbox: bool = False, light: bool = False) -> dict[str, Any]:
    _require_writer()
    """Single canonical entry for agent memory writes. Server-side LLM routes to mem0 / inbox / discard.

    If the text may need human review, start it with one concise Chinese sentence
    summarizing the item. Inbox files store that sentence as summary_cn for the
    daily digest review UI; if omitted, the inbox record is marked as missing a
    Chinese summary instead of being translated later by cron.

    Args:
        agent: Agent name
        topic: Topic name
        text: Memory text content; first Chinese line becomes inbox summary_cn
        force_inbox: If True, bypass routing and write directly to inbox (agent requests human review)
        light: If True, skip DeepSeek routing for allowlisted low-risk pointer writes

    Returns:
        {"route": "mem0", "id": "..."} or {"route": "inbox", "path": "...", "commit_sha": "..."}
        or {"route": "discard", "score": int, "issues": [...]}
    """
    return tm_memory_ops.write_memory_with_review(
        agent,
        topic,
        text,
        force_inbox=force_inbox,
        light=light,
        total_budget_s=None,
        include_readback=True,
    )


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
def lint_repo(path: str | None = None) -> dict[str, Any]:
    """Lint tigermemory repository.

    Two modes:
    - path=None (default): full repo governance scan (orphan pages, stale
      drafts, missing sources, partition mismatches).
    - path="wiki/<partition>/<slug>.md": validate a single page against
      PAGE_FORMATS.md (frontmatter shape, headings).

    Args:
        path: Optional relative page path. Omit for repo-wide scan.

    Returns:
        Repo scan: {"orphan_pages": [...], "stale_drafts": [...],
                    "missing_sources": [...], "partition_mismatches": [...]}
        Page scan: {"ok": bool, "errors": [str]}
    """
    if path is not None:
        full_path = tm_core.REPO_ROOT / path
        if not full_path.exists():
            raise FileNotFoundError(f"not found: {path}")
        errors = tm_core.lint_page_errors(full_path.read_text(encoding="utf-8"))
        return {"ok": len(errors) == 0, "errors": errors}

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
            if rel in tm_core.LINTER_DASHBOARDS or tm_core.is_auto_generated_path(rel):
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
            if rel in tm_core.LINTER_DASHBOARDS or tm_core.is_auto_generated_path(rel):
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


@mcp.tool()
def single_stock_deep_dive(ticker: str, trade_date: str, profile: str = "deep") -> dict[str, Any]:
    """Run TradingAgents single-stock deep research and return a JSON summary.

    The tool is intentionally single-stock only. It writes detailed reports to
    wiki/investment/decision-log through TradingAgents' CLI adapter and returns
    the final rating, report paths, provider trace, warnings, and cost estimate.
    profile may be "deep" for the full chain or "fast" for daily scanning.
    """
    _require_writer()
    ta_root = os.environ.get("TRADINGAGENTS_ROOT", "/home/giant/workspaces/TradingAgents")
    python_bin = os.environ.get("TRADINGAGENTS_PYTHON", os.path.join(ta_root, ".venv", "bin", "python"))
    env = os.environ.copy()
    env["PYTHONPATH"] = ta_root
    result = subprocess.run(
        [python_bin, "tools/tm_adapter.py", ticker, trade_date, "--profile", profile],
        cwd=ta_root,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("TRADINGAGENTS_MCP_TIMEOUT_SEC", "1800")),
        env=env,
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "returncode": result.returncode,
            "error": (result.stderr or result.stdout)[-2000:],
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {
            "ok": False,
            "error": f"invalid tm_adapter JSON output: {exc}",
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
    if result.stderr.strip():
        payload.setdefault("warnings", []).append("stderr_nonempty")
        payload["stderr_tail"] = result.stderr[-2000:]
    return payload


@mcp.tool()
def start_deep_dive(ticker: str, trade_date: str, profile: str = "deep") -> dict[str, Any]:
    """Start a TradingAgents single-stock deep-dive background job.

    This returns immediately with a job_id. Use get_deep_dive_status(job_id)
    to poll and fetch_deep_dive_result(job_id) after completion.
    profile may be "deep" for the full chain or "fast" for daily scanning.
    """
    _require_writer()
    return tm_deep_dive_jobs.start_job(ticker, trade_date, profile=profile)


@mcp.tool()
def get_deep_dive_status(job_id: str) -> dict[str, Any]:
    """Return status for a TradingAgents background deep-dive job."""
    return tm_deep_dive_jobs.get_status(job_id)


@mcp.tool()
def fetch_deep_dive_result(job_id: str) -> dict[str, Any]:
    """Return the completed TradingAgents deep-dive result JSON for a job."""
    return tm_deep_dive_jobs.fetch_result(job_id)


@mcp.tool()
def start_stability_eval(ticker: str, trade_date: str, profile: str = "deep", n: int = 3) -> dict[str, Any]:
    """Run N TradingAgents deep dives and write a stability consensus report.

    This is intentionally opt-in: fast scan remains single-run, while a human or
    dashboard action can request a deeper stability evaluation for one ticker.
    """
    _require_writer()
    return tm_stability_eval.start_stability_eval(ticker, trade_date, profile=profile, n=n)


# ---------- Daily Digest Review Tools ----------

@mcp.tool()
def review_digest(date: str | None = None, action: str | None = None) -> dict[str, Any]:
    """日报审稿统一入口。

    三种用法（按参数区分）：
    - 不传参数：列出所有未审核的日报
      返回 {"ok": True, "digests": [...], "count": N}
    - 只传 date：读取该日报的 facts 列表（供人审）
      返回 {"ok": True, "date": ..., "facts": [...], "frontmatter": ..., "fact_count": N}
    - date + action="mark_reviewed"：把日报 frontmatter status 改为 'reviewed' 并 commit
      返回 {"ok": True, "committed": True, "commit_sha": ...}

    Args:
        date: "YYYY-MM-DD"，省略则列出待审日报
        action: "mark_reviewed" 触发收尾，省略则只读
    """
    try:
        if date is None or not date.strip():
            digests = tm_review_tools.list_pending_digests()
            return {"ok": True, "digests": digests, "count": len(digests)}

        if action == "mark_reviewed":
            _require_writer()
            updated = tm_review_tools.save_digest_with_log(date, {"status": "reviewed"})
            if not updated:
                return {"ok": False, "error": f"Failed to update digest: {date}"}
            digest_path = f"inbox/daily/{date}.md"
            sha = tm_core.git_commit_push(
                [digest_path], f"[human] update: mark {date} digest as reviewed"
            )
            return {"ok": True, "committed": True, "commit_sha": sha}

        if action is not None:
            return {"ok": False, "error": f"Invalid action: {action} (allowed: mark_reviewed)"}

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


# ---------- Expense Tracker Tools ----------

@mcp.tool()
def expense_record(
    kind: str,
    amount: float,
    category: str,
    occurred_at: str | None = None,
    currency: str = "CNY",
    merchant: str | None = None,
    note: str | None = None,
    payment_method: str | None = None,
    source_agent: str = "openclaw",
    source_text: str | None = None,
) -> dict[str, Any]:
    _require_writer()
    """Record an expense or income entry into the private SQLite ledger.

    This is a private structured ledger, NOT part of Wiki / Mem0 / digest.
    The ledger file is git-ignored and never committed.

    Args:
        kind: "expense" or "income"
        amount: Positive number (e.g. 36.50)
        category: Category label (e.g. 餐饮, 交通, 购物, 住房, 娱乐)
        occurred_at: ISO8601 datetime string (optional, defaults to now). Asia/Shanghai.
        currency: Default "CNY"
        merchant: Optional merchant name
        note: Optional free-text note
        payment_method: Optional (e.g. 微信, 支付宝, 银行卡, 现金)
        source_agent: Calling agent name (default "openclaw")
        source_text: Original natural-language input that triggered this record

    Returns:
        {"ok": true, "id": <int>, "kind": "...", "amount": ..., "category": "..."}
    """
    try:
        return tm_expense.expense_record(
            kind=kind, amount=amount, category=category,
            occurred_at=occurred_at, currency=currency,
            merchant=merchant, note=note, payment_method=payment_method,
            source_agent=source_agent, source_text=source_text,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def expense_query(
    start_date: str | None = None,
    end_date: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    group_by: str = "category",
    limit: int = 50,
) -> dict[str, Any]:
    """Query and aggregate expense entries from the private SQLite ledger (v1 alias).

    This reads from the private structured ledger, NOT from Wiki / Mem0.
    Use this for questions like "本月餐饮花了多少" or "上月总支出".

    Args:
        start_date: Optional start date "YYYY-MM-DD"
        end_date: Optional end date "YYYY-MM-DD"
        kind: Optional filter: "expense" or "income"
        category: Optional category filter
        group_by: "category" (default), "month", "kind", or "none"
        limit: Max result rows (default 50, max 200)

    Returns:
        {"ok": true, "group_by": "...", "groups": [...], "grand_total": {...}}
    """
    try:
        return tm_expense.expense_query(
            start_date=start_date, end_date=end_date,
            kind=kind, category=category,
            group_by=group_by, limit=limit,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def expense_write(
    action: str = "record",
    id: int | None = None,
    kind: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    occurred_at: str | None = None,
    currency: str | None = None,
    merchant: str | None = None,
    note: str | None = None,
    payment_method: str | None = None,
    tags: list[str] | None = None,
    source_agent: str = "openclaw",
    source_text: str | None = None,
    entries: list[dict] | None = None,
    confirm_new_category: bool = False,
    auto_classify: bool = False,
    # P1: manage_category
    manage_category_action: str = "add",
    manage_category_name: str | None = None,
    manage_category_new_name: str | None = None,
    manage_category_target_name: str | None = None,
    manage_category_alias: str | None = None,
    manage_category_kind: str = "expense",
    # P1: manage_merchant
    manage_merchant_action: str = "add",
    manage_merchant_name: str | None = None,
    manage_merchant_new_name: str | None = None,
    manage_merchant_target_name: str | None = None,
    manage_merchant_alias: str | None = None,
    manage_merchant_default_category_id: int | None = None,
    # P1: budget
    budget_period: str = "month",
    budget_period_key: str | None = None,
    budget_category_id: int | None = None,
    budget_amount: float | None = None,
    budget_note: str | None = None,
    budget_id: int | None = None,
) -> dict[str, Any]:
    _require_writer()
    """Unified write endpoint for the private expense tracker ledger.

    Actions:
        record:       Write a single entry (requires kind, amount, category)
        update:       Modify an existing entry by id
        delete:       Soft-delete an entry by id
        restore:      Restore a soft-deleted entry by id
        batch_record: Write multiple entries in one transaction
        manage_category: add/rename/merge/archive/alias_add categories
        manage_merchant: add/rename/merge/archive/alias_add merchants
        set_budget:   Set a monthly/yearly budget for a category
        delete_budget: Delete a budget by id

    Returns:
        {"ok": true, "action": "...", "id": N, "normalized": {...}}
        or {"ok": false, "needs_confirmation": true, ...} for unknown categories.
    """
    try:
        return tm_expense.expense_write(
            action=action,
            id=id,
            kind=kind,
            amount=amount,
            category=category,
            occurred_at=occurred_at,
            currency=currency,
            merchant=merchant,
            note=note,
            payment_method=payment_method,
            tags=tags,
            source_agent=source_agent,
            source_text=source_text,
            entries=entries,
            confirm_new_category=confirm_new_category,
            auto_classify=auto_classify,
            manage_category_action=manage_category_action,
            manage_category_name=manage_category_name,
            manage_category_new_name=manage_category_new_name,
            manage_category_target_name=manage_category_target_name,
            manage_category_alias=manage_category_alias,
            manage_category_kind=manage_category_kind,
            manage_merchant_action=manage_merchant_action,
            manage_merchant_name=manage_merchant_name,
            manage_merchant_new_name=manage_merchant_new_name,
            manage_merchant_target_name=manage_merchant_target_name,
            manage_merchant_alias=manage_merchant_alias,
            manage_merchant_default_category_id=manage_merchant_default_category_id,
            budget_period=budget_period,
            budget_period_key=budget_period_key,
            budget_category_id=budget_category_id,
            budget_amount=budget_amount,
            budget_note=budget_note,
            budget_id=budget_id,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def expense_read(
    mode: str = "aggregate",
    start_date: str | None = None,
    end_date: str | None = None,
    kind: str | None = None,
    category: str | list[str] | None = None,
    merchant: str | list[str] | None = None,
    payment_method: str | None = None,
    tags: list[str] | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
    order_by: str = "occurred_at desc",
    group_by: list[str] | None = None,
    metric: str = "sum",
    bucket: str = "month",
    sql: str | None = None,
    sql_params: dict | None = None,
    # P1
    compare: str = "yoy",
    compare_group_by: list[str] | None = None,
    anomaly_window_days: int = 90,
    anomaly_sigma: float = 2.0,
    export_format: str = "markdown",
    # P2
    query: str | None = None,
) -> dict[str, Any]:
    """Unified read endpoint for the private expense tracker ledger.

    Modes:
        list:      Raw rows with filters and pagination
        aggregate: Multi-dimensional grouping (group_by=["category","month"], metric="sum")
        trend:     Time-bucketed analysis (bucket="month", group_by=["category"])
        compare:   mom/yoy/qoq comparison with delta_pct
        anomaly:   Entries exceeding mean ± sigma*std
        budget_status: Current budget vs spent
        categories: List all categories (with archived/aliases)
        merchants:  List all merchants
        export:     Export data as markdown/csv/json
        sql:       Free-form readonly SELECT against the ledger (validated, max 1000 rows)
        search:    FTS5 full-text query against note + tags (use `query` param)

    Returns:
        {"ok": true, "mode": "...", "rows": [...]} or similar shape per mode.
    """
    try:
        return tm_expense.expense_read(
            mode=mode,
            start_date=start_date,
            end_date=end_date,
            kind=kind,
            category=category,
            merchant=merchant,
            payment_method=payment_method,
            tags=tags,
            min_amount=min_amount,
            max_amount=max_amount,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
            order_by=order_by,
            group_by=group_by,
            metric=metric,
            bucket=bucket,
            sql=sql,
            sql_params=sql_params,
            compare=compare,
            compare_group_by=compare_group_by,
            anomaly_window_days=anomaly_window_days,
            anomaly_sigma=anomaly_sigma,
            export_format=export_format,
            query=query,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
