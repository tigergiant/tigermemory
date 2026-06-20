#!/usr/bin/env python3
"""Validate lightweight Spec Capsule proposal cards.
Inputs: spec-capsule markdown files or cron proposal directories.
Outputs: JSON/text validation results for proposal review and renderer use.
Depends-on (must-have): local filesystem proposal material; no network or LLM calls.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any


REQUIRED_SECTIONS: list[tuple[str, str]] = [
    ("problem", "问题"),
    ("evidence", "证据"),
    ("constraints", "约束"),
    ("solution", "方案"),
    ("acceptance", "验收"),
    ("rollback", "回滚"),
    ("needs_tiger_confirmation", "是否需要虎哥确认"),
]

CAPSULE_FILENAMES = ("spec-capsule.md", "spec_capsule.md", "capsule.md")


def _heading_key(raw: str) -> str | None:
    text = re.sub(r"^[#\s\d.、:：-]+", "", raw).strip()
    for key, label in REQUIRED_SECTIONS:
        if text == label or text.startswith(label):
            return key
    return None


def _first_line(value: str, limit: int = 160) -> str:
    for line in value.splitlines():
        cleaned = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def parse_capsule_text(text: str, *, source: str = "") -> dict[str, Any]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("##"):
            key = _heading_key(line)
            if key:
                current = key
                sections.setdefault(key, [])
                continue
            current = None
            continue
        if current:
            sections.setdefault(current, []).append(line.rstrip())

    section_text = {key: "\n".join(lines).strip() for key, lines in sections.items()}
    missing = [label for key, label in REQUIRED_SECTIONS if not section_text.get(key)]
    needs_text = section_text.get("needs_tiger_confirmation", "")
    lowered = needs_text.lower()
    if re.search(r"\b(no|not needed|false)\b|不需要|无需", lowered):
        needs_confirm: bool | None = False
    elif re.search(r"\b(yes|true|required|need)\b|需要", lowered):
        needs_confirm = True
    else:
        needs_confirm = None

    return {
        "present": True,
        "ok": not missing,
        "source": source,
        "missing_sections": missing,
        "sections": section_text,
        "summary": {key: _first_line(section_text.get(key, "")) for key, _label in REQUIRED_SECTIONS},
        "needs_tiger_confirmation": needs_confirm,
    }


def load_capsule_file(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "ok": False,
            "source": str(path),
            "missing_sections": [label for _key, label in REQUIRED_SECTIONS],
            "sections": {},
            "summary": {},
            "needs_tiger_confirmation": None,
        }
    return parse_capsule_text(path.read_text(encoding="utf-8"), source=str(path))


def _proposal_json(path: pathlib.Path) -> dict[str, Any]:
    proposal_json = path / "proposal.json"
    if not proposal_json.exists():
        return {}
    try:
        data = json.loads(proposal_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_warning": "proposal.json is invalid JSON"}
    return data if isinstance(data, dict) else {}


def _patch_files(patch_text: str) -> list[str]:
    files: list[str] = []
    for line in patch_text.splitlines():
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            files.append(match.group(2))
    if files:
        return sorted(set(files))
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
    return sorted(set(files))


def capsule_required_for(
    proposal_type: str,
    *,
    patch_text: str = "",
    meta: dict[str, Any] | None = None,
) -> bool:
    meta = meta or {}
    if bool(meta.get("requires_spec_capsule") or meta.get("spec_capsule_required")):
        return True
    paths = _patch_files(patch_text)
    if proposal_type in {"meta", "policy-rule"}:
        return True
    if any(path == "AGENTS.md" or path == "index.md" or path.startswith("schemas/") for path in paths):
        return True
    high_risk_paths = (
        "tools/tm_route.py",
        "tools/tm_cron_apply.py",
        "tools/tm_route_replay.py",
        "tools/tm_io.py",
        ".githooks/",
        "deploy/mcp/",
    )
    normalized_paths = [path.replace("\\", "/") for path in paths]
    return any(
        path.startswith(high_risk_paths)
        or path in high_risk_paths
        or path.startswith(".codex/automations/")
        or "/.codex/automations/" in path
        for path in normalized_paths
    )


def load_proposal_capsule(proposal_dir: pathlib.Path, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    for filename in CAPSULE_FILENAMES:
        path = proposal_dir / filename
        if path.exists():
            return load_capsule_file(path)

    meta = meta if meta is not None else _proposal_json(proposal_dir)
    embedded = meta.get("spec_capsule") if isinstance(meta, dict) else None
    if isinstance(embedded, str):
        return parse_capsule_text(embedded, source=f"{proposal_dir / 'proposal.json'}:spec_capsule")
    if isinstance(embedded, dict):
        sections = {}
        for key, label in REQUIRED_SECTIONS:
            value = embedded.get(key)
            if value is None:
                value = embedded.get(label)
            sections[key] = str(value or "").strip()
        missing = [label for key, label in REQUIRED_SECTIONS if not sections.get(key)]
        return {
            "present": True,
            "ok": not missing,
            "source": f"{proposal_dir / 'proposal.json'}:spec_capsule",
            "missing_sections": missing,
            "sections": sections,
            "summary": {key: _first_line(value) for key, value in sections.items()},
            "needs_tiger_confirmation": None,
        }

    return {
        "present": False,
        "ok": False,
        "source": "",
        "missing_sections": [label for _key, label in REQUIRED_SECTIONS],
        "sections": {},
        "summary": {},
        "needs_tiger_confirmation": None,
    }


def validate_proposal_dir(proposal_dir: pathlib.Path) -> dict[str, Any]:
    meta = _proposal_json(proposal_dir)
    proposal_type = str(meta.get("type") or meta.get("proposal_type") or "other")
    patch_path = proposal_dir / "patch"
    patch_text = patch_path.read_text(encoding="utf-8") if patch_path.exists() else ""
    capsule = load_proposal_capsule(proposal_dir, meta=meta)
    required = capsule_required_for(proposal_type, patch_text=patch_text, meta=meta)
    return {
        "ok": (not required) or bool(capsule.get("ok")),
        "proposal_dir": str(proposal_dir),
        "proposal_type": proposal_type,
        "required": required,
        "capsule": capsule,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate TigerMemory Spec Capsule markdown.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_file = sub.add_parser("check")
    p_file.add_argument("--file", required=True)
    p_file.add_argument("--json", action="store_true")
    p_prop = sub.add_parser("check-proposal")
    p_prop.add_argument("--proposal-dir", required=True)
    p_prop.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "check":
        result = load_capsule_file(pathlib.Path(args.file))
    else:
        result = validate_proposal_dir(pathlib.Path(args.proposal_dir))

    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "ok" if result.get("ok") else "failed"
        print(status)
        missing = result.get("missing_sections") or result.get("capsule", {}).get("missing_sections") or []
        if missing:
            print("missing: " + ", ".join(missing))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
