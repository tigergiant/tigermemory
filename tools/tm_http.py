#!/usr/bin/env python3
"""
tools/tm_http.py — HTTP wrapper for tigermemory (FastAPI).

Exposes 7 endpoints for OpenClaw context-engine plugin:
- GET /health
- POST /search_memories
- POST /read_wiki
- POST /list_partition
- POST /write_memory
- POST /write_inbox
- POST /review_draft

All business logic lives in tm_core.py / tm_review.py. This module only
does HTTP ↔ Python function conversion.

Usage:
  python tools/tm_http.py --bind 127.0.0.1 --port 8790
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import tm_core
import tm_review
import tm_route

_INDEX_ITEM_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\(([^)]+)\)(?:\s*[—\-]\s*(.+))?$")
_PARTITIONS = ("brand", "investment", "operations", "production", "systems", "person", "self-evolution")


def _load_wiki_catalog(partition: str) -> list[dict]:
    """Parse `wiki/<partition>/index.md` into a list of {page, summary} dicts.

    `partition="all"` unions every partition. Pages that already have a
    curated one-line summary in the index are used verbatim; pages without
    a summary are included with an empty summary field.
    """
    parts = _PARTITIONS if partition == "all" else (partition,)
    items: list[dict] = []
    for part in parts:
        idx = tm_core.REPO_ROOT / "wiki" / part / "index.md"
        if not idx.exists():
            continue
        in_pages = False
        for line in idx.read_text(encoding="utf-8").splitlines():
            if line.strip() == "## 页面":
                in_pages = True
                continue
            if not in_pages:
                continue
            m = _INDEX_ITEM_RE.match(line)
            if not m:
                continue
            fn = m.group(2).strip()
            if fn == "index.md" or fn.startswith("http"):
                continue
            items.append({
                "page": f"wiki/{part}/{fn}",
                "summary": (m.group(3) or "").strip(),
            })
    return items


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


# ---------- Debounced digest auto-refresh ----------
#
# After every successful write (mem0 or inbox), schedule a digest regen of
# today's file. Debounced so a burst of writes coalesces into one synthesis.
# Writes only to `inbox/daily/YYYY-MM-DD.md`; the 03:15 cron still owns the
# canonical commit. This gives the user a near-real-time view of the day's
# accumulated facts via the live file.

DIGEST_DEBOUNCE_SECONDS = int(os.environ.get("TM_DIGEST_DEBOUNCE_SECONDS", "180"))
_digest_timer: threading.Timer | None = None
_digest_lock = threading.Lock()

# 2026-04-30: end-to-end budget for /write_memory. Worst-case path is
# route_memory(DeepSeek 10s) + mem0_write(15s) = 25s. If route alone burns
# most of the budget, we skip mem0 and degrade to inbox so the caller is
# never held longer than the budget. Min 5s reserved for mem0 attempt;
# below that, force inbox immediately.
WRITE_MEMORY_TOTAL_BUDGET_S = int(os.environ.get("TM_WRITE_MEMORY_BUDGET_S", "25"))
WRITE_MEMORY_MEM0_MIN_RESERVE_S = 5


def _refresh_digest_today() -> None:
    """Background callback: regenerate today's digest file. Logs failures."""
    try:
        # Lazy import: tm_digest pulls in DeepSeek SDK; avoid at module load
        from tm_digest import generate_daily_digest
        today = datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d")
        result = generate_daily_digest(target_date=today, dry_run=False)
        log_json(
            "info", str(uuid.uuid4()), "/_digest_refresh", 200, 0,
            ok=result.get("ok"), path=result.get("path"),
            facts=result.get("fact_count"), reason=result.get("reason"),
        )
    except Exception as e:
        log_json("error", str(uuid.uuid4()), "/_digest_refresh", 500, 0, detail=str(e))


def _schedule_digest_refresh() -> None:
    """Debounced trigger: each call resets a DIGEST_DEBOUNCE_SECONDS timer."""
    global _digest_timer
    with _digest_lock:
        if _digest_timer is not None:
            _digest_timer.cancel()
        t = threading.Timer(DIGEST_DEBOUNCE_SECONDS, _refresh_digest_today)
        t.daemon = True
        _digest_timer = t
        t.start()


