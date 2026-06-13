from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

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
                "Start with **local** unless you already know you need a shared memory service.\n\n"
                "Use `tm ask --offline --query \"hello local memory\"` to return local evidence without AI.\n\n"
                "Do not install WSL, Docker, Qdrant, Caddy, or OpenMemory just to try the basic mode.\n"
                "Do not use `python -m tm`; use the installed `tm` console script.\n",
                encoding="utf-8",
            )
        elif dst == "AGENTS.md":
            path.write_text(
                "# public AGENTS\n\n"
                "tm ask --offline returns local evidence only and must not call online Mem0.\n",
                encoding="utf-8",
            )
        elif dst == "index.md":
            path.write_text("# public index\n\nNo private endpoint here.\n\nUse `tm ask --offline` for local evidence.\n", encoding="utf-8")
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

    # runtime config template (commit-safe).
    openmemory = root / "runtime" / "openmemory"
    openmemory.mkdir(parents=True)
    template_name = "." + "env.example"
    real_name = "." + "env"
    (openmemory / template_name).write_text("KEY=\n", encoding="utf-8")
    # Real runtime config (no .example suffix) must NOT be picked up.
    (openmemory / real_name).write_text("KEY=stub-value\n", encoding="utf-8")


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


def test_collect_publish_plan_default_private(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert plan["top_files"] == sorted([".gitignore"])
    assert plan["whole_dirs"] == [
        "packages/tigermemory-publish/src",
        "schemas",
    ]
    assert plan["mapped_files"] == [
        "packages/tigermemory-publish/src/tigermemory_publish/templates/AGENTS.md=>AGENTS.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/LICENSE=>LICENSE",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/README.md=>README.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/THIRD_PARTY_NOTICES.md=>THIRD_PARTY_NOTICES.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/index.md=>index.md",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/pyproject.toml=>pyproject.toml",
        "packages/tigermemory-publish/src/tigermemory_publish/templates/wiki/operations/project-canvas.md=>wiki/operations/project-canvas.md",
    ]
    assert set(plan["tool_files"]) >= {"tools/tm_io.py", "tools/tm_review_ui.py"}
    assert plan["tool_dirs"] == sorted(["tools/memory_answer", "tools/static"])
    assert plan["wiki_public_pages"] == ["wiki/systems/public-page.md"]
    assert plan["excluded_by_public_field"] == [
        "wiki/systems/private-flagged.md",
        "wiki/systems/untagged.md",
    ]
    assert plan["excluded_by_person_partition"] == ["wiki/person/tiger-preferences.md"]
    expected_template = "runtime/openmemory/." + "env.example"
    assert plan["config_files"] == [expected_template]
    findings = tigermemory_publish.audit_publish_plan(plan, tmp_path)
    assert findings == []


def test_collect_publish_plan_excludes_person_partition(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    person_pages = [p for p in plan["wiki_public_pages"] if "/person/" in p]
    assert person_pages == [], "wiki/person/ must never appear in the plan"


def test_collect_publish_plan_forces_person_excluded_even_if_partition_list_includes_it(tmp_path: pathlib.Path, monkeypatch) -> None:
    _build_fake_repo(tmp_path)
    monkeypatch.setattr(
        tigermemory_publish,
        "WIKI_PUBLISH_PARTITIONS",
        tigermemory_publish.WIKI_PUBLISH_PARTITIONS + ("person",),
    )

    plan = tigermemory_publish.collect_publish_plan(tmp_path)

    assert "wiki/person/tiger-preferences.md" not in plan["wiki_public_pages"]


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
    assert "Start with **local**" in public_readme
    assert "tm ask --offline" in public_readme
    assert "Do not install WSL, Docker, Qdrant, Caddy, or OpenMemory just to try the basic" in public_readme
    assert "Do not use `python -m tm`" in public_readme
    assert "tm ask --offline" in (dest / "AGENTS.md").read_text(encoding="utf-8")
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
    assert (dest / "tools" / "static" / "asset.txt").is_file()
    assert (dest / "tools" / "static" / "start.html").is_file()
    assert (dest / "tools" / "static" / "review.html").is_file()
    assert (dest / "tools" / "static" / "health.html").is_file()
    assert (dest / "tools" / "static" / "quality.html").is_file()
    assert (dest / "tools" / "static" / "canvas.html").is_file()
    assert (dest / "tools" / "static" / "dashboard-common.js").is_file()
    assert (dest / "tools" / "static" / "dashboard-pages.js").is_file()
    assert (dest / "schemas" / "PAGE_FORMATS.md").is_file()
    assert (dest / "wiki" / "systems" / "public-page.md").is_file()
    assert not (dest / "wiki" / "systems" / "private-flagged.md").exists()
    assert not (dest / "wiki" / "systems" / "untagged.md").exists()
    assert not (dest / "wiki" / "person").exists()
    template_name = "." + "env.example"
    real_name = "." + "env"
    assert (dest / "runtime" / "openmemory" / template_name).is_file()
    assert not (dest / "runtime" / "openmemory" / real_name).exists()


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
    assert summary["excluded_counts"]["excluded_by_person_partition"] == 1
    assert summary["plan"]["wiki_public_pages"] == ["wiki/systems/public-page.md"]
    assert not (tmp_path / "out" / "AGENTS.md").exists()
