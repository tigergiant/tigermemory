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
import socket
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import tm_core
import tm_review

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
    agent: str = Field(..., pattern=r"^(claude-code|codex|openclaw|hermes|deerflow|human|mem0|tigermemory-ce)$")
    topic: str = Field(..., pattern=r"^(brand|investment|operations|production|systems|person|cross)$")
    text: str = Field(..., min_length=1, max_length=10000)


class WriteInboxRequest(BaseModel):
    agent: str = Field(..., pattern=r"^(claude-code|codex|openclaw|hermes|deerflow|human|mem0|tigermemory-ce)$")
    topic: str = Field(..., pattern=r"^(brand|investment|operations|production|systems|person|cross)$")
    title: str = Field(..., min_length=1, max_length=80)
    body: str = Field(..., min_length=1, max_length=50000)


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


# ---------- FastAPI App ----------

app = FastAPI(lifespan=lifespan)


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
        response_body = tm_core.mem0_write(req.agent, req.topic, req.text)
        data = json.loads(response_body)
        return data
    except Exception as e:
        log_json("error", trace_id, "/write_memory", 502, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=502, detail=f"mem0 unreachable: {e}")
    finally:
        log_json("info", trace_id, "/write_memory", 200, (time.time() - start) * 1000, text_len=len(req.text))


@app.post("/write_inbox")
async def write_inbox(req: WriteInboxRequest):
    trace_id = str(uuid.uuid4())
    start = time.time()
    try:
        rel, sha = tm_core.write_and_commit_inbox(req.agent, req.topic, req.title, req.body)
        return {"path": rel, "commit_sha": sha, "url": tm_core.git_remote_blob_url(rel)}
    except Exception as e:
        log_json("error", trace_id, "/write_inbox", 503, (time.time() - start) * 1000, detail=str(e))
        raise HTTPException(status_code=503, detail=f"git push failed: {e}")
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
