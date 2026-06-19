"""Module declarations for TigerMemory public snapshots.

This file is intentionally data-only. The public snapshot is still a copied
tree, but the copy plan is now owned by named modules instead of scattered
tuples in the publisher entrypoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ast
from pathlib import Path


MappedFile = tuple[str, str]

@dataclass(frozen=True)
class PublishModule:
    id: str
    description: str
    stability: str
    package_roots: tuple[str, ...] = field(default_factory=tuple)
    data_dirs: tuple[str, ...] = field(default_factory=tuple)
    top_files: tuple[str, ...] = field(default_factory=tuple)
    mapped_files: tuple[MappedFile, ...] = field(default_factory=tuple)
    tool_files: tuple[str, ...] = field(default_factory=tuple)
    tool_dirs: tuple[str, ...] = field(default_factory=tuple)
    wiki_partitions: tuple[str, ...] = field(default_factory=tuple)
    checks: tuple[str, ...] = field(default_factory=tuple)
    excluded_wiki_partitions: tuple[str, ...] = field(default_factory=tuple)
    private_package_names: tuple[str, ...] = field(default_factory=tuple)


PUBLIC_MODULES: tuple[PublishModule, ...] = (
    PublishModule(
        id="public-cli",
        description="Root CLI entrypoint and basic checkout files.",
        stability="core",
        top_files=(
            "tigermemory_cli.py",
            ".gitignore",
        ),
        checks=(
            "tests/test_tm_cli.py",
            "tests/test_pre_push_publish_smoke.py",
        ),
    ),
    PublishModule(
        id="public-core",
        description="Local-first memory, config, search, routing, index, lessons, persona, doctor, digest, and schemas.",
        stability="core",
        package_roots=(
            "packages/tigermemory-config/src",
            "packages/tigermemory-core/src",
            "packages/tigermemory-digest/src",
            "packages/tigermemory-doctor/src",
            "packages/tigermemory-index/src",
            "packages/tigermemory-lessons/src",
            "packages/tigermemory-persona/src",
            "packages/tigermemory-protocols/src",
            "packages/tigermemory-route/src",
            "packages/tigermemory-search/src",
        ),
        data_dirs=("schemas",),
        tool_files=(
            "tools/_bootstrap_paths.py",
            "tools/tm_agent_doctor.py",
            "tools/tm_compile_index.py",
            "tools/tm_core.py",
            "tools/tm_dashboard_prefs.py",
            "tools/tm_io.py",
            "tools/tm_lessons.py",
            "tools/tm_local_memory.py",
            "tools/tm_memory_ops.py",
            "tools/tm_memory_reflection.py",
            "tools/tm_persona.py",
            "tools/tm_retention_audit.py",
            "tools/tm_route.py",
            "tools/tm_route_audit.py",
            "tools/tm_route_events.py",
            "tools/tm_runtime_events.py",
        ),
        checks=(
            "tests/test_public_boundary.py",
            "tests/test_tm_publish.py",
        ),
    ),
    PublishModule(
        id="public-answer-offline",
        description="Evidence-first answer and offline ask path that does not require an online model.",
        stability="core",
        package_roots=("packages/tigermemory-answer/src",),
        tool_files=("tools/tm_answer_trace.py",),
        tool_dirs=("tools/memory_answer",),
        checks=("tests/test_tm_cli.py",),
    ),
    PublishModule(
        id="public-dashboard",
        description="Local dashboard entrypoint and static assets; advanced hosted service features remain optional.",
        stability="core",
        tool_files=(
            "tools/tm_cron_apply.py",
            "tools/tm_review.py",
            "tools/tm_review_tools.py",
            "tools/tm_review_ui.py",
            "tools/tm_self_evolution.py",
        ),
        tool_dirs=("tools/static",),
        checks=("tests/test_tm_cli.py",),
    ),
    PublishModule(
        id="public-publish",
        description="Snapshot builder, publish audit, and public release templates.",
        stability="core",
        package_roots=("packages/tigermemory-publish/src",),
        mapped_files=(
            ("packages/tigermemory-publish/src/tigermemory_publish/templates/AGENTS.md", "AGENTS.md"),
            ("packages/tigermemory-publish/src/tigermemory_publish/templates/index.md", "index.md"),
            ("packages/tigermemory-publish/src/tigermemory_publish/templates/LICENSE", "LICENSE"),
            (
                "packages/tigermemory-publish/src/tigermemory_publish/templates/THIRD_PARTY_NOTICES.md",
                "THIRD_PARTY_NOTICES.md",
            ),
            ("packages/tigermemory-publish/src/tigermemory_publish/templates/pyproject.toml", "pyproject.toml"),
            ("packages/tigermemory-publish/src/tigermemory_publish/templates/README.md", "README.md"),
        ),
        tool_files=("tools/tm_publish.py",),
        checks=(
            "packages/tigermemory-publish/tests/test_tigermemory_publish.py",
            "tests/test_pre_push_publish_smoke.py",
        ),
    ),
    PublishModule(
        id="public-wiki-seed",
        description="Starter public wiki canvas and public markdown partitions.",
        stability="core",
        mapped_files=(
            (
                "packages/tigermemory-publish/src/tigermemory_publish/templates/wiki/operations/project-canvas.md",
                "wiki/operations/project-canvas.md",
            ),
        ),
        wiki_partitions=(
            "brand",
            "operations",
            "production",
            "self-evolution",
            "systems",
        ),
        checks=("tests/test_public_boundary.py",),
    ),
)


PRIVATE_EXCLUDED_MODULES: tuple[PublishModule, ...] = (
    PublishModule(
        id="private-dogfood",
        description="TigerMemory's local dogfood governance, supervisor, OpenClaw, investment, person, runtime, and review data.",
        stability="private-excluded",
        excluded_wiki_partitions=("person", "investment"),
        private_package_names=(
            "tigerledger",
            "tigermemory_eval",
            "tigermemory_minimax",
        ),
    ),
    PublishModule(
        id="optional-hybrid",
        description="OpenMemory, Qdrant, Caddy, WSL service, and other hybrid deployment integrations.",
        stability="optional",
    ),
)


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _flatten(modules: tuple[PublishModule, ...], attr: str) -> tuple:
    values: list = []
    for module in modules:
        values.extend(getattr(module, attr))
    return tuple(values)


def public_modules(module_id: str | None = None) -> tuple[PublishModule, ...]:
    if module_id is None:
        return PUBLIC_MODULES
    modules = tuple(module for module in PUBLIC_MODULES if module.id == module_id)
    if not modules:
        raise KeyError(module_id)
    return modules


def public_top_files(module_id: str | None = None) -> tuple[str, ...]:
    return _flatten(public_modules(module_id), "top_files")


def public_mapped_files(module_id: str | None = None) -> tuple[MappedFile, ...]:
    return _flatten(public_modules(module_id), "mapped_files")


def public_package_roots(module_id: str | None = None) -> tuple[str, ...]:
    return _flatten(public_modules(module_id), "package_roots")


def public_data_dirs(module_id: str | None = None) -> tuple[str, ...]:
    return _flatten(public_modules(module_id), "data_dirs")


def public_whole_dirs(module_id: str | None = None) -> tuple[str, ...]:
    return (*public_data_dirs(module_id), *public_package_roots(module_id))


def public_tool_files(module_id: str | None = None) -> tuple[str, ...]:
    return _flatten(public_modules(module_id), "tool_files")


def public_tool_dirs(module_id: str | None = None) -> tuple[str, ...]:
    return _flatten(public_modules(module_id), "tool_dirs")


def public_wiki_partitions(module_id: str | None = None) -> tuple[str, ...]:
    return _flatten(public_modules(module_id), "wiki_partitions")


def private_excluded_wiki_partitions() -> tuple[str, ...]:
    return _unique(list(_flatten(PRIVATE_EXCLUDED_MODULES, "excluded_wiki_partitions")))


def private_package_names() -> tuple[str, ...]:
    return _unique(list(_flatten(PRIVATE_EXCLUDED_MODULES, "private_package_names")))


PRIVATE_PACKAGE_NAMES = private_package_names()


def module_ids() -> tuple[str, ...]:
    return tuple(module.id for module in PUBLIC_MODULES)


def module_checks(module_id: str | None = None) -> dict[str, list[str]]:
    modules = public_modules(module_id)
    return {module.id: list(module.checks) for module in modules}


def validate_module_checks(project_root: str | Path) -> dict[str, object]:
    """Return module check path coverage for a given project root."""
    root = Path(project_root)
    checked: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for module in PUBLIC_MODULES:
        for check_path in module.checks:
            checked.append({"module": module.id, "path": check_path})
            if not (root / check_path).exists():
                missing.append({"module": module.id, "path": check_path})
    return {"ok": not missing, "checked": checked, "missing": missing}


def module_details(module_id: str | None = None) -> list[dict[str, object]]:
    return [
        {
            "id": module.id,
            "description": module.description,
            "stability": module.stability,
            "package_roots": list(module.package_roots),
            "data_dirs": list(module.data_dirs),
            "top_files": list(module.top_files),
            "mapped_files": [list(item) for item in module.mapped_files],
            "tool_files": list(module.tool_files),
            "tool_dirs": list(module.tool_dirs),
            "wiki_partitions": list(module.wiki_partitions),
            "checks": list(module.checks),
        }
        for module in public_modules(module_id)
    ]


def module_summary(module_id: str | None = None) -> dict[str, object]:
    modules = public_modules(module_id)
    return {
        "included": [module.id for module in modules],
        "excluded": [module.id for module in PRIVATE_EXCLUDED_MODULES],
        "count": len(modules),
        "private_package_names": list(PRIVATE_PACKAGE_NAMES),
        "private_excluded_wiki_partitions": list(private_excluded_wiki_partitions()),
        "details": module_details(module_id),
    }


def validate_public_boundaries(project_root: str | Path, module_id: str | None = None) -> dict[str, object]:
    """Return module-level public/private boundary validation results.

    This guard is intentionally independent from the snapshot PII scanner: it
    checks release structure, imports, and private path ownership before files
    are copied.
    """
    root = Path(project_root)
    violations: list[dict[str, str]] = []
    private_wiki = set(private_excluded_wiki_partitions())
    private_modules = set(PRIVATE_PACKAGE_NAMES)

    for module in public_modules(module_id):
        for partition in module.wiki_partitions:
            if partition in private_wiki:
                violations.append(
                    {
                        "kind": "private_wiki_partition",
                        "module": module.id,
                        "path": f"wiki/{partition}",
                    }
                )
        for rel in (
            *module.top_files,
            *(src for src, _dst in module.mapped_files),
            *module.package_roots,
            *module.data_dirs,
            *module.tool_files,
            *module.tool_dirs,
        ):
            path = Path(rel)
            parts = set(path.parts)
            if ".tmp" in parts or "review-artifacts" in parts:
                violations.append({"kind": "forbidden_path", "module": module.id, "path": rel})
            if path.parts[:2] in (("wiki", "person"), ("wiki", "investment")):
                violations.append({"kind": "private_wiki_path", "module": module.id, "path": rel})
            if "runtime" in parts and not rel.endswith(".example"):
                violations.append({"kind": "runtime_non_template", "module": module.id, "path": rel})
            lowered = rel.lower()
            if rel.startswith("tools/") and any(
                marker in lowered for marker in ("openclaw", "investment", "supervisor", "internal-analysis")
            ):
                violations.append({"kind": "private_tool_pattern", "module": module.id, "path": rel})

    for rel_root in public_package_roots(module_id):
        source_root = root / rel_root
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                violations.append(
                    {
                        "kind": "python_parse_error",
                        "module": module_id or "*",
                        "path": path.relative_to(root).as_posix(),
                        "detail": str(exc),
                    }
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module.split(".", 1)[0]]
                else:
                    continue
                for name in imported:
                    if name in private_modules:
                        violations.append(
                            {
                                "kind": "private_package_import",
                                "module": module_id or "*",
                                "path": path.relative_to(root).as_posix(),
                                "detail": name,
                            }
                        )

    return {"ok": not violations, "violations": violations}
