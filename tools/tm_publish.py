#!/usr/bin/env python3
"""tools/tm_publish.py — outward distribution snapshot builder.

Inputs:  argparse args (--dest / --dry-run / --json) + repo state (wiki
         frontmatter flags, file existence under tools/ schemas/ runtime/).
Outputs: destination tree populated with whitelisted files (or a dry-run
         plan on stdout) plus a JSON or pretty summary.
Depends-on: Python stdlib (argparse / pathlib / re / shutil / json) and
            tm_core (REPO_ROOT, configure_stdio). Pure file I/O — no HTTP,
            no LLM, no git.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

from tm_core import REPO_ROOT, configure_stdio


PUBLISH_TOP_FILES = (
    "AGENTS.md",
    "index.md",
    "README.md",
    ".gitignore",
)

PUBLISH_WHOLE_DIRS = (
    "tools",
    "schemas",
)

WIKI_PUBLISH_PARTITIONS = (
    "brand",
    "investment",
    "operations",
    "production",
    "self-evolution",
    "systems",
)

DEFAULT_DEST_DIRNAME = "dist"


def _has_public_true(path: pathlib.Path) -> bool:
    """Return True iff the markdown file's frontmatter contains `public: true`."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not text.startswith("---\n"):
        return False
    fm_end = text.find("\n---\n", 4)
    if fm_end < 0:
        return False
    fm = text[4:fm_end]
    m = re.search(r"^public:\s*(\S+)", fm, re.MULTILINE)
    if not m:
        return False
    return m.group(1).lower() == "true"


def _runtime_config_templates(repo_root: pathlib.Path) -> list[str]:
    """Collect commit-safe runtime config templates (filename ends with `.example`).

    Only files matching the `*.example` glob are picked up; real runtime
    configs (without the suffix) are deliberately skipped so secrets never
    leak into the snapshot.
    """
    runtime = repo_root / "runtime"
    if not runtime.is_dir():
        return []
    rels: list[str] = []
    for p in sorted(runtime.rglob("*.example")):
        if not p.is_file():
            continue
        rels.append(p.relative_to(repo_root).as_posix())
    return rels


def collect_publish_plan(repo_root: pathlib.Path) -> dict[str, list[str]]:
    """Return categorized lists of repo-relative paths slated for publishing.

    Keys: top_files, whole_dirs, wiki_public_pages, config_files.
    Each value is a sorted list of forward-slash relative paths. Paths that
    do not exist on disk are skipped silently.
    """
    plan: dict[str, list[str]] = {
        "top_files": [],
        "whole_dirs": [],
        "wiki_public_pages": [],
        "config_files": [],
    }

    for name in PUBLISH_TOP_FILES:
        if (repo_root / name).is_file():
            plan["top_files"].append(name)

    for d in PUBLISH_WHOLE_DIRS:
        if (repo_root / d).is_dir():
            plan["whole_dirs"].append(d)

    wiki_root = repo_root / "wiki"
    if wiki_root.is_dir():
        for partition in WIKI_PUBLISH_PARTITIONS:
            partition_dir = wiki_root / partition
            if not partition_dir.is_dir():
                continue
            for md in sorted(partition_dir.rglob("*.md")):
                if _has_public_true(md):
                    rel = md.relative_to(repo_root).as_posix()
                    plan["wiki_public_pages"].append(rel)

    plan["config_files"] = _runtime_config_templates(repo_root)

    for k in plan:
        plan[k] = sorted(plan[k])
    return plan


_BYTECODE_CACHE_DIRNAME = "__py" + "cache__"


def _ignore_compiled_artifacts(_dir: str, names: list[str]) -> list[str]:
    """shutil.copytree ignore callback: drop bytecode cache dirs and .pyc files."""
    return [n for n in names if n == _BYTECODE_CACHE_DIRNAME or n.endswith(".pyc")]


def _copy_file(src: pathlib.Path, dst: pathlib.Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def execute_plan(
    plan: dict[str, list[str]],
    repo_root: pathlib.Path,
    dest: pathlib.Path,
) -> int:
    """Copy every file listed in the plan into dest, mirroring repo layout.

    Returns the count of files copied (excluding directory recursion bookkeeping).
    """
    copied = 0

    for rel in plan["top_files"]:
        _copy_file(repo_root / rel, dest / rel)
        copied += 1

    for rel in plan["whole_dirs"]:
        src = repo_root / rel
        dst = dest / rel
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=_ignore_compiled_artifacts)
        copied += sum(1 for _ in dst.rglob("*") if _.is_file())

    for rel in plan["wiki_public_pages"]:
        _copy_file(repo_root / rel, dest / rel)
        copied += 1

    for rel in plan["config_files"]:
        _copy_file(repo_root / rel, dest / rel)
        copied += 1

    return copied


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    p = argparse.ArgumentParser(
        prog="tm_publish.py",
        description=__doc__.split("\n\n", 1)[0] if __doc__ else None,
    )
    p.add_argument(
        "--dest",
        default=DEFAULT_DEST_DIRNAME,
        help=f"output directory (default: {DEFAULT_DEST_DIRNAME}/, gitignored)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print plan without copying anything",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON summary instead of pretty text",
    )
    args = p.parse_args(argv)

    dest = pathlib.Path(args.dest)
    if not dest.is_absolute():
        dest = REPO_ROOT / dest

    plan = collect_publish_plan(REPO_ROOT)
    counts = {k: len(v) for k, v in plan.items()}

    copied = 0
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        copied = execute_plan(plan, REPO_ROOT, dest)

    summary = {
        "dest": str(dest),
        "dry_run": args.dry_run,
        "counts": counts,
        "files_copied": copied,
        "plan": plan,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"dest: {summary['dest']}")
        print(f"dry_run: {summary['dry_run']}")
        print("counts:")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        if not args.dry_run:
            print(f"files_copied: {copied}")
        else:
            print("plan (would copy):")
            for k in ("top_files", "whole_dirs", "wiki_public_pages", "config_files"):
                if not plan[k]:
                    continue
                print(f"  [{k}]")
                for f in plan[k]:
                    print(f"    {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
