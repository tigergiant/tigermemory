#!/usr/bin/env python3
"""Read-only Mem0 retrospective audit for duplicate-like memories.
Inputs: Local repo state, service health endpoints, lessons/wiki pages, Mem0 records, or dashboard preference files.
Outputs: Doctor/audit/onboarding/metrics reports, local UI helper effects, or JSON diagnostics.
Depends-on (must-have): tm_core helpers, local filesystem/git state, and configured local services when the command asks for live checks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import tigermemory_core as tm_core
import tm_memory_ops
import tm_route_audit

REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_AUDIT_ROOT = REPO_ROOT / ".tmp" / "mem0-audit"
MAX_DEDUP_CANDIDATES = 20
DEDUP_DISTANCE_THRESHOLD = 6

CLOSEOUT_BOILERPLATE_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?i)\*Rules used for this response:\*"),
    re.compile(r"(?i)- \((?:Always On|Global)\) Triggered Rule:[^.]*\."),
    re.compile(r"(?i)Sanitized Cascade response:"),
    re.compile(r"(?i)Windsurf Cascade post-response closeout summary"),
    re.compile(r"(?i)Triggered Rule(?: list)?:[^.]*\."),
]


def _item_text(item: Dict[str, Any]) -> str:
    return str(item.get("content") or item.get("memory") or item.get("text") or "")


def _item_meta(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("metadata_") or item.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _memory_id(item: Dict[str, Any], index: int) -> str:
    return str(item.get("id") or item.get("memory_id") or f"rank-{index}")


def _parse_dt(value: Any) -> Optional[dt.datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, tm_core.TZ_CN)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tm_core.TZ_CN)
    return parsed.astimezone(tm_core.TZ_CN)


def audit_window(date: str) -> Tuple[dt.datetime, dt.datetime]:
    day = dt.date.fromisoformat(date)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=tm_core.TZ_CN)
    start = end - dt.timedelta(days=2)
    return start, end


def strip_boilerplate(text: str) -> str:
    out = text
    for pattern in CLOSEOUT_BOILERPLATE_PATTERNS:
        out = pattern.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def normalize_text(text: str) -> str:
    stripped = strip_boilerplate(text)
    stripped = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", " ", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"https?://\S+", " ", stripped)
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _tokens(text: str) -> List[str]:
    ascii_tokens = re.findall(r"[a-z0-9_]{2,}", text.lower())
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_tokens = ["".join(cjk_chars[i:i + 2]) for i in range(max(0, len(cjk_chars) - 1))]
    return ascii_tokens + cjk_tokens


def simhash64(text: str) -> int:
    weights = [0] * 64
    tokens = _tokens(text)
    if not tokens and text:
        tokens = [text[:64]]
    for token in tokens:
        digest = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:16], 16)
        for bit in range(64):
            weights[bit] += 1 if digest & (1 << bit) else -1
    signature = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            signature |= 1 << bit
    return signature


def hamming_distance(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def _created_sort_key(item: Dict[str, Any]) -> dt.datetime:
    return _parse_dt(item.get("created_at")) or dt.datetime.min.replace(tzinfo=tm_core.TZ_CN)


def _preview(text: str, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", tm_route_audit._redact(text)).strip()[:limit].rstrip()


def _record(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    meta = _item_meta(item)
    topic = str(meta.get("topic") or meta.get("stored_topic") or "unknown")
    agent = str(meta.get("source") or meta.get("agent") or "unknown")
    normalized = normalize_text(_item_text(item))
    signature_input = " ".join([topic, agent, normalized[:200]])
    created_dt = _created_sort_key(item)
    return {
        "id": _memory_id(item, index),
        "item": item,
        "topic": topic,
        "agent": agent,
        "created_at": created_dt.isoformat() if created_dt.year > 1 else "",
        "created_dt": created_dt,
        "normalized": normalized,
        "signature": simhash64(signature_input),
        "preview": _preview(_item_text(item)),
    }


def dedup_candidates(items: Iterable[Dict[str, Any]], *, threshold: int = DEDUP_DISTANCE_THRESHOLD) -> List[Dict[str, Any]]:
    records = [_record(item, index) for index, item in enumerate(items)]
    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[rroot] = lroot

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if records[i]["agent"] != records[j]["agent"] or records[i]["topic"] != records[j]["topic"]:
                continue
            if hamming_distance(int(records[i]["signature"]), int(records[j]["signature"])) <= threshold:
                union(i, j)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)

    candidates: List[Dict[str, Any]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        canonical = max(group, key=lambda row: row["created_dt"])
        for record in group:
            if record["id"] == canonical["id"]:
                continue
            distance = hamming_distance(int(record["signature"]), int(canonical["signature"]))
            candidates.append({
                "candidate_id": record["id"],
                "canonical_id": canonical["id"],
                "agent": record["agent"],
                "topic": record["topic"],
                "created_at": str(record["created_at"] or ""),
                "signature_distance": distance,
                "preview": record["preview"],
                "reason": f"signature_cluster_distance={distance}; canonical={canonical['id']}",
            })
    candidates.sort(key=lambda row: (str(row.get("created_at") or ""), str(row["candidate_id"])))
    return candidates[:MAX_DEDUP_CANDIDATES]


def fetch_items_for_date(date: str, *, max_items: int = 1000) -> List[Dict[str, Any]]:
    start, end = audit_window(date)
    return tm_memory_ops.fetch_mem0_items_by_date_range(start, end, max_items=max_items)


def _audit_paths(date: str, audit_root: pathlib.Path) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    out_dir = audit_root / date
    return out_dir, out_dir / "dedup_candidates.json", out_dir / "status.json"


def _write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def audit_dedup(date: str, *, items: Optional[List[Dict[str, Any]]] = None, audit_root: pathlib.Path = DEFAULT_AUDIT_ROOT) -> Dict[str, Any]:
    out_dir, out_path, status_path = _audit_paths(date, audit_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        rows = dedup_candidates(items if items is not None else fetch_items_for_date(date))
        _write_json(out_path, rows)
        report: Dict[str, Any] = {
            "ok": True,
            "status": "ok",
            "date": date,
            "pass": "dedup",
            "candidate_count": len(rows),
            "path": str(out_path),
            "status_path": str(status_path),
            "warnings": [],
            "candidates": rows,
        }
    except Exception as exc:
        rows = []
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        _write_json(out_path, rows)
        report = {
            "ok": False,
            "status": "warning",
            "date": date,
            "pass": "dedup",
            "candidate_count": 0,
            "path": str(out_path),
            "status_path": str(status_path),
            "warnings": [f"tm_mem0_audit dedup failed: {error}"],
            "error": error,
            "candidates": rows,
        }
    _write_json(status_path, {key: value for key, value in report.items() if key != "candidates"})
    return report


def load_dedup_candidates(date: str, *, audit_root: pathlib.Path = DEFAULT_AUDIT_ROOT) -> List[Dict[str, Any]]:
    path = audit_root / date / "dedup_candidates.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def load_audit_status(date: str, *, audit_root: pathlib.Path = DEFAULT_AUDIT_ROOT) -> Dict[str, Any]:
    _out_dir, candidates_path, status_path = _audit_paths(date, audit_root)
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "ok": None,
            "status": "missing",
            "date": date,
            "pass": "dedup",
            "candidate_count": None,
            "path": str(candidates_path),
            "status_path": str(status_path),
            "warnings": ["tm_mem0_audit status.json missing; candidate count may mean audit did not run"],
        }
    if not isinstance(data, dict):
        return {
            "ok": None,
            "status": "invalid",
            "date": date,
            "pass": "dedup",
            "candidate_count": None,
            "path": str(candidates_path),
            "status_path": str(status_path),
            "warnings": ["tm_mem0_audit status.json is not an object"],
        }
    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        data["warnings"] = []
    return data


def main() -> int:
    parser = argparse.ArgumentParser(prog="tm_mem0_audit.py")
    parser.add_argument("--date", required=True)
    parser.add_argument("--pass", dest="audit_pass", choices=["dedup"], default="dedup")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit_dedup(args.date)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['pass']} candidates: {report['candidate_count']}")
        print(report["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
