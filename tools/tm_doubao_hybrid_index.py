#!/usr/bin/env python3
"""
tools/tm_doubao_hybrid_index.py — Phase 5d eval-only Doubao multimodal
dense+sparse hybrid index builder + searcher.

Why this exists: Phase 5c (`wiki/systems/openviking-upstream-grounding.md` §10)
verified that Volcengine standard ARK API
`https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal` returns
dense (1024-dim with `dimensions=1024`) + sparse (list of {index, value}
pairs with `sparse_embedding={"type":"enabled"}`). This module is the
EVAL-ONLY prototype that ports the OpenViking dense+sparse hybrid retrieval
shape to tigermemory's wiki/sources corpus. It does NOT change any production
path: it builds a separate jsonl, has its own meta manifest, and
`tm_memory_eval.py --recall doubao-hybrid` is the only consumer.

Hard boundaries (Phase 5d brief):
  - Does NOT modify runtime/openmemory/.env.
  - Does NOT touch runtime/embed_index/wiki.jsonl (Qwen v5 production).
  - Does NOT modify tm_core.embed_texts or any production search path.
  - Does NOT introduce volcenginesdkarkruntime SDK dependency.

Output:
  runtime/embed_index/wiki_doubao_hybrid.jsonl
  runtime/embed_index/wiki_doubao_hybrid.meta.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import tm_core
import tm_embed_index

REPO_ROOT = tm_core.REPO_ROOT
INDEX_DIR = REPO_ROOT / "runtime" / "embed_index"
INDEX_PATH = INDEX_DIR / "wiki_doubao_hybrid.jsonl"
META_PATH = INDEX_DIR / "wiki_doubao_hybrid.meta.json"
SCHEMA_VERSION = "phase5d-v1"

ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
MODEL = "doubao-embedding-vision-251215"
DIMENSIONS = 1024
PREVIEW_CHARS = 200

# Retry config: re-uses tm_core's stability layer pattern but kept local so
# we don't depend on production env tunables. These are spike-time defaults.
MAX_RETRIES = 3
BASE_DELAY = 0.5
MAX_DELAY = 8.0
TIMEOUT_S = 45


# ---------- env / api key ----------

def _load_env_file() -> dict[str, str]:
    """Read `runtime/openmemory/.env` into a dict. Never logs key contents."""
    p = REPO_ROOT / "runtime" / "openmemory" / ".env"
    if not p.exists():
        raise RuntimeError(f"missing env file: {p}")
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _api_key() -> str:
    env = _load_env_file()
    key = env.get("OPENAI_API_KEY") or env.get("EMBEDDING_API_KEY", "")
    if not key:
        raise RuntimeError("no OPENAI_API_KEY / EMBEDDING_API_KEY in runtime/openmemory/.env")
    return key


# ---------- HTTP embed call ----------

class _Stats:
    """Mutable counters for build-time telemetry. Survives across calls
    so the build summary can report retry / error totals."""

    def __init__(self) -> None:
        self.requests = 0
        self.retries = 0
        self.transient_errors = 0
        self.permanent_errors = 0
        self.latencies_ms: list[float] = []


def _classify(status: int | None, body: str) -> str:
    if status in (400, 401, 403, 404, 422):
        return "permanent"
    if status in (408, 429, 500, 502, 503, 504):
        return "transient"
    low = (body or "").lower()
    for tok in ("invalidendpointormodel", "accountoverdue", "model or endpoint",
                "does not exist", "unauthorized", "forbidden", "shape mismatch"):
        if tok in low:
            return "permanent"
    for tok in ("timeout", "timed out", "rate limit", "ratelimit",
                "connection refused", "connection reset", "temporarily unavailable",
                "server overloaded"):
        if tok in low:
            return "transient"
    return "unknown"


def _embed_once(text: str, api_key: str, *, sparse: bool = True) -> dict[str, Any]:
    """Single call to /embeddings/multimodal. Returns parsed JSON or raises.

    Raises RuntimeError("permanent: ...") for permanent failures (no retry),
    RuntimeError("transient: ...") for transient (caller may retry), and
    RuntimeError("unknown: ...") otherwise (treated as fail-fast).
    """
    body: dict[str, Any] = {
        "model": MODEL,
        "input": [{"type": "text", "text": text}],
        "dimensions": DIMENSIONS,
        "encoding_format": "float",
    }
    if sparse:
        body["sparse_embedding"] = {"type": "enabled"}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")[:300]
        kind = _classify(e.code, body_err)
        raise RuntimeError(f"{kind}: HTTP {e.code} {body_err}")
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if isinstance(e.reason, socket.timeout) or "timed out" in reason.lower():
            raise RuntimeError(f"transient: timeout {reason}")
        kind = _classify(None, reason)
        if kind == "unknown":
            kind = "transient"
        raise RuntimeError(f"{kind}: {reason}")
    return json.loads(raw)


def _embed_with_retry(text: str, api_key: str, stats: _Stats, *, sparse: bool = True) -> dict[str, Any]:
    """Retry transient failures with exponential backoff + jitter."""
    attempt = 0
    while True:
        stats.requests += 1
        t0 = time.monotonic()
        try:
            data = _embed_once(text, api_key, sparse=sparse)
            stats.latencies_ms.append((time.monotonic() - t0) * 1000)
            return data
        except RuntimeError as exc:
            stats.latencies_ms.append((time.monotonic() - t0) * 1000)
            msg = str(exc)
            if msg.startswith("permanent"):
                stats.permanent_errors += 1
                raise
            stats.transient_errors += 1
            if attempt >= MAX_RETRIES:
                raise
            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            delay += random.uniform(0.0, min(BASE_DELAY, delay))
            stats.retries += 1
            print(f"  [retry {attempt + 1}/{MAX_RETRIES}] {msg[:120]}; sleep {delay:.2f}s",
                  file=sys.stderr)
            time.sleep(delay)
            attempt += 1


def _parse_response(d: dict[str, Any]) -> tuple[list[float], list[tuple[int, float]]]:
    """Return (dense_vec, sparse_pairs_as_int_float_tuples). Raises if shape wrong."""
    data = d.get("data")
    item: dict[str, Any]
    # Multimodal endpoint returns `data` as single object; some variants return list.
    if isinstance(data, list):
        if not data:
            raise RuntimeError("permanent: empty data list")
        item = data[0]
    elif isinstance(data, dict):
        item = data
    else:
        raise RuntimeError(f"permanent: unexpected data type {type(data).__name__}")
    dense = item.get("embedding")
    if not isinstance(dense, list) or not dense:
        raise RuntimeError("permanent: missing embedding")
    sparse_raw = item.get("sparse_embedding") or []
    pairs: list[tuple[int, float]] = []
    if isinstance(sparse_raw, list):
        for p in sparse_raw:
            if isinstance(p, dict) and "index" in p and "value" in p:
                try:
                    pairs.append((int(p["index"]), float(p["value"])))
                except (TypeError, ValueError):
                    continue
    return [float(x) for x in dense], pairs


# ---------- index I/O ----------

def _load_index() -> dict[str, dict[str, Any]]:
    if not INDEX_PATH.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("path"):
                out[e["path"]] = e
    return out


def _save_index(entries: dict[str, dict[str, Any]]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rel in sorted(entries):
            f.write(json.dumps(entries[rel], ensure_ascii=False) + "\n")
    tmp.replace(INDEX_PATH)


def _save_meta(meta: dict[str, Any]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = META_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(META_PATH)


def _load_meta() -> dict[str, Any] | None:
    if not META_PATH.exists():
        return None
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------- preview helper ----------

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _preview(body: str, n: int = PREVIEW_CHARS) -> str:
    stripped = _FRONTMATTER_RE.sub("", body, count=1)
    return " ".join(stripped[:n * 4].split())[:n]


# ---------- build ----------

def build(*, force: bool = False, limit: int | None = None) -> dict[str, Any]:
    api_key = _api_key()
    existing = {} if force else _load_index()
    keep: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, str, list[str], str, str, str]] = []  # rel,title,aliases,body,partition,text
    seen: set[str] = set()

    # Reuse tm_embed_index page iteration + embed_text composition for parity
    # with the Qwen baseline. Same input text -> head-to-head comparison.
    for abs_path, rel, title, aliases, body in tm_embed_index._iter_pages("wiki"):
        seen.add(rel)
        text = tm_embed_index._embed_text(rel, title, aliases, body)
        h = tm_embed_index._content_hash(rel, title, aliases, body)
        prior = existing.get(rel)
        if (
            prior
            and prior.get("hash") == h
            and isinstance(prior.get("dense"), list)
            and isinstance(prior.get("sparse"), list)
        ):
            keep[rel] = dict(prior)
            keep[rel]["title"] = title  # title may have moved without hash flip in rare cases
            continue
        partition = tm_embed_index._partition_of(rel)
        pending.append((rel, title, aliases, body, partition, text))

    if limit is not None:
        pending = pending[:limit]

    dropped = sorted(set(existing) - seen)
    stats = _Stats()
    embedded = 0
    started = time.monotonic()

    print(f"[doubao-hybrid] reused={len(keep)} pending={len(pending)} dropped={len(dropped)}",
          file=sys.stderr)

    for i, (rel, title, aliases, body, partition, text) in enumerate(pending, 1):
        try:
            d = _embed_with_retry(text, api_key, stats, sparse=True)
            dense, sparse_pairs = _parse_response(d)
        except RuntimeError as exc:
            # On permanent failure, abort the build but keep the existing
            # production-untouched index file intact (we save what we have).
            print(f"[doubao-hybrid] FAIL {rel}: {str(exc)[:200]}", file=sys.stderr)
            if str(exc).startswith("permanent"):
                # Do not save partial — let user see the failure, fix, retry.
                raise
            # transient still raises after retries — same handling
            raise

        try:
            mtime = int((REPO_ROOT / rel).stat().st_mtime)
        except OSError:
            mtime = 0

        keep[rel] = {
            "path": rel,
            "title": title,
            "aliases": aliases,
            "partition": partition,
            "hash": tm_embed_index._content_hash(rel, title, aliases, body),
            "mtime": mtime,
            "dim": len(dense),
            "sparse_count": len(sparse_pairs),
            "dense": dense,
            # Store sparse as list of [int,float] so it round-trips through
            # plain JSON; loader converts to dict{int->float} for scoring.
            "sparse": [[idx, val] for idx, val in sparse_pairs],
            "preview": _preview(body),
        }
        embedded += 1

        if i % 25 == 0 or i == len(pending):
            elapsed = time.monotonic() - started
            rate = i / elapsed if elapsed else 0
            print(f"  embedded {i:>4}/{len(pending)} ({rate:.1f}/s, retries={stats.retries})",
                  file=sys.stderr)
            # Save progress every 25 to survive interrupts.
            _save_index(keep)

    _save_index(keep)

    p50 = round(sorted(stats.latencies_ms)[len(stats.latencies_ms) // 2], 1) if stats.latencies_ms else 0.0
    p95 = round(sorted(stats.latencies_ms)[int(len(stats.latencies_ms) * 0.95)], 1) if stats.latencies_ms else 0.0
    meta = {
        "schema": SCHEMA_VERSION,
        "endpoint": ENDPOINT,
        "model": MODEL,
        "dimensions": DIMENSIONS,
        "sparse_enabled": True,
        "entry_count": len(keep),
        "page_count": len(keep),
        "built_at": _dt.datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d %H:%M:%S"),
        "build_stats": {
            "embedded": embedded,
            "reused": len(keep) - embedded,
            "dropped_count": len(dropped),
            "requests": stats.requests,
            "retries": stats.retries,
            "transient_errors": stats.transient_errors,
            "permanent_errors": stats.permanent_errors,
            "latency_ms_p50": p50,
            "latency_ms_p95": p95,
            "elapsed_s": round(time.monotonic() - started, 1),
        },
    }
    _save_meta(meta)
    return meta


# ---------- search ----------

def _to_sparse_dict(pairs: Any) -> dict[int, float]:
    out: dict[int, float] = {}
    if not isinstance(pairs, list):
        return out
    for p in pairs:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            try:
                out[int(p[0])] = float(p[1])
            except (TypeError, ValueError):
                continue
        elif isinstance(p, dict) and "index" in p and "value" in p:
            try:
                out[int(p["index"])] = float(p["value"])
            except (TypeError, ValueError):
                continue
    return out


def _cosine_dense(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    import math
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _cosine_sparse(a: dict[int, float], b: dict[int, float]) -> float:
    """Cosine similarity over sparse {index -> weight} dicts."""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for k, va in a.items():
        vb = b.get(k)
        if vb is not None:
            dot += va * vb
    import math
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_INDEX_CACHE: list[dict[str, Any]] | None = None


def _entries_cached() -> list[dict[str, Any]]:
    global _INDEX_CACHE
    if _INDEX_CACHE is None:
        _INDEX_CACHE = list(_load_index().values())
    return _INDEX_CACHE


def search(query: str, *, k: int = 10, dense_weight: float = 0.55,
           sparse_weight: float = 0.45) -> list[dict[str, Any]]:
    """Embed `query` via multimodal endpoint, score by `w_d*cos_dense + w_s*cos_sparse`.

    Returns list of {path, title, score, score_dense, score_sparse, source}.
    """
    api_key = _api_key()
    stats = _Stats()
    d = _embed_with_retry(query, api_key, stats, sparse=True)
    q_dense, q_sparse_pairs = _parse_response(d)
    q_sparse = {idx: val for idx, val in q_sparse_pairs}

    entries = _entries_cached()
    scored: list[tuple[float, float, float, dict[str, Any]]] = []
    for e in entries:
        dscore = _cosine_dense(q_dense, e.get("dense") or [])
        sscore = _cosine_sparse(q_sparse, _to_sparse_dict(e.get("sparse") or []))
        fused = dense_weight * dscore + sparse_weight * sscore
        scored.append((fused, dscore, sscore, e))
    scored.sort(key=lambda t: -t[0])

    out: list[dict[str, Any]] = []
    for fused, dscore, sscore, e in scored[:k]:
        out.append({
            "path": e["path"],
            "title": e.get("title", ""),
            "score": round(fused, 6),
            "score_dense": round(dscore, 6),
            "score_sparse": round(sscore, 6),
            "source": "doubao-hybrid",
        })
    return out


# ---------- CLI ----------

def cmd_build(args: argparse.Namespace) -> int:
    meta = build(force=args.force, limit=args.limit)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    entries = _load_index()
    meta = _load_meta()
    out = {
        "exists": INDEX_PATH.exists(),
        "entry_count": len(entries),
        "index_path": str(INDEX_PATH.relative_to(REPO_ROOT)),
        "meta": meta,
    }
    if entries:
        sample = next(iter(entries.values()))
        out["dense_dim_sample"] = len(sample.get("dense") or [])
        out["sparse_count_sample"] = len(sample.get("sparse") or [])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    hits = search(args.query, k=args.k,
                  dense_weight=args.dense_weight,
                  sparse_weight=args.sparse_weight)
    print(json.dumps(hits, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tm_doubao_hybrid_index.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build / refresh the doubao-hybrid index")
    b.add_argument("--force", action="store_true", help="rebuild from scratch")
    b.add_argument("--limit", type=int, default=None, help="cap pending pages (smoke)")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("stats", help="print index size + meta")
    s.set_defaults(func=cmd_stats)

    q = sub.add_parser("search", help="ad-hoc query (debug)")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=10)
    q.add_argument("--dense-weight", type=float, default=0.55)
    q.add_argument("--sparse-weight", type=float, default=0.45)
    q.set_defaults(func=cmd_search)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
