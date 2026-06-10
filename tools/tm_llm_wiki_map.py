#!/usr/bin/env python3
"""Build and inspect the derived LLM Wiki map for memory_answer P3.7.

The map is a small, rebuildable runtime index. It helps natural-language
questions find candidate Wiki/source pages before any LLM planner is needed.
It is not a fact store: answers must still cite the original Wiki/source page.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, TypedDict

import _bootstrap_paths  # noqa: F401

import tigermemory_core as tm_core

SCHEMA_VERSION = "p37-wiki-map-v1"
RUNTIME_DIR = tm_core.REPO_ROOT / "runtime" / "llm_wiki"
MAP_PATH = RUNTIME_DIR / "wiki_map.jsonl"
META_PATH = RUNTIME_DIR / "wiki_map.meta.json"
QUALITY_REPORT_PATH = tm_core.REPO_ROOT / ".tmp" / "llm-wiki-map-quality-report.md"

FORBIDDEN_PATHS = ("tests/", ".tmp/", "runtime/")
PERSON_PATH_PREFIXES = ("wiki/person/", "sources/person/")
SOURCE_SURFACES = {"wiki", "sources"}
ROOT_WIKI_PATHS = {"AGENTS.md"}
ROOT_WIKI_KEYWORDS = {
    "AGENTS.md": [
        "agent",
        "rules",
        "开工",
        "规则",
        "git",
        "commit",
        "push",
        "rebase",
        "inbox",
        "write_memory",
        "route",
        "topic",
        "systems",
        "investment",
        "cross",
        "live",
        "live-state",
        "dashboard",
        "service",
        "服务",
        "端口",
        "1998",
        "8790",
        "hooks",
        "hook",
        "MCP",
        "WSL",
        "D盘",
    ]
}
ROOT_RULE_SIGNAL_TOKENS = {
    "agent",
    "rules",
    "git",
    "commit",
    "push",
    "rebase",
    "inbox",
    "write_memory",
    "route",
    "topic",
    "systems",
    "investment",
    "cross",
    "live",
    "dashboard",
    "service",
    "1998",
    "8790",
    "hooks",
    "hook",
    "mcp",
    "wsl",
}
ROOT_RULE_PORT_TOKENS = {"1998", "8790", "9766", "9776", "8765"}
SENSITIVITY_VALUES = {"normal", "person_excluded", "sensitive_surface"}
ANSWER_FACETS = ("现状", "规则", "计划", "验收", "故障", "风险", "流程", "证据")
GENERIC_CJK_TERMS = {
    "这个",
    "那个",
    "我们",
    "他们",
    "为什么",
    "怎么",
    "如何",
    "是否",
    "可以",
    "需要",
    "系统",
    "内容",
    "问题",
    "进行",
    "当前",
    "已经",
    "一个",
    "还有",
}


class WikiMapRecordDict(TypedDict):
    path: str
    source_surface: str
    partition: str
    subtopic: list[str]
    title: str
    aliases: list[str]
    summary: str
    headings: list[str]
    lead: str
    keywords: list[str]
    cjk_bridge_terms: list[str]
    typed_entities: dict[str, list[str]]
    answer_facets: list[str]
    status: str
    updated: str
    authority: str
    sensitivity: str
    skip_reason: str | None
    extraction_sources: list[str]
    text_hash: str


@dataclasses.dataclass(frozen=True)
class WikiMapRecord:
    path: str
    source_surface: str
    partition: str
    subtopic: list[str]
    title: str
    aliases: list[str]
    summary: str
    headings: list[str]
    lead: str
    keywords: list[str]
    cjk_bridge_terms: list[str]
    typed_entities: dict[str, list[str]]
    answer_facets: list[str]
    status: str
    updated: str
    authority: str
    sensitivity: str
    skip_reason: str | None
    extraction_sources: list[str]
    text_hash: str

    def to_dict(self) -> WikiMapRecordDict:
        return dataclasses.asdict(self)  # type: ignore[return-value]


def _clean_text(value: Any, *, max_chars: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\r\n", "\n")).strip()
    text = text.strip('"').strip("'").strip()
    return text[:max_chars].rstrip()


def _clean_list(values: Any, *, max_items: int, max_chars: int = 80) -> list[str]:
    if values is None:
        raw: list[Any] = []
    elif isinstance(values, str):
        raw = [values]
    elif isinstance(values, (list, tuple, set)):
        raw = list(values)
    else:
        raw = [values]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str) and "," in item and not item.strip().startswith("wiki/"):
            pieces = [part.strip() for part in item.split(",")]
        else:
            pieces = [item]
        for piece in pieces:
            clean = _clean_text(piece, max_chars=max_chars)
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)
            if len(result) >= max_items:
                return result
    return result


def _repo_rel(path: Path, repo_root: Path = tm_core.REPO_ROOT) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def normalize_rel_path(path: str) -> str:
    rel = str(path or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def is_forbidden_input_path(path: str) -> bool:
    rel = normalize_rel_path(path).lower()
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in FORBIDDEN_PATHS)


def is_person_path(path: str) -> bool:
    rel = normalize_rel_path(path).lower()
    return any(rel.startswith(prefix) for prefix in PERSON_PATH_PREFIXES)


def _strip_frontmatter(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    if normalized.startswith("---\n"):
        end = normalized.find("\n---\n", 4)
        if end >= 0:
            return normalized[end + 5 :]
    return normalized


def parse_frontmatter(text: str) -> dict[str, Any]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] = []
    for raw_line in normalized[4:end].splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_key:
            item = line.strip()
            if item.startswith("- "):
                current_list.append(_clean_text(item[2:], max_chars=160))
            continue
        if current_key and current_list:
            result[current_key] = [item for item in current_list if item]
            current_list = []
        current_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        if not value:
            current_key = key
            current_list = []
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = _clean_list(inner.split(",") if inner else [], max_items=20, max_chars=160)
        else:
            result[key] = _clean_text(value, max_chars=300)
    if current_key and current_list:
        result[current_key] = [item for item in current_list if item]
    return result


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return _clean_text(stripped.lstrip("#"), max_chars=120)
    return ""


def _headings(body: str) -> list[str]:
    result: list[str] = []
    in_code = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped.startswith("#"):
            continue
        heading = _clean_text(stripped.lstrip("#"), max_chars=120)
        if heading and heading not in result:
            result.append(heading)
        if len(result) >= 8:
            break
    return result


def _summary_section(body: str) -> str:
    lines = body.splitlines()
    in_summary = False
    collected: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("#"):
            if in_summary and collected:
                break
            heading = stripped.lstrip("#").strip().lower()
            in_summary = "摘要" in heading or "summary" in heading or "overview" in heading
            continue
        if not in_summary:
            continue
        if stripped and not stripped.startswith("|"):
            collected.append(stripped)
        if len(" ".join(collected)) >= 320:
            break
    return _clean_text(" ".join(collected), max_chars=180)


def _lead_text(body: str) -> str:
    chunks: list[str] = []
    in_code = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        chunks.append(stripped)
        if len(" ".join(chunks)) >= 220:
            break
    return _clean_text(" ".join(chunks), max_chars=180)


def _path_tokens(rel: str) -> list[str]:
    pieces = re.split(r"[/_.\-\s]+", rel)
    return [piece for piece in pieces if len(piece) >= 2 and piece not in {"md", "wiki", "sources"}]


def _cjk_terms(text: str, *, max_items: int = 16) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,12}", text):
        candidates = [chunk] if len(chunk) <= 8 else []
        for size in (2, 3, 4):
            candidates.extend(chunk[i : i + size] for i in range(0, len(chunk) - size + 1))
        for item in candidates:
            clean = item.strip()
            if len(clean) < 2 or clean in GENERIC_CJK_TERMS or clean in seen:
                continue
            seen.add(clean)
            terms.append(clean)
            if len(terms) >= max_items:
                return terms
    return terms


def _keywords(rel: str, text: str, frontmatter: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    raw.extend(_path_tokens(rel))
    raw.extend(ROOT_WIKI_KEYWORDS.get(rel, []))
    for key in ("title", "summary", "description", "subtopic"):
        raw.extend(tm_core.signal_tokens(str(frontmatter.get(key) or "")))
    signal_text = text if rel in ROOT_WIKI_PATHS else text[:4000]
    regex_text = text if rel in ROOT_WIKI_PATHS else text[:8000]
    raw.extend(tm_core.signal_tokens(signal_text))
    raw.extend(re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{2,}\b", regex_text))
    result: list[str] = []
    seen: set[str] = set()
    max_items = 60 if rel in ROOT_WIKI_PATHS else 24
    for token in raw:
        clean = str(token or "").strip().lower()
        if len(clean) < 2 or clean in seen:
            continue
        if clean in {"the", "and", "with", "this", "that", "from", "true", "false", "none"}:
            continue
        seen.add(clean)
        result.append(clean[:80])
        if len(result) >= max_items:
            break
    return result


def _extract_ports(text: str) -> list[str]:
    ports: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\b([1-9]\d{3,4})\b", text):
        value = match.group(1)
        number = int(value)
        if number < 1000 or number > 65535:
            continue
        window = text[max(0, match.start() - 24) : match.end() + 24].lower()
        if not any(marker in window for marker in ("port", "端口", "localhost", "127.0.0.1", "http://", "https://")):
            continue
        if value in seen:
            continue
        seen.add(value)
        ports.append(value)
        if len(ports) >= 8:
            break
    return ports


def _typed_entities(rel: str, text: str) -> dict[str, list[str]]:
    sample_limit = len(text) if rel in ROOT_WIKI_PATHS else 12000
    sample = f"{rel}\n{text[:sample_limit]}"
    known_agents = sorted(tm_core.AGENTS | {"claude-code", "codex", "cascade", "openclaw", "hermes", "deerflow", "gemini", "kimi"})
    entities = {
        "paths": _clean_list(
            re.findall(r"(?:[A-Za-z]:\\[^\s`'\"<>|]+|(?:wiki|sources|tools|packages|tests|runtime|deploy)/[^\s`'\"<>|]+)", sample),
            max_items=12,
            max_chars=140,
        ),
        "tools": _clean_list(
            re.findall(r"\b(?:tm_[a-zA-Z0-9_]+|memory_answer(?:_[a-zA-Z0-9_]+)?|search_tigermemory|write_memory|plan_query)\b", sample),
            max_items=12,
            max_chars=100,
        ),
        "ports": _clean_list(
            _extract_ports(sample),
            max_items=8,
            max_chars=12,
        ),
        "agents": _clean_list(
            [agent for agent in known_agents if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(agent)}(?![A-Za-z0-9_-])", sample, re.I)],
            max_items=10,
            max_chars=40,
        ),
        "phases": _clean_list(
            re.findall(r"\bP\d+(?:\.\d+)?[a-z]?\b", sample, flags=re.I),
            max_items=8,
            max_chars=20,
        ),
        "modules": _clean_list(
            _path_tokens(rel)[:8],
            max_items=8,
            max_chars=80,
        ),
    }
    return {key: value for key, value in entities.items()}


def _answer_facets(text: str, headings: list[str]) -> list[str]:
    haystack = " ".join(headings) + "\n" + text[:4000]
    facets: list[str] = []
    rules = [
        ("现状", ("已验证现状", "current", "status", "状态")),
        ("规则", ("规则", "policy", "contract", "协议", "边界")),
        ("计划", ("规划", "计划", "roadmap", "phase", "阶段")),
        ("验收", ("验收", "acceptance", "测试", "验证")),
        ("故障", ("故障", "失败", "问题", "bug", "error")),
        ("风险", ("风险", "risk", "安全", "隐私")),
        ("流程", ("流程", "workflow", "runbook", "步骤")),
        ("证据", ("来源", "证据", "evidence", "refs")),
    ]
    lower = haystack.lower()
    for facet, markers in rules:
        if any(marker.lower() in lower for marker in markers):
            facets.append(facet)
    return facets[:8]


def _partition(rel: str, surface: str) -> str:
    if rel in ROOT_WIKI_PATHS:
        return "systems"
    parts = rel.split("/")
    if len(parts) >= 2 and surface == "wiki":
        return parts[1]
    if len(parts) >= 2 and surface == "sources":
        return parts[1]
    return surface


def _source_surface(rel: str) -> str:
    if rel in ROOT_WIKI_PATHS:
        return "wiki"
    first = rel.split("/", 1)[0]
    return first if first in SOURCE_SURFACES else ""


def _authority(surface: str) -> str:
    return "wiki" if surface == "wiki" else "sources"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def build_record_for_file(path: Path, *, repo_root: Path = tm_core.REPO_ROOT) -> WikiMapRecord:
    rel = _repo_rel(path, repo_root)
    if is_forbidden_input_path(rel):
        raise ValueError(f"forbidden input path: {rel}")
    if is_person_path(rel):
        raise ValueError(f"person path excluded: {rel}")
    surface = _source_surface(rel)
    if surface not in SOURCE_SURFACES:
        raise ValueError(f"unsupported source surface: {rel}")
    text = path.read_text(encoding="utf-8")
    body = _strip_frontmatter(text)
    frontmatter = parse_frontmatter(text)
    headings = _headings(body)
    title = _clean_text(
        frontmatter.get("title") or _first_heading(body) or path.stem.replace("-", " "),
        max_chars=120,
    )
    aliases = _clean_list(frontmatter.get("aliases"), max_items=10, max_chars=100)
    if title and title not in aliases:
        aliases.insert(0, title)
    summary = _clean_text(
        frontmatter.get("summary") or frontmatter.get("description") or _summary_section(body),
        max_chars=180,
    )
    lead = _lead_text(body)
    extracted_text = " ".join([title, " ".join(aliases), summary, " ".join(headings), lead])
    extraction_sources = ["path"]
    if frontmatter:
        extraction_sources.append("frontmatter")
    if summary:
        extraction_sources.append("summary_section" if _summary_section(body) else "frontmatter_summary")
    if headings:
        extraction_sources.append("headings")
    if lead:
        extraction_sources.append("lead")

    record = WikiMapRecord(
        path=rel,
        source_surface=surface,
        partition=_partition(rel, surface),
        subtopic=_clean_list(frontmatter.get("subtopic") or frontmatter.get("tags"), max_items=8, max_chars=80),
        title=title,
        aliases=aliases[:10],
        summary=summary,
        headings=headings[:8],
        lead=lead,
        keywords=_keywords(rel, extracted_text + "\n" + text, frontmatter),
        cjk_bridge_terms=_cjk_terms(extracted_text, max_items=12),
        typed_entities=_typed_entities(rel, text),
        answer_facets=_answer_facets(text, headings),
        status=_clean_text(frontmatter.get("status") or "active", max_chars=40),
        updated=_clean_text(frontmatter.get("updated") or frontmatter.get("updated_at"), max_chars=30),
        authority=_authority(surface),
        sensitivity="normal",
        skip_reason=None,
        extraction_sources=_clean_list(extraction_sources, max_items=8, max_chars=40),
        text_hash=_text_hash(text),
    )
    errors = validate_map_record(record.to_dict())
    if errors:
        raise ValueError(f"invalid map record for {rel}: {'; '.join(errors)}")
    return record


def _iter_input_files(repo_root: Path = tm_core.REPO_ROOT) -> list[Path]:
    paths: list[Path] = []
    for root_name in ("wiki", "sources"):
        root = repo_root / root_name
        if not root.exists():
            continue
        paths.extend(sorted(root.rglob("*.md")))
    for rel in sorted(ROOT_WIKI_PATHS):
        path = repo_root / rel
        if path.exists():
            paths.append(path)
    return paths


def build_records(repo_root: Path = tm_core.REPO_ROOT) -> tuple[list[WikiMapRecord], list[dict[str, str]]]:
    records: list[WikiMapRecord] = []
    skipped: list[dict[str, str]] = []
    for path in _iter_input_files(repo_root):
        rel = _repo_rel(path, repo_root)
        if is_forbidden_input_path(rel):
            skipped.append({"path": rel, "reason": "forbidden_path"})
            continue
        if is_person_path(rel):
            skipped.append({"path": rel, "reason": "person_excluded"})
            continue
        try:
            records.append(build_record_for_file(path, repo_root=repo_root))
        except UnicodeDecodeError:
            skipped.append({"path": rel, "reason": "decode_error"})
        except Exception as exc:
            skipped.append({"path": rel, "reason": f"record_error:{type(exc).__name__}"})
    records.sort(key=lambda item: item.path)
    return records, skipped


def validate_map_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = set(WikiMapRecordDict.__annotations__)
    extra = set(record) - required
    missing = required - set(record)
    if extra:
        errors.append(f"unexpected fields: {sorted(extra)}")
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
    if errors:
        return errors
    if record["source_surface"] not in SOURCE_SURFACES:
        errors.append("source_surface must be wiki or sources")
    if record["sensitivity"] not in SENSITIVITY_VALUES:
        errors.append("invalid sensitivity")
    if not isinstance(record["skip_reason"], (str, type(None))):
        errors.append("skip_reason must be string or null")
    for key in ("path", "partition", "title", "authority", "status", "text_hash"):
        if not isinstance(record[key], str) or not record[key]:
            errors.append(f"{key} must be non-empty string")
    for key in ("subtopic", "aliases", "headings", "keywords", "cjk_bridge_terms", "answer_facets", "extraction_sources"):
        if not isinstance(record[key], list) or not all(isinstance(item, str) for item in record[key]):
            errors.append(f"{key} must be list[str]")
    typed = record.get("typed_entities")
    if not isinstance(typed, dict):
        errors.append("typed_entities must be object")
    else:
        for key in ("paths", "tools", "ports", "agents", "phases", "modules"):
            value = typed.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append(f"typed_entities.{key} must be list[str]")
    if str(record["path"]).startswith(("wiki/person/", "sources/person/")):
        errors.append("person paths must not be indexed")
    if is_forbidden_input_path(str(record["path"])):
        errors.append("forbidden path must not be indexed")
    for key, limit in (("summary", 180), ("lead", 180), ("title", 120)):
        if len(str(record[key])) > limit:
            errors.append(f"{key} exceeds {limit} chars")
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 4096:
        errors.append("record exceeds 4096 bytes")
    return errors


def _metadata(records: list[WikiMapRecord], skipped: list[dict[str, str]]) -> dict[str, Any]:
    lines = [json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for record in records]
    payload = "\n".join(lines) + ("\n" if lines else "")
    sizes = [len(line.encode("utf-8")) for line in lines]
    return {
        "schema_version": SCHEMA_VERSION,
        "built_at": dt.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds"),
        "page_count": len(records),
        "skipped_count": len(skipped),
        "skipped": skipped[:200],
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "avg_record_bytes": round(statistics.mean(sizes), 2) if sizes else 0,
        "max_record_bytes": max(sizes) if sizes else 0,
    }


def write_map(records: list[WikiMapRecord], skipped: list[dict[str, str]], *, map_path: Path = MAP_PATH, meta_path: Path = META_PATH) -> dict[str, Any]:
    map_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for record in records:
        item = record.to_dict()
        errors = validate_map_record(item)
        if errors:
            raise ValueError(f"{record.path}: {'; '.join(errors)}")
        rows.append(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    payload = "\n".join(rows) + ("\n" if rows else "")
    map_path.write_text(payload, encoding="utf-8")
    meta = _metadata(records, skipped)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return meta


def load_map(map_path: Path = MAP_PATH) -> list[dict[str, Any]]:
    if not map_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with map_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            errors = validate_map_record(item)
            if errors:
                raise ValueError(f"{map_path}:{line_no}: {'; '.join(errors)}")
            records.append(item)
    return records


def _score_tokens(query: str) -> list[str]:
    raw = tm_core.signal_tokens(query) + _cjk_terms(query, max_items=40)
    raw.extend(re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]{2,}\b", query))
    result: list[str] = []
    seen: set[str] = set()
    for token in raw:
        clean = str(token or "").strip().lower()
        if len(clean) < 2 or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _field_text(record: dict[str, Any], fields: list[str]) -> str:
    values: list[str] = []
    for field in fields:
        value = record.get(field)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return " ".join(values).lower()


def _entity_text(record: dict[str, Any]) -> str:
    typed = record.get("typed_entities")
    if not isinstance(typed, dict):
        return ""
    values: list[str] = []
    for items in typed.values():
        if isinstance(items, list):
            values.extend(str(item) for item in items)
    return " ".join(values).lower()


def score_record(query: str, record: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    tokens = _score_tokens(query)
    if not tokens:
        return 0.0, {"matched_terms": []}
    high = _field_text(record, ["path", "title", "aliases", "cjk_bridge_terms", "keywords"])
    medium = _field_text(record, ["summary", "headings", "lead", "answer_facets"])
    entity = _entity_text(record)
    score = 0.0
    matched: list[str] = []
    matched_fields: set[str] = set()
    for token in tokens:
        token_score = 0.0
        if token in high:
            token_score += 4.0 + min(len(token), 8) / 2.0
            matched_fields.add("high")
        if token in medium:
            token_score += 1.6 + min(len(token), 8) / 4.0
            matched_fields.add("medium")
        if token in entity:
            token_score += 2.4 + min(len(token), 8) / 3.0
            matched_fields.add("typed_entities")
        if token_score:
            matched.append(token)
            score += token_score
    if record.get("path") in ROOT_WIKI_PATHS and matched:
        root_matches = {term.lower() for term in matched}
        strong_matches = root_matches & ROOT_RULE_SIGNAL_TOKENS
        port_matches = root_matches & ROOT_RULE_PORT_TOKENS
        if strong_matches or port_matches:
            score += 10.0
            matched_fields.add("root_rules")
        if len(root_matches) >= 2 and (strong_matches or port_matches):
            score += 8.0
            matched_fields.add("root_rules")
    if record.get("path", "").endswith("/index.md"):
        score -= 1.5
    return round(max(score, 0.0), 3), {"matched_terms": matched[:20], "matched_fields": sorted(matched_fields)}


def map_recall(query: str, *, limit: int = 80, map_path: Path = MAP_PATH, records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    source = records if records is not None else load_map(map_path)
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for record in source:
        score, breakdown = score_record(query, record)
        if score <= 0:
            continue
        item = {
            "path": record["path"],
            "title": record["title"],
            "partition": record["partition"],
            "source_surface": record["source_surface"],
            "score": score,
            "score_breakdown": breakdown,
            "keywords": record.get("keywords", [])[:8],
            "cjk_bridge_terms": record.get("cjk_bridge_terms", [])[:8],
        }
        scored.append((score, str(record["path"]), item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [item for _score, _path, item in scored[: max(0, int(limit))]]


def summarize_records(records: list[dict[str, Any]], skipped: list[dict[str, str]] | None = None) -> dict[str, Any]:
    sizes = [
        len(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        for record in records
    ]
    missing_summary = [record["path"] for record in records if not record.get("summary")]
    missing_aliases = [record["path"] for record in records if len(record.get("aliases") or []) <= 1]
    missing_facets = [record["path"] for record in records if not record.get("answer_facets")]
    missing_entities = [
        record["path"]
        for record in records
        if not any(record.get("typed_entities", {}).get(key) for key in ("paths", "tools", "ports", "agents", "phases"))
    ]
    by_partition: dict[str, int] = {}
    for record in records:
        partition = str(record.get("partition") or "__unset__")
        by_partition[partition] = by_partition.get(partition, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "page_count": len(records),
        "avg_record_bytes": round(statistics.mean(sizes), 2) if sizes else 0,
        "max_record_bytes": max(sizes) if sizes else 0,
        "missing_summary_count": len(missing_summary),
        "missing_aliases_count": len(missing_aliases),
        "missing_answer_facets_count": len(missing_facets),
        "missing_typed_entities_count": len(missing_entities),
        "typed_entity_coverage_rate": (
            (len(records) - len(missing_entities)) / len(records) if records else 1.0
        ),
        "by_partition": dict(sorted(by_partition.items())),
        "skipped_count": len(skipped or []),
        "sample_missing_summary": missing_summary[:20],
        "sample_missing_aliases": missing_aliases[:20],
        "sample_missing_answer_facets": missing_facets[:20],
        "sample_missing_typed_entities": missing_entities[:20],
    }


def write_quality_report(records: list[dict[str, Any]], *, output: Path = QUALITY_REPORT_PATH) -> Path:
    stats = summarize_records(records)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# LLM Wiki Map Quality Report",
        "",
        "This report is derived from page metadata only. It does not include raw eval queries.",
        "",
        f"- pages: {stats['page_count']}",
        f"- avg_record_bytes: {stats['avg_record_bytes']}",
        f"- missing_summary_count: {stats['missing_summary_count']}",
        f"- missing_aliases_count: {stats['missing_aliases_count']}",
        f"- missing_answer_facets_count: {stats['missing_answer_facets_count']}",
        f"- missing_typed_entities_count: {stats['missing_typed_entities_count']}",
        "",
        "## Suggested Page Fixes",
    ]
    for label, key in [
        ("missing summary", "sample_missing_summary"),
        ("single/no alias", "sample_missing_aliases"),
        ("missing answer facets", "sample_missing_answer_facets"),
        ("missing typed entities", "sample_missing_typed_entities"),
    ]:
        lines.append("")
        lines.append(f"### {label}")
        sample = stats[key]
        if not sample:
            lines.append("- none")
        for path in sample:
            lines.append(f"- {path}: add stable title/summary/aliases or headings if this page is meant to answer user questions.")
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output


def cmd_build(args: argparse.Namespace) -> int:
    records, skipped = build_records(tm_core.REPO_ROOT)
    meta = write_map(records, skipped, map_path=Path(args.output), meta_path=Path(args.meta))
    if args.json:
        print(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"built pages={meta['page_count']} skipped={meta['skipped_count']} output={args.output}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    records = load_map(Path(args.map))
    stats = summarize_records(records)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"pages={stats['page_count']} avg_record_bytes={stats['avg_record_bytes']} "
            f"missing_summary={stats['missing_summary_count']} missing_aliases={stats['missing_aliases_count']}"
        )
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    hits = map_recall(args.query, limit=args.limit, map_path=Path(args.map))
    if args.json:
        print(json.dumps({"query_hash": hashlib.sha256(args.query.encode('utf-8')).hexdigest()[:16], "hits": hits}, ensure_ascii=False, indent=2))
    else:
        for index, hit in enumerate(hits, 1):
            print(f"{index}. {hit['score']:>6} {hit['path']} {hit['title']}")
    return 0


def cmd_quality_report(args: argparse.Namespace) -> int:
    records = load_map(Path(args.map))
    path = write_quality_report(records, output=Path(args.output))
    if args.json:
        print(json.dumps({"output": str(path), **summarize_records(records)}, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"wrote {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tm_llm_wiki_map.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="build runtime LLM Wiki map")
    build.add_argument("--output", default=str(MAP_PATH))
    build.add_argument("--meta", default=str(META_PATH))
    build.add_argument("--json", action="store_true")
    build.set_defaults(func=cmd_build)

    stats = sub.add_parser("stats", help="summarize an existing map")
    stats.add_argument("--map", default=str(MAP_PATH))
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(func=cmd_stats)

    inspect = sub.add_parser("inspect", help="rank map candidates for a query")
    inspect.add_argument("--query", required=True)
    inspect.add_argument("--limit", type=int, default=10)
    inspect.add_argument("--map", default=str(MAP_PATH))
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    quality = sub.add_parser("quality-report", help="write a page-field quality report")
    quality.add_argument("--map", default=str(MAP_PATH))
    quality.add_argument("--output", default=str(QUALITY_REPORT_PATH))
    quality.add_argument("--json", action="store_true")
    quality.set_defaults(func=cmd_quality_report)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
