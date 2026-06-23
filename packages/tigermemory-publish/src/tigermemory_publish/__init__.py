#!/usr/bin/env python3
"""tigermemory_publish — outward distribution snapshot builder.

Inputs:  argparse args (--dest / --dry-run / --json) + repo state (wiki
         frontmatter flags, file existence under tools/ schemas/ runtime/).
Outputs: destination tree populated with whitelisted files (or a dry-run
         plan on stdout) plus a JSON or pretty summary.
Depends-on: Python stdlib (argparse / pathlib / re / shutil / json). Pure
            file I/O — no HTTP, no LLM, no git.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

from .modules import (
    PRIVATE_EXCLUDED_MODULES,
    PRIVATE_PACKAGE_NAMES,
    PUBLIC_MODULES,
    PUBLIC_STARTER_WIKI_PARTITIONS,
    PUBLIC_STARTER_WIKI_TEMPLATE_FILES,
    module_details,
    validate_module_checks,
    validate_public_boundaries,
    module_checks,
    module_ids,
    module_summary,
    private_excluded_wiki_partitions,
    public_mapped_files,
    public_package_roots,
    public_tool_dirs,
    public_tool_files,
    public_top_files,
    public_whole_dirs,
    public_wiki_partitions,
)
from .split import build_split_report, run_public_core_instance_smoke, run_public_core_source_update_smoke

PUBLISH_TOP_FILES = public_top_files()
PUBLISH_MAPPED_FILES = public_mapped_files()
PUBLISH_WHOLE_DIRS = public_whole_dirs()
PUBLISH_PACKAGE_ROOTS = public_package_roots()
PUBLISH_TOOL_FILES = public_tool_files()
PUBLISH_TOOL_DIRS = public_tool_dirs()
WIKI_PUBLISH_PARTITIONS = public_wiki_partitions()

EXCLUDED_WIKI_PARTITIONS = private_excluded_wiki_partitions()
DEFAULT_DEST_DIRNAME = "dist"
MAX_FINDINGS = 50
PUBLIC_FIELD_DEFAULT = False
PUBLIC_TRUE_VALUES = {"true", "True", "yes", "Yes", "1"}

PATH_LEAK_WARNING_PATHS = {"AGENTS.md", "README.md", "index.md"}
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{24,})")
SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(api[_-]?key|secret|token|password|passwd)\b
    \s*[:=]\s*
    ["']?([A-Za-z0-9][A-Za-z0-9_./+=-]{23,})
    """
)
CN_ID_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}"
    r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
CN_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
TEXT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".css",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

INCLUDED_PLAN_KEYS = (
    "top_files",
    "whole_dirs",
    "mapped_files",
    "tool_files",
    "tool_dirs",
    "wiki_public_pages",
    "config_files",
    "generated_files",
)
EXCLUDED_PLAN_KEYS = (
    "excluded_by_public_field",
    "excluded_by_private_partition",
    "excluded_by_person_partition",
    "excluded_by_pii",
)


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        git_marker = ancestor / ".git"
        if (git_marker.is_dir() or git_marker.is_file()) and (ancestor / "wiki").is_dir():
            return ancestor
    return here.parent.parent.parent.parent


REPO_ROOT = _detect_repo_root()


def configure_stdio() -> None:
    if sys.version_info >= (3, 7):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                try:
                    stream.reconfigure(errors="backslashreplace")
                except Exception:
                    pass


def parse_frontmatter_public(content: str) -> bool:
    """Return True only when markdown frontmatter explicitly opts into publish."""
    if not content.startswith("---\n"):
        return PUBLIC_FIELD_DEFAULT
    fm_end = content.find("\n---\n", 4)
    if fm_end < 0:
        return PUBLIC_FIELD_DEFAULT
    fm = content[4:fm_end]
    for line in fm.splitlines():
        if not line.startswith("public:"):
            continue
        val = line.split(":", 1)[1].strip()
        return val.strip('"').strip("'") in PUBLIC_TRUE_VALUES
    return PUBLIC_FIELD_DEFAULT


