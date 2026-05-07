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
import datetime as _dt
import hashlib
import json
import math
import os
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

# Root-level governance files that are first-class agent knowledge but
# don't live under wiki/ or sources/. Only added to the default `wiki`
# scope (agent-facing) so narrower scopes stay focused. `log.md` is
# explicitly excluded — it's a `git log` compilation that drifts on
# every commit, has no frontmatter, and would just dilute partition
# centroids with low-signal noise.
EXTRA_ROOT_FILES: dict[str, tuple[str, ...]] = {
    "wiki": ("AGENTS.md",),
}

# Embedding text budget. Qwen3-Embedding-0.6B accepts up to ~8K tokens; we
# keep characters well under that to stay safe across CJK content. Title +
# path slug are prepended so very short pages still get a usable signal.
EMBED_TEXT_CHARS = 6000


# ---------- text extraction ----------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_TITLE_FM_RE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
# Inline list form: aliases: ["a", "b"] — covers ~all current pages.
# Multi-line YAML list (aliases:\n  - a\n  - b) is rare; skip for now.
_ALIASES_INLINE_RE = re.compile(r'^aliases:\s*\[(.+?)\]\s*$', re.MULTILINE)
_ALIAS_ITEM_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')

# Frontmatter `summary: "..."` (single-line, single or double-quoted).
_SUMMARY_FM_RE = re.compile(r'^summary:\s*(["\'])(.+?)\1\s*$', re.MULTILINE)