def _write_memory_with_review(agent: str, topic: str, text: str, force_inbox: bool = False) -> dict[str, Any]:
    # 2026-04-30: enforce end-to-end budget. route_memory may take up to
    # ~10s of DeepSeek; if it does, we reserve at least
    # WRITE_MEMORY_MEM0_MIN_RESERVE_S for mem0_write or fall back to inbox.
    t0 = time.monotonic()
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
        elapsed = time.monotonic() - t0
        remaining = WRITE_MEMORY_TOTAL_BUDGET_S - elapsed
        if remaining < WRITE_MEMORY_MEM0_MIN_RESERVE_S:
            # Route ate the budget; degrade to inbox immediately rather than
            # holding the request open for a likely-doomed mem0 attempt.
            log_json(
                "warn", str(uuid.uuid4()), "/_write_memory_budget_exhausted", 200, 0,
                detail=f"route consumed {elapsed:.1f}s of {WRITE_MEMORY_TOTAL_BUDGET_S}s budget",
                agent=agent, topic=decision.topic_inferred,
            )
            decision = tm_route.RouteDecision(
                route="inbox", score=decision.score,
                topic_inferred=decision.topic_inferred,
                issues=decision.issues,
                reasons=f"budget exhausted by route ({elapsed:.1f}s/{WRITE_MEMORY_TOTAL_BUDGET_S}s); fallback to inbox | original: {decision.reasons}",
                is_transient=decision.is_transient,
                is_sensitive=decision.is_sensitive,
                needs_human_review=decision.needs_human_review,
                unreviewed=decision.unreviewed,
            )
        else:
            # Pass the smaller of (configured Mem0 write timeout, remaining
            # budget) so a slow Mem0 cannot blow the overall deadline.
            mem0_timeout = max(
                WRITE_MEMORY_MEM0_MIN_RESERVE_S,
                int(min(tm_core.MEM0_WRITE_TIMEOUT, remaining)),
            )
            try:
                data = json.loads(tm_core.mem0_write(
                    agent,
                    decision.topic_inferred,
                    text,
                    metadata_extra=decision.as_metadata(),
                    timeout=mem0_timeout,
                ))
                data["route"] = "mem0"
                data["score"] = decision.score
                data["topic_inferred"] = decision.topic_inferred
                data["reasons"] = decision.reasons
                _schedule_digest_refresh()
                return data
            except RuntimeError as e:
                # 2026-04-30: Mem0 write timeout/failure → fall back to inbox so
                # the user's data is never lost. Log so we can spot regressions.
                err = str(e)
                log_json(
                    "warn", str(uuid.uuid4()), "/_write_memory_fallback", 200, 0,
                    detail=err, agent=agent, topic=decision.topic_inferred,
                    text_len=len(text), mem0_timeout=mem0_timeout,
                )
                decision = tm_route.RouteDecision(
                    route="inbox", score=decision.score,
                    topic_inferred=decision.topic_inferred,
                    issues=decision.issues,
                    reasons=f"mem0 write failed, fallback to inbox: {err[:120]} | original: {decision.reasons}",
                    is_transient=decision.is_transient,
                    is_sensitive=decision.is_sensitive,
                    needs_human_review=decision.needs_human_review,
                    unreviewed=decision.unreviewed,
                )
                # fall through to inbox branch below

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
    _schedule_digest_refresh()
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


def _write_inbox_with_review(agent: str, topic: str, title: str, body: str, reason: str) -> dict[str, Any]:
    fm_extra = {
        "routed_by": "tigermemory",
        "route_decision_reason": reason.strip()[:200],
    }
    rel, sha = tm_core.write_and_commit_inbox(agent, topic, title, body, frontmatter_extra=fm_extra)
    _schedule_digest_refresh()
    return {
        "path": rel,
        "commit_sha": sha,
        "url": tm_core.git_remote_blob_url(rel),
        "memory_route": "inbox",
    }


try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    from uvicorn import run
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    print("Run: pip install fastapi uvicorn pydantic", file=sys.stderr)
    sys.exit(1)

# ---------- Configuration ----------

VERSION = "0.1.0"
BIND_HOST = os.getenv("TM_HTTP_BIND", "127.0.0.1")
PORT = int(os.getenv("TM_HTTP_PORT", "8790"))

# ---------- Request Schemas ----------


class HealthResponse(BaseModel):
    ok: bool
    version: str
    tm_core_version: str | None = None
    mem0_reachable: bool
    deepseek_reachable: bool | None = None
    uptime_seconds: float


class SearchMemoriesRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)


