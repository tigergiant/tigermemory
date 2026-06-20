from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

import tigermemory_publish


PUBLIC_TRUE_PAGE = """---
owner: cascade
status: active
updated: 2026-05-24
public: true
title: "public page"
---

# public page

## 摘要

Sample public page body.

## 来源

- none
"""

PUBLIC_SECRET_PAGE = """---
owner: cascade
status: active
updated: 2026-05-24
public: true
title: "secret page"
---

# secret page

## 摘要

This page accidentally contains api_key: sk-1234567890abcdefghijklmnopqrstuvwxyz

## 来源

- none
"""

PUBLIC_FALSE_PAGE = """---
owner: cascade
status: active
updated: 2026-05-24
public: false
title: "private page"
---

# private page

## 摘要

Sample private page body.

## 来源

- none
"""

NO_FLAG_PAGE = """---
owner: cascade
status: active
updated: 2026-05-24
title: "untagged page"
---

# untagged page

## 摘要

Sample untagged page body.

## 来源

- none
"""

PUBLIC_PATH_LEAK_PAGE = """---
owner: cascade
status: active
updated: 2026-05-24
public: true
title: "path leak page"
---

# path leak page

## 摘要

This sample references {{PRIVATE_PATH}} for demo.

## 来源

- none
"""

PRIVATE_PATH_LEAK_PAGE = """---
owner: cascade
status: active
updated: 2026-05-24
title: "private path page"
---

# private path page

## 摘要

This private page references {{PRIVATE_PATH}}.

## 来源

- none
"""