def _has_public_true(path: pathlib.Path) -> bool:
    """Return True iff the markdown file's frontmatter opts into publish."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return PUBLIC_FIELD_DEFAULT
    return parse_frontmatter_public(text)


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


def _module_publish_inputs(module_id: str | None) -> dict[str, tuple]:
    if module_id is None:
        return {
            "top_files": PUBLISH_TOP_FILES,
            "mapped_files": PUBLISH_MAPPED_FILES,
            "whole_dirs": PUBLISH_WHOLE_DIRS,
            "tool_files": PUBLISH_TOOL_FILES,
            "tool_dirs": PUBLISH_TOOL_DIRS,
            "wiki_partitions": WIKI_PUBLISH_PARTITIONS,
        }
    return {
        "top_files": public_top_files(module_id),
        "mapped_files": public_mapped_files(module_id),
        "whole_dirs": public_whole_dirs(module_id),
        "tool_files": public_tool_files(module_id),
        "tool_dirs": public_tool_dirs(module_id),
        "wiki_partitions": public_wiki_partitions(module_id),
    }


def collect_publish_plan(repo_root: pathlib.Path, module_id: str | None = None) -> dict[str, list[str]]:
    """Return categorized lists of repo-relative paths slated for publishing.

    Keys include top_files, mapped_files, whole_dirs, wiki_public_pages, config_files.
    Each value is a sorted list of forward-slash relative paths. Paths that
    do not exist on disk are skipped silently.
    """
    inputs = _module_publish_inputs(module_id)
    plan: dict[str, list[str]] = {
        "top_files": [],
        "whole_dirs": [],
        "mapped_files": [],
        "tool_files": [],
        "tool_dirs": [],
        "wiki_public_pages": [],
        "config_files": [],
        "generated_files": ["MODULES.md", "tigermemory-public-modules.json"],
        "excluded_by_public_field": [],
        "excluded_by_private_partition": [],
        "excluded_by_person_partition": [],
        "excluded_by_pii": [],
    }
    pii_findings: list[dict[str, object]] = []

    for name in inputs["top_files"]:
        if (repo_root / name).is_file():
            plan["top_files"].append(name)

    for src, dst in inputs["mapped_files"]:
        if (repo_root / src).is_file():
            plan["mapped_files"].append(f"{src}=>{dst}")

    for d in inputs["whole_dirs"]:
        if (repo_root / d).is_dir():
            plan["whole_dirs"].append(d)

    for name in inputs["tool_files"]:
        if (repo_root / name).is_file():
            plan["tool_files"].append(name)

    for d in inputs["tool_dirs"]:
        if (repo_root / d).is_dir():
            plan["tool_dirs"].append(d)

    wiki_root = repo_root / "wiki"
    if wiki_root.is_dir():
        # Module inspection is intentionally narrow and should not scan the
        # private dogfood wiki on every pre-push smoke.
        private_partition_scan = module_id is None
        for partition in EXCLUDED_WIKI_PARTITIONS if private_partition_scan else ():
            partition_dir = wiki_root / partition
            if not partition_dir.is_dir():
                continue
            for md in sorted(partition_dir.rglob("*.md")):
                if md.is_file():
                    rel = md.relative_to(repo_root).as_posix()
                    plan["excluded_by_private_partition"].append(rel)
                    if partition == "person":
                        plan["excluded_by_person_partition"].append(rel)
        for partition in inputs["wiki_partitions"]:
            if partition in EXCLUDED_WIKI_PARTITIONS:
                continue
            partition_dir = wiki_root / partition
            if not partition_dir.is_dir():
                continue
            for md in sorted(partition_dir.rglob("*.md")):
                rel = md.relative_to(repo_root).as_posix()
                try:
                    text = md.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    plan["excluded_by_public_field"].append(rel)
                    continue
                if not parse_frontmatter_public(text):
                    plan["excluded_by_public_field"].append(rel)
                    continue
                findings = _scan_text_for_sensitive(text, rel, "wiki_public_pages", repo_root)
                if findings:
                    plan["excluded_by_pii"].append(rel)
                    pii_findings.extend(findings)
                    continue
                plan["wiki_public_pages"].append(rel)

    plan["config_files"] = _runtime_config_templates(repo_root)

    for k in plan:
        plan[k] = sorted(plan[k])
    plan["pii_findings"] = pii_findings  # type: ignore[assignment]
    return plan


def _looks_placeholder(value: str) -> bool:
    if re.match(r"^[A-Z][A-Za-z0-9_]*\.", value):
        return True
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "changeme",
            "dummy",
            "example",
            "placeholder",
            "redacted",
            "sample",
            "stub",
            "test",
            "your",
            "xxxx",
        )
    )


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "[REDACTED]"
    return f"{value[:4]}...[REDACTED]...{value[-4:]}"


def _context_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _redact_line(line: str) -> str:
    line = PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", line)
    line = BEARER_TOKEN_RE.sub(lambda m: "Bearer " + _mask_secret(m.group(1)), line)
    line = SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={_mask_secret(m.group(2))}", line)
    line = CN_ID_RE.sub("[REDACTED_CN_ID]", line)
    line = CN_PHONE_RE.sub("[REDACTED_PHONE]", line)
    return line.strip()[:160]


def _path_leak_tokens(repo_root: pathlib.Path | None = None) -> set[str]:
    tokens: set[str] = set()
    root = (repo_root or REPO_ROOT).resolve()
    candidates = [root, pathlib.Path.home()]
    for env_key in ("USERPROFILE", "HOME"):
        value = os.environ.get(env_key)
        if value:
            candidates.append(pathlib.Path(value))

    for candidate in candidates:
        normalized = str(candidate).replace("\\", "/").lower().rstrip("/")
        if normalized:
            tokens.add(normalized)

    user_name = pathlib.Path.home().name.lower()
    if user_name:
        tokens.add(f"/home/{user_name}")
        tokens.add(f"//wsl.localhost/ubuntu/home/{user_name}")
        tokens.add(f"/wsl.localhost/ubuntu/home/{user_name}")
    return {token for token in tokens if len(token) >= 6}


def _contains_path_leak(line: str, repo_root: pathlib.Path | None = None) -> bool:
    normalized = line.replace("\\\\", "/").replace("\\", "/").lower()
    return any(token in normalized for token in _path_leak_tokens(repo_root))


def _path_leak_severity(path: str) -> str:
    rel = path.replace("\\", "/").strip("/")
    if rel == "packages/tigermemory-publish/src/tigermemory_publish/__init__.py":
        return "warning"
    return "warning" if rel in PATH_LEAK_WARNING_PATHS else "high"


def _is_test_fixture_path(path: str) -> bool:
    rel = path.replace("\\", "/").strip("/")
    return rel.startswith("tests/") or "/tests/" in rel


def _repo_audit_severity(category: str, rel: str, kind: str, severity: str, line: str) -> str:
    """Downgrade explicit test fixtures without hiding source/doc leaks."""
    if category != "repo_audit" or not _is_test_fixture_path(rel):
        return severity
    return "warning"


def _planned_text_files(plan: dict[str, list[str]], repo_root: pathlib.Path) -> list[tuple[str, str]]:
    """Return (category, rel_path) pairs that should be scanned before publish."""
    items: list[tuple[str, str]] = []
    for category in ("top_files", "tool_files", "wiki_public_pages", "config_files"):
        for rel in plan.get(category, []):
            items.append((category, rel))
    for item in plan.get("mapped_files", []):
        src, _dst = _split_mapped_file(item)
        items.append(("mapped_files", src))
    for rel_dir in [*plan.get("whole_dirs", []), *plan.get("tool_dirs", [])]:
        root = repo_root / rel_dir
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name.endswith(".pyc") or _BYTECODE_CACHE_DIRNAME in path.parts:
                continue
            rel = path.relative_to(repo_root).as_posix()
            items.append(("whole_dirs", rel))
    return items


def _add_finding(
    findings: list[dict[str, object]],
    *,
    category: str,
    rel: str,
    line_no: int,
    kind: str,
    severity: str,
    line: str,
    regex_name: str | None = None,
) -> None:
    if len(findings) >= MAX_FINDINGS:
        return
    preview = _redact_line(line)
    findings.append(
        {
            "path": rel,
            "file_path": rel,
            "category": category,
            "line": line_no,
            "line_number": line_no,
            "kind": kind,
            "regex_name": regex_name or kind,
            "severity": severity,
            "preview": preview,
            "context_50chars": preview[:50],
            "sha256_of_context": _context_hash(preview),
        }
    )


def _scan_text_for_sensitive(
    text: str,
    rel: str,
    category: str,
    repo_root: pathlib.Path | None = None,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    scan_pii = category in {"wiki_public_pages", "top_files", "mapped_files", "config_files", "repo_audit"}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_KEY_RE.search(line):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind="private_key",
                severity=_repo_audit_severity(category, rel, "private_key", "high", line),
                line=line,
                regex_name="PRIVATE_KEY_RE",
            )
        bearer = BEARER_TOKEN_RE.search(line)
        if bearer and not _looks_placeholder(bearer.group(1)):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind="bearer_token",
                severity=_repo_audit_severity(category, rel, "bearer_token", "high", line),
                line=line,
                regex_name="BEARER_TOKEN_RE",
            )
        secret = SECRET_ASSIGNMENT_RE.search(line)
        if secret and not _looks_placeholder(secret.group(2)):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind=secret.group(1).lower(),
                severity=_repo_audit_severity(category, rel, secret.group(1).lower(), "high", line),
                line=line,
                regex_name="SECRET_ASSIGNMENT_RE",
            )
        if scan_pii and CN_ID_RE.search(line):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind="cn_id",
                severity=_repo_audit_severity(category, rel, "cn_id", "high", line),
                line=line,
                regex_name="CN_ID_RE",
            )
        if scan_pii and CN_PHONE_RE.search(line):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind="cn_phone",
                severity=_repo_audit_severity(category, rel, "cn_phone", "medium", line),
                line=line,
                regex_name="CN_PHONE_RE",
            )
        if _contains_path_leak(line, repo_root):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind="path_leak",
                severity=_repo_audit_severity(
                    category,
                    rel,
                    "path_leak",
                    _path_leak_severity(rel),
                    line,
                ),
                line=line,
                regex_name="PATH_LEAK_TOKENS",
            )
    return findings


def audit_publish_plan(plan: dict[str, list[str]], repo_root: pathlib.Path) -> list[dict[str, object]]:
    """Scan files in a publish plan for high-confidence sensitive material."""
    findings: list[dict[str, object]] = list(plan.get("pii_findings", []))  # type: ignore[arg-type]
    for category, rel in _planned_text_files(plan, repo_root):
        if len(findings) >= MAX_FINDINGS:
            break
        path = repo_root / rel
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS and not path.name.endswith(".example"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:4096]:
            continue
        findings.extend(_scan_text_for_sensitive(text, rel, category, repo_root))
        if len(findings) > MAX_FINDINGS:
            findings = findings[:MAX_FINDINGS]
    return findings


def _repo_audit_files(repo_root: pathlib.Path) -> list[str]:
    """Return tracked text-ish files for whole-repo privacy readiness audits."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        proc = None

    rels: list[str] = []
    if proc and proc.returncode == 0:
        rels = [item for item in proc.stdout.decode("utf-8", errors="replace").split("\0") if item]
    else:
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file():
                continue
            try:
                rels.append(path.relative_to(repo_root).as_posix())
            except ValueError:
                continue

    out: list[str] = []
    for rel in rels:
        path = pathlib.PurePosixPath(rel)
        if _BYTECODE_CACHE_DIRNAME in path.parts:
            continue
        if any(part in {".git", ".tmp", "dist", "node_modules"} for part in path.parts):
            continue
        if path.name.endswith(".pyc"):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name.endswith(".example"):
            out.append(rel)
    return sorted(out)


