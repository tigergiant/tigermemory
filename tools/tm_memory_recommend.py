#!/usr/bin/env python3
"""Build and inspect P3.8-A related evidence recommendation data."""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

try:  # optional in test environments
    import _bootstrap_paths  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    _bootstrap_paths = None  # type: ignore[assignment]

import tigermemory_core as tm_core

SCHEMA_VERSION = "p38-recommendation-map-v1"
RECOMMENDED_MAX_TARGETS = 12
REC_DIR = "memory_recommendation"

DEFAULT_REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_WIKI_MAP_PATH = DEFAULT_REPO_ROOT / "runtime" / "llm_wiki" / "wiki_map.jsonl"
DEFAULT_OUTPUT_DIR = DEFAULT_REPO_ROOT / "runtime" / REC_DIR

FORBIDDEN_PREFIXES = (
    "wiki/person/",
    "sources/person/",
    "tests/",
    ".tmp/",
    "runtime/",
    "review-artifacts/",
)

WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclasses.dataclass(frozen=True)
class PageRecord:
    path: str
    source_surface: str
    partition: str
    subtopics: frozenset[str]
    title: str
    aliases: frozenset[str]
    summary: str
    keywords: frozenset[str]
    cjk_bridge_terms: frozenset[str]
    typed_entities: tuple[tuple[str, tuple[str, ...]], ...]
    answer_facets: frozenset[str]
    status: str
    sensitivity: str
    text_hash: str
    links: frozenset[str]

    @property
    def typed_entity_map(self) -> dict[str, frozenset[str]]:
        return {key: frozenset(values) for key, values in self.typed_entities}


def _normalize_path(value: str | Path) -> str:
    rel = str(value).replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _normalize_for_compare(value: str) -> str:
    return _normalize_path(value).strip().lower()


def _dedupe_ordered(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        candidate = str(value).strip()
        if candidate.startswith("- "):
            candidate = candidate[2:].strip()
        candidate = candidate.strip("\"'")
        candidate = _normalize_for_compare(candidate)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return tuple(out)


def _split_scalar_list(value: str) -> list[str]:
    raw = value.strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        raw = raw[1:-1]
    return [item.strip() for item in raw.split(",") if item.strip()]


def _coerce_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        values = _split_scalar_list(value)
    elif isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value if str(item).strip()]
    else:
        values = [str(value).strip()]
    return _dedupe_ordered(values)


def _coerce_set(value: Any) -> frozenset[str]:
    return frozenset(_coerce_list(value))


def _coerce_typed_entities(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, dict):
        return tuple()
    merged: dict[str, list[str]] = {}
    for raw_key, raw_values in value.items():
        key = _normalize_for_compare(str(raw_key))
        if not key:
            continue
        merged[key] = list(_coerce_list(raw_values))
    return tuple((key, tuple(vals)) for key, vals in sorted(merged.items()) if vals)


def _merge_sets(left: frozenset[str], right: Any) -> frozenset[str]:
    return frozenset(_coerce_list(tuple(sorted(left))) + _coerce_list(right))


