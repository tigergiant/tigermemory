from __future__ import annotations

import json
import pathlib

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


def _build_fake_repo(root: pathlib.Path) -> None:
    """Populate `root` with the minimal tigermemory layout the publisher inspects."""
    (root / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (root / "index.md").write_text("# index\n", encoding="utf-8")
    (root / "README.md").write_text("# README\n", encoding="utf-8")
    (root / ".gitignore").write_text("placeholder\n", encoding="utf-8")

    (root / "tools").mkdir()
    (root / "tools" / "tm_dummy.py").write_text("# stub tool\n", encoding="utf-8")
    (root / "tools" / "tm_phone_regex.py").write_text(
        'PHONE_RE = r"(?<!\\\\d)1[3-9]\\\\d{9}(?!\\\\d)"\n'
        "token = RefreshToken.model_validate(raw)\n",
        encoding="utf-8",
    )

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

    assert plan["top_files"] == sorted([".gitignore", "AGENTS.md", "README.md", "index.md"])
    assert plan["whole_dirs"] == ["schemas", "tools"]
    assert plan["wiki_public_pages"] == ["wiki/systems/public-page.md"]
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
    assert (dest / "AGENTS.md").is_file()
    assert (dest / "tools" / "tm_dummy.py").is_file()
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
    assert (tmp_path / "out" / "AGENTS.md").is_file()


def test_detect_repo_root_honors_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_publish._detect_repo_root() == tmp_path.resolve()


def test_audit_publish_plan_flags_public_secret(tmp_path: pathlib.Path) -> None:
    _build_fake_repo(tmp_path)
    (tmp_path / "wiki" / "systems" / "secret-page.md").write_text(PUBLIC_SECRET_PAGE, encoding="utf-8")

    plan = tigermemory_publish.collect_publish_plan(tmp_path)
    findings = tigermemory_publish.audit_publish_plan(plan, tmp_path)

    assert len(findings) == 1
    assert findings[0]["path"] == "wiki/systems/secret-page.md"
    assert findings[0]["kind"] == "api_key"
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