def audit_repo_sensitive(repo_root: pathlib.Path) -> list[dict[str, object]]:
    """Scan tracked text files to prove whether the full repo is safe to make public."""
    findings: list[dict[str, object]] = []
    for rel in _repo_audit_files(repo_root):
        if len(findings) >= MAX_FINDINGS:
            break
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:4096]:
            continue
        findings.extend(_scan_text_for_sensitive(text, rel, "repo_audit", repo_root))
        if len(findings) > MAX_FINDINGS:
            findings = findings[:MAX_FINDINGS]
    return findings


TRUE_SPLIT_BUCKETS = {
    "private_instance_data": {
        "description": "Private user/runtime data that should live outside the public source repo.",
        "suggested_action": "Move to a private instance/data repository or keep it behind the publish snapshot boundary.",
    },
    "private_wiki_partition": {
        "description": "Private wiki partitions that should not be in the public source repo.",
        "suggested_action": "Move to a private instance/wiki repository or keep the public repo on generated public seed pages.",
    },
    "internal_review_archive": {
        "description": "Internal review archives and supervisor evidence.",
        "suggested_action": "Keep in the private dogfood instance; do not ship as public source history.",
    },
    "root_agent_rules": {
        "description": "Local agent entry rules with machine-specific paths.",
        "suggested_action": "Replace with public template files in the public source repo; keep dogfood rules private.",
    },
    "public_source_surface": {
        "description": "Code, tests, templates, or public docs that are intended to be source-level public.",
        "suggested_action": "Remove the leak or replace it with a portable path before true split.",
    },
    "unclassified_repo_surface": {
        "description": "Tracked files that need human classification before the full repo can be public.",
        "suggested_action": "Classify as public source, private instance data, or intentional exclusion.",
    },
}


