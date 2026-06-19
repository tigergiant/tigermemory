"""Module declarations for TigerMemory public snapshots.

This file is intentionally data-only. The public snapshot is still a copied
tree, but the copy plan is now owned by named modules instead of scattered
tuples in the publisher entrypoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    ),
    PublishModule(
        id="optional-hybrid",
        description="OpenMemory, Qdrant, Caddy, WSL service, and other hybrid deployment integrations.",
        stability="optional",
    ),
)


def _flatten(modules: tuple[PublishModule, ...], attr: str) -> tuple:
    values: list = []
    for module in modules:
        values.extend(getattr(module, attr))
    return tuple(values)


def public_top_files() -> tuple[str, ...]:
    return _flatten(PUBLIC_MODULES, "top_files")


def public_mapped_files() -> tuple[MappedFile, ...]:
    return _flatten(PUBLIC_MODULES, "mapped_files")


def public_package_roots() -> tuple[str, ...]:
    return _flatten(PUBLIC_MODULES, "package_roots")


def public_data_dirs() -> tuple[str, ...]:
    return _flatten(PUBLIC_MODULES, "data_dirs")


def public_whole_dirs() -> tuple[str, ...]:
    return (*public_data_dirs(), *public_package_roots())


def public_tool_files() -> tuple[str, ...]:
    return _flatten(PUBLIC_MODULES, "tool_files")


def public_tool_dirs() -> tuple[str, ...]:
    return _flatten(PUBLIC_MODULES, "tool_dirs")


def public_wiki_partitions() -> tuple[str, ...]:
    return _flatten(PUBLIC_MODULES, "wiki_partitions")


def module_summary() -> dict[str, object]:
    return {
        "included": [module.id for module in PUBLIC_MODULES],
        "excluded": [module.id for module in PRIVATE_EXCLUDED_MODULES],
        "count": len(PUBLIC_MODULES),
        "details": [
            {
                "id": module.id,
                "description": module.description,
                "stability": module.stability,
                "checks": list(module.checks),
            }
            for module in PUBLIC_MODULES
        ],
    }