class SearchMemoriesResponse(BaseModel):
    count: int
    results: list[dict] | None = None


class ReadWikiRequest(BaseModel):
    path: str = Field(..., pattern=r"^(wiki/|inbox/)[^/]+(/[^/]+)*\.md$")


class ReadWikiResponse(BaseModel):
    path: str
    content: str
    size_bytes: int
    mtime: str


class ListPartitionRequest(BaseModel):
    partition: str = Field(..., pattern=r"^(brand|investment|operations|production|systems|person)$")


class ListPartitionResponse(BaseModel):
    partition: str
    slugs: list[str]
    count: int


class WriteMemoryRequest(BaseModel):
    agent: str = Field(..., pattern=r"^(claude-code|codex|openclaw|hermes|deerflow|human|linter|mem0|tigermemory-ce|kimi)$")
    topic: str = Field(..., pattern=r"^(brand|investment|operations|production|systems|person|cross)$")
    text: str = Field(..., min_length=1, max_length=10000)
    force_inbox: bool = False


class WriteInboxRequest(BaseModel):
    agent: str = Field(..., pattern=r"^(claude-code|codex|openclaw|hermes|deerflow|human|linter|mem0|tigermemory-ce|kimi)$")
    topic: str = Field(..., pattern=r"^(brand|investment|operations|production|systems|person|cross)$")
    title: str = Field(..., min_length=1, max_length=80)
    body: str = Field(..., min_length=1, max_length=50000)
    reason: str = Field(..., min_length=1, max_length=200)


class ReviewDraftRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)


class RefineFactsRequest(BaseModel):
    summary: str = Field(..., min_length=30, max_length=50000)
    max_facts: int = Field(default=3, ge=1, le=10)
    session_key: str | None = Field(default=None, max_length=200)


class RefinedFact(BaseModel):
    topic: str
    text: str


class RefineFactsResponse(BaseModel):
    count: int
    facts: list[RefinedFact]


class SuggestPatchesRequest(BaseModel):
    summary: str = Field(..., min_length=30, max_length=50000)
    partition: str = Field(
        default="all",
        pattern=r"^(brand|investment|operations|production|systems|person|all)$",
    )
    max_patches: int = Field(default=5, ge=1, le=20)
    save: bool = Field(default=True)
    source: str = Field(
        default="claude-code",
        pattern=r"^(claude-code|codex|openclaw|hermes|deerflow|human|linter|tigermemory-ce|kimi)$",
    )


class WikiPatchItem(BaseModel):
    page: str
    type: str
    section: str
    content: str
    rationale: str


class SuggestPatchesResponse(BaseModel):
    count: int
    patches: list[WikiPatchItem]
    inbox_path: str | None = None


# ---------- Error Response ----------


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    trace_id: str


# ---------- Logging ----------


def log_json(level: str, trace_id: str, endpoint: str, status: int, duration_ms: float, **extra: dict) -> None:
    """Emit JSON line to stderr."""
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": level,
        "trace_id": trace_id,
        "endpoint": endpoint,
        "status": status,
        "duration_ms": duration_ms,
        **extra,
    }
    print(json.dumps(entry), file=sys.stderr)


# ---------- Lifespan ----------

_start_time = time.time()


def _probe_mem0_reachable() -> bool:
    """TCP connect probe. Any HTTP response (incl. 4xx) means reachable.

    urllib.urlopen raises on 4xx which previously caused false 'unreachable'
    when Mem0's root path returned 404. Use a socket connect instead.
    """
    try:
        parsed = urlparse(tm_core.mem0_base())
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def _git_sha() -> str | None:
    """Return short git sha of tigermemory HEAD, or None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(tm_core.REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: check Mem0 reachability (TCP-level, see _probe_mem0_reachable docstring)
    app.state.mem0_reachable = _probe_mem0_reachable()
    app.state.tm_core_version = _git_sha()
    yield
    # Shutdown: nothing to clean up


# ---------- Bearer Auth Middleware ----------
# When TM_MCP_API_KEY is set and bind is not localhost, enforce Bearer auth.
# Health endpoint (/health) is exempt for tunnel health checks.

_API_KEY: str | None = None
try:
    _API_KEY = tm_core.mcp_api_key()
except RuntimeError:
    pass  # key not configured; auth disabled (localhost-only is safe)


class _OptionalBearerAuth:
    """ASGI middleware: enforce Bearer token when _API_KEY is set."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _API_KEY is None:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/health":
            return await self.app(scope, receive, send)
        # Check Authorization header
        headers = dict(scope.get("headers", []))
        auth = (headers.get(b"authorization", b"")).decode("latin-1", errors="replace")
        if not auth.startswith("Bearer "):
            response = JSONResponse({"error": "missing Bearer token"}, status_code=401)
            return await response(scope, receive, send)
        if auth[7:].strip() != _API_KEY:
            response = JSONResponse({"error": "invalid token"}, status_code=403)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


