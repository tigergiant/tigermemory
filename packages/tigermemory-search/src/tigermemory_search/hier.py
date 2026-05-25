#!/usr/bin/env python3
"""
tools/tm_hier_index.py — build L0/L1/L2 hierarchical index for tigermemory.

Phase 4 (2026-05-09): eval-only prototype of OpenViking-style multi-layer index.
Goal: improve hit@1 by separating abstract (L0), overview (L1), and body (L2)
signals into distinct vectors with hierarchical recall scoring.

Storage: runtime/embed_index/wiki_layers.jsonl (gitignored, eval-only).
  Each line: {"path", "layer", "title", "aliases", "partition", "text_hash", "vec", "preview"}
  layer ∈ {"L0", "L1", "L2"}

Meta: runtime/embed_index/wiki_layers.meta.json
  Records embedding config, schema, entry_count, page_count, layer_counts, built_at.

Scoring (in tm_memory_eval.py):
  page_score = 0.45 * max_L0 + 0.35 * max_L1 + 0.20 * max_L2
  Missing layers score 0.

Text rules:
  L0 (~100 tokens / 320 chars): frontmatter summary > ## 摘要/Summary/概述/TL;DR first para
    > H1 after first para > title+aliases+slug fallback.
  L1 (~1k tokens / 3000 chars): title + aliases + summary + first 3 H2 headings with excerpts.
  L2: title + aliases + slug + body[:6000] (same as v5 production).

Exclusions: skip OpenClaw documentation boilerplate, blank paragraphs, code blocks.
No LLM calls: v1 uses rule-based extraction only.
Inputs: CLI args, fixture cases, trace JSONL, wiki/Mem0 data, or local index files as selected by the command.
Outputs: Search/eval/trace/index reports printed to stdout or written to the requested output path.
Depends-on (must-have): tm_core search/memory helpers, local Markdown/JSONL files, and optional configured LLM or embedding providers.
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
from typing import Any

import tigermemory_core as tm_core

REPO_ROOT = tm_core.REPO_ROOT
INDEX_DIR = REPO_ROOT / "runtime" / "embed_index"

# ---------- text extraction regexes (borrowed from tm_embed_index.py) ----------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_TITLE_FM_RE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_ALIASES_INLINE_RE = re.compile(r'^aliases:\s*\[(.+?)\]\s*$', re.MULTILINE)
_ALIAS_ITEM_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_SUMMARY_FM_RE = re.compile(r'^summary:\s*(["\'])(.+?)\1\s*$', re.MULTILINE)
_SUMMARY_HEADING_RE = re.compile(
    r'^##\s+(?:摘要|Summary|概述|TL;DR)\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Noise patterns to skip (OpenClaw docs boilerplate, etc.)
_NOISE_PATTERNS = (
    "fetch the complete documentation",
    "use this file to discover all available pages",
    "this page intentionally left blank",
)
_NOISE_RE = re.compile("|".join(re.escape(p) for p in _NOISE_PATTERNS), re.IGNORECASE)

# Character budgets
L0_MAX_CHARS = 320
L1_MAX_CHARS = 3000
L2_MAX_CHARS = 6000


def _extract_title(text: str, path: pathlib.Path) -> str:
    """Title precedence: frontmatter title > first H1 > filename stem."""
    m = _FRONTMATTER_RE.match(text)
    if m:
        tm = _TITLE_FM_RE.search(m.group(1))
        if tm:
            return tm.group(1).strip()
    h1 = _H1_RE.search(text)
    if h1:
        return h1.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def _extract_aliases(text: str) -> list[str]:
    """Pull frontmatter aliases: ["a", "b"] inline list."""
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


def _extract_body(text: str) -> str:
    """Return text after frontmatter (if any)."""
    m = _FRONTMATTER_RE.match(text)
    if m:
        return text[m.end():]
    return text


def _first_signal_paragraph(start_pos: int, body: str) -> str:
    """Extract first non-noise paragraph starting from start_pos."""
    paras = body[start_pos:].split("\n\n")
    for para in paras:
        para = para.strip()
        if not para or _NOISE_RE.search(para):
            continue
        # Skip code blocks (indented or fenced)
        if para.startswith("```") or para.startswith("    "):
            continue
        return para
    return ""


def _extract_l0_text(title: str, aliases: list[str], body: str, rel_path: str) -> str:
    """Extract L0 abstract text (~100 tokens / 320 chars).

    Priority:
      1. frontmatter summary
      2. ## 摘要/Summary/概述/TL;DR first signal paragraph
      3. H1 after first signal paragraph
      4. title + aliases + slug fallback
    """
    # 1. frontmatter summary
    m = _FRONTMATTER_RE.match(body)
    if m:
        sm = _SUMMARY_FM_RE.search(m.group(1))
        if sm:
            return sm.group(2).strip()[:L0_MAX_CHARS]

    # 2. Summary heading first paragraph
    shm = _SUMMARY_HEADING_RE.search(body)
    if shm:
        para = _first_signal_paragraph(shm.end(), body)
        if para:
            return para[:L0_MAX_CHARS]

    # 3. H1 after first paragraph
    h1 = _H1_RE.search(body)
    if h1:
        para = _first_signal_paragraph(h1.end(), body)
        if para:
            return para[:L0_MAX_CHARS]

    # 4. title + aliases + slug fallback
    slug = rel_path.replace("/", " ").replace("-", " ").replace("_", " ")
    alias_line = "; ".join(aliases)
    parts = [title, alias_line, slug]
    composed = " ".join(p for p in parts if p)
    return composed[:L0_MAX_CHARS]


def _extract_l1_text(title: str, aliases: list[str], body: str, rel_path: str) -> str:
    """Extract L1 overview text (~1k tokens / 3000 chars).

    Composition: title + aliases + summary (L0) + first 3 H2 headings with excerpts.
    """
    summary = _extract_l0_text(title, aliases, body, rel_path)
    parts = [title]
    if aliases:
        parts.append("; ".join(aliases))
    if summary:
        parts.append(summary)

    # Extract first 3 H2 headings with short excerpts
    h2_matches = list(_H2_RE.finditer(body))[:3]
    for h2_match in h2_matches:
        heading = h2_match.group(1).strip()
        parts.append(f"## {heading}")
        # Get first paragraph after this H2
        excerpt = _first_signal_paragraph(h2_match.end(), body)
        if excerpt:
            # Limit excerpt to ~200 chars
            parts.append(excerpt[:200])

    composed = "\n\n".join(parts)
    return composed[:L1_MAX_CHARS]


def _extract_l2_text(title: str, aliases: list[str], body: str, rel_path: str) -> str:
    """Extract L2 body text (same as v5 production: title + aliases + slug + body[:6000])."""
    alias_line = "; ".join(a.strip() for a in aliases if a.strip())
    slug = rel_path.replace("/", " ").replace("-", " ").replace("_", " ")
    parts = [title.strip(), alias_line, slug, body.strip()]
    composed = "\n\n".join(p for p in parts if p)
    return composed[:L2_MAX_CHARS]


def _partition_of(rel_path: str) -> str:
    """Extract partition from rel_path (e.g., wiki/systems/xxx.md -> systems)."""
    parts = rel_path.split("/")
    if len(parts) >= 2 and parts[0] in ("wiki", "sources"):
        return parts[1]
    return ""


def _slug_words(rel_path: str) -> str:
    """Convert path slug to space-separated words."""
    return rel_path.replace("/", " ").replace("-", " ").replace("_", " ")


def _iter_pages(scope: str = "wiki"):
    """Yield (abs_path, rel_path, title, aliases, body) for .md files under scope."""
    roots = ("wiki", "sources") if scope == "wiki" else (scope,)
    for root in roots:
        root_dir = REPO_ROOT / root
        if not root_dir.exists():
            continue
        for p in sorted(root_dir.rglob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = p.relative_to(REPO_ROOT).as_posix()
            title = _extract_title(text, p)
            aliases = _extract_aliases(text)
            body = _extract_body(text)
            yield p, rel, title, aliases, body

    # Add root AGENTS.md for wiki scope
    if scope == "wiki":
        p = REPO_ROOT / "AGENTS.md"
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                return
            rel = p.relative_to(REPO_ROOT).as_posix()
            title = _extract_title(text, p)
            aliases = _extract_aliases(text)
            body = _extract_body(text)
            yield p, rel, title, aliases, body


def _layer_hash(rel_path: str, layer: str, text: str) -> str:
    """Hash for a layer's text content."""
    h = hashlib.md5()
    h.update(b"layers-v1")
    h.update(b"\n")
    h.update(rel_path.encode("utf-8"))
    h.update(b"\n")
    h.update(layer.encode("utf-8"))
    h.update(b"\n")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
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


