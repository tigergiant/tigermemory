#!/usr/bin/env python3
"""
tools/tm_embed_index.py — build & query an embedding index over wiki/ + sources/.

Why this exists: lexical AND search (`tm_core.search_wiki`) returns 0 hits for
many Chinese / synonym / cross-lingual queries. 2026-05-06 Phase 2e proved
post-hoc rerank cannot fix that — if the candidate pool is empty, reranking
nothing is still nothing. This module replaces lexical recall as the candidate
generator. Lexical stays as a parallel branch because exact-token English
queries are still served well by token AND.

Storage: `runtime/embed_index/<scope>.jsonl` (gitignored, rebuilt on demand).
  Each line: {"path", "title", "hash", "mtime", "vec"}

Build / refresh / search / stats are CLI subcommands; library callers should
use `search()` directly.

Backend: respects EMBEDDING_BASE_URL / EMBEDDING_MODEL env (see tm_core).
Default expectation in this repo is local Qwen3-Embedding-0.6B at
http://localhost:19190/v1 (free, no rate limit, dim=1024).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys
import time
from typing import Any, Iterable

import tm_core

REPO_ROOT = tm_core.REPO_ROOT
INDEX_DIR = REPO_ROOT / "runtime" / "embed_index"

# Scope = which roots to walk. Each scope produces one index file so callers
# can reload cheaply and skip irrelevant content.
SCOPES: dict[str, tuple[str, ...]] = {
    "wiki": ("wiki", "sources"),  # default: agent-facing knowledge
    "wiki_only": ("wiki",),
    "sources_only": ("sources",),
}

# Embedding text budget. Qwen3-Embedding-0.6B accepts up to ~8K tokens; we
# keep characters well under that to stay safe across CJK content. Title +
# path slug are prepended so very short pages still get a usable signal.
EMBED_TEXT_CHARS = 6000


# ---------- text extraction ----------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_TITLE_FM_RE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _extract_title(text: str, path: pathlib.Path) -> str:
    """Title precedence: frontmatter `title:` > first H1 > filename stem."""
    m = _FRONTMATTER_RE.match(text)
    if m:
        tm = _TITLE_FM_RE.search(m.group(1))
        if tm:
            return tm.group(1).strip()
    h1 = _H1_RE.search(text)
    if h1:
        return h1.group(1).strip()
    return path.stem


def _slug_words(rel_path: str) -> str:
    """Expand path separators / hyphens to words so slug tokens count."""
    return re.sub(r"[^a-z0-9_]+", " ", rel_path.lower()).strip()


def _embed_text(rel_path: str, title: str, body: str) -> str:
    """Compose the string handed to the embedding model.

    Order: title | slug | body. Title and slug are short, high-signal, and
    compensate for very short pages (e.g. index stubs).
    """
    parts = [title.strip(), _slug_words(rel_path), body.strip()]
    composed = "\n\n".join(p for p in parts if p)
    return composed[:EMBED_TEXT_CHARS]


def _content_hash(rel_path: str, title: str, body: str) -> str:
    h = hashlib.md5()
    h.update(rel_path.encode("utf-8"))
    h.update(b"\n")
    h.update(title.encode("utf-8"))
    h.update(b"\n")
    # only first EMBED_TEXT_CHARS matter — that's what gets embedded
    h.update(body[:EMBED_TEXT_CHARS].encode("utf-8"))
    return h.hexdigest()


def _iter_pages(scope: str) -> Iterable[tuple[pathlib.Path, str, str, str]]:
    """Yield (abs_path, rel_path, title, body) for every .md under scope roots."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; valid: {sorted(SCOPES)}")
    for root in SCOPES[scope]:
        root_dir = REPO_ROOT / root
        if not root_dir.exists():
            continue
        for p in sorted(root_dir.rglob("*.md")):
            try:
                body = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = p.relative_to(REPO_ROOT).as_posix()
            title = _extract_title(body, p)
            yield p, rel, title, body


# ---------- index file I/O ----------

def _index_path(scope: str) -> pathlib.Path:
    return INDEX_DIR / f"{scope}.jsonl"


def _load_index(scope: str) -> dict[str, dict[str, Any]]:
    """Return {rel_path: entry_dict}. Empty dict if file missing."""
    path = _index_path(scope)
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            rel = entry.get("path")
            if rel:
                out[rel] = entry
    return out


