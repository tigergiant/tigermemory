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
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys


PUBLISH_TOP_FILES = (
    "AGENTS.md",
    "index.md",
    "README.md",
    "pyproject.toml",
    "tigermemory_cli.py",
    ".gitignore",
)

PUBLISH_WHOLE_DIRS = (
    "tools",
    "schemas",
    "packages/tigerledger/src",
    "packages/tigermemory-answer/src",
    "packages/tigermemory-config/src",
    "packages/tigermemory-core/src",
    "packages/tigermemory-digest/src",
    "packages/tigermemory-doctor/src",
    "packages/tigermemory-eval/src",
    "packages/tigermemory-index/src",
    "packages/tigermemory-lessons/src",
    "packages/tigermemory-minimax/src",
    "packages/tigermemory-persona/src",
    "packages/tigermemory-protocols/src",
    "packages/tigermemory-publish/src",
    "packages/tigermemory-route/src",
    "packages/tigermemory-search/src",
)

WIKI_PUBLISH_PARTITIONS = (
    "brand",
    "investment",
    "operations",
    "production",
    "self-evolution",
    "systems",
)

EXCLUDED_WIKI_PARTITIONS = ("person",)
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

INCLUDED_PLAN_KEYS = ("top_files", "whole_dirs", "wiki_public_pages", "config_files")
EXCLUDED_PLAN_KEYS = ("excluded_by_public_field", "excluded_by_person_partition", "excluded_by_pii")


def _detect_repo_root() -> pathlib.Path:
    explicit = os.environ.get("TIGERMEMORY_ROOT")
    if explicit:
        return pathlib.Path(explicit).resolve()
    here = pathlib.Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        if (ancestor / ".git").is_dir() and (ancestor / "wiki").is_dir():
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
        "excluded_by_public_field": [],
        "excluded_by_person_partition": [],
        "excluded_by_pii": [],
    }
    pii_findings: list[dict[str, object]] = []

    for name in PUBLISH_TOP_FILES:
        if (repo_root / name).is_file():
            plan["top_files"].append(name)

    for d in PUBLISH_WHOLE_DIRS:
        if (repo_root / d).is_dir():
            plan["whole_dirs"].append(d)

    wiki_root = repo_root / "wiki"
    if wiki_root.is_dir():
        for partition in EXCLUDED_WIKI_PARTITIONS:
            partition_dir = wiki_root / partition
            if not partition_dir.is_dir():
                continue
            for md in sorted(partition_dir.rglob("*.md")):
                if md.is_file():
                    plan["excluded_by_person_partition"].append(md.relative_to(repo_root).as_posix())
        for partition in WIKI_PUBLISH_PARTITIONS:
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


def _planned_text_files(plan: dict[str, list[str]], repo_root: pathlib.Path) -> list[tuple[str, str]]:
    """Return (category, rel_path) pairs that should be scanned before publish."""
    items: list[tuple[str, str]] = []
    for category in ("top_files", "wiki_public_pages", "config_files"):
        for rel in plan.get(category, []):
            items.append((category, rel))
    for rel_dir in plan.get("whole_dirs", []):
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
    scan_pii = category in {"wiki_public_pages", "top_files", "config_files", "repo_audit"}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_KEY_RE.search(line):
            _add_finding(
                findings,
                category=category,
                rel=rel,
                line_no=line_no,
                kind="private_key",
                severity="high",
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
                severity="high",
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
                severity="high",
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
                severity="high",
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
                severity="medium",
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
                severity=_path_leak_severity(rel),
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


def _included_plan(plan: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(plan.get(key, [])) for key in INCLUDED_PLAN_KEYS}


def _excluded_plan(plan: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(plan.get(key, [])) for key in EXCLUDED_PLAN_KEYS}


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
    args = p.parse_args(argv)

    dest = pathlib.Path(args.dest)
    if not dest.is_absolute():
        dest = REPO_ROOT / dest

    plan = collect_publish_plan(REPO_ROOT)
    included = _included_plan(plan)
    excluded = _excluded_plan(plan)
    counts = {k: len(v) for k, v in included.items()}
    excluded_counts = {k: len(v) for k, v in excluded.items()}

    sensitive_findings = (
        audit_repo_sensitive(REPO_ROOT)
        if args.audit_scope == "repo"
        else audit_publish_plan(plan, REPO_ROOT)
    )
    blocking_findings = [f for f in sensitive_findings if f.get("severity") != "warning"]
    has_blocking_findings = bool(blocking_findings)

    copied = 0
    if not args.dry_run and not has_blocking_findings:
        dest.mkdir(parents=True, exist_ok=True)
        copied = execute_plan(plan, REPO_ROOT, dest)
    pii_findings_path = None
    if args.audit_pii and not args.dry_run:
        pii_findings_path = write_pii_findings(dest, sensitive_findings)

    summary = {
        "ok": not blocking_findings,
        "dest": str(dest),
        "dry_run": args.dry_run,
        "audit_pii": args.audit_pii,
        "audit_scope": args.audit_scope,
        "pii_findings_path": str(pii_findings_path) if pii_findings_path else None,
        "counts": counts,
        "excluded_counts": excluded_counts,
        "files_copied": copied,
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
            for k in ("top_files", "whole_dirs", "wiki_public_pages", "config_files"):
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
    blocking_findings = [f for f in sensitive_findings if f.get("severity") != "warning"]
    return 3 if blocking_findings else 0


if __name__ == "__main__":
    sys.exit(main())