def build(scope: str = "wiki", *, force: bool = False, batch_log: int = 50) -> dict[str, Any]:
    """Build hierarchical index (L0/L1/L2 vectors per page)."""
    index_path = INDEX_DIR / "wiki_layers.jsonl"
    meta_path = INDEX_DIR / "wiki_layers.meta.json"

    # Load existing for cache reuse
    existing: dict[tuple[str, str], dict[str, Any]] = {}
    if not force and index_path.exists():
        with index_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (entry["path"], entry["layer"])
                existing[key] = entry

    keep: dict[tuple[str, str], dict[str, Any]] = {}
    pending_texts: list[tuple[str, str, str]] = []  # (rel, layer, text)
    seen_paths: set[str] = set()
    layer_counts = {"L0": 0, "L1": 0, "L2": 0}

    for abs_path, rel, title, aliases, body in _iter_pages(scope):
        seen_paths.add(rel)
        partition = _partition_of(rel)

        # L0
        l0_text = _extract_l0_text(title, aliases, body, rel)
        if l0_text:
            l0_hash = _layer_hash(rel, "L0", l0_text)
            prior = existing.get((rel, "L0"))
            if prior and prior.get("text_hash") == l0_hash and isinstance(prior.get("vec"), list):
                keep[(rel, "L0")] = prior
                layer_counts["L0"] += 1
            else:
                pending_texts.append((rel, "L0", l0_text))

        # L1
        l1_text = _extract_l1_text(title, aliases, body, rel)
        if l1_text:
            l1_hash = _layer_hash(rel, "L1", l1_text)
            prior = existing.get((rel, "L1"))
            if prior and prior.get("text_hash") == l1_hash and isinstance(prior.get("vec"), list):
                keep[(rel, "L1")] = prior
                layer_counts["L1"] += 1
            else:
                pending_texts.append((rel, "L1", l1_text))

        # L2
        l2_text = _extract_l2_text(title, aliases, body, rel)
        if l2_text:
            l2_hash = _layer_hash(rel, "L2", l2_text)
            prior = existing.get((rel, "L2"))
            if prior and prior.get("text_hash") == l2_hash and isinstance(prior.get("vec"), list):
                keep[(rel, "L2")] = prior
                layer_counts["L2"] += 1
            else:
                pending_texts.append((rel, "L2", l2_text))

    # Drop entries for deleted pages
    all_existing_keys = set(existing.keys())
    current_keys = {(rel, layer) for rel in seen_paths for layer in ("L0", "L1", "L2")}
    dropped = sorted(all_existing_keys - current_keys)

    # Embed pending texts
    embedded = 0
    if pending_texts:
        texts = [t[2] for t in pending_texts]
        print(f"[tm_hier_index] embedding {len(pending_texts)} layer texts...", file=sys.stderr)
        t0 = time.perf_counter()
        vectors = tm_core.embed_texts(texts)
        dt = (time.perf_counter() - t0) * 1000
        print(f"[tm_hier_index] embedded in {dt:.0f}ms", file=sys.stderr)

        for (rel, layer, text), vec in zip(pending_texts, vectors):
            # Re-read for current metadata
            try:
                body_now = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
                title_now = _extract_title(body_now, REPO_ROOT / rel)
                aliases_now = _extract_aliases(body_now)
                partition_now = _partition_of(rel)
            except OSError:
                continue

            text_hash = _layer_hash(rel, layer, text)
            preview = text[:200] + "..." if len(text) > 200 else text

            keep[(rel, layer)] = {
                "path": rel,
                "layer": layer,
                "title": title_now,
                "aliases": aliases_now,
                "partition": partition_now,
                "text_hash": text_hash,
                "vec": vec,
                "preview": preview,
            }
            embedded += 1
            layer_counts[layer] += 1

    # Save index
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = index_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for entry in keep.values():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    tmp_path.replace(index_path)

    # Build and save meta
    actual_dim = 0
    for entry in keep.values():
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
        base = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
        model = os.environ.get("EMBEDDING_MODEL", "")
        env_dim_raw = os.environ.get("EMBEDDING_DIMENSIONS", "").strip()
        env_dim = int(env_dim_raw) if env_dim_raw else None

    built_at = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).isoformat(timespec="seconds")
    meta = {
        "scope": scope,
        "schema": "layers-v1",
        "embedding_base_url": base,
        "embedding_model": model,
        "embedding_dimensions": actual_dim,
        "embedding_dimensions_env_hint": env_dim,
        "entry_count": len(keep),
        "page_count": len(seen_paths),
        "layer_counts": layer_counts,
        "built_at": built_at,
    }

    tmp_meta = meta_path.with_suffix(".json.tmp")
    with tmp_meta.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    tmp_meta.replace(meta_path)

    return {
        "scope": scope,
        "total_entries": len(keep),
        "pages": len(seen_paths),
        "embedded": embedded,
        "reused": len(keep) - embedded,
        "dropped": dropped,
        "layer_counts": layer_counts,
        "meta": meta,
    }