def _merge_typed_entities(left: dict[str, frozenset[str]], right: dict[str, frozenset[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    merged: dict[str, list[str]] = {}
    keys = set(left) | set(right)
    for key in sorted(keys):
        values = _dedupe_ordered(tuple(sorted(left.get(key, frozenset()))) + tuple(sorted(right.get(key, frozenset()))))
        if values:
            merged[key] = list(values)
    return tuple((key, tuple(vals)) for key, vals in merged.items())


def _token_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(WORD_TOKEN_RE.findall(_normalize_for_compare(value)))


def _extract_cjk_bridge_terms(value: str | None, max_terms: int = 200) -> frozenset[str]:
    if not value:
        return frozenset()
    chars = CJK_CHAR_RE.findall(_normalize_for_compare(value))
    terms = set()
    for idx in range(len(chars) - 1):
        if len(terms) >= max_terms:
            break
        terms.add(f"{chars[idx]}{chars[idx + 1]}")
    return frozenset(terms)


def _is_forbidden_path(rel_path: str) -> bool:
    rel = _normalize_for_compare(rel_path)
    if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    # forbid subfolders inside disallowed roots, including sources/runtime/** etc.
    if "/tests/" in f"/{rel}/" or "/.tmp/" in f"/{rel}/" or "/runtime/" in f"/{rel}/" or "/review-artifacts/" in f"/{rel}/":
        return True
    return False


def _infer_surface_and_partition(rel_path: str) -> tuple[str, str]:
    parts = rel_path.split("/")
    if len(parts) < 2:
        return "wiki", "wiki"
    if parts[0] == "wiki":
        return "wiki", parts[1]
    if parts[0] == "sources":
        return "sources", parts[1] if len(parts) > 1 else "sources"
    return "wiki", "wiki"


def _parse_frontmatter(text: str) -> dict[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}
    block = normalized[4:end]
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_key:
            if line.strip().startswith("- "):
                current_list.append(line.strip()[2:].strip())
            continue
        if current_key and current_list:
            result[current_key] = [entry for entry in current_list if entry]
            current_list = []
        if ":" not in line:
            current_key = None
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            result[key] = value
            current_key = None
        else:
            current_key = key
            current_list = []
    if current_key and current_list:
        result[current_key] = [entry for entry in current_list if entry]
    return result


def _strip_frontmatter(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return normalized
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return normalized
    return normalized[end + 5 :]


def _resolve_link(raw: str, source_path: Path, repo_root: Path) -> str | None:
    target = raw.split("#", 1)[0].strip()
    if not target or target.startswith(("http://", "https://")):
        return None
    if target.startswith("http:") or target.startswith("https:"):
        return None
    target = target.replace("\\", "/")
    if not target.lower().endswith(".md"):
        return None
    candidate: Path
    if target.startswith("wiki/") or target.startswith("sources/"):
        candidate = repo_root / target
    else:
        candidate = source_path.parent / target
    try:
        candidate = candidate.resolve()
    except (OSError, ValueError):
        return None
    try:
        rel = candidate.relative_to(repo_root).as_posix()
    except ValueError:
        return None
    rel = _normalize_path(rel)
    if _is_forbidden_path(rel) or not rel.lower().endswith(".md"):
        return None
    return rel


def _extract_markdown_links(raw: str, page: Path, repo_root: Path) -> frozenset[str]:
    links: set[str] = set()
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", raw):
        resolved = _resolve_link(target.strip(), page, repo_root)
        if resolved:
            links.add(resolved)
    for target in re.findall(r"\[\[([^\]]+)\]\]", raw):
        resolved = _resolve_link(target.strip(), page, repo_root)
        if resolved:
            links.add(resolved)
    return frozenset(links)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_record(path: Path, repo_root: Path) -> PageRecord | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    rel = _normalize_path(path.relative_to(repo_root).as_posix())
    if _is_forbidden_path(rel):
        return None
    fm = _parse_frontmatter(raw)
    body = _strip_frontmatter(raw)
    surface, partition = _infer_surface_and_partition(rel)
    title = str(fm.get("title", "")).strip()
    if not title:
        title_match = re.search(r"^#\s+(.+)$", body, re.M)
        title = title_match.group(1).strip() if title_match else rel
    summary = str(fm.get("summary", "")).strip() or _normalize_path(" ".join(body.splitlines())[:200])
    aliases = _coerce_set(fm.get("aliases") or fm.get("alias"))
    keywords = _coerce_set(fm.get("keywords"))
    answer_facets = _coerce_set(fm.get("answer_facets"))
    typed_entities = _coerce_typed_entities(fm.get("typed_entities"))
    cjk_bridge_terms = _extract_cjk_bridge_terms(f"{title} {summary}")
    if not cjk_bridge_terms:
        cjk_bridge_terms = _extract_cjk_bridge_terms(body)
    links = _extract_markdown_links(raw, path, repo_root)
    partition_meta = str(fm.get("partition", "")).strip() or partition
    status = str(fm.get("status", "")).strip() or "active"
    sensitivity = str(fm.get("sensitivity", "")).strip() or "normal"
    subtopics = _coerce_set(fm.get("subtopic"))
    return PageRecord(
        path=rel,
        source_surface=surface,
        partition=_normalize_for_compare(partition_meta) or partition,
        subtopics=subtopics,
        title=title,
        aliases=aliases,
        summary=summary,
        keywords=keywords,
        cjk_bridge_terms=cjk_bridge_terms,
        typed_entities=typed_entities,
        answer_facets=answer_facets,
        status=status,
        sensitivity=sensitivity,
        text_hash=_hash_text(f"{rel}|{title}|{summary}"),
        links=links,
    )


def _iter_markdown_files(repo_root: Path) -> Iterator[Path]:
    for base in (repo_root / "wiki", repo_root / "sources"):
        if not base.exists():
            continue
        yield from base.rglob("*.md")


def _metadata_record(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _normalize_path(path),
        "source_surface": _normalize_for_compare(str(payload.get("source_surface", ""))),
        "partition": _normalize_for_compare(str(payload.get("partition", ""))),
        "subtopic": payload.get("subtopic"),
        "title": payload.get("title"),
        "aliases": payload.get("aliases", []),
        "summary": payload.get("summary"),
        "keywords": payload.get("keywords", []),
        "cjk_bridge_terms": payload.get("cjk_bridge_terms", []),
        "typed_entities": payload.get("typed_entities", {}),
        "answer_facets": payload.get("answer_facets", []),
        "status": payload.get("status"),
        "sensitivity": payload.get("sensitivity"),
        "text_hash": payload.get("text_hash"),
    }


def _apply_metadata(record: PageRecord, metadata: dict[str, Any]) -> PageRecord:
    source_surface = _normalize_for_compare(metadata.get("source_surface")) or record.source_surface
    partition = _normalize_for_compare(metadata.get("partition")) or record.partition
    aliases = _merge_sets(record.aliases, metadata.get("aliases"))
    keywords = _merge_sets(record.keywords, metadata.get("keywords"))
    cjk_bridge_terms = _merge_sets(record.cjk_bridge_terms, metadata.get("cjk_bridge_terms"))
    subtopics = _merge_sets(record.subtopics, metadata.get("subtopic"))
    answer_facets = _merge_sets(record.answer_facets, metadata.get("answer_facets"))
    summary = str(metadata.get("summary") or record.summary).strip()
    title = str(metadata.get("title") or record.title).strip() or record.title
    status = str(metadata.get("status") or record.status or "active").strip()
    sensitivity = str(metadata.get("sensitivity") or record.sensitivity or "normal").strip()
    text_hash = str(metadata.get("text_hash") or record.text_hash)
    merged_entities = _merge_typed_entities(
        record.typed_entity_map,
        {key: frozenset(vals) for key, vals in _coerce_typed_entities(metadata.get("typed_entities"))},
    )
    return PageRecord(
        path=record.path,
        source_surface=source_surface,
        partition=partition,
        subtopics=subtopics,
        title=title,
        aliases=aliases,
        summary=summary,
        keywords=keywords,
        cjk_bridge_terms=cjk_bridge_terms,
        typed_entities=merged_entities,
        answer_facets=answer_facets,
        status=status,
        sensitivity=sensitivity,
        text_hash=text_hash,
        links=record.links,
    )


def _build_synthetic_record(path: str, metadata: dict[str, Any], record_from_map: bool = True) -> PageRecord:
    surface, partition = _infer_surface_and_partition(path)
    title = str(metadata.get("title") or path).strip()
    summary = str(metadata.get("summary", "")).strip()
    subtopics = _coerce_set(metadata.get("subtopic"))
    aliases = _coerce_set(metadata.get("aliases"))
    keywords = _coerce_set(metadata.get("keywords"))
    cjk_bridge_terms = _coerce_set(metadata.get("cjk_bridge_terms"))
    typed_entities = _coerce_typed_entities(metadata.get("typed_entities"))
    answer_facets = _coerce_set(metadata.get("answer_facets"))
    status = str(metadata.get("status") or "active").strip()
    sensitivity = str(metadata.get("sensitivity") or "normal").strip()
    partition_meta = _normalize_for_compare(metadata.get("partition", "")) or partition
    source_surface = _normalize_for_compare(metadata.get("source_surface")) or surface
    if not record_from_map or not title:
        title = path
    if not cjk_bridge_terms:
        cjk_bridge_terms = _extract_cjk_bridge_terms(f"{title} {summary}")
    return PageRecord(
        path=path,
        source_surface=source_surface,
        partition=partition_meta or partition,
        subtopics=subtopics,
        title=title,
        aliases=aliases,
        summary=summary,
        keywords=keywords,
        cjk_bridge_terms=cjk_bridge_terms,
        typed_entities=typed_entities,
        answer_facets=answer_facets,
        status=status,
        sensitivity=sensitivity,
        text_hash=str(metadata.get("text_hash") or _hash_text(f"{path}|{title}|{summary}")),
        links=frozenset(),
    )


def load_page_records(repo_root: Path, wiki_map_path: Path | None = None) -> tuple[dict[str, PageRecord], int]:
    skipped_count = 0
    records: dict[str, PageRecord] = {}
    for path in _iter_markdown_files(repo_root):
        rel = _normalize_path(path.relative_to(repo_root).as_posix())
        if _is_forbidden_path(rel):
            skipped_count += 1
            continue
        record = _build_record(path, repo_root)
        if record is None:
            skipped_count += 1
            continue
        records[record.path] = record

    wiki_map_file = wiki_map_path or DEFAULT_WIKI_MAP_PATH
    if not wiki_map_file or not wiki_map_file.exists():
        return records, skipped_count

    map_entries: dict[str, dict[str, Any]] = {}
    for line in wiki_map_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            skipped_count += 1
            continue
        meta = _metadata_record(payload.get("path", ""), payload)
        path = meta.get("path", "")
        if not path:
            skipped_count += 1
            continue
        if _is_forbidden_path(path):
            skipped_count += 1
            continue
        map_entries[path] = meta

    for path, metadata in map_entries.items():
        if path in records:
            records[path] = _apply_metadata(records[path], metadata)
        else:
            records[path] = _build_synthetic_record(path, metadata)

    return records, skipped_count


def _score_pair(source: PageRecord, target: PageRecord) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    built_from: set[str] = set()

    if source.partition and target.partition and source.partition == target.partition:
        score += 6.0
        reasons.append(f"same_partition:{source.partition}")
        built_from.add("subpartition")

    source_dir = _normalize_path(str(Path(source.path).parent))
    target_dir = _normalize_path(str(Path(target.path).parent))
    if source_dir == target_dir and source_dir:
        score += 3.0
        reasons.append(f"same_directory:{source_dir}")
        built_from.add("directory")

    shared_subtopics = source.subtopics & target.subtopics
    if shared_subtopics:
        score += 4.0 + 2.0 * len(shared_subtopics)
        reasons.append("same_subtopic:" + ",".join(sorted(shared_subtopics)))
        built_from.add("subtopic")

    if target.path in source.links:
        score += 8.0
        reasons.append(f"markdown_link:{target.path}")
        built_from.add("markdown_links")
    if source.path in target.links:
        score += 4.0
        reasons.append(f"markdown_link_reverse:{source.path}")
        built_from.add("markdown_links")

    shared_aliases = source.aliases & target.aliases
    if shared_aliases:
        score += 5.0 + 1.5 * len(shared_aliases)
        reasons.append("shared_alias:" + ",".join(sorted(shared_aliases)))
        built_from.add("aliases")

    title_overlap = _token_set(source.title) & _token_set(target.title)
    if title_overlap:
        score += 2.0 + 0.4 * len(title_overlap)
        reasons.append("shared_title_term:" + ",".join(sorted(title_overlap)))
        built_from.add("title")

    summary_overlap = _token_set(source.summary) & _token_set(target.summary)
    if summary_overlap:
        score += 2.0 + 0.2 * len(summary_overlap)
        reasons.append("shared_summary_token:" + ",".join(sorted(summary_overlap)))
        built_from.add("summary")

    keyword_overlap = source.keywords & target.keywords
    if keyword_overlap:
        score += 1.0 + 0.4 * len(keyword_overlap)
        reasons.append("shared_keyword:" + ",".join(sorted(keyword_overlap)[:3]))
        built_from.add("keywords")

    cjk_overlap = source.cjk_bridge_terms & target.cjk_bridge_terms
    if cjk_overlap:
        score += 1.0 + 0.3 * len(cjk_overlap)
        reasons.append("shared_cjk_bridge:" + ",".join(sorted(cjk_overlap)[:3]))
        built_from.add("cjk_bridge_terms")

    for key, source_values in source.typed_entity_map.items():
        target_values = target.typed_entity_map.get(key, frozenset())
        shared_entities = source_values & target_values
        if shared_entities:
            score += 1.0 + 0.5 * len(shared_entities)
            reasons.append(f"typed_entity:{key}:{','.join(sorted(shared_entities)[:2])}")
            built_from.add("typed_entities")

    shared_facets = source.answer_facets & target.answer_facets
    if shared_facets:
        score += 0.6 * len(shared_facets)
        reasons.append("answer_facets:" + ",".join(sorted(shared_facets)))
        built_from.add("answer_facets")

    # Pure location-based adjacency is a weak signal and should not look
    # high-confidence without any content or link overlap.
    if built_from and built_from <= {"subpartition", "directory"}:
        score = min(score, 3.0)

    return round(score, 4), sorted(reasons), sorted(built_from)


def _build_edge(source: PageRecord, target: PageRecord) -> dict[str, Any] | None:
    score, reasons, built_from = _score_pair(source, target)
    if score <= 0:
        return None
    if source.path == target.path:
        return None
    return {
        "source_path": source.path,
        "target_path": target.path,
        "score": score,
        "reasons": reasons,
        "source_surface": source.source_surface,
        "target_surface": target.source_surface,
        "target_title": target.title,
        "target_status": target.status,
        "sensitivity": target.sensitivity,
        "built_from": built_from,
        "text_hash": _hash_text(f"{source.path}|{target.path}|{source.text_hash}|{target.text_hash}"),
    }


def _validate_edge_shape(edge: dict[str, Any]) -> None:
    required_fields = (
        "source_path",
        "target_path",
        "score",
        "reasons",
        "source_surface",
        "target_surface",
        "target_title",
        "target_status",
        "sensitivity",
        "built_from",
        "text_hash",
    )
    for key in required_fields:
        if key not in edge:
            raise ValueError(f"missing field: {key}")
    if edge["source_path"] == edge["target_path"]:
        raise ValueError("self-edge is not allowed")
    if not isinstance(edge["score"], (float, int)):
        raise ValueError("score must be numeric")
    if not isinstance(edge["reasons"], list):
        raise ValueError("reasons must be list")
    if not isinstance(edge["built_from"], list):
        raise ValueError("built_from must be list")


def _reason_category(reason: str) -> str:
    return reason.split(":", 1)[0] if ":" in reason else reason


def build_stats(*, edges: Sequence[dict[str, Any]], records: dict[str, PageRecord], skipped_count: int) -> dict[str, Any]:
    per_source = {path: 0 for path in records}
    reason_counter: collections.Counter[str] = collections.Counter()
    for edge in edges:
        per_source[edge["source_path"]] = per_source.get(edge["source_path"], 0) + 1
        for reason in edge.get("reasons", []):
            reason_counter[_reason_category(str(reason))] += 1

    page_count = len(records)
    edge_count = len(edges)
    top_isolated = [path for path, count in per_source.items() if count == 0]
    return {
        "page_count": page_count,
        "edge_count": edge_count,
        "avg_edges_per_page": round(float(edge_count) / page_count, 4) if page_count else 0.0,
        "reason_distribution": dict(sorted(reason_counter.items(), key=lambda item: (-item[1], item[0]))),
        "skipped_count": skipped_count,
        "top_isolated_pages": sorted(top_isolated)[:10],
    }


def build_related_map(
    *,
    repo_root: Path,
    wiki_map_path: Path | None = None,
    output_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, skipped_count = load_page_records(repo_root, wiki_map_path)
    ordered = sorted(records)
    all_edges: list[dict[str, Any]] = []
    for source_path in ordered:
        source = records[source_path]
        candidate_edges: list[dict[str, Any]] = []
        for target_path in ordered:
            if target_path == source_path:
                continue
            target = records[target_path]
            edge = _build_edge(source, target)
            if edge is None:
                continue
            _validate_edge_shape(edge)
            candidate_edges.append(edge)
        candidate_edges.sort(key=lambda item: (-float(item["score"]), item["target_path"]))
        all_edges.extend(candidate_edges[:RECOMMENDED_MAX_TARGETS])

    out_dir = output_dir or DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "related_map.jsonl"
    with map_path.open("w", encoding="utf-8") as f:
        for edge in all_edges:
            f.write(json.dumps(edge, ensure_ascii=False) + "\n")

    stats = build_stats(edges=all_edges, records=records, skipped_count=skipped_count)
    meta = {
        "version": SCHEMA_VERSION,
        "sha256": _hash_edges(all_edges),
        "page_count": stats["page_count"],
        "edge_count": stats["edge_count"],
        "skipped_count": stats["skipped_count"],
        "avg_edges_per_page": stats["avg_edges_per_page"],
        "reason_distribution": stats["reason_distribution"],
        "top_isolated_pages": stats["top_isolated_pages"],
    }
    meta_path = out_dir / "related_map.meta.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    return all_edges, meta


def load_related_map(related_map_path: Path) -> list[dict[str, Any]]:
    if not related_map_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in related_map_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        _validate_edge_shape(row)
        rows.append(row)
    return rows


def _hash_edges(edges: Sequence[dict[str, Any]]) -> str:
    payload = "\n".join(json.dumps(edge, ensure_ascii=False, sort_keys=True) for edge in edges)
    return _hash_text(payload)


def _load_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def cmd_build(args: argparse.Namespace) -> int:
    _, meta = build_related_map(
        repo_root=Path(args.repo_root).resolve(),
        wiki_map_path=Path(args.wiki_map).resolve() if args.wiki_map else None,
        output_dir=Path(args.output_dir).resolve(),
    )
    print(json.dumps(meta, ensure_ascii=True, sort_keys=True, indent=2) if args.json else _build_cli_summary(meta))
    return 0


def _build_cli_summary(meta: dict[str, Any]) -> str:
    return (
        f"recommendation map built\n"
        f"pages: {meta['page_count']}\n"
        f"edges: {meta['edge_count']}\n"
        f"skipped: {meta['skipped_count']}\n"
    )


def cmd_stats(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    related_map_path = output_dir / "related_map.jsonl"
    meta_path = output_dir / "related_map.meta.json"
    if not related_map_path.exists():
        print(f"related map not found: {related_map_path}")
        return 1

    meta = _load_meta(meta_path)
    if meta.get("page_count"):
        # use canonical meta if present
        stats_payload = {
            "version": meta.get("version", SCHEMA_VERSION),
            "sha256": meta.get("sha256"),
            "page_count": int(meta["page_count"]),
            "edge_count": int(meta["edge_count"]),
            "avg_edges_per_page": float(meta["avg_edges_per_page"]),
            "reason_distribution": meta.get("reason_distribution", {}),
            "skipped_count": int(meta.get("skipped_count", 0)),
            "top_isolated_pages": meta.get("top_isolated_pages", []),
        }
    else:
        edges = load_related_map(related_map_path)
        repo_root = Path(args.repo_root).resolve()
        wiki_map_path = Path(args.wiki_map).resolve() if args.wiki_map else None
        records, skipped_count = load_page_records(repo_root, wiki_map_path)
        stats_payload = build_stats(edges=edges, records=records, skipped_count=skipped_count)

    print(json.dumps(stats_payload, ensure_ascii=True, sort_keys=True, indent=2 if args.json else None))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    related_map_path = output_dir / "related_map.jsonl"
    if not related_map_path.exists():
        print(f"related map not found: {related_map_path}")
        return 1
    source_path = _normalize_path(args.path)
    edges = [edge for edge in load_related_map(related_map_path) if edge["source_path"] == source_path]
    print(json.dumps(edges, ensure_ascii=True, sort_keys=True, indent=2 if args.json else None))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tm_memory_recommend.py",
        description="Build and inspect P3.8-A related evidence recommendation map",
    )
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT), help="repository root path")
    parser.add_argument("--wiki-map", default=str(DEFAULT_WIKI_MAP_PATH), help="optional existing wiki_map.jsonl path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="runtime output directory")

    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="build related_map.jsonl and related_map.meta.json")
    build_parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="runtime output directory")
    build_parser.add_argument("--json", action="store_true", help="print json summary")
    build_parser.set_defaults(func=cmd_build)

    stats_parser = sub.add_parser("stats", help="show map statistics")
    stats_parser.add_argument("--json", action="store_true", help="print json")
    stats_parser.set_defaults(func=cmd_stats)

    inspect_parser = sub.add_parser("inspect", help="inspect related edges for a source path")
    inspect_parser.add_argument("--path", required=True, help="wiki or source path")
    inspect_parser.add_argument("--json", action="store_true", help="print json")
    inspect_parser.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