# `## 摘要` / `## Summary` / `## 概述` / `## TL;DR` headings (case-insensitive).
_SUMMARY_HEADING_RE = re.compile(
    r'^##\s+(?:摘要|Summary|概述|TL;DR)\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Summary character budget (~OpenViking L0 ~100 tokens ≈ 240 CJK chars).
SUMMARY_MAX_CHARS = 240

# Noise paragraph patterns to skip when extracting summary. Most external
# sources (OpenClaw 104 pages all share this) have a "## Documentation
# Index" boilerplate paragraph right after the H1 that drowns the real
# content. Match case-insensitively against full paragraph text.
_NOISE_PATTERNS = (
    "fetch the complete documentation",
    "use this file to discover all available pages",
    "this page intentionally left blank",
)
_NOISE_RE = re.compile("|".join(re.escape(p) for p in _NOISE_PATTERNS), re.IGNORECASE)


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


def _extract_summary(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Extract a short L0-style summary from a markdown body.

    Borrowed from OpenViking's L0 (Abstract) layer (see
    `wiki/systems/memory-retrieval-eval.md` Phase 2j). OpenViking
    asynchronously LLM-generates ~100-token abstracts; tigermemory exploits
    its existing `## 摘要` writing convention (87/100 wiki pages already
    have one) to do the same thing **rule-based, zero LLM cost**.

    Priority:
      1. Frontmatter `summary: "..."` field (explicit override).
      2. `## 摘要` / `## Summary` / `## 概述` / `## TL;DR` heading first
         non-empty paragraph (until next heading or blank line).
      3. H1 followed by first non-empty paragraph.
      4. Empty string (caller falls back to title-only embed).

    Returns up to `max_chars` chars trimmed at safe boundaries.
    """
    # 1. frontmatter summary field
    fm = _FRONTMATTER_RE.match(text)
    if fm:
        sm = _SUMMARY_FM_RE.search(fm.group(1))
        if sm:
            return sm.group(2).strip()[:max_chars]

    # Strip frontmatter for body search
    body = _FRONTMATTER_RE.sub('', text, count=1) if fm else text

    def _first_signal_paragraph(after_idx: int) -> str:
        """Walk text after `after_idx`, collect paragraphs, return first
        non-noise paragraph (≥20 chars after trimming).

        A paragraph is a run of non-empty, non-heading lines. Blockquote /
        list markers are stripped. Code fences (``` lines) are skipped.
        Noise paragraphs (matching `_NOISE_RE`, e.g. OpenClaw documentation
        index boilerplate) are skipped, not returned.
        """
        rest = body[after_idx:]
        paragraphs: list[str] = []
        current: list[str] = []
        in_code = False
        for line in rest.splitlines():
            stripped = line.strip()
            if stripped.startswith('```'):
                in_code = not in_code
                if current:
                    paragraphs.append(' '.join(current))
                    current = []
                continue
            if in_code:
                continue
            if stripped.startswith('#'):
                if current:
                    paragraphs.append(' '.join(current))
                    current = []
                continue
            if not stripped:
                if current:
                    paragraphs.append(' '.join(current))
                    current = []
                continue
            cleaned = re.sub(r'^[>\-*+]\s*', '', stripped)
            if cleaned:
                current.append(cleaned)
            # Hard ceiling so we don't scan whole files for the noise check
            if sum(len(s) for s in current) >= max_chars * 4:
                paragraphs.append(' '.join(current))
                current = []
                break
        if current:
            paragraphs.append(' '.join(current))

        for para in paragraphs:
            text_p = para.strip()
            if len(text_p) < 20:
                continue
            if _NOISE_RE.search(text_p):
                continue
            return text_p[:max_chars]
        return ''

    # 2. `## 摘要` heading
    m = _SUMMARY_HEADING_RE.search(body)
    if m:
        para = _first_signal_paragraph(m.end())
        if para:
            return para

    # 3. H1 + first paragraph (skipping noise like docs index boilerplate)
    h1 = _H1_RE.search(body)
    if h1:
        para = _first_signal_paragraph(h1.end())
        if para:
            return para

    return ''


def _extract_aliases(text: str) -> list[str]:
    """Pull frontmatter `aliases: ["a", "b"]` inline list. [] when absent.

    OpenViking v0.3.13 `embedding.text_source` 借鉴：metadata 字段（标题、
    aliases）作为 first-class embedding 输入，不依赖 body 顺带覆盖。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return []
    am = _ALIASES_INLINE_RE.search(m.group(1))
    if not am:
        return []
    items: list[str] = []
    for match in _ALIAS_ITEM_RE.finditer(am.group(1)):
        items.append((match.group(1) or match.group(2)).strip())
    return [a for a in items if a]


def _embed_text(rel_path: str, title: str, body: str) -> str:
    """Compose the string handed to the embedding model.

    Order: title | slug | body. Title and slug are short, high-signal, and
    compensate for very short pages.

    Phase 2j experiment (2026-05-06, reverted): tried prepending an
    OpenViking-L0-style summary extracted from `## 摘要` / H1+first-para.
    Result: -3 net hit@3 (3 broken edges, 0 fixed). Root cause: in our
    single-vector setup, summary text overlaps the body[:6000] head that
    already contains it (87/100 wiki pages have `## 摘要`); prepending it
    just duplicates signal and reorders neighboring pages. OpenViking's L0
    is a *separate* embedding vector (multi-vector retrieval), not a
    prefix — we can't get the same lift in single-vector mode.

    `_extract_summary` is kept for future use:
      • LLM-generated `summary:` frontmatter on `sources/external/*` pages
        that lack `## 摘要` (would actually add new signal there).
      • Multi-vector retrieval where summary gets its own vector.
    See wiki/systems/memory-retrieval-eval.md Phase 2j for full analysis.
    """
    parts = [title.strip(), _slug_words(rel_path), body.strip()]
    composed = "\n\n".join(p for p in parts if p)
    return composed[:EMBED_TEXT_CHARS]


# Bump `_HASH_SCHEMA` whenever `_embed_text` composition changes; cached
# vectors are then automatically invalidated on next refresh (no --force).
_HASH_SCHEMA = b"v4-revert-summary-front"


def _content_hash(rel_path: str, title: str, body: str) -> str:
    h = hashlib.md5()
    h.update(_HASH_SCHEMA)
    h.update(b"\n")
    h.update(rel_path.encode("utf-8"))
    h.update(b"\n")
    h.update(title.encode("utf-8"))
    h.update(b"\n")
    # only first EMBED_TEXT_CHARS matter — that's what gets embedded
    h.update(body[:EMBED_TEXT_CHARS].encode("utf-8"))
    return h.hexdigest()


def _iter_pages(scope: str) -> Iterable[tuple[pathlib.Path, str, str, list[str], str]]:
    """Yield (abs_path, rel_path, title, aliases, body) for every .md under
    scope roots, plus any explicit `EXTRA_ROOT_FILES` for the scope (e.g.
    root AGENTS.md). Root files have empty `_partition_of` and so are
    excluded from centroid computation by design."""
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
            aliases = _extract_aliases(body)
            yield p, rel, title, aliases, body

    for rel_name in EXTRA_ROOT_FILES.get(scope, ()):
        p = REPO_ROOT / rel_name
        if not p.exists():
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        title = _extract_title(body, p)
        aliases = _extract_aliases(body)
        yield p, rel, title, aliases, body


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


# ---------- partition centroids (Phase 2k: OpenViking score_propagation) ----------

def _partition_of(rel_path: str) -> str:
    """Return the partition key for a page path.

    Phase 2k borrows OpenViking's hierarchical retrieval idea: pages share
    a parent partition, and queries can be routed by partition centroid
    similarity before page-level cosine.

    Conventions (matching tigermemory layout):
      - `wiki/<part>/...`              -> `wiki/<part>`
        (e.g. `wiki/systems/foo.md` and `wiki/systems/lessons/x.md` share
        the `wiki/systems` partition, even nested)
      - `sources/<sub1>/<sub2>/...`    -> `sources/<sub1>/<sub2>`
        (e.g. `sources/external/openclaw/x.md` -> `sources/external/openclaw`)
      - `sources/<sub1>/...` (no sub2) -> `sources/<sub1>`
        (e.g. `sources/huawei-celia/x.md` -> `sources/huawei-celia`)
      - other (root files like `AGENTS.md`) -> `''` (no partition)
    """
    parts = rel_path.split("/")
    if len(parts) < 2:
        return ""
    head = parts[0]
    if head == "wiki" and len(parts) >= 2:
        return f"wiki/{parts[1]}"
    if head == "sources":
        if len(parts) >= 3:
            return f"sources/{parts[1]}/{parts[2]}"
        return f"sources/{parts[1]}"
    return head


def _centroid_path(scope: str) -> pathlib.Path:
    return INDEX_DIR / f"{scope}.centroids.json"


def _vec_mean(vectors: list[list[float]]) -> list[float]:
    """Return the per-dimension mean of `vectors`. Empty list if input empty."""
    if not vectors:
        return []
    dim = len(vectors[0])
    sums = [0.0] * dim
    n = 0
    for v in vectors:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            sums[i] += x
        n += 1
    if n == 0:
        return []
    return [s / n for s in sums]


def compute_centroids(scope: str) -> dict[str, list[float]]:
    """Compute per-partition centroid vectors from the current index.

    Returns {partition_key: mean_vector}. Empty partition key (root files)
    is excluded — propagation has no meaning for a 1-page partition.
    """
    entries = _load_index(scope)
    by_part: dict[str, list[list[float]]] = {}
    for entry in entries.values():
        vec = entry.get("vec")
        if not isinstance(vec, list):
            continue
        part = _partition_of(entry["path"])
        if not part:
            continue
        by_part.setdefault(part, []).append(vec)
    centroids: dict[str, list[float]] = {}
    for part, vecs in by_part.items():
        if len(vecs) < 2:  # 1-page partition gives same vec as page itself
            continue
        c = _vec_mean(vecs)
        if c:
            centroids[part] = c
    return centroids


def _save_centroids(scope: str, centroids: dict[str, list[float]]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    path = _centroid_path(scope)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(centroids, f, ensure_ascii=False)
    tmp.replace(path)


def _load_centroids(scope: str) -> dict[str, list[float]]:
    path = _centroid_path(scope)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


# ---------- index meta manifest (P0-1: env consistency guard) ----------
#
# Why this exists: `runtime/openmemory/.env` may point at a different
# embedding model/dimension than what the cached `wiki.jsonl` was built
# with (e.g. .env says ARK doubao-embedding-vision dim=2048, but the
# index on disk is Qwen3-Embedding-0.6B dim=1024). Without a guard,
# `search()` will silently cosine a 2048-dim query vec against 1024-dim
# entry vecs (returning 0.0 every time after the early-return) and
# poison every eval that follows. The meta file is the single source
# of truth: built once when the index is built, validated on every
# search() call.


def _meta_path(scope: str) -> pathlib.Path:
    return INDEX_DIR / f"{scope}.meta.json"


def _build_meta(scope: str, entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compose the meta dict written alongside the index.

    `embedding_dimensions` comes from the *actual* vector length, not
    from the env hint, because the env hint is optional and the server
    may choose its own dim.
    """
    actual_dim = 0
    for entry in entries.values():
        vec = entry.get("vec")
        if isinstance(vec, list) and vec:
            actual_dim = len(vec)
            break
    try:
        cfg = tm_core.embedding_config()
        base = cfg["base"]
        model = cfg["model"]
        env_dim = cfg.get("dim")
    except RuntimeError:
        # Embedding not configured (e.g. eval-only env). Fall back to
        # whatever the env says directly so the meta still records intent.
        base = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
        model = os.environ.get("EMBEDDING_MODEL", "")
        env_dim_raw = os.environ.get("EMBEDDING_DIMENSIONS", "").strip()
        env_dim = int(env_dim_raw) if env_dim_raw else None
    built_at = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).isoformat(timespec="seconds")
    return {
        "scope": scope,
        "embedding_base_url": base,
        "embedding_model": model,
        "embedding_dimensions": actual_dim,
        "embedding_dimensions_env_hint": env_dim,
        "hash_schema": _HASH_SCHEMA.decode("utf-8", errors="replace"),
        "entry_count": len(entries),
        "built_at": built_at,
    }


def _save_meta(scope: str, meta: dict[str, Any]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    path = _meta_path(scope)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _load_meta(scope: str) -> dict[str, Any] | None:
    path = _meta_path(scope)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


class IndexConfigMismatch(RuntimeError):
    """Raised when the live embedding env doesn't match what the index was built with."""


def _check_query_compat(scope: str, query_dim: int) -> None:
    """Reject queries whose embedding dim doesn't match the index meta.

    No-op (with a stderr warning) when meta is missing — that case only
    happens for legacy indexes built before P0-1; users will see the
    warning and can `build` once to materialize the meta.
    """
    meta = _load_meta(scope)
    if meta is None:
        print(
            f"[tm_embed_index] WARN: scope={scope!r} has no meta file; "
            f"run `python tools/tm_embed_index.py build --scope {scope}` to materialize one.",
            file=sys.stderr,
        )
        return
    index_dim = int(meta.get("embedding_dimensions") or 0)
    if index_dim and query_dim and index_dim != query_dim:
        # Surface env vs index for fast diagnosis.
        env_base = os.environ.get("EMBEDDING_BASE_URL", "")
        env_model = os.environ.get("EMBEDDING_MODEL", "")
        raise IndexConfigMismatch(
            f"embedding dim mismatch for scope={scope!r}: "
            f"index built with {meta.get('embedding_model')!r} dim={index_dim}, "
            f"query was embedded with {env_model!r} dim={query_dim} "
            f"(EMBEDDING_BASE_URL={env_base!r}). "
            f"Either re-export env to match the index, or rebuild the index "
            f"with the desired model."
        )


def _propagation_alpha(explicit: float | None) -> float:
    """Resolve effective alpha: explicit arg > env var > 0.0 (off)."""
    if explicit is not None:
        return max(0.0, min(1.0, explicit))
    env = os.environ.get("TIGERMEMORY_PROPAGATION_ALPHA")
    if env:
        try:
            return max(0.0, min(1.0, float(env)))
        except ValueError:
            return 0.0
    return 0.0


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

    for abs_path, rel, title, _aliases, body in _iter_pages(scope):
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
    # Persist meta manifest so `search()` can guard against
    # model/dimension drift (P0-1).
    meta = _build_meta(scope, keep)
    _save_meta(scope, meta)
    return {
        "scope": scope,
        "total_pages": len(keep),
        "reused": len(keep) - embedded,
        "embedded": embedded,
        "dropped": dropped,
        "meta": meta,
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


def search(
    query: str,
    *,
    scope: str = "wiki",
    k: int = 5,
    propagation_alpha: float | None = None,
) -> list[dict[str, Any]]:
    """Embed `query`, cosine-rank against the index, return top-k entries.

    Each entry: {"path", "title", "score", "source": "embedding"}. Snippet is
    deferred to the caller because rebuilding it requires re-reading the file
    (the index intentionally does not cache full bodies).

    Phase 2k: if `propagation_alpha` > 0 (or env
    `TIGERMEMORY_PROPAGATION_ALPHA` set), final score is
    `(1-α) * cosine(q, page) + α * cosine(q, partition_centroid)`,
    borrowed from OpenViking `retrieval.score_propagation_alpha`. Default
    α=0 preserves baseline pure-cosine behavior.
    """
    entries = _load_index(scope)
    if not entries:
        raise RuntimeError(
            f"embed index empty for scope {scope!r}; "
            f"run `python tools/tm_embed_index.py build --scope {scope}` first"
        )
    alpha = _propagation_alpha(propagation_alpha)
    centroids = _load_centroids(scope) if alpha > 0 else {}
    q_vec = tm_core.embed_one(query)
    # P0-1 guard: refuse to cosine when query dim doesn't match index dim.
    # Catches the .env-vs-index drift that otherwise produces silent zeros.
    _check_query_compat(scope, len(q_vec))
    # Pre-compute query × centroid cosine per partition (cheap, ~10 partitions).
    part_score: dict[str, float] = {
        part: _cosine(q_vec, c) for part, c in centroids.items()
    } if alpha > 0 else {}
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in entries.values():
        vec = entry.get("vec")
        if not isinstance(vec, list):
            continue
        page_score = _cosine(q_vec, vec)
        if alpha > 0:
            part = _partition_of(entry["path"])
            p_s = part_score.get(part, 0.0)
            score = (1.0 - alpha) * page_score + alpha * p_s
        else:
            score = page_score
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
        return {"scope": scope, "total": 0, "exists": False, "meta": _load_meta(scope)}
    dims = {len(e.get("vec", [])) for e in entries.values()}
    return {
        "scope": scope,
        "exists": True,
        "total": len(entries),
        "dims": sorted(dims),
        "index_path": str(_index_path(scope).relative_to(REPO_ROOT)),
        "meta": _load_meta(scope),
    }


# ---------- CLI ----------

def cmd_build(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    result = build(scope=args.scope, force=args.force)
    # Recompute partition centroids from the freshly-built index.
    centroids = compute_centroids(args.scope)
    _save_centroids(args.scope, centroids)
    result["partitions"] = len(centroids)
    # Refresh meta now that centroids are also in place (entry_count
    # already reflects index; but we re-materialize so partitions count
    # would naturally appear if we ever add it to meta).
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