def _true_split_bucket(path: str) -> str:
    rel = path.replace("\\", "/").strip("/")
    if rel in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}:
        return "root_agent_rules"
    if any(rel.startswith(f"wiki/{partition}/") for partition in EXCLUDED_WIKI_PARTITIONS):
        return "private_wiki_partition"
    if rel.startswith("sources/internal-analysis/development-reviews/"):
        return "internal_review_archive"
    if rel.startswith(("runtime/", ".tmp/", "data/expense_import/")):
        return "private_instance_data"
    if rel.startswith(("packages/", "tools/", "tests/", "schemas/", "wiki/")) or rel in {
        "README.md",
        "index.md",
        "pyproject.toml",
    }:
        return "public_source_surface"
    return "unclassified_repo_surface"


def true_split_blocker_summary(
    findings: list[dict[str, object]],
    *,
    max_findings: int = MAX_FINDINGS,
) -> dict[str, object]:
    """Group repo-scope findings into migration buckets for the True Split Gate."""
    buckets: dict[str, dict[str, object]] = {}
    for finding in findings:
        path = str(finding.get("path") or finding.get("file_path") or "")
        bucket_id = _true_split_bucket(path)
        bucket = buckets.setdefault(
            bucket_id,
            {
                "id": bucket_id,
                "description": TRUE_SPLIT_BUCKETS[bucket_id]["description"],
                "suggested_action": TRUE_SPLIT_BUCKETS[bucket_id]["suggested_action"],
                "count": 0,
                "blocking_count": 0,
                "warning_count": 0,
                "top_paths": {},
            },
        )
        bucket["count"] = int(bucket["count"]) + 1
        if finding.get("severity") == "warning":
            bucket["warning_count"] = int(bucket["warning_count"]) + 1
        else:
            bucket["blocking_count"] = int(bucket["blocking_count"]) + 1
        top_paths = bucket["top_paths"]
        if isinstance(top_paths, dict):
            top_paths[path] = int(top_paths.get(path, 0)) + 1

    normalized_buckets: list[dict[str, object]] = []
    for bucket in buckets.values():
        top_paths = bucket.pop("top_paths")
        if not isinstance(top_paths, dict):
            top_paths = {}
        bucket["top_paths"] = [
            {"path": path, "count": count}
            for path, count in sorted(top_paths.items(), key=lambda item: (-item[1], item[0]))[:10]
        ]
        normalized_buckets.append(bucket)
    normalized_buckets.sort(key=lambda item: (-int(item["blocking_count"]), -int(item["count"]), str(item["id"])))

    blocking_count = sum(1 for finding in findings if finding.get("severity") != "warning")
    warning_count = len(findings) - blocking_count
    counts_are_complete = len(findings) < max_findings
    repo_public_ready = blocking_count == 0 and counts_are_complete
    return {
        "schema": "tigermemory-true-split-blockers-v1",
        "repo_public_ready": repo_public_ready,
        "repo_warning_free": len(findings) == 0,
        "finding_count": len(findings),
        "max_findings": max_findings,
        "findings_capped": len(findings) >= max_findings,
        "counts_are_complete": counts_are_complete,
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "readiness_reason": (
            "blocking_findings_present"
            if blocking_count
            else "finding_cap_reached"
            if not counts_are_complete
            else "warning_only_findings_present"
            if warning_count
            else "no_findings"
        ),
        "buckets": normalized_buckets,
        "next_actions": [
            "Move private instance data out of the public source repo or keep publishing from generated public-core artifacts.",
            "Replace local AGENTS/GEMINI/CLAUDE entry files with portable public templates before opening the full repo.",
            "Treat repo-scope audit as the True Split Gate; do not conflate it with the snapshot release gate.",
        ],
    }


