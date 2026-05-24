#!/usr/bin/env python3
"""
tools/tm_qwen_v4_index.py — Phase 6 eval-only Aliyun DashScope
text-embedding-v4 dense / dense+sparse index builder + searcher.

Why this exists: Phase 5d (Doubao multimodal vision) closed-negative
because vision embeddings underperformed on text RAG. Phase 6 evaluates
text-embedding-v4 (Qwen3-Embedding family) on the same 80-case eval.
This module builds two SEPARATE indices that never touch the production
Qwen v5 index (`runtime/embed_index/wiki.jsonl`):

  runtime/embed_index/wiki_qwen_v4_dense.jsonl  + .meta.json
  runtime/embed_index/wiki_qwen_v4_hybrid.jsonl + .meta.json

API: DashScope native endpoint
  https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding
Model: text-embedding-v4 (Qwen3-Embedding family).
Document side: text_type=document, dimension=1024, output_type=dense | dense&sparse.
Query side  : text_type=query   , dimension=1024, output_type matches index,
              instruct=<retrieval-style instruction>.

Hard boundaries (Phase 6 brief):
  - Reads API key only from env DASHSCOPE_API_KEY. Never prints it.
  - Does NOT modify runtime/openmemory/.env.
  - Does NOT touch runtime/embed_index/wiki.jsonl.
  - Does NOT modify tm_core.embed_texts or any production search path.
  - Does NOT introduce dashscope SDK dependency (urllib only).
Inputs: CLI args, fixture cases, trace JSONL, wiki/Mem0 data, or local index files as selected by the command.
Outputs: Search/eval/trace/index reports printed to stdout or written to the requested output path.
Depends-on (must-have): tm_core search/memory helpers, local Markdown/JSONL files, and optional configured LLM or embedding providers.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import tigermemory_core as tm_core
import tm_embed_index

REPO_ROOT = tm_core.REPO_ROOT
INDEX_DIR = REPO_ROOT / "runtime" / "embed_index"

ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
    "text-embedding/text-embedding"
)
MODEL = "text-embedding-v4"
DIMENSION = 1024  # default; --dim overrides for Phase 6b 2048 ablation
ALLOWED_DIMS = {64, 128, 256, 512, 768, 1024, 1536, 2048}
PREVIEW_CHARS = 200
SCHEMA_VERSION = "phase6-v1"

# Default retrieval instruction used on the QUERY side only.
DEFAULT_INSTRUCT = (
    "Given a user memory-search query, retrieve the most relevant "
    "tigermemory wiki or source page"
)

MAX_RETRIES = 3
BASE_DELAY = 0.5
MAX_DELAY = 8.0
TIMEOUT_S = 45
BATCH_SIZE = 10  # DashScope native accepts up to 25; we stay conservative.


# ---------- mode helpers ----------

def _paths(mode: str, dim: int = DIMENSION) -> tuple[Path, Path]:
    """Return (jsonl, meta) paths. Dim != 1024 gets a suffix to avoid
    overwriting Phase 6 indices. Dim 1024 keeps the legacy unsuffixed name
    for backward compat with the Phase 6 recall code paths.
    """
    if dim not in ALLOWED_DIMS:
        raise ValueError(f"dim must be one of {sorted(ALLOWED_DIMS)}, got {dim}")
    suffix = "" if dim == 1024 else f"_{dim}"
    if mode == "dense":
        return (
            INDEX_DIR / f"wiki_qwen_v4_dense{suffix}.jsonl",
            INDEX_DIR / f"wiki_qwen_v4_dense{suffix}.meta.json",
        )
    if mode == "hybrid":
        return (
            INDEX_DIR / f"wiki_qwen_v4_hybrid{suffix}.jsonl",
            INDEX_DIR / f"wiki_qwen_v4_hybrid{suffix}.meta.json",
        )
    raise ValueError(f"unknown mode: {mode}")


def _output_type(mode: str) -> str:
    return "dense&sparse" if mode == "hybrid" else "dense"


def _api_key() -> str:
    k = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not k:
        raise RuntimeError("DASHSCOPE_API_KEY env var is not set")
    return k


# ---------- HTTP / retry ----------

class _Stats:
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
    for tok in ("invalidapikey", "unauthorized", "forbidden", "model not found",
                "invalid parameter", "shape mismatch"):
        if tok in low:
            return "permanent"
    for tok in ("timeout", "timed out", "rate limit", "ratelimit",
                "connection refused", "connection reset",
                "temporarily unavailable", "server overloaded"):
        if tok in low:
            return "transient"
    return "unknown"


def _post_once(body: dict, api_key: str) -> dict:
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
        body_err = e.read().decode("utf-8", errors="replace")[:400]
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


def _embed_with_retry(body: dict, api_key: str, stats: _Stats) -> dict:
    attempt = 0
    while True:
        stats.requests += 1
        t0 = time.monotonic()
        try:
            data = _post_once(body, api_key)
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


def _request_body(texts: list[str], *, mode: str, side: str, instruct: str | None,
                  dim: int = DIMENSION) -> dict:
    """side ∈ {document, query}. mode ∈ {dense, hybrid}."""
    params: dict[str, Any] = {
        "dimension": dim,
        "text_type": side,
        "output_type": _output_type(mode),
    }
    if side == "query" and instruct:
        params["instruct"] = instruct
    return {
        "model": MODEL,
        "input": {"texts": texts},
        "parameters": params,
    }


def _parse_embeddings(d: dict, mode: str) -> list[tuple[list[float], list[tuple[int, float]]]]:
    """Return list of (dense, sparse_pairs) aligned with input order."""
    out_list: list[tuple[list[float], list[tuple[int, float]]]] = []
    rows = (d.get("output") or {}).get("embeddings") or []
    if not rows:
        raise RuntimeError(f"permanent: empty embeddings; raw_keys={sorted(d.keys())}")
    rows = sorted(rows, key=lambda r: r.get("text_index", 0))
    for r in rows:
        dense = r.get("embedding")
        if not isinstance(dense, list) or not dense:
            raise RuntimeError("permanent: missing dense embedding")
        pairs: list[tuple[int, float]] = []
        if mode == "hybrid":
            sparse = r.get("sparse_embedding") or r.get("sparse") or []
            if isinstance(sparse, list):
                for p in sparse:
                    if isinstance(p, dict) and "index" in p and "value" in p:
                        try:
                            pairs.append((int(p["index"]), float(p["value"])))
                        except (TypeError, ValueError):
                            continue
                    elif isinstance(p, (list, tuple)) and len(p) == 2:
                        try:
                            pairs.append((int(p[0]), float(p[1])))
                        except (TypeError, ValueError):
                            continue
        out_list.append(([float(x) for x in dense], pairs))
    return out_list


# ---------- index I/O ----------

def _load_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
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


def _save_index(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rel in sorted(entries):
            f.write(json.dumps(entries[rel], ensure_ascii=False) + "\n")
    tmp.replace(path)


def _save_meta(path: Path, meta: dict[str, Any]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _preview(body: str, n: int = PREVIEW_CHARS) -> str:
    stripped = _FRONTMATTER_RE.sub("", body, count=1)
    return " ".join(stripped[:n * 4].split())[:n]


# ---------- build ----------

def build(*, mode: str, dim: int = DIMENSION, force: bool = False, limit: int | None = None) -> dict:
    if mode not in ("dense", "hybrid"):
        raise ValueError(f"mode must be dense|hybrid, got {mode}")
    if dim not in ALLOWED_DIMS:
        raise ValueError(f"dim must be one of {sorted(ALLOWED_DIMS)}, got {dim}")
    api_key = _api_key()
    index_path, meta_path = _paths(mode, dim)
    existing = {} if force else _load_index(index_path)
    keep: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, str, list[str], str, str, str]] = []
    seen: set[str] = set()

    for abs_path, rel, title, aliases, body in tm_embed_index._iter_pages("wiki"):
        seen.add(rel)
        text = tm_embed_index._embed_text(rel, title, aliases, body)
        h = tm_embed_index._content_hash(rel, title, aliases, body)
        prior = existing.get(rel)
        if prior and prior.get("hash") == h and isinstance(prior.get("dense"), list):
            if mode == "hybrid" and not isinstance(prior.get("sparse"), list):
                pass  # need sparse → re-embed
            else:
                keep[rel] = dict(prior)
                keep[rel]["title"] = title
                continue
        partition = tm_embed_index._partition_of(rel)
        pending.append((rel, title, aliases, body, partition, text))

    if limit is not None:
        pending = pending[:limit]

    dropped = sorted(set(existing) - seen)
    stats = _Stats()
    embedded = 0
    started = time.monotonic()
    print(f"[qwen-v4-{mode}] reused={len(keep)} pending={len(pending)} dropped={len(dropped)} "
          f"batch={BATCH_SIZE}", file=sys.stderr)

    # Batch the pending list
    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start: batch_start + BATCH_SIZE]
        texts = [t[5] for t in batch]
        body_req = _request_body(texts, mode=mode, side="document", instruct=None, dim=dim)
        try:
            d = _embed_with_retry(body_req, api_key, stats)
            results = _parse_embeddings(d, mode)
        except RuntimeError as exc:
            print(f"[qwen-v4-{mode}] FAIL batch starting {batch_start}: {str(exc)[:240]}",
                  file=sys.stderr)
            raise

        if len(results) != len(batch):
            raise RuntimeError(f"permanent: result count mismatch: got={len(results)} want={len(batch)}")

        for (rel, title, aliases, body, partition, _text), (dense, sparse_pairs) in zip(batch, results):
            try:
                mtime = int((REPO_ROOT / rel).stat().st_mtime)
            except OSError:
                mtime = 0
            entry: dict[str, Any] = {
                "path": rel,
                "title": title,
                "aliases": aliases,
                "partition": partition,
                "hash": tm_embed_index._content_hash(rel, title, aliases, body),
                "mtime": mtime,
                "dim": len(dense),
                "dense": dense,
                "preview": _preview(body),
            }
            if mode == "hybrid":
                entry["sparse_count"] = len(sparse_pairs)
                entry["sparse"] = [[idx, val] for idx, val in sparse_pairs]
            keep[rel] = entry
            embedded += 1

        done = batch_start + len(batch)
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed else 0
        print(f"  embedded {done:>4}/{len(pending)} ({rate:.1f}/s, retries={stats.retries})",
              file=sys.stderr)
        if (batch_start // BATCH_SIZE) % 5 == 4 or done == len(pending):
            _save_index(index_path, keep)

    _save_index(index_path, keep)

    p50 = round(sorted(stats.latencies_ms)[len(stats.latencies_ms) // 2], 1) if stats.latencies_ms else 0.0
    p95 = round(sorted(stats.latencies_ms)[int(len(stats.latencies_ms) * 0.95)], 1) if stats.latencies_ms else 0.0
    meta = {
        "schema": SCHEMA_VERSION,
        "endpoint": ENDPOINT,
        "model": MODEL,
        "dimension": dim,
        "mode": mode,
        "output_type": _output_type(mode),
        "instruct_default": DEFAULT_INSTRUCT,
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
            "batch_size": BATCH_SIZE,
        },
    }
    _save_meta(meta_path, meta)
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
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _cosine_sparse(a: dict[int, float], b: dict[int, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for k, va in a.items():
        vb = b.get(k)
        if vb is not None:
            dot += va * vb
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_ENTRY_CACHE: dict[tuple[str, int], list[dict[str, Any]]] = {}


def _entries(mode: str, dim: int = DIMENSION) -> list[dict[str, Any]]:
    key = (mode, dim)
    if key not in _ENTRY_CACHE:
        _ENTRY_CACHE[key] = list(_load_index(_paths(mode, dim)[0]).values())
    return _ENTRY_CACHE[key]


_QUERY_CACHE: dict[tuple[str, int, str, str], tuple[list[float], dict[int, float]]] = {}


def _effective_instruct(instruct: str | None) -> str:
    """Resolve instruct from explicit arg → env TM_QWENV4_INSTRUCT_TEXT → DEFAULT.
    Env override exists for Phase 6b prompt ablation without code edits.
    """
    if instruct is not None:
        return instruct
    env_text = os.environ.get("TM_QWENV4_INSTRUCT_TEXT", "").strip()
    return env_text or DEFAULT_INSTRUCT


def _embed_query(query: str, *, mode: str, dim: int = DIMENSION,
                 instruct: str | None = None) -> tuple[list[float], dict[int, float]]:
    eff_instruct = _effective_instruct(instruct)
    cache_key = (mode, dim, eff_instruct, query)
    if cache_key in _QUERY_CACHE:
        return _QUERY_CACHE[cache_key]
    api_key = _api_key()
    stats = _Stats()
    body = _request_body([query], mode=mode, side="query", instruct=eff_instruct, dim=dim)
    d = _embed_with_retry(body, api_key, stats)
    parsed = _parse_embeddings(d, mode)
    dense, pairs = parsed[0]
    result = (dense, dict(pairs))
    _QUERY_CACHE[cache_key] = result
    return result


def search_dense(query: str, *, k: int = 10, dim: int = DIMENSION,
                 instruct: str | None = None) -> list[dict[str, Any]]:
    q_dense, _ = _embed_query(query, mode="dense", dim=dim, instruct=instruct)
    entries = _entries("dense", dim)
    scored: list[tuple[float, dict[str, Any]]] = []
    for e in entries:
        score = _cosine_dense(q_dense, e.get("dense") or [])
        scored.append((score, e))
    scored.sort(key=lambda t: -t[0])
    return [
        {
            "path": e["path"],
            "title": e.get("title", ""),
            "score": round(s, 6),
            "score_dense": round(s, 6),
            "score_sparse": 0.0,
            "source": f"qwen-v4-dense-{dim}",
        }
        for s, e in scored[:k]
    ]


def search_hybrid(query: str, *, k: int = 10, dense_weight: float = 0.5,
                  sparse_weight: float | None = None,
                  dim: int = DIMENSION,
                  instruct: str | None = None) -> list[dict[str, Any]]:
    if sparse_weight is None:
        sparse_weight = 1.0 - dense_weight
    q_dense, q_sparse = _embed_query(query, mode="hybrid", dim=dim, instruct=instruct)
    entries = _entries("hybrid", dim)
    scored: list[tuple[float, float, float, dict[str, Any]]] = []
    for e in entries:
        ds = _cosine_dense(q_dense, e.get("dense") or [])
        ss = _cosine_sparse(q_sparse, _to_sparse_dict(e.get("sparse") or []))
        fused = dense_weight * ds + sparse_weight * ss
        scored.append((fused, ds, ss, e))
    scored.sort(key=lambda t: -t[0])
    return [
        {
            "path": e["path"],
            "title": e.get("title", ""),
            "score": round(fused, 6),
            "score_dense": round(ds, 6),
            "score_sparse": round(ss, 6),
            "source": "qwen-v4-hybrid",
        }
        for fused, ds, ss, e in scored[:k]
    ]


def search_hybrid_branches(query: str, *, k: int = 10, dim: int = DIMENSION,
                           instruct: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Phase 6b: return separately ranked dense and sparse top-K from the hybrid
    index, NOT a fused score. Caller (e.g. triple-RRF) RRF-merges externally.
    """
    q_dense, q_sparse = _embed_query(query, mode="hybrid", dim=dim, instruct=instruct)
    entries = _entries("hybrid", dim)
    dense_scored: list[tuple[float, dict[str, Any]]] = []
    sparse_scored: list[tuple[float, dict[str, Any]]] = []
    for e in entries:
        ds = _cosine_dense(q_dense, e.get("dense") or [])
        ss = _cosine_sparse(q_sparse, _to_sparse_dict(e.get("sparse") or []))
        dense_scored.append((ds, e))
        sparse_scored.append((ss, e))
    dense_scored.sort(key=lambda t: -t[0])
    sparse_scored.sort(key=lambda t: -t[0])
    def fmt(label: str, scored: list[tuple[float, dict[str, Any]]]) -> list[dict[str, Any]]:
        return [
            {
                "path": e["path"],
                "title": e.get("title", ""),
                "score": round(s, 6),
                "source": label,
            }
            for s, e in scored[:k]
        ]
    return {
        "dense": fmt("qwen-v4-dense", dense_scored),
        "sparse": fmt("qwen-v4-sparse", sparse_scored),
    }