def _build_fake_repo(root: pathlib.Path) -> None:
    """Populate `root` with the minimal tigermemory layout the publisher inspects."""
    (root / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (root / "index.md").write_text("# private index\n\nTM_MCP_API_KEY lives elsewhere.\n", encoding="utf-8")
    (root / "README.md").write_text("# README\n", encoding="utf-8")
    (root / ".gitignore").write_text("placeholder\n", encoding="utf-8")
    for src, dst in tigermemory_publish.PUBLISH_MAPPED_FILES:
        path = root / src
        path.parent.mkdir(parents=True, exist_ok=True)
        if dst == "README.md":
            path.write_text(
                "# public README\n\n"
                "Install from this snapshot checkout.\n\n"
                "## Do Not Install From npm\n\n"
                "Do not run `npm install -g tigermemory` for this project. "
                "That npm package is a different Node/TypeScript Claude Code memory server.\n\n"
                "## Which Mode Should I Use?\n\n"
                "Start with **local + LLM** unless you already know you need a shared memory service.\n\n"
                "Run `tm llm guide` and `tm llm status` before the full Wiki Admin path.\n\n"
                "Use `tm ask --offline --query \"hello local memory\"` to return local evidence without AI.\n\n"
                "Do not install WSL, Docker, Qdrant, Caddy, or OpenMemory just to try the basic mode.\n"
                "Do not use `python -m tm`; use the installed `tm` console script.\n",
                encoding="utf-8",
            )
        elif dst == "AGENTS.md":
            path.write_text(
                "# public AGENTS\n\n"
                "tm llm status checks provider configuration without printing secrets.\n"
                "tm ask --offline returns local evidence only and must not call online Mem0.\n",
                encoding="utf-8",
            )
        elif dst == "index.md":
            path.write_text(
                "# public index\n\n"
                "No private endpoint here.\n\n"
                "Run `tm llm status` before the LLM Wiki Admin path.\n\n"
                "Use `tm ask --offline` for local evidence.\n",
                encoding="utf-8",
            )
        elif dst == "LICENSE":
            path.write_text("AGPL-3.0-or-later\n", encoding="utf-8")
        elif dst == "THIRD_PARTY_NOTICES.md":
            path.write_text("# Third-Party Notices\n\nTailwind CSS — MIT\n", encoding="utf-8")
        elif dst == "pyproject.toml":
            path.write_text(
                "[project]\n"
                "name='tigermemory'\n"
                "license = { text = 'AGPL-3.0-or-later' }\n"
                "\n[project.scripts]\n"
                "tm = 'tigermemory_cli:main'\n",
                encoding="utf-8",
            )
        elif dst == "wiki/operations/project-canvas.md":
            path.write_text(
                "---\npublic: true\n---\n\n```mermaid\nstateDiagram-v2\n    [*] --> P0_Setup: done\n```\n",
                encoding="utf-8",
            )
        else:
            path.write_text("# public AGENTS\n\nNo private path here.\n", encoding="utf-8")

    (root / "tools").mkdir()
    (root / "tools" / "tm_dummy.py").write_text("# stub tool\n", encoding="utf-8")
    (root / "tools" / "tm_phone_regex.py").write_text(
        'PHONE_RE = r"(?<!\\\\d)1[3-9]\\\\d{9}(?!\\\\d)"\n'
        "token = RefreshToken.model_validate(raw)\n",
        encoding="utf-8",
    )
    for rel in tigermemory_publish.PUBLISH_TOOL_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# publish tool stub\n", encoding="utf-8")
    for rel in tigermemory_publish.PUBLISH_TOOL_DIRS:
        path = root / rel
        path.mkdir(parents=True, exist_ok=True)
        (path / "asset.txt").write_text("asset\n", encoding="utf-8")
        if rel == "tools/static":
            for name in (
                "start.html",
                "review.html",
                "health.html",
                "quality.html",
                "canvas.html",
                "dashboard-common.js",
                "dashboard-pages.js",
            ):
                (path / name).write_text(f"// {name}\n", encoding="utf-8")

    (root / "schemas").mkdir()
    (root / "schemas" / "PAGE_FORMATS.md").write_text("# schemas\n", encoding="utf-8")

    for rel in tigermemory_publish.PUBLISH_WHOLE_DIRS:
        if rel == "schemas":
            continue
        package_src = root / rel
        package_src.mkdir(parents=True, exist_ok=True)
        (package_src / "__init__.py").write_text("# package\n", encoding="utf-8")
        if rel == "packages/tigermemory-dashboard/src":
            static = package_src / "tigermemory_dashboard" / "static"
            static.mkdir(parents=True, exist_ok=True)
            for name in (
                "start.html",
                "review.html",
                "health.html",
                "quality.html",
                "canvas.html",
                "dashboard-common.js",
                "dashboard-pages.js",
            ):
                (static / name).write_text(f"// {name}\n", encoding="utf-8")

    wiki = root / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "public-page.md").write_text(PUBLIC_TRUE_PAGE, encoding="utf-8")
    (wiki / "systems" / "private-flagged.md").write_text(PUBLIC_FALSE_PAGE, encoding="utf-8")
    (wiki / "systems" / "untagged.md").write_text(NO_FLAG_PAGE, encoding="utf-8")

    # wiki/person is sensitive — even if `public: true` it must be skipped.
    (wiki / "person").mkdir()
    (wiki / "person" / "tiger-preferences.md").write_text(
        PUBLIC_TRUE_PAGE.replace('title: "public page"', 'title: "person page"'),
        encoding="utf-8",
    )
    (wiki / "investment").mkdir()
    (wiki / "investment" / "portfolio-rules.md").write_text(
        PUBLIC_TRUE_PAGE.replace('title: "public page"', 'title: "investment page"'),
        encoding="utf-8",
    )

    # runtime config template (commit-safe).
    openmemory = root / "runtime" / "openmemory"
    openmemory.mkdir(parents=True)
    template_name = "." + "env.example"
    real_name = "." + "env"
    (openmemory / template_name).write_text("KEY=\n", encoding="utf-8")
    # Real runtime config (no .example suffix) must NOT be picked up.
    (openmemory / real_name).write_text("KEY=stub-value\n", encoding="utf-8")

    for checks in tigermemory_publish.module_checks().values():
        for check in checks:
            path = root / check
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# module check placeholder\n", encoding="utf-8")

    for checks in tigermemory_publish.module_checks().values():
        for check in checks:
            path = root / check
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# module check placeholder\n", encoding="utf-8")


def test_has_public_true_recognizes_flag(tmp_path: pathlib.Path) -> None:
    page = tmp_path / "p.md"
    page.write_text(PUBLIC_TRUE_PAGE, encoding="utf-8")
    assert tigermemory_publish._has_public_true(page) is True


def test_has_public_true_rejects_false(tmp_path: pathlib.Path) -> None:
    page = tmp_path / "p.md"
    page.write_text(PUBLIC_FALSE_PAGE, encoding="utf-8")
    assert tigermemory_publish._has_public_true(page) is False


def test_has_public_true_rejects_missing_flag(tmp_path: pathlib.Path) -> None:
    page = tmp_path / "p.md"
    page.write_text(NO_FLAG_PAGE, encoding="utf-8")
    assert tigermemory_publish._has_public_true(page) is False


def test_parse_frontmatter_public_defaults_false_without_frontmatter() -> None:
    assert tigermemory_publish.parse_frontmatter_public("# no frontmatter\n") is False


def test_parse_frontmatter_public_defaults_false_when_field_missing() -> None:
    assert tigermemory_publish.parse_frontmatter_public(NO_FLAG_PAGE) is False


