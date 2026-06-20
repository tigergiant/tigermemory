#!/usr/bin/env python3
"""Build a small runtime context card for TigerMemory agents.

This is intentionally a deterministic "context delivery" helper, not a
retrieval/scoring engine. It reads a small allowlist of stable local files,
writes an ignored runtime card, and records privacy-safe events.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


TZ_CN = ZoneInfo("Asia/Shanghai")
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "runtime" / "agent-context"
LATEST_JSON = OUT_DIR / "latest.json"
LATEST_MD = OUT_DIR / "latest.md"
EVENTS_JSONL = OUT_DIR / "events.jsonl"
DEFAULT_STALE_AFTER_MINUTES = 240
DEFAULT_MAX_MARKDOWN_CHARS = 1600

PROJECT_CANVAS = REPO_ROOT / "wiki" / "operations" / "project-canvas.md"
MEMORY_ANSWER_PLAN = REPO_ROOT / "wiki" / "systems" / "memory-answer-development-plan.md"
SESSION_HANDOFF_PROTOCOL = REPO_ROOT / "wiki" / "systems" / "session-handoff-protocol.md"

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
]

FORBIDDEN_PATH_MARKERS = (
    "wiki/person/",
    "wiki\\person\\",
    "sources/person/",
    "sources\\person\\",
)


def _now() -> datetime:
    return datetime.now(TZ_CN)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first_section(text: str, heading: str, *, max_chars: int = 500) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    body = re.sub(r"\n{3,}", "\n\n", match.group("body").strip())
    return body[:max_chars].strip()


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*\"?([^\"\n]+?)\"?\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_table_row(text: str, label: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("|") and label in line:
            return line.strip()
    return ""


def _strip_markdown_table_row(row: str, *, max_chars: int = 360) -> str:
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    if not cells:
        return ""
    return _normalize_ws(" / ".join(cells))[:max_chars]


def _safe_summary_from_memory_plan(text: str) -> tuple[str, list[str]]:
    summary = _normalize_ws(_first_section(text, "摘要", max_chars=900))
    lines = []
    for line in text.splitlines():
        if "P3.23" in line or "P5" in line or "packer" in line or "旧 100 问" in line:
            clean = _normalize_ws(line.lstrip("-# "))
            if clean and not _scan_forbidden(clean) and clean not in lines:
                lines.append(clean[:260])
        if len(lines) >= 6:
            break
    return summary[:420], lines


def _scan_forbidden(text: str) -> list[str]:
    findings: list[str] = []
    normalized = text.replace("\\", "/").lower()
    for marker in FORBIDDEN_PATH_MARKERS:
        if marker.replace("\\", "/").lower() in normalized:
            findings.append(f"forbidden_path:{marker}")
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            findings.append(f"sensitive_pattern:{pattern.pattern[:28]}")
    return findings


def _scan_sensitive_only(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            findings.append(f"sensitive_pattern:{pattern.pattern[:28]}")
    return findings


def _canonical_hash(data: dict[str, Any]) -> str:
    copy = {k: v for k, v in data.items() if k != "pack_hash"}
    raw = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _event(event_type: str, *, profile: str, pack_hash: str = "", ok: bool = True, reason: str = "") -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": _now().isoformat(),
        "event_type": event_type,
        "profile": profile,
        "pack_hash": pack_hash,
        "ok": bool(ok),
        "reason": reason,
    }
    with EVENTS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _safe_stdout_write(text: str) -> None:
    try:
        sys.stdout.write(text)
    except BrokenPipeError:
        return
    except UnicodeEncodeError:
        if not sys.stdout.isatty() and hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(text.encode("utf-8"))
            return
        sys.stdout.write(text.encode("gbk", errors="replace").decode("gbk", errors="replace"))


def _write_json(data: dict[str, Any]) -> None:
    _safe_stdout_write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _load_onboarding_30s() -> str:
    tools_path = str(REPO_ROOT / "tools")
    package_path = str(REPO_ROOT / "packages" / "tigermemory-persona" / "src")
    for candidate in (tools_path, package_path):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        import tigermemory_persona  # type: ignore[import-not-found]

        return tigermemory_persona.compile_snapshot("30s")
    except Exception as exc:  # pragma: no cover - defensive fallback
        return f"onboarding unavailable: {type(exc).__name__}"


def build_context_pack(
    *,
    profile: str = "codex",
    task_hint: str = "",
    stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
    max_markdown_chars: int = DEFAULT_MAX_MARKDOWN_CHARS,
) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []

    canvas_text = _read(PROJECT_CANVAS) if PROJECT_CANVAS.exists() else ""
    memory_text = _read(MEMORY_ANSWER_PLAN) if MEMORY_ANSWER_PLAN.exists() else ""
    handoff_text = _read(SESSION_HANDOFF_PROTOCOL) if SESSION_HANDOFF_PROTOCOL.exists() else ""
    for path, text in (
        (PROJECT_CANVAS, canvas_text),
        (MEMORY_ANSWER_PLAN, memory_text),
        (SESSION_HANDOFF_PROTOCOL, handoff_text),
    ):
        if not text:
            missing.append(str(path))

    # Source documents may legitimately mention privacy boundary paths while
    # documenting policy. Only real secret-like text blocks input reads; path
    # markers are blocked after extraction so they cannot persist in the
    # runtime card or agent-facing markdown.
    input_findings = _scan_sensitive_only("\n".join([canvas_text, memory_text, handoff_text]))
    if input_findings:
        raise ValueError("context pack blocked by privacy guard: " + ", ".join(input_findings))

    canvas_updated = _frontmatter_value(canvas_text, "updated")
    memory_row = _strip_markdown_table_row(_extract_table_row(canvas_text, "Memory Answer Natural QA"))
    supervisor_row = _strip_markdown_table_row(_extract_table_row(canvas_text, "Development Supervisor"))
    memory_summary, memory_lines = _safe_summary_from_memory_plan(memory_text)
    onboarding = _load_onboarding_30s()

    current_phase = "Memory Answer P5 / Agent Context Injection P5.6"
    if "P5" in memory_summary:
        current_phase = "Memory Answer P5 active; P5.6 focuses on context delivery, not retrieval retuning."

    must_read = [
        {
            "path": str(PROJECT_CANVAS),
            "reason": "current project map and active module state",
        },
        {
            "path": str(MEMORY_ANSWER_PLAN),
            "reason": "Memory Answer P5 boundaries and release discipline",
        },
        {
            "path": str(SESSION_HANDOFF_PROTOCOL),
            "reason": "handoff card schema and retrieval convention",
        },
    ]

    open_loops = [
        "Do not use old 100-question evidence-hit as the main release metric.",
        "Keep evidence prompt packer opt-in until non-diagnostic holdouts justify defaulting.",
        "Use Memory Answer trace and real failure pool for P5 decisions.",
    ]
    if "P3.23" in memory_text:
        open_loops.insert(0, "P3.23 is closed: map arm is private service default; core library remains conservative.")

    summary_parts = [
        "TigerMemory is in Memory Answer P5 operations hardening.",
        "P5.6 should deliver a short, fresh agent context card for TigerMemory development tasks.",
        "This card is navigation/context only; authoritative answers still require memory_answer evidence.",
    ]
    if memory_row:
        summary_parts.append(memory_row)
    if supervisor_row:
        summary_parts.append(supervisor_row)

    data: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": _now().isoformat(),
        "repo": str(REPO_ROOT),
        "profile": profile,
        "task_hint_hash": hashlib.sha256(task_hint.encode("utf-8")).hexdigest()[:12] if task_hint else "",
        "task_hint_len": len(task_hint),
        "summary": _normalize_ws(" ".join(summary_parts))[:700],
        "current_phase": current_phase,
        "must_read": must_read,
        "open_loops": open_loops[:6],
        "recent_decisions": [
            "P5.6 is a context delivery layer, not a new retrieval/scoring layer.",
            "task_context remains trace-only in this stage.",
            "get_agent_onboarding is extended first; no new MCP tool in the first slice.",
        ],
        "warnings": warnings,
        "source_refs": [
            {"path": str(PROJECT_CANVAS), "updated": canvas_updated},
            {"path": str(MEMORY_ANSWER_PLAN), "summary_lines": memory_lines[:4]},
            {"path": str(SESSION_HANDOFF_PROTOCOL), "available": bool(handoff_text)},
        ],
        "onboarding_30s": _normalize_ws(onboarding)[:650],
        "freshness": {
            "stale_after_minutes": int(stale_after_minutes),
            "is_stale": False,
            "age_minutes": 0,
        },
        "privacy_level": "normal",
        "missing_sources": missing,
        "max_markdown_chars": int(max_markdown_chars),
    }
    data["pack_hash"] = _canonical_hash(data)
    rendered = render_markdown(data, max_chars=max_markdown_chars)
    findings = _scan_forbidden(json.dumps(data, ensure_ascii=False) + "\n" + rendered)
    if findings:
        raise ValueError("context pack blocked by privacy guard: " + ", ".join(findings))
    return data


def render_markdown(data: dict[str, Any], *, max_chars: int = DEFAULT_MAX_MARKDOWN_CHARS) -> str:
    lines = [
        "# TigerMemory Agent Context",
        "",
        f"- generated_at: {data.get('generated_at')}",
        f"- profile: {data.get('profile')}",
        f"- pack_hash: {data.get('pack_hash')}",
        "",
        "## Summary",
        str(data.get("summary") or ""),
        "",
        "## Current Phase",
        str(data.get("current_phase") or ""),
        "",
        "## Must Read",
    ]
    for item in data.get("must_read") or []:
        lines.append(f"- {item.get('path')} — {item.get('reason')}")
    lines.extend(["", "## Open Loops"])
    for item in data.get("open_loops") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Warnings"])
    warnings = list(data.get("warnings") or [])
    if not warnings:
        warnings = ["This context card is a navigation aid. Use memory_answer for evidence-backed answers."]
    for item in warnings:
        lines.append(f"- {item}")
    text = "\n".join(lines).strip() + "\n"
    if len(text) > max_chars:
        text = text[: max_chars - 80].rstrip() + "\n\n[trimmed: context card budget]\n"
    return text


def write_context_pack(data: dict[str, Any]) -> tuple[pathlib.Path, pathlib.Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    LATEST_MD.write_text(render_markdown(data, max_chars=int(data.get("max_markdown_chars") or DEFAULT_MAX_MARKDOWN_CHARS)), encoding="utf-8")
    _event("built", profile=str(data.get("profile") or "codex"), pack_hash=str(data.get("pack_hash") or ""), ok=True)
    return LATEST_JSON, LATEST_MD


def load_latest() -> dict[str, Any] | None:
    if not LATEST_JSON.exists():
        return None
    return json.loads(LATEST_JSON.read_text(encoding="utf-8"))


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ_CN)
    return parsed.astimezone(TZ_CN)


def validate_context_pack(data: dict[str, Any] | None = None) -> dict[str, Any]:
    if data is None:
        data = load_latest()
    if data is None:
        return {"status": "missing", "ok": False, "issues": ["latest.json missing"]}
    issues: list[str] = []
    for key in ("schema_version", "generated_at", "repo", "profile", "summary", "current_phase", "must_read", "pack_hash"):
        if key not in data or data.get(key) in (None, "", []):
            issues.append(f"missing:{key}")
    generated_at = _parse_dt(str(data.get("generated_at") or ""))
    age_minutes = None
    stale = False
    if generated_at:
        age_minutes = max(0, int((_now() - generated_at).total_seconds() // 60))
        stale_after = int((data.get("freshness") or {}).get("stale_after_minutes") or DEFAULT_STALE_AFTER_MINUTES)
        stale = age_minutes > stale_after
    else:
        issues.append("invalid:generated_at")
    text = json.dumps(data, ensure_ascii=False)
    findings = _scan_forbidden(text)
    issues.extend(findings)
    return {
        "status": "ok" if not issues else "fail",
        "ok": not issues,
        "issues": issues,
        "stale": stale,
        "age_minutes": age_minutes,
        "pack_hash": data.get("pack_hash"),
        "path": str(LATEST_JSON),
    }


def stats() -> dict[str, Any]:
    counts: dict[str, int] = {}
    if EVENTS_JSONL.exists():
        for line in EVENTS_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = str(row.get("event_type") or "unknown")
            counts[event_type] = counts.get(event_type, 0) + 1
    validation = validate_context_pack()
    return {
        "status": "ok",
        "event_counts": counts,
        "latest": validation,
    }


def cmd_build(args: argparse.Namespace) -> int:
    try:
        data = build_context_pack(
            profile=args.profile,
            task_hint=args.task or "",
            stale_after_minutes=args.stale_after_minutes,
            max_markdown_chars=args.max_chars,
        )
        json_path, md_path = write_context_pack(data)
        result = {
            "status": "ok",
            "json_path": str(json_path),
            "markdown_path": str(md_path),
            "pack_hash": data["pack_hash"],
        }
    except Exception as exc:
        _event("built", profile=args.profile, ok=False, reason=type(exc).__name__)
        result = {"status": "fail", "error": str(exc)}
        if args.json:
            _write_json(result)
        else:
            print(f"fail: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _write_json(result)
    else:
        _safe_stdout_write(f"wrote {json_path}\n")
        _safe_stdout_write(f"wrote {md_path}\n")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    data = load_latest()
    if data is None:
        print("latest context pack missing", file=sys.stderr)
        return 1
    if args.json:
        _write_json(data)
    else:
        _safe_stdout_write(LATEST_MD.read_text(encoding="utf-8") if LATEST_MD.exists() else render_markdown(data))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_context_pack()
    _event("validated", profile=str((load_latest() or {}).get("profile") or args.profile), pack_hash=str(result.get("pack_hash") or ""), ok=bool(result.get("ok")), reason=",".join(result.get("issues") or []))
    if args.json:
        _write_json(result)
    else:
        _safe_stdout_write(result["status"] + "\n")
    return 0 if result.get("ok") else 1


def cmd_stats(args: argparse.Namespace) -> int:
    result = stats()
    if args.json:
        _write_json(result)
    else:
        _write_json(result)
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tm_agent_context.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="build latest runtime context card")
    build.add_argument("--profile", default="codex")
    build.add_argument("--task", default="")
    build.add_argument("--stale-after-minutes", type=int, default=DEFAULT_STALE_AFTER_MINUTES)
    build.add_argument("--max-chars", type=int, default=DEFAULT_MAX_MARKDOWN_CHARS)
    build.add_argument("--json", action="store_true")
    build.set_defaults(func=cmd_build)

    show = sub.add_parser("show", help="show latest context card")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)

    validate = sub.add_parser("validate", help="validate latest context card")
    validate.add_argument("--profile", default="codex")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    statsp = sub.add_parser("stats", help="show privacy-safe context card stats")
    statsp.add_argument("--json", action="store_true")
    statsp.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