def search(query: str, *, k: int = 10) -> list[dict[str, Any]]:
    """Search hierarchical index, return layer-level results with page aggregation.

    Returns list of {"path", "layer", "title", "score", "preview"}.
    Caller should aggregate by path using the scoring formula.
    """
    index_path = INDEX_DIR / "wiki_layers.jsonl"
    meta_path = INDEX_DIR / "wiki_layers.meta.json"

    if not index_path.exists():
        raise RuntimeError(f"Hierarchical index not found at {index_path}. Run `build` first.")

    # Check meta compatibility
    meta = None
    if meta_path.exists():
        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)

    # Embed query
    query_vec = tm_core.embed_texts([query])[0]
    query_dim = len(query_vec)

    if meta and meta.get("embedding_dimensions"):
        index_dim = meta.get("embedding_dimensions")
        if index_dim and index_dim != query_dim:
            raise RuntimeError(
                f"Embedding dim mismatch: index built with dim={index_dim}, "
                f"query dim={query_dim}. Rebuild index."
            )

    # Load and score entries
    results: list[dict[str, Any]] = []
    with index_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            vec = entry.get("vec")
            if not isinstance(vec, list):
                continue
            score = _cosine(query_vec, vec)
            if score > 0:
                results.append({
                    "path": entry["path"],
                    "layer": entry["layer"],
                    "title": entry["title"],
                    "score": score,
                    "preview": entry.get("preview", ""),
                })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def search_pages(
    query: str,
    *,
    k: int = 10,
    weights: tuple[float, float, float] = (0.45, 0.35, 0.20),
) -> list[dict[str, Any]]:
    """Score ALL layer entries, aggregate by path, return top-k pages.

    Critical: unlike `search()` (which returns top-k *layer entries* and is
    unsuitable for path aggregation because most pages will be missing
    layers in the truncated pool), this function computes cosine for every
    entry in the index and groups them by `path`. Each path gets its
    per-layer max score, then a weighted aggregate:

        page_score = w0 * max_L0 + w1 * max_L1 + w2 * max_L2

    Missing layers score 0 (intended: a page with no L0 should be
    penalized, but only by the absence of the L0 contribution, not by
    treating an actually-strong L2 as if it lost its sibling layers).

    Returns: [{path, title, score, layer_scores, winning_layer, preview}]
    """
    index_path = INDEX_DIR / "wiki_layers.jsonl"
    meta_path = INDEX_DIR / "wiki_layers.meta.json"

    if not index_path.exists():
        raise RuntimeError(f"Hierarchical index not found at {index_path}. Run `build` first.")

    # Check meta compatibility
    if meta_path.exists():
        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)
    else:
        meta = None

    # Embed query
    query_vec = tm_core.embed_texts([query])[0]
    query_dim = len(query_vec)

    if meta and meta.get("embedding_dimensions"):
        index_dim = meta.get("embedding_dimensions")
        if index_dim and index_dim != query_dim:
            raise RuntimeError(
                f"Embedding dim mismatch: index built with dim={index_dim}, "
                f"query dim={query_dim}. Rebuild index."
            )

    # Score every entry, group by path
    by_path: dict[str, dict[str, Any]] = {}
    with index_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            vec = entry.get("vec")
            if not isinstance(vec, list):
                continue
            score = _cosine(query_vec, vec)
            path = entry["path"]
            layer = entry.get("layer", "")
            if path not in by_path:
                by_path[path] = {
                    "path": path,
                    "title": entry.get("title", ""),
                    "L0": 0.0,
                    "L1": 0.0,
                    "L2": 0.0,
                    "previews": {},
                }
            if score > by_path[path].get(layer, 0.0):
                by_path[path][layer] = score
                by_path[path]["previews"][layer] = entry.get("preview", "")

    # Compute aggregated page scores
    w0, w1, w2 = weights
    aggregated: list[dict[str, Any]] = []
    for path, data in by_path.items():
        page_score = w0 * data["L0"] + w1 * data["L1"] + w2 * data["L2"]
        layer_scores = {layer: data[layer] for layer in ("L0", "L1", "L2") if data[layer] > 0}
        winning_layer = max(layer_scores.items(), key=lambda x: x[1])[0] if layer_scores else None
        aggregated.append({
            "path": path,
            "title": data["title"],
            "score": page_score,
            "layer_scores": layer_scores,
            "winning_layer": winning_layer,
            "preview": data["previews"].get(winning_layer, "") if winning_layer else "",
        })

    aggregated.sort(key=lambda x: -x["score"])
    return aggregated[:k]


