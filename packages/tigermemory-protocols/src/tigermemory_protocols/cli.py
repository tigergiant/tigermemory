"""Command-line interface for TigerMemory protocol validation."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Any

import yaml

from tigermemory_protocols import __version__
from tigermemory_protocols import validator


def _repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    for path in [current, *current.parents]:
        if (path / "AGENTS.md").exists() and (path / "wiki").is_dir():
            return path
    return Path.cwd()


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} did not load as a YAML object")
    return _normalize_yaml_scalars(loaded)


def _normalize_yaml_scalars(value: Any) -> Any:
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize_yaml_scalars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_scalars(item) for item in value]
    return value


def _required_fields(protocol_name: str) -> list[str]:
    schema = validator._load_schema(protocol_name)
    required = schema.get("required", [])
    return list(required) if isinstance(required, list) else []


def _frontmatter(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            loaded = yaml.safe_load("\n".join(lines[1:index])) or {}
            return _normalize_yaml_scalars(loaded) if isinstance(loaded, dict) else {}
    return {}


def _iter_wiki_pages(root: Path, partition: str | None) -> list[Path]:
    wiki_root = root / "wiki"
    scan_root = wiki_root / partition if partition else wiki_root
    if not scan_root.exists():
        return []
    pages: list[Path] = []
    for path in scan_root.rglob("*.md"):
        rel_parts = path.relative_to(wiki_root).parts
        if any(part.startswith(".") or part.startswith("_") for part in rel_parts):
            continue
        pages.append(path)
    return sorted(pages)


def _cmd_list_schemas(_args: argparse.Namespace) -> int:
    for name in validator.list_protocols():
        required = ", ".join(_required_fields(name))
        print(f"{name}: required={required}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = validator.validate(args.protocol, _load_yaml(Path(args.instance)))
    if not errors:
        print("OK")
        return 0
    for error in errors:
        print(f"ERROR: {error}")
    return 1


def _cmd_scan_wiki(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else _repo_root()
    rows: list[dict[str, Any]] = []
    for path in _iter_wiki_pages(root, args.partition):
        errors = validator.validate("wiki_page", _frontmatter(path))
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "ok": not errors,
                "error_count": len(errors),
                "errors": errors,
            }
        )
    if args.json:
        ok_count = sum(1 for row in rows if row["ok"])
        print(json.dumps({"pages": rows, "summary": {"ok": ok_count, "fail": len(rows) - ok_count}}, ensure_ascii=False))
    else:
        for row in rows:
            state = "ok" if row["ok"] else "fail"
            print(f"{row['path']} {state} {row['error_count']}")
    return 1 if args.strict and any(not row["ok"] for row in rows) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TigerMemory protocol schema tools")
    parser.add_argument("--version", action="store_true", help="print package version")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list-schemas", help="list packaged protocol schemas")
    list_parser.set_defaults(func=_cmd_list_schemas)

    validate_parser = subparsers.add_parser("validate", help="validate a YAML instance")
    validate_parser.add_argument("protocol")
    validate_parser.add_argument("instance")
    validate_parser.set_defaults(func=_cmd_validate)

    scan_parser = subparsers.add_parser("scan-wiki", help="dry-run wiki frontmatter validation")
    scan_parser.add_argument("--partition")
    scan_parser.add_argument("--strict", action="store_true")
    scan_parser.add_argument("--json", action="store_true")
    scan_parser.add_argument("--root", help=argparse.SUPPRESS)
    scan_parser.set_defaults(func=_cmd_scan_wiki)

    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