def _included_plan(plan: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(plan.get(key, [])) for key in INCLUDED_PLAN_KEYS}


def _excluded_plan(plan: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(plan.get(key, [])) for key in EXCLUDED_PLAN_KEYS}


def _format_status(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _snapshot_audit_payload(
    sensitive_findings: list[dict[str, object]],
    has_blocking_findings: bool,
) -> dict[str, object]:
    return {"ok": not has_blocking_findings, "sensitive_total": len(sensitive_findings)}


def _skipped_snapshot_audit_payload(reason: str) -> dict[str, object]:
    return {"ok": None, "sensitive_total": None, "skipped": True, "reason": reason}


def _public_module_manifest(module_id: str | None = None) -> dict[str, object]:
    return {
        "schema": "tigermemory-public-modules-v1",
        "inspection_only": module_id is not None,
        "selected_module": module_id,
        "modules": module_details(module_id),
        "excluded_surfaces": [
            {
                "id": module.id,
                "description": module.description,
                "stability": module.stability,
            }
            for module in PRIVATE_EXCLUDED_MODULES
        ],
        "private_wiki_partitions": [f"wiki/{partition}" for partition in EXCLUDED_WIKI_PARTITIONS],
    }


def _public_modules_markdown(module_id: str | None = None) -> str:
    manifest = _public_module_manifest(module_id)
    lines = [
        "# TigerMemory Public Modules",
        "",
        "This file is generated from TigerMemory's public module manifest during snapshot publish.",
        "",
        f"Inspection only: {'yes' if manifest['inspection_only'] else 'no'}",
        "",
        "## Included Modules",
        "",
    ]
    for module in manifest["modules"]:  # type: ignore[assignment]
        lines.extend(
            [
                f"### {module['id']}",
                "",
                f"- Stability: {module['stability']}",
                f"- Description: {module['description']}",
                f"- Package roots: {len(module['package_roots'])}",
                f"- Tool files: {len(module['tool_files'])}",
                f"- Tool dirs: {len(module['tool_dirs'])}",
                f"- Wiki partitions: {', '.join(module['wiki_partitions']) or '(none)'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Excluded Private Surfaces",
            "",
            "These surfaces are intentionally not part of the public snapshot:",
            "",
        ]
    )
    for item in manifest["excluded_surfaces"]:  # type: ignore[assignment]
        lines.append(f"- {item['id']}: {item['description']}")
    lines.extend(
        [
            "",
            "Private wiki partitions are force-excluded even if a module declaration is wrong:",
            "",
        ]
    )
    for partition in manifest["private_wiki_partitions"]:  # type: ignore[assignment]
        lines.append(f"- {partition}")
    return "\n".join(lines) + "\n"


def _write_generated_module_files(dest: pathlib.Path, module_id: str | None = None) -> int:
    payload = _public_module_manifest(module_id)
    (dest / "tigermemory-public-modules.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (dest / "MODULES.md").write_text(_public_modules_markdown(module_id), encoding="utf-8")
    return 2


def _write_evidence_report(
    out_path: pathlib.Path,
    *,
    generated: str,
    module_checks_payload: dict[str, list[str]],
    private_package_names: list[str],
    snapshot_audit: dict[str, object],
    module_check_validation: dict[str, object] | None,
) -> None:
    if module_check_validation is None:
        module_check_validation = validate_module_checks(REPO_ROOT)

    rows = sorted(module_checks_payload.items())
    lines = [
        "---",
        'source_url: "local-command:tm-publish-release-evidence"',
        f'fetched_at: "{generated}"',
        'fetched_by: "codex-via-tm_publish"',
        'title: "TigerMemory Public Release Evidence"',
        f"status: {'passed' if snapshot_audit['ok'] and module_check_validation['ok'] else 'failed'}",
        "---",
        "",
        "# TigerMemory Public Release Evidence",
        "",
        f"Generated at: {generated}",
        "",
        "## Summary",
        f"- Snapshot audit: {_format_status(snapshot_audit['ok'])}",
        f"- Module check validation: {_format_status(module_check_validation['ok'])}",
        "",
        "## Module checks",
        "| Module | Checks |",
        "| --- | --- |",
    ]
    for module_id, checks in rows:
        line = ", ".join(checks) if checks else "(none)"
        lines.append(f"| {module_id} | {line} |")

    lines.extend(
        [
            "",
            "## Private package names",
            "",
            "- " + "\n- ".join(private_package_names),
            "",
            "## Snapshot audit",
            f"- Result: {_format_status(snapshot_audit['ok'])}",
            f"- Sensitive findings: {snapshot_audit['sensitive_total']}",
            "",
        ]
    )

    lines.extend(
        [
            "## Module check validation",
            f"- Result: {_format_status(module_check_validation['ok'])}",
            f"- Checked: {len(module_check_validation['checked'])}",
            f"- Missing: {len(module_check_validation['missing'])}",
            "",
        ]
    )
    for item in module_check_validation["missing"]:
        lines.append(f"- {item['module']}: {item['path']}")

    lines.extend(
        [
            "",
            "## Recommended pre-release commands",
            "- tm publish --dry-run --json --audit-pii --evidence-report --validate-checks",
            "- py -m pytest tests/test_tm_cli.py packages/tigermemory-publish/tests/test_tigermemory_publish.py -q",
            "",
            "## Repo-scope audit",
            "- Repo-scope audit is a separate true-split readiness check and is run with `tm publish --audit-scope repo`.",
        ]
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_BYTECODE_CACHE_DIRNAME = "__py" + "cache__"


def _ignore_compiled_artifacts(_dir: str, names: list[str]) -> list[str]:
    """shutil.copytree ignore callback: drop bytecode cache dirs and .pyc files."""
    return [
        n
        for n in names
        if n == _BYTECODE_CACHE_DIRNAME or n.endswith(".pyc") or n.endswith(".egg-info")
    ]


def _copy_file(src: pathlib.Path, dst: pathlib.Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _split_mapped_file(item: str) -> tuple[str, str]:
    src, sep, dst = item.partition("=>")
    if not sep or not src or not dst:
        raise ValueError(f"invalid mapped file entry: {item}")
    return src, dst


def execute_plan(
    plan: dict[str, list[str]],
    repo_root: pathlib.Path,
    dest: pathlib.Path,
    module_id: str | None = None,
) -> int:
    """Copy every file listed in the plan into dest, mirroring repo layout.

    Returns the count of files copied (excluding directory recursion bookkeeping).
    """
    copied = 0

    for rel in plan["top_files"]:
        _copy_file(repo_root / rel, dest / rel)
        copied += 1

    for item in plan["mapped_files"]:
        src, dst = _split_mapped_file(item)
        _copy_file(repo_root / src, dest / dst)
        copied += 1

    for rel in plan["whole_dirs"]:
        src = repo_root / rel
        dst = dest / rel
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=_ignore_compiled_artifacts)
        copied += sum(1 for _ in dst.rglob("*") if _.is_file())

    for rel in plan["tool_files"]:
        _copy_file(repo_root / rel, dest / rel)
        copied += 1

    for rel in plan["tool_dirs"]:
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

    copied += _write_generated_module_files(dest, module_id)

    return copied


def write_pii_findings(dest: pathlib.Path, findings: list[dict[str, object]]) -> pathlib.Path:
    """Write the standalone PII audit report next to the publish snapshot."""
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "pii_findings.json"
    out.write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


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
    p.add_argument(
        "--audit-pii",
        action="store_true",
        help="write a standalone pii_findings.json report under --dest",
    )
    p.add_argument(
        "--audit-scope",
        choices=["snapshot", "repo"],
        default="snapshot",
        help="sensitive audit scope: publish snapshot only, or the full tracked repo",
    )
    p.add_argument(
        "--module",
        choices=module_ids(),
        help="inspect one public module; module output is not a full release gate",
    )
    p.add_argument(
        "--print-checks",
        action="store_true",
        help="print declared checks for public modules and exit",
    )
    p.add_argument(
        "--evidence-report",
        action="store_true",
        help="add release_evidence object in JSON output",
    )
    p.add_argument(
        "--validate-checks",
        action="store_true",
        help="validate module check paths",
    )
    p.add_argument(
        "--evidence-output",
        help="write public release evidence as Markdown to path",
    )
    p.add_argument(
        "--target",
        choices=["snapshot", "public-core"],
        default="snapshot",
        help="publish target; public-core is the true split export candidate",
    )
    p.add_argument(
        "--split-report",
        action="store_true",
        help="include true split readiness fields in JSON output",
    )
    p.add_argument(
        "--verify-split-smoke",
        action="store_true",
        help="run a temporary public-core install plus external instance smoke before marking public_core_independent true",
    )
    p.add_argument(
        "--verify-source-update-smoke",
        action="store_true",
        help="run a temporary public-core Git checkout smoke proving tm update can fast-forward without touching instance data",
    )
    args = p.parse_args(argv)

    if args.print_checks:
        payload = {
            "ok": True,
            "module": args.module,
            "checks": module_checks(args.module),
            "modules": module_summary(args.module),
            "inspection_only": args.module is not None,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for module_id, checks in payload["checks"].items():
                print(f"{module_id}:")
                for check in checks:
                    print(f"  {check}")
        return 0

    dest = pathlib.Path(args.dest)
    if not dest.is_absolute():
        dest = REPO_ROOT / dest

    plan = collect_publish_plan(REPO_ROOT, module_id=args.module)
    included = _included_plan(plan)
    excluded = _excluded_plan(plan)
    counts = {k: len(v) for k, v in included.items()}
    excluded_counts = {k: len(v) for k, v in excluded.items()}

    sensitive_findings = (
        audit_repo_sensitive(REPO_ROOT)
        if args.audit_scope == "repo"
        else audit_publish_plan(plan, REPO_ROOT)
    )
    full_snapshot_findings: list[dict[str, object]] = []
    if args.module is None:
        full_snapshot_plan = collect_publish_plan(REPO_ROOT)
        full_snapshot_findings = audit_publish_plan(full_snapshot_plan, REPO_ROOT)
    full_snapshot_blocking_findings = [
        f for f in full_snapshot_findings if f.get("severity") != "warning"
    ]
    blocking_findings = [f for f in sensitive_findings if f.get("severity") != "warning"]
    has_blocking_findings = bool(blocking_findings)

    split_smoke_ok = False
    source_update_smoke_ok = False
    if args.verify_split_smoke:
        def _publish_public_core_for_smoke(dest: pathlib.Path) -> None:
            smoke_plan = collect_publish_plan(REPO_ROOT)
            execute_plan(smoke_plan, REPO_ROOT, dest)

        split_smoke_ok = run_public_core_instance_smoke(
            repo_root=REPO_ROOT,
            publish_func=_publish_public_core_for_smoke,
        )
    if args.verify_source_update_smoke:
        def _publish_public_core_for_update_smoke(dest: pathlib.Path) -> None:
            smoke_plan = collect_publish_plan(REPO_ROOT)
            execute_plan(smoke_plan, REPO_ROOT, dest)

        source_update_smoke_ok = run_public_core_source_update_smoke(
            repo_root=REPO_ROOT,
            publish_func=_publish_public_core_for_update_smoke,
        )

    copied = 0
    if not args.dry_run and not has_blocking_findings:
        dest.mkdir(parents=True, exist_ok=True)
        copied = execute_plan(plan, REPO_ROOT, dest, module_id=args.module)
    pii_findings_path = None
    if args.audit_pii and not args.dry_run:
        pii_findings_path = write_pii_findings(dest, sensitive_findings)

    module_check_validation = None
    if (
        args.validate_checks
        or args.evidence_output
        or args.evidence_report
        or args.split_report
        or args.verify_split_smoke
        or args.verify_source_update_smoke
    ):
        module_check_validation = validate_module_checks(REPO_ROOT)
    public_boundary_validation = None
    if (
        args.validate_checks
        or args.evidence_output
        or args.evidence_report
        or args.split_report
        or args.verify_split_smoke
        or args.verify_source_update_smoke
    ):
        public_boundary_validation = validate_public_boundaries(REPO_ROOT, args.module)
    module_check_failure_is_blocking = bool(
        (args.validate_checks or args.evidence_output or args.evidence_report)
        and module_check_validation is not None
        and not module_check_validation["ok"]
    )
    public_boundary_failure_is_blocking = bool(
        (
            args.validate_checks
            or args.evidence_output
            or args.evidence_report
            or args.verify_split_smoke
            or args.verify_source_update_smoke
        )
        and public_boundary_validation is not None
        and not public_boundary_validation["ok"]
    )
    split_smoke_failure_is_blocking = bool(args.verify_split_smoke and not split_smoke_ok)
    source_update_smoke_failure_is_blocking = bool(args.verify_source_update_smoke and not source_update_smoke_ok)
    overall_ok = (
        not blocking_findings
        and not module_check_failure_is_blocking
        and not public_boundary_failure_is_blocking
        and not split_smoke_failure_is_blocking
        and not source_update_smoke_failure_is_blocking
    )

    summary = {
        "ok": overall_ok,
        "dest": str(dest),
        "dry_run": args.dry_run,
        "module": args.module,
        "target": args.target,
        "inspection_only": args.module is not None,
        "release_gate_scope": "inspection-only" if args.module else "full-snapshot",
        "release_gate_ok": False if args.module else overall_ok,
        "audit_pii": args.audit_pii,
        "audit_scope": args.audit_scope,
        "pii_findings_path": str(pii_findings_path) if pii_findings_path else None,
        "counts": counts,
        "excluded_counts": excluded_counts,
        "files_copied": copied,
        "modules": module_summary(args.module),
        "plan": included,
        "included": included,
        "excluded": excluded,
        "pii_findings": sensitive_findings,
        "sensitive_findings": sensitive_findings,
        "sensitive_counts": {
            "total": len(sensitive_findings),
            "warning": sum(1 for f in sensitive_findings if f["severity"] == "warning"),
            "high": sum(1 for f in sensitive_findings if f["severity"] == "high"),
            "medium": sum(1 for f in sensitive_findings if f["severity"] == "medium"),
        },
    }
    if args.audit_scope == "repo":
        summary["true_split_blockers"] = true_split_blocker_summary(sensitive_findings)

    if (args.validate_checks or args.evidence_report) and module_check_validation is not None:
        summary["module_check_validation"] = module_check_validation
    if (
        args.validate_checks
        or args.evidence_report
        or args.split_report
        or args.verify_split_smoke
        or args.verify_source_update_smoke
    ) and public_boundary_validation is not None:
        summary["public_boundary_validation"] = public_boundary_validation
    if args.split_report:
        if public_boundary_validation is None:
            public_boundary_validation = validate_public_boundaries(REPO_ROOT, args.module)
        boundary_ok = bool(public_boundary_validation and public_boundary_validation["ok"])
        summary["split_report"] = build_split_report(
            args.target,
            boundary_ok=boundary_ok,
            smoke_ok=split_smoke_ok,
            source_update_smoke_ok=source_update_smoke_ok,
        )

    if args.evidence_report and args.json:
        summary["release_evidence"] = {
            "schema": "tigermemory-public-release-evidence-v1",
            "inspection_only": args.module is not None,
            "selected_module": args.module,
            "release_gate_scope": "inspection-only" if args.module else "full-snapshot",
            "module_count": len(PUBLIC_MODULES) if args.module is None else 1,
            "module_details": module_details(args.module),
            "module_checks": module_checks(args.module),
            "private_package_names": list(PRIVATE_PACKAGE_NAMES),
            "snapshot_audit": _snapshot_audit_payload(sensitive_findings, has_blocking_findings),
            "full_snapshot_audit": (
                _skipped_snapshot_audit_payload("inspection-only module dry-run")
                if args.module
                else _snapshot_audit_payload(
                    full_snapshot_findings,
                    bool(full_snapshot_blocking_findings),
                )
            ),
            "module_check_validation": module_check_validation,
            "public_boundary_validation": public_boundary_validation,
        }
        if args.audit_scope == "repo" and "true_split_blockers" in summary:
            summary["release_evidence"]["true_split_blockers"] = summary["true_split_blockers"]
        if args.split_report and "split_report" in summary:
            summary["release_evidence"]["true_split"] = summary["split_report"]

    if not args.json and args.validate_checks:
        if module_check_validation and module_check_validation["ok"]:
            print("module check validation: PASS")
        else:
            print("module check validation: FAIL")
            if module_check_validation is not None:
                for item in module_check_validation["missing"]:
                    print(f"- {item['module']}: {item['path']}")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"dest: {summary['dest']}")
        print(f"dry_run: {summary['dry_run']}")
        print(f"audit_pii: {summary['audit_pii']}")
        print("counts:")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        if not args.dry_run:
            print(f"files_copied: {copied}")
        else:
            print("plan (would copy):")
            for k in ("top_files", "mapped_files", "whole_dirs", "tool_files", "tool_dirs", "wiki_public_pages", "config_files"):
                if not plan[k]:
                    continue
                print(f"  [{k}]")
                for f in plan[k]:
                    print(f"    {f}")
        if sensitive_findings:
            print("sensitive findings:")
            for finding in sensitive_findings:
                print(
                    f"  - {finding['severity']} {finding['kind']} "
                    f"{finding['path']}:{finding['line']} {finding['preview']}"
                )

    if args.evidence_output:
        _write_evidence_report(
            pathlib.Path(args.evidence_output),
            generated=datetime.now().astimezone().isoformat(),
            module_checks_payload=module_checks(),
            private_package_names=list(PRIVATE_PACKAGE_NAMES),
            snapshot_audit=_snapshot_audit_payload(sensitive_findings, has_blocking_findings),
            module_check_validation=module_check_validation,
        )

    return_code = 0 if overall_ok else 3
    return return_code


if __name__ == "__main__":
    sys.exit(main())