def stats() -> dict[str, Any]:
    """Print hierarchical index statistics."""
    index_path = INDEX_DIR / "wiki_layers.jsonl"
    meta_path = INDEX_DIR / "wiki_layers.meta.json"

    meta: dict[str, Any] | None = None
    if meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = None

    if not index_path.exists():
        return {"exists": False, "meta": meta}

    entry_count = 0
    layer_counts = {"L0": 0, "L1": 0, "L2": 0}
    pages: set[str] = set()

    with index_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_count += 1
            layer = entry.get("layer")
            if layer in layer_counts:
                layer_counts[layer] += 1
            pages.add(entry.get("path", ""))

    return {
        "exists": True,
        "entry_count": entry_count,
        "page_count": len(pages),
        "layer_counts": layer_counts,
        "meta": meta,
    }


def main():
    parser = argparse.ArgumentParser(description="Build/query hierarchical L0/L1/L2 index")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # build
    build_parser = subparsers.add_parser("build", help="Build hierarchical index")
    build_parser.add_argument("--scope", default="wiki", help="Scope to index (default: wiki)")
    build_parser.add_argument("--force", action="store_true", help="Rebuild from scratch")
    build_parser.add_argument("--batch-log", type=int, default=50, help="Batch size for progress logging")

    # search
    search_parser = subparsers.add_parser("search", help="Search hierarchical index")
    search_parser.add_argument("query", help="Query text")
    search_parser.add_argument("-k", type=int, default=10, help="Top-k results")

    # stats
    subparsers.add_parser("stats", help="Print index statistics")

    args = parser.parse_args()

    if args.command == "build":
        result = build(scope=args.scope, force=args.force, batch_log=args.batch_log)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "search":
        results = search(args.query, k=args.k)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.command == "stats":
        result = stats()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