# ---------- FastAPI App ----------

app = FastAPI(lifespan=lifespan)
app.add_middleware(_OptionalBearerAuth)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = str(uuid.uuid4())
    log_json("error", trace_id, request.url.path if request.url else "unknown", 500, 0, detail=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "internal error", "detail": str(exc), "trace_id": trace_id},
        headers={"X-Trace-ID": trace_id},
    )


# ---------- Endpoints ----------


@app.get("/health", response_model=HealthResponse)
async def health():
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        return HealthResponse(
            ok=True,
            version=VERSION,
            tm_core_version=app.state.tm_core_version,
            mem0_reachable=_probe_mem0_reachable(),
            deepseek_reachable=None,
            uptime_seconds=time.time() - _start_time,
        )
    finally:
        log_json("info", trace_id, "/health", 200, (time.time() - start) * 1000)


def _normalize_mem0_item(item: dict) -> dict:
    """Normalize a Mem0 record for OpenClaw consumption.

    Mem0 stores user metadata under the key `metadata_` (trailing underscore)
    but OpenClaw's context-engine expects `metadata`. We rename it here so the
    plugin can read `item.metadata.source` / `item.metadata.topic` directly.
    Returns a shallow copy with the fixed key; original response is untouched.
    """
    if not isinstance(item, dict):
        return item
    out = dict(item)
    if "metadata_" in out and "metadata" not in out:
        out["metadata"] = out.pop("metadata_")
    return out


def _synthesize_rank_score(rank: int, total: int) -> float:
    """Rank-based synthetic score in [0.2, 1.0].

    OpenMemory CE does not return a similarity score — its search is a
    substring filter over the memory list, already ordered by relevance.
    We assign a linearly decaying score so OpenClaw's `recallScoreThreshold`
    keeps meaningful semantics (top results pass, tail results filtered).

    When true vector search is available (Mem0 Pro or upstream upgrade),
    remove this synthesis and pass the native score through.
    """
    if total <= 1:
        return 1.0
    return round(1.0 - 0.8 * (rank / (total - 1)), 3)