def test_parse_frontmatter_public_accepts_true_yes_and_one() -> None:
    for value in ("true", "True", "yes", "Yes", "1", '"true"', "'yes'"):
        content = PUBLIC_FALSE_PAGE.replace("public: false", f"public: {value}")
        assert tigermemory_publish.parse_frontmatter_public(content) is True


def test_parse_frontmatter_public_rejects_false_like_values() -> None:
    for value in ("false", "False", "no", "0", "maybe"):
        content = PUBLIC_FALSE_PAGE.replace("public: false", f"public: {value}")
        assert tigermemory_publish.parse_frontmatter_public(content) is False


def test_public_templates_document_source_first_update_install() -> None:
    templates = pathlib.Path(tigermemory_publish.__file__).resolve().parent / "templates"
    readme = (templates / "README.md").read_text(encoding="utf-8")
    index = (templates / "index.md").read_text(encoding="utf-8")

    assert "py -m pip install ." in readme
    assert "py -m pip install -e ." in readme
    assert "tm update status" in readme
    assert "tm admin guide" in readme
    assert "tm admin propose" in readme
    assert "propose` writes a proposal" in readme
    assert "git reset --hard" in readme
    assert "py -m pip install ." in index
    assert "tm admin guide" in index
    assert "py -m pip install -e ." not in index


def test_collect_publish_plan_default_private(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert plan["top_files"] == sorted([".gitignore"])
    assert "packages/tigermemory-dashboard/src" in plan["whole_dirs"]
    assert "packages/tigermemory-publish/src" in plan["whole_dirs"]
    assert "schemas" in plan["whole_dirs"]
    assert plan["mapped_files"] == [
        "packages/tigermemory-publish/src/tigermemory_publish/templates/AGENTS.md=>AGENTS.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/LICENSE=>LICENSE",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/README.md=>README.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/THIRD_PARTY_NOTICES.md=>THIRD_PARTY_NOTICES.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/index.md=>index.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/pyproject.toml=>pyproject.toml",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/wiki/operations/project-canvas.md=>wiki/operations/project-canvas.md",
    ]
    assert "tools/tm_io.py" in plan["tool_files"]
    assert "tools/tm_review_ui.py" not in plan["tool_files"]
    assert "tools/tm_review_tools.py" not in plan["tool_files"]
    assert plan["tool_dirs"] == sorted(["tools/memory_answer"])
    assert plan["wiki_public_pages"] == ["wiki/systems/public-page.md"]
    assert plan["excluded_by_public_field"] == [
        "wiki/systems/private-flagged.md",
        "wiki/systems/untagged.md",
    ]
    assert plan["excluded_by_private_partition"] == [
        "wiki/investment/portfolio-rules.md",
        "wiki/person/tiger-preferences.md",
    ]
    assert plan["excluded_by_person_partition"] == ["wiki/person/tiger-preferences.md"]
    expected_template = "runtime/openmemory/." + "env.example"
    assert plan["config_files"] == [expected_template]
    findings = tigermemory_publish.audit_publish_plan(plan, tmp_path)
    assert findings == []


def test_public_modules_own_publish_constants() -> None:
    modules = tigermemory_publish.PUBLIC_MODULES

    assert tigermemory_publish.PUBLISH_TOP_FILES == tuple(
        item for module in modules for item in module.top_files
    )
    assert tigermemory_publish.PUBLISH_MAPPED_FILES == tuple(
        item for module in modules for item in module.mapped_files
    )
    assert tigermemory_publish.PUBLISH_WHOLE_DIRS == (
        *tuple(item for module in modules for item in module.data_dirs),
        *tuple(item for module in modules for item in module.package_roots),
    )
    assert tigermemory_publish.PUBLISH_TOOL_FILES == tuple(
        item for module in modules for item in module.tool_files
    )
    assert tigermemory_publish.PUBLISH_TOOL_DIRS == tuple(
        item for module in modules for item in module.tool_dirs
    )
    assert tigermemory_publish.WIKI_PUBLISH_PARTITIONS == tuple(
        item for module in modules for item in module.wiki_partitions
    )


def test_schemas_are_data_dir_not_installable_package_root() -> None:
    assert "schemas" in tigermemory_publish.PUBLISH_WHOLE_DIRS
    assert "schemas" not in tigermemory_publish.PUBLISH_PACKAGE_ROOTS


def test_public_pyproject_package_roots_match_modules() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    template = repo_root / "packages" / "tigermemory-publish" / "src" / "tigermemory_publish" / "templates" / "pyproject.toml"
    data = tomllib.loads(template.read_text(encoding="utf-8"))
    where = data["tool"]["setuptools"]["packages"]["find"]["where"]

    assert set(where) == set(tigermemory_publish.PUBLISH_PACKAGE_ROOTS)
    assert "schemas" not in where

    private_package_roots = {
        "packages/tigerledger/src",
        "packages/tigermemory-eval/src",
        "packages/tigermemory-minimax/src",
    }
    existing_private_roots = {
        rel
        for rel in private_package_roots
        if (repo_root / rel).exists()
    }
    assert not existing_private_roots.intersection(where)


def test_module_summary_exposes_private_package_single_source() -> None:
    summary = tigermemory_publish.module_summary()

    assert summary["private_package_names"] == list(tigermemory_publish.PRIVATE_PACKAGE_NAMES)
    assert summary["private_excluded_wiki_partitions"] == ["person", "investment"]
    assert {"tigerledger", "tigermemory_eval", "tigermemory_minimax"}.issubset(
        set(tigermemory_publish.PRIVATE_PACKAGE_NAMES)
    )


def test_private_excluded_wiki_partitions_are_manifest_owned() -> None:
    assert tigermemory_publish.EXCLUDED_WIKI_PARTITIONS == ("person", "investment")
    assert tigermemory_publish.private_excluded_wiki_partitions() == ("person", "investment")


def test_public_packages_do_not_import_private_packages() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    private_modules = set(tigermemory_publish.PRIVATE_PACKAGE_NAMES)
    violations: list[str] = []

    for rel_root in tigermemory_publish.PUBLISH_PACKAGE_ROOTS:
        root = repo_root / rel_root
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module.split(".", 1)[0]]
                else:
                    continue
                for name in imported:
                    if name in private_modules:
                        rel = path.relative_to(repo_root).as_posix()
                        violations.append(f"{rel} imports {name}")

    assert violations == []