# ---------- CLI ----------

def cmd_build(args: argparse.Namespace) -> int:
    meta = build(mode=args.mode, dim=args.dim, force=args.force, limit=args.limit)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    index_path, meta_path = _paths(args.mode, args.dim)
    entries = _load_index(index_path)
    meta = _load_meta(meta_path)
    out = {
        "mode": args.mode,
        "exists": index_path.exists(),
        "entry_count": len(entries),
        "index_path": str(index_path.relative_to(REPO_ROOT)),
        "meta": meta,
    }
    if entries:
        sample = next(iter(entries.values()))
        out["dense_dim_sample"] = len(sample.get("dense") or [])
        if args.mode == "hybrid":
            out["sparse_count_sample"] = len(sample.get("sparse") or [])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if args.mode == "dense":
        hits = search_dense(args.query, k=args.k, dim=args.dim)
    else:
        hits = search_hybrid(args.query, k=args.k, dim=args.dim,
                             dense_weight=args.dense_weight)
    print(json.dumps(hits, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tm_qwen_v4_index.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build / refresh a qwen-v4 index")
    b.add_argument("--mode", choices=["dense", "hybrid"], required=True)
    b.add_argument("--dim", type=int, default=DIMENSION,
                   help=f"embedding dimension; one of {sorted(ALLOWED_DIMS)} (default 1024)")
    b.add_argument("--force", action="store_true")
    b.add_argument("--limit", type=int, default=None)
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("stats", help="print index size + meta")
    s.add_argument("--mode", choices=["dense", "hybrid"], required=True)
    s.add_argument("--dim", type=int, default=DIMENSION)
    s.set_defaults(func=cmd_stats)

    q = sub.add_parser("search", help="ad-hoc query (debug)")
    q.add_argument("--mode", choices=["dense", "hybrid"], required=True)
    q.add_argument("--dim", type=int, default=DIMENSION)
    q.add_argument("query")
    q.add_argument("-k", type=int, default=10)
    q.add_argument("--dense-weight", type=float, default=0.5)
    q.set_defaults(func=cmd_search)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