@app.post("/search_memories", response_model=SearchMemoriesResponse)
async def search_memories(req: SearchMemoriesRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        response_body = tm_core.mem0_search(req.query, req.limit)
        data = json.loads(response_body)
        raw_results = data.get("items", [])
        normalized = [_normalize_mem0_item(r) for r in raw_results]
        total = len(normalized)
        for rank, r in enumerate(normalized):
            if "score" not in r or r.get("score") is None:
                r["score"] = _synthesize_rank_score(rank, total)
        return SearchMemoriesResponse(count=total, results=normalized)
    except Exception as e:
        log_json("error", trace_id, "/search_memories", 500, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=502, detail=f"mem0 unreachable: {e}")
    finally:
        log_json("info", trace_id, "/search_memories", 200, (time.time() - start) * 1000, query_len=len(req.query))


@app.post("/read_wiki", response_model=ReadWikiResponse)
async def read_wiki(req: ReadWikiRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        path = req.path
        if not path.startswith("wiki/") and not path.startswith("inbox/"):
            raise HTTPException(status_code=400, detail="path outside wiki/ or inbox/")
        if ".." in path:
            raise HTTPException(status_code=400, detail="path contains ..")

        full_path = tm_core.REPO_ROOT / path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="file not found")

        content = full_path.read_text(encoding="utf-8")
        stat = full_path.stat()
        return ReadWikiResponse(
            path=path,
            content=content,
            size_bytes=stat.st_size,
            mtime=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
        )
    except HTTPException:
        raise
    except Exception as e:
        log_json("error", trace_id, "/read_wiki", 500, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        log_json("info", trace_id, "/read_wiki", 200, (time.time() - start) * 1000, path=path)


@app.post("/list_partition", response_model=ListPartitionResponse)
async def list_partition(req: ListPartitionRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        partition = req.partition
        partition_dir = tm_core.REPO_ROOT / "wiki" / partition
        if not partition_dir.exists():
            raise HTTPException(status_code=404, detail="partition not found")

        slugs = [f.stem for f in partition_dir.glob("*.md") if f.is_file()]
        return ListPartitionResponse(partition=partition, slugs=slugs, count=len(slugs))
    except HTTPException:
        raise
    except Exception as e:
        log_json("error", trace_id, "/list_partition", 500, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        log_json("info", trace_id, "/list_partition", 200, (time.time() - start) * 1000)


@app.post("/write_memory")
async def write_memory(req: WriteMemoryRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        return _write_memory_with_review(req.agent, req.topic, req.text)
    except Exception as e:
        log_json("error", trace_id, "/write_memory", 502, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=502, detail=f"write_memory failed: {e}")
    finally:
        log_json("info", trace_id, "/write_memory", 200, (time.time() - start) * 1000, text_len=len(req.text))


@app.post("/write_inbox")
async def write_inbox(req: WriteInboxRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        return _write_inbox_with_review(req.agent, req.topic, req.title, req.body, req.reason)
    except Exception as e:
        log_json("error", trace_id, "/write_inbox", 503, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=503, detail=f"write_inbox failed: {e}")
    finally:
        log_json("info", trace_id, "/write_inbox", 200, (time.time() - start) * 1000, body_len=len(req.body))


@app.post("/refine_facts", response_model=RefineFactsResponse)
async def refine_facts(req: RefineFactsRequest):
    """P6.1: Distill structured facts from a conversation summary via DeepSeek.

    Returns an empty list on DeepSeek failure (fail-closed). Callers should
    treat empty as "nothing to write" rather than retrying.
    """
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        facts = tm_core.refine_from_summary(req.summary, req.max_facts)
        return RefineFactsResponse(count=len(facts), facts=facts)
    except Exception as e:
        log_json("error", trace_id, "/refine_facts", 500, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        log_json(
            "info", trace_id, "/refine_facts", 200, (time.time() - start) * 1000,
            summary_len=len(req.summary), max_facts=req.max_facts,
            session_key=req.session_key,
        )


@app.post("/suggest_wiki_patches", response_model=SuggestPatchesResponse)
async def suggest_wiki_patches(req: SuggestPatchesRequest):
    """Phase B1: propose patches to existing wiki pages from a conversation summary.

    Loads the wiki catalog from index.md (single partition or all). Calls
    tm_core.suggest_wiki_patches (MiniMax M2). Optionally writes the result
    to inbox/YYYY-MM-DD-HHMM-<source>-cross.md.

    Fail-closed: returns count=0 / patches=[] on any LLM failure.
    """
    trace_id = str(uuid.uuid4())
    start = time.time()
    inbox_path = None
    try:
        catalog = _load_wiki_catalog(req.partition)
        if not catalog:
            return SuggestPatchesResponse(count=0, patches=[], inbox_path=None)

        patches = tm_core.suggest_wiki_patches(
            req.summary, catalog, max_patches=req.max_patches
        )
        if patches and req.save:
            try:
                inbox_path = tm_core.save_wiki_patches_to_inbox(
                    patches, req.source, summary_excerpt=req.summary
                )
            except (ValueError, OSError) as e:
                log_json("warn", trace_id, "/suggest_wiki_patches", 200,
                         (time.time() - start) * 1000, save_error=str(e))

        return SuggestPatchesResponse(
            count=len(patches), patches=patches, inbox_path=inbox_path
        )
    except Exception as e:
        log_json("error", trace_id, "/suggest_wiki_patches", 500,
                 (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        log_json(
            "info", trace_id, "/suggest_wiki_patches", 200,
            (time.time() - start) * 1000,
            partition=req.partition, summary_len=len(req.summary),
            saved=inbox_path is not None,
        )


@app.post("/review_draft")
async def review_draft(req: ReviewDraftRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        result = tm_review.review_draft(req.body)
        return result
    except Exception as e:
        log_json("error", trace_id, "/review_draft", 500, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        log_json("info", trace_id, "/review_draft", 200, (time.time() - start) * 1000, score=result.get("score"), skipped=result.get("review_skipped"))


# ---------- CLI ----------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="tigermemory HTTP server")
    parser.add_argument("--bind", default=BIND_HOST, help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=PORT, help="Port (default: 8790)")
    args = parser.parse_args()

    run(app, host=args.bind, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