def _save_index(scope: str, entries: dict[str, dict[str, Any]]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    path = _index_path(scope)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rel in sorted(entries):
            f.write(json.dumps(entries[rel], ensure_ascii=False) + "\n")
    tmp.replace(path)


# ---------- build / refresh ----------

def build(scope: str = "wiki", *, force: bool = False, batch_log: int = 50) -> dict[str, Any]:
    """Build or refresh the embedding index for `scope`.

    Reuses cached vectors whose content hash hasn't changed (`force=True`
    bypasses cache). Returns a stats dict.
    """
    existing = {} if force else _load_index(scope)
    keep: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, str, str]] = []  # (rel, title, embed_text)
    seen_paths: set[str] = set()

    for abs_path, rel, title, body in _iter_pages(scope):
        seen_paths.add(rel)
        text_for_embed = _embed_text(rel, title, body)
        h = _content_hash(rel, title, body)
        prior = existing.get(rel)
        if prior and prior.get("hash") == h and isinstance(prior.get("vec"), list):
            keep[rel] = {
                "path": rel,
                "title": title,
                "hash": h,
                "mtime": int(abs_path.stat().st_mtime),
                "vec": prior["vec"],
            }
        else:
            pending.append((rel, title, text_for_embed))

    # Drop entries whose source file disappeared (rename / archive).
    dropped = sorted(set(existing) - seen_paths)

    embedded = 0
    if pending:
        # tm_core.embed_texts handles batching internally (default 10/req).
        for start in range(0, len(pending), batch_log):
            batch = pending[start:start + batch_log]
            texts = [t[2] for t in batch]
            t0 = time.perf_counter()
            vectors = tm_core.embed_texts(texts)
            dt = (time.perf_counter() - t0) * 1000
            print(
                f"  embedded {start + len(batch):>4}/{len(pending)} "
                f"(+{len(batch)} in {dt:.0f}ms)",
                file=sys.stderr,
            )
            for (rel, title, _), vec in zip(batch, vectors):
                # mtime fetched lazily — file might have been touched between
                # iter_pages and now; we re-stat from REPO_ROOT/rel.
                try:
                    mtime = int((REPO_ROOT / rel).stat().st_mtime)
                except OSError:
                    mtime = 0
                # we know the hash from the first pass; recompute the same way
                body_now = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
                h_now = _content_hash(rel, title, body_now)
                keep[rel] = {
                    "path": rel,
                    "title": title,
                    "hash": h_now,
                    "mtime": mtime,
                    "vec": vec,
                }
                embedded += 1

    _save_index(scope, keep)
    return {
        "scope": scope,
        "total_pages": len(keep),
        "reused": len(keep) - embedded,
        "embedded": embedded,
        "dropped": dropped,
    }


# ---------- search ----------

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def search(query: str, *, scope: str = "wiki", k: int = 5) -> list[dict[str, Any]]:
    """Embed `query`, cosine-rank against the index, return top-k entries.

    Each entry: {"path", "title", "score", "source": "embedding"}. Snippet is
    deferred to the caller because rebuilding it requires re-reading the file
    (the index intentionally does not cache full bodies).
    """
    entries = _load_index(scope)
    if not entries:
        raise RuntimeError(
            f"embed index empty for scope {scope!r}; "
            f"run `python tools/tm_embed_index.py build --scope {scope}` first"
        )
    q_vec = tm_core.embed_one(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in entries.values():
        vec = entry.get("vec")
        if not isinstance(vec, list):
            continue
        score = _cosine(q_vec, vec)
        scored.append((score, entry))
    scored.sort(key=lambda kv: -kv[0])
    out: list[dict[str, Any]] = []
    for score, entry in scored[:k]:
        out.append({
            "path": entry["path"],
            "title": entry["title"],
            "score": round(score, 4),
            "source": "embedding",
        })
    return out


def stats(scope: str = "wiki") -> dict[str, Any]:
    entries = _load_index(scope)
    if not entries:
        return {"scope": scope, "total": 0, "exists": False}
    dims = {len(e.get("vec", [])) for e in entries.values()}
    return {
        "scope": scope,
        "exists": True,
        "total": len(entries),
        "dims": sorted(dims),
        "index_path": str(_index_path(scope).relative_to(REPO_ROOT)),
    }


# ---------- CLI ----------

def cmd_build(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    result = build(scope=args.scope, force=args.force)
    dt = (time.perf_counter() - t0) * 1000
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"build done in {dt:.0f} ms", file=sys.stderr)
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    return cmd_build(argparse.Namespace(scope=args.scope, force=False))


def cmd_search(args: argparse.Namespace) -> int:
    hits = search(args.query, scope=args.scope, k=args.k)
    print(json.dumps(hits, ensure_ascii=False, indent=2))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    print(json.dumps(stats(scope=args.scope), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="tm_embed_index.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="(re)build the embedding index for a scope")
    b.add_argument("--scope", default="wiki", choices=sorted(SCOPES))
    b.add_argument("--force", action="store_true",
                   help="re-embed every page even if hash matches cache")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("refresh", help="alias of `build` without --force")
    r.add_argument("--scope", default="wiki", choices=sorted(SCOPES))
    r.set_defaults(func=cmd_refresh)

    s = sub.add_parser("search", help="cosine-search the index")
    s.add_argument("query")
    s.add_argument("--scope", default="wiki", choices=sorted(SCOPES))
    s.add_argument("--k", type=int, default=5)
    s.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="print index size + dim")
    st.add_argument("--scope", default="wiki", choices=sorted(SCOPES))
    st.set_defaults(func=cmd_stats)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