def test_print_checks_json_outputs_module_checks(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main(["--print-checks", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["module"] is None
    assert "public-dashboard" in payload["checks"]
    assert "tests/test_tm_cli.py" in payload["checks"]["public-dashboard"]
    assert payload["modules"]["private_package_names"] == list(tigermemory_publish.PRIVATE_PACKAGE_NAMES)


def test_module_checks_unknown_module_raises_key_error() -> None:
    with pytest.raises(KeyError):
        tigermemory_publish.module_checks("missing-module")


def test_print_checks_json_can_filter_one_module(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main(["--print-checks", "--module", "public-publish", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert payload["module"] == "public-publish"
    assert set(payload["checks"]) == {"public-publish"}
    assert "packages/tigermemory-publish/tests/test_tigermemory_publish.py" in payload["checks"]["public-publish"]


def test_validate_module_checks_points_to_existing_paths() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    result = tigermemory_publish.validate_module_checks(repo_root)

    assert result["ok"] is True
    assert result["missing"] == []
    assert result["checked"], "there should be at least one module check configured"


def test_validate_module_checks_reports_missing_path(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    checks = {
        check
        for checks in tigermemory_publish.module_checks().values()
        for check in checks
    }
    for check in checks:
        path = repo_root / check
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")

    payload = tigermemory_publish.validate_module_checks(repo_root)
    assert payload["ok"] is True

    (repo_root / "tests/test_tm_cli.py").unlink()
    payload = tigermemory_publish.validate_module_checks(repo_root)
    assert payload["ok"] is False
    assert any(item["path"] == "tests/test_tm_cli.py" for item in payload["missing"])


def test_detect_repo_root_accepts_git_worktree_file(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    module_file = repo / "packages/tigermemory-publish/src/tigermemory_publish/__init__.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("# module placeholder\n", encoding="utf-8")
    (repo / "wiki").mkdir()
    (repo / ".git").write_text("gitdir: ../.git/worktrees/repo\n", encoding="utf-8")

    monkeypatch.delenv("TIGERMEMORY_ROOT", raising=False)
    monkeypatch.setattr(tigermemory_publish, "__file__", str(module_file))

    assert tigermemory_publish._detect_repo_root() == repo.resolve()


def test_main_with_evidence_report_json_includes_release_evidence_payload(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "out"),
        "--dry-run",
        "--json",
        "--audit-pii",
        "--evidence-report",
    ])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["ok"] is True
    release = out["release_evidence"]
    assert release["schema"] == "tigermemory-public-release-evidence-v1"
    assert release["module_count"] == len(tigermemory_publish.PUBLIC_MODULES)
    assert release["inspection_only"] is False
    assert release["release_gate_scope"] == "full-snapshot"
    assert release["module_details"]
    assert "public-dashboard" in release["module_checks"]
    assert release["snapshot_audit"]["sensitive_total"] == 0
    assert release["full_snapshot_audit"]["sensitive_total"] == 0
    assert release["module_check_validation"]["ok"] is True
    assert release["public_boundary_validation"]["ok"] is True
    assert out["module_check_validation"]["ok"] is True


def test_release_evidence_includes_true_split_status(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)
    monkeypatch.setattr(tigermemory_publish, "run_public_core_instance_smoke", lambda **_kwargs: True)

    rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "out"),
        "--dry-run",
        "--json",
        "--audit-pii",
        "--evidence-report",
        "--validate-checks",
        "--target",
        "public-core",
        "--split-report",
        "--verify-split-smoke",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["release_evidence"]["schema"] == "tigermemory-public-release-evidence-v1"
    assert payload["release_evidence"]["true_split"]["target"] == "public-core"
    assert payload["release_evidence"]["true_split"]["public_core_independent"] is True
    assert payload["release_evidence"]["true_split"]["public_core_independence_reason"] == "verified"


def test_module_dry_run_is_inspection_only_and_keeps_full_snapshot_audit(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "out"),
        "--dry-run",
        "--json",
        "--audit-pii",
        "--evidence-report",
        "--module",
        "public-core",
    ])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["inspection_only"] is True
    assert out["release_gate_scope"] == "inspection-only"
    assert out["release_gate_ok"] is False
    assert "schemas" in out["plan"]["whole_dirs"]
    assert "packages/tigermemory-core/src" in out["plan"]["whole_dirs"]
    assert "packages/tigermemory-dashboard/src" not in out["plan"]["whole_dirs"]
    assert out["plan"]["tool_dirs"] == []
    release = out["release_evidence"]
    assert release["schema"] == "tigermemory-public-release-evidence-v1"
    assert release["inspection_only"] is True
    assert release["selected_module"] == "public-core"
    assert set(release["module_checks"]) == {"public-core"}
    assert release["snapshot_audit"]["sensitive_total"] == 0
    assert release["full_snapshot_audit"]["sensitive_total"] == 0


def test_main_with_evidence_output_generates_markdown(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    out_path = tmp_path / "release-evidence.md"
    rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "out"),
        "--dry-run",
        "--audit-pii",
        "--evidence-output",
        str(out_path),
    ])
    _ = capsys.readouterr().out

    assert rc == 0
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "# TigerMemory Public Release Evidence" in text
    assert "Generated at:" in text
    assert "Snapshot audit" in text
    assert "Repo-scope audit" in text
    assert "public-dashboard" in text


def test_collect_publish_plan_excludes_person_partition(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    person_pages = [p for p in plan["wiki_public_pages"] if "/person/" in p]
    assert person_pages == [], "wiki/person/ must never appear in the plan"


def test_collect_publish_plan_excludes_investment_partition_even_if_public(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert "wiki/investment/portfolio-rules.md" not in plan["wiki_public_pages"]
    assert "wiki/investment/portfolio-rules.md" in plan["excluded_by_private_partition"]


def test_collect_publish_plan_forces_person_excluded_even_if_partition_list_includes_it(tmp_path: pathlib.Path, monkeypatch) -> None:
    _build_fake_repo(tmp_path)
    monkeypatch.setattr(
        tigermemory_publish,
        "WIKI_PUBLISH_PARTITIONS",
        tigermemory_publish.WIKI_PUBLISH_PARTITIONS + ("person",),
    )

    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert "wiki/person/tiger-preferences.md" not in plan["wiki_public_pages"]


def test_collect_publish_plan_forces_private_partition_excluded_even_if_module_declares_it(tmp_path: pathlib.Path, monkeypatch) -> None:
    _build_fake_repo(tmp_path)
    real_inputs = tigermemory_publish._module_publish_inputs

    def fake_inputs(module_id: str | None) -> dict[str, tuple]:
        inputs = real_inputs(module_id)
        values = dict(inputs)
        values["wiki_partitions"] = (*values["wiki_partitions"], "person", "investment")
        return values

    monkeypatch.setattr(tigermemory_publish, "_module_publish_inputs", fake_inputs)

    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert "wiki/person/tiger-preferences.md" not in plan["wiki_public_pages"]
    assert "wiki/investment/portfolio-rules.md" not in plan["wiki_public_pages"]
    assert "wiki/person/tiger-preferences.md" in plan["excluded_by_private_partition"]
    assert "wiki/investment/portfolio-rules.md" in plan["excluded_by_private_partition"]


def test_collect_publish_plan_allows_public_non_person_partition_when_person_is_forced_excluded(tmp_path: pathlib.Path, monkeypatch) -> None:
    _build_fake_repo(tmp_path)
    monkeypatch.setattr(
        tigermemory_publish,
        "WIKI_PUBLISH_PARTITIONS",
        ("person", "systems"),
    )

    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert plan["wiki_public_pages"] == ["wiki/systems/public-page.md"]


def test_execute_plan_copies_files(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    dest = tmp_path / "out"

    plan = tigermemory_publish.collect_publish_plan(repo)
    copied = tigermemory_publish.execute_plan(plan, repo, dest)

    assert copied > 0
    assert (dest / "AGENTS.md").read_text(encoding="utf-8").startswith("# public AGENTS")
    public_index = (dest / "index.md").read_text(encoding="utf-8")
    assert public_index.startswith("# public index")
    assert "TM_MCP_API_KEY" not in public_index
    public_readme = (dest / "README.md").read_text(encoding="utf-8")
    assert public_readme.startswith("# public README")
    assert "git clone https://github.com/tigergiant/tigermemory.git" not in public_readme
    assert "Do Not Install From npm" in public_readme
    assert "npm install -g tigermemory" in public_readme
    assert "different Node/TypeScript Claude Code memory server" in public_readme
    assert "Which Mode Should I Use?" in public_readme
    assert "Start with **local + LLM**" in public_readme
    assert "tm llm status" in public_readme
    assert "tm ask --offline" in public_readme
    assert "Do not install WSL, Docker, Qdrant, Caddy, or OpenMemory just to try the basic" in public_readme
    assert "Do not use `python -m tm`" in public_readme
    assert "tm llm status" in (dest / "AGENTS.md").read_text(encoding="utf-8")
    assert "tm ask --offline" in (dest / "AGENTS.md").read_text(encoding="utf-8")
    assert "tm llm status" in (dest / "index.md").read_text(encoding="utf-8")
    assert "tm ask --offline" in (dest / "index.md").read_text(encoding="utf-8")
    public_pyproject = (dest / "pyproject.toml").read_text(encoding="utf-8")
    assert "AGPL-3.0-or-later" in public_pyproject
    assert "tm = 'tigermemory_cli:main'" in public_pyproject
    assert "Internal; open-source release pending" not in public_pyproject
    assert (dest / "LICENSE").read_text(encoding="utf-8").startswith("AGPL-3.0-or-later")
    assert "Tailwind CSS" in (dest / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    public_canvas = (dest / "wiki" / "operations" / "project-canvas.md").read_text(encoding="utf-8")
    assert "stateDiagram-v2" in public_canvas
    assert "TigerMemory 当前项目拓扑" not in public_canvas
    assert "Expense Tracker" not in public_canvas
    assert "TradingAgents" not in public_canvas
    assert not (dest / "tools" / "tm_dummy.py").exists()
    assert (dest / "tools" / "tm_io.py").is_file()
    assert not (dest / "tools" / "tm_review_ui.py").exists()
    assert not (dest / "tools" / "tm_review_tools.py").exists()
    dashboard_static = dest / "packages" / "tigermemory-dashboard" / "src" / "tigermemory_dashboard" / "static"
    assert (dashboard_static / "start.html").is_file()
    assert (dashboard_static / "review.html").is_file()
    assert (dashboard_static / "health.html").is_file()
    assert (dashboard_static / "quality.html").is_file()
    assert (dashboard_static / "canvas.html").is_file()
    assert (dashboard_static / "dashboard-common.js").is_file()
    assert (dashboard_static / "dashboard-pages.js").is_file()
    assert not (dest / "tools" / "static").exists()
    assert (dest / "schemas" / "PAGE_FORMATS.md").is_file()
    assert (dest / "wiki" / "systems" / "public-page.md").is_file()
    assert not (dest / "wiki" / "systems" / "private-flagged.md").exists()
    assert not (dest / "wiki" / "systems" / "untagged.md").exists()
    assert not (dest / "wiki" / "person").exists()
    assert not (dest / "wiki" / "investment").exists()
    template_name = "." + "env.example"
    real_name = "." + "env"
    assert (dest / "runtime" / "openmemory" / template_name).is_file()
    assert not (dest / "runtime" / "openmemory" / real_name).exists()
    assert (dest / "MODULES.md").is_file()
    manifest = json.loads((dest / "tigermemory-public-modules.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "tigermemory-public-modules-v1"
    assert manifest["inspection_only"] is False
    assert "public-core" in [item["id"] for item in manifest["modules"]]
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert str(tmp_path) not in manifest_text
    assert "wiki/person/tiger-preferences.md" not in manifest_text


def test_execute_plan_dry_run_via_main(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main(["--dest", str(tmp_path / "out"), "--dry-run", "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    summary = json.loads(out)
    assert summary["ok"] is True
    assert summary["dry_run"] is True
    assert summary["files_copied"] == 0
    assert summary["counts"]["wiki_public_pages"] == 1
    assert "public-core" in summary["modules"]["included"]
    assert "private-dogfood" in summary["modules"]["excluded"]
    assert summary["modules"]["count"] == len(tigermemory_publish.PUBLIC_MODULES)
    assert not (tmp_path / "out" / "AGENTS.md").exists()


def test_main_writes_files_when_not_dry_run(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main(["--dest", str(tmp_path / "out"), "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    summary = json.loads(out)
    assert summary["ok"] is True
    assert summary["dry_run"] is False
    assert summary["files_copied"] > 0
    public_agents = tmp_path / "out" / "AGENTS.md"
    assert public_agents.read_text(encoding="utf-8").startswith("# public AGENTS")


def test_detect_repo_root_honors_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_publish._detect_repo_root() == tmp_path.resolve()


def test_audit_publish_plan_flags_public_secret(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    (tmp_path / "wiki" / "systems" / "secret-page.md").write_text(PUBLIC_SECRET_PAGE, encoding="utf-8")

    plan = tigermemory_publish.collect_publish_plan(tmp_path)
    findings = tigermemory_publish.audit_publish_plan(plan, tmp_path)

    assert "wiki/systems/secret-page.md" not in plan["wiki_public_pages"]
    assert plan["excluded_by_pii"] == ["wiki/systems/secret-page.md"]
    assert len(findings) == 1
    assert findings[0]["path"] == "wiki/systems/secret-page.md"
    assert findings[0]["file_path"] == "wiki/systems/secret-page.md"
    assert findings[0]["kind"] == "api_key"
    assert findings[0]["regex_name"] == "SECRET_ASSIGNMENT_RE"
    assert findings[0]["line_number"] > 0
    assert len(findings[0]["context_50chars"]) <= 50
    assert findings[0]["sha256_of_context"]
    assert "abcdefghijklmnopqrstuvwxyz" not in findings[0]["preview"]
    assert "[REDACTED]" in findings[0]["preview"]


def test_main_blocks_write_when_sensitive_public_page_exists(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    (repo / "wiki" / "systems" / "secret-page.md").write_text(PUBLIC_SECRET_PAGE, encoding="utf-8")
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main(["--dest", str(tmp_path / "out"), "--json"])
    summary = json.loads(capsys.readouterr().out)

    assert rc == 3
    assert summary["ok"] is False
    assert summary["sensitive_counts"]["high"] == 1
    assert summary["files_copied"] == 0
    assert not (tmp_path / "out" / "wiki" / "systems" / "secret-page.md").exists()


def test_main_audit_pii_writes_standalone_report_even_when_publish_blocked(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    (repo / "wiki" / "systems" / "secret-page.md").write_text(PUBLIC_SECRET_PAGE, encoding="utf-8")
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    out_dir = tmp_path / "out"
    rc = tigermemory_publish.main(["--dest", str(out_dir), "--audit-pii", "--json"])
    summary = json.loads(capsys.readouterr().out)

    assert rc == 3
    report = out_dir / "pii_findings.json"
    assert summary["audit_pii"] is True
    assert summary["pii_findings_path"] == str(report)
    assert report.is_file()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data[0]["file_path"] == "wiki/systems/secret-page.md"


def test_main_without_audit_pii_does_not_write_standalone_report(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    out_dir = tmp_path / "out"
    rc = tigermemory_publish.main(["--dest", str(out_dir), "--json"])
    summary = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert summary["audit_pii"] is False
    assert summary["pii_findings_path"] is None
    assert not (out_dir / "pii_findings.json").exists()


def test_main_blocks_public_wiki_path_leak(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    (repo / "wiki" / "systems" / "path-leak-page.md").write_text(
        PUBLIC_PATH_LEAK_PAGE.replace("{{PRIVATE_PATH}}", str(repo)),
        encoding="utf-8",
    )
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main(["--dest", str(tmp_path / "out"), "--json"])
    summary = json.loads(capsys.readouterr().out)

    assert rc == 3
    assert summary["ok"] is False
    path_findings = [f for f in summary["pii_findings"] if f["kind"] == "path_leak"]
    assert path_findings
    assert any(f["path"] == "wiki/systems/path-leak-page.md" for f in path_findings)
    assert any(f["severity"] == "high" for f in path_findings)


def test_snapshot_excludes_private_agents_md_but_repo_audit_reports_it(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    (repo / "AGENTS.md").write_text(
        f"""# AGENTS\n\nLocal runbook path: {repo}\n""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    snapshot_rc = tigermemory_publish.main(["--dest", str(tmp_path / "snapshot"), "--dry-run", "--json"])
    snapshot_summary = json.loads(capsys.readouterr().out)
    repo_rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "repo-audit"),
        "--dry-run",
        "--json",
        "--audit-scope",
        "repo",
    ])
    repo_summary = json.loads(capsys.readouterr().out)

    assert snapshot_rc == 0
    assert snapshot_summary["ok"] is True
    assert not any(f["path"] == "AGENTS.md" for f in snapshot_summary["pii_findings"])
    assert repo_rc == 0
    warnings = [f for f in repo_summary["pii_findings"] if f["kind"] == "path_leak"]
    assert warnings
    assert all(f["severity"] == "warning" for f in warnings if f["path"] == "AGENTS.md")


def test_repo_audit_scope_flags_private_non_public_pages(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    (repo / "wiki" / "systems" / "private-path.md").write_text(
        PRIVATE_PATH_LEAK_PAGE.replace("{{PRIVATE_PATH}}", str(repo)),
        encoding="utf-8",
    )
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    snapshot_rc = tigermemory_publish.main(["--dest", str(tmp_path / "snapshot"), "--dry-run", "--json"])
    snapshot_summary = json.loads(capsys.readouterr().out)
    repo_rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "repo-audit"),
        "--dry-run",
        "--json",
        "--audit-scope",
        "repo",
    ])
    repo_summary = json.loads(capsys.readouterr().out)

    assert snapshot_rc == 0
    assert snapshot_summary["audit_scope"] == "snapshot"
    assert not any(f["path"] == "wiki/systems/private-path.md" for f in snapshot_summary["pii_findings"])
    assert repo_rc == 3
    assert repo_summary["audit_scope"] == "repo"
    assert any(
        f["path"] == "wiki/systems/private-path.md" and f["kind"] == "path_leak"
        for f in repo_summary["pii_findings"]
    )


def test_module_entrypoint_dry_run_json_reports_public_field_exclusions(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    env = os.environ.copy()
    env["TIGERMEMORY_ROOT"] = str(repo)
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tigermemory_publish",
            "--dest",
            str(tmp_path / "out"),
            "--dry-run",
            "--json",
        ],
        cwd=str(repo),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["ok"] is True
    assert summary["counts"]["wiki_public_pages"] == 1
    assert summary["excluded_counts"]["excluded_by_public_field"] == 2
    assert summary["excluded_counts"]["excluded_by_private_partition"] == 2
    assert summary["excluded_counts"]["excluded_by_person_partition"] == 1
    assert summary["plan"]["wiki_public_pages"] == ["wiki/systems/public-page.md"]
    assert not (tmp_path / "out" / "AGENTS.md").exists()


def test_main_validate_checks_json_includes_payload_and_non_json_fails_on_missing_checks(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    checks_root = tmp_path / "repo"
    checks_root.mkdir()
    checks = {
        check
        for checks in tigermemory_publish.module_checks().values()
        for check in checks
    }
    for check in checks:
        path = checks_root / check
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    (checks_root / "tests/test_tm_cli.py").unlink()

    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", checks_root)
    rc_non_json = tigermemory_publish.main(["--dry-run", "--validate-checks"])
    out_non_json = capsys.readouterr().out
    assert rc_non_json == 3
    assert "module check validation: FAIL" in out_non_json

    rc_json = tigermemory_publish.main(["--dry-run", "--json", "--validate-checks"])
    payload = json.loads(capsys.readouterr().out)
    assert rc_json == 3
    assert payload["ok"] is False
    assert payload["module_check_validation"]["ok"] is False
    assert payload["module_check_validation"]["missing"]


def test_main_evidence_output_fails_when_module_checks_are_missing(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    checks_root = tmp_path / "repo"
    checks_root.mkdir()
    checks = {
        check
        for checks in tigermemory_publish.module_checks().values()
        for check in checks
    }
    for check in checks:
        path = checks_root / check
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    (checks_root / "tests/test_tm_cli.py").unlink()

    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", checks_root)
    out_path = tmp_path / "release-evidence.md"
    rc = tigermemory_publish.main(["--dry-run", "--evidence-output", str(out_path)])
    _ = capsys.readouterr().out

    assert rc == 3
    text = out_path.read_text(encoding="utf-8")
    assert "Module check validation: FAIL" in text


def test_main_evidence_report_json_fails_when_module_checks_are_missing(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    checks_root = tmp_path / "repo"
    checks_root.mkdir()
    checks = {
        check
        for checks in tigermemory_publish.module_checks().values()
        for check in checks
    }
    for check in checks:
        path = checks_root / check
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    (checks_root / "tests/test_tm_cli.py").unlink()

    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", checks_root)
    rc = tigermemory_publish.main(["--dry-run", "--json", "--evidence-report"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 3
    assert payload["ok"] is False
    assert payload["module_check_validation"]["ok"] is False
    assert payload["release_evidence"]["module_check_validation"]["ok"] is False
