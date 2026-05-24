from __future__ import annotations

import io
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_io  # type: ignore[import-not-found]
import tm_route  # type: ignore[import-not-found]


def test_status_pretty_includes_phantom_count(monkeypatch, capsys):
    fake_status = {
        "ok": True,
        "branch": "master",
        "upstream": "origin/master",
        "head": "abc123",
        "ahead": 0,
        "behind": 0,
        "dirty_count": 0,
        "phantom_count": 2,
        "staged_count": 0,
        "unstaged_count": 0,
        "untracked_count": 0,
        "unmerged_count": 0,
        "hooks_path": ".githooks",
        "hooks_installed": True,
        "blockers": [],
        "paths": [],
    }
    monkeypatch.setattr(tm_io.tm_core, "git_session_status", lambda strict_clean=False: fake_status)

    class Args:
        json = False
        strict_clean = False

    tm_io.cmd_status(Args())

    out = capsys.readouterr().out
    assert "dirty_count: 0" in out
    assert "phantom_count: 2" in out


def test_cmd_mem0_update_content_reads_stdin(monkeypatch, capsys):
    captured = {}

    class Args:
        id = "fd65b298-05bd-493c-83ce-e37d84447362"

    def fake_update(memory_id, memory_content):
        captured["memory_id"] = memory_id
        captured["memory_content"] = memory_content
        return '{"ok": true}'

    monkeypatch.setattr(tm_io.sys, "stdin", io.StringIO("replacement content"))
    monkeypatch.setattr(tm_io.tm_core, "mem0_update_content", fake_update)

    tm_io.cmd_mem0_update_content(Args())

    assert captured == {
        "memory_id": "fd65b298-05bd-493c-83ce-e37d84447362",
        "memory_content": "replacement content",
    }
    assert '{"ok": true}' in capsys.readouterr().out


def test_mem0_update_content_cli_has_no_metadata_option(monkeypatch):
    monkeypatch.setattr(tm_io.sys, "argv", [
        "tm_io.py",
        "mem0-update-content",
        "--id",
        "fd65b298-05bd-493c-83ce-e37d84447362",
        "--metadata",
        '{"topic": "systems"}',
    ])

    with pytest.raises(SystemExit) as exc:
        tm_io.main()

    assert exc.value.code == 2


def test_write_inbox_routed_mem0_preserves_requested_topic(monkeypatch, capsys):
    captured = {}

    class Args:
        agent = "codex"
        topic = "systems"
        title = "Title"
        force_inbox = False

    def fake_mem0_write(agent, topic, text, metadata_extra=None):
        captured["agent"] = agent
        captured["topic"] = topic
        captured["text"] = text
        captured["metadata_extra"] = metadata_extra
        return '{"id": "fd65b298-05bd-493c-83ce-e37d84447362"}'

    monkeypatch.setattr(tm_io.sys, "stdin", io.StringIO("Memory Answer production-ready closeout."))
    monkeypatch.setattr(
        tm_route,
        "route_memory",
        lambda *_args, **_kwargs: tm_route.RouteDecision(
            route="mem0",
            score=90,
            topic_inferred="production",
            issues=[],
            reasons="misread production-ready wording",
            is_transient=False,
            is_sensitive=False,
            needs_human_review=False,
        ),
    )
    monkeypatch.setattr(tm_io.tm_core, "mem0_write", fake_mem0_write)

    tm_io.cmd_write_inbox(Args())

    assert captured["topic"] == "systems"
    assert captured["metadata_extra"]["route_requested_topic"] == "systems"
    assert captured["metadata_extra"]["route_topic_inferred"] == "production"
    assert captured["metadata_extra"]["stored_topic"] == "systems"
    assert "fd65b298" in capsys.readouterr().out


def test_write_inbox_routed_inbox_preserves_requested_topic(monkeypatch, capsys):
    captured = {}

    class Args:
        agent = "codex"
        topic = "systems"
        title = "Title"
        force_inbox = False

    def fake_write_inbox_file(agent, topic, title, body, frontmatter_extra=None):
        captured["agent"] = agent
        captured["topic"] = topic
        captured["title"] = title
        captured["body"] = body
        captured["frontmatter_extra"] = frontmatter_extra
        return "inbox/x.md"

    monkeypatch.setattr(tm_io.sys, "stdin", io.StringIO("Memory Answer production-ready closeout."))
    monkeypatch.setattr(
        tm_route,
        "route_memory",
        lambda *_args, **_kwargs: tm_route.RouteDecision(
            route="inbox",
            score=50,
            topic_inferred="production",
            issues=[],
            reasons="needs review",
            is_transient=False,
            is_sensitive=False,
            needs_human_review=True,
        ),
    )
    monkeypatch.setattr(tm_io.tm_core, "write_inbox_file", fake_write_inbox_file)

    tm_io.cmd_write_inbox(Args())

    result = json.loads(capsys.readouterr().out)
    assert result["route"] == "inbox"
    assert result["topic"] == "systems"
    assert result["topic_inferred"] == "production"
    assert result["warnings"] == [
        "topic mismatch: requested_topic=systems, topic_inferred=production, stored_topic=systems"
    ]
    assert captured["topic"] == "systems"
    assert captured["frontmatter_extra"]["route_requested_topic"] == "systems"
    assert captured["frontmatter_extra"]["route_topic_inferred"] == "production"
    assert captured["frontmatter_extra"]["stored_topic"] == "systems"


def test_lint_page_allows_inbox_without_summary_or_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tm_io.tm_core, "REPO_ROOT", tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    page = inbox / "2026-05-24-1200-codex-systems.md"
    page.write_text(
        "---\n"
        "owner: codex\n"
        "status: draft\n"
        "updated: 2026-05-24\n"
        "routed_by: tigermemory\n"
        "---\n\n"
        "# Needs review\n\n"
        "Inbox payload without required wiki sections.\n",
        encoding="utf-8",
    )

    class Args:
        path = "inbox/2026-05-24-1200-codex-systems.md"

    tm_io.cmd_lint_page(Args())

    assert capsys.readouterr().out.strip() == "OK"


def test_lint_repo_emits_four_key_json(monkeypatch, capsys):
    """tm_io.py lint-repo --json must mirror mcp_lint_repo's 4-key shape.

    Regression for 2026-05-24: P2 stage1 spec failed because lint-repo did not
    exist as a CLI subcommand and codex hit AttributeError repeatedly.
    """
    fake_result = {
        "orphan_pages": ["wiki/brand/foo.md"],
        "stale_drafts": ["inbox/2025-01-01-old.md"],
        "missing_sources": ["wiki/systems/bar.md"],
        "partition_mismatches": ["wiki/operations/baz.md (owner: tm_compile_index)"],
    }
    monkeypatch.setattr(tm_io.tm_core, "lint_repo_scan", lambda: fake_result)

    monkeypatch.setattr(tm_io.sys, "argv", ["tm_io.py", "lint-repo", "--json"])
    tm_io.main()

    payload = json.loads(capsys.readouterr().out)
    assert set(payload.keys()) == {
        "orphan_pages",
        "stale_drafts",
        "missing_sources",
        "partition_mismatches",
    }
    assert payload["orphan_pages"] == ["wiki/brand/foo.md"]


def test_retention_audit_subparser_has_source_default_sample(monkeypatch):
    """Regression for 2026-05-24 wrapper schema drift.

    tm_retention_audit.cmd_audit reads args.source / args.input / args.output, but
    tm_io.py's retention-audit sub-parser previously declared none of them, causing
    AttributeError at runtime. tm_io must now expose --source with default 'sample'.
    """
    captured: dict = {}

    def fake_cmd_audit(args):
        captured["source"] = args.source
        captured["input"] = args.input
        captured["output"] = args.output
        captured["max_items"] = args.max_items
        return 0

    import tm_retention_audit  # type: ignore[import-not-found]
    monkeypatch.setattr(tm_retention_audit, "cmd_audit", fake_cmd_audit)

    monkeypatch.setattr(tm_io.sys, "argv", ["tm_io.py", "retention-audit", "--max-items", "10", "--json"])
    tm_io.main()

    assert captured["source"] == "sample"
    assert captured["input"] is None
    assert captured["output"] is None
    assert captured["max_items"] == 10


def test_lint_page_still_rejects_wiki_without_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tm_io.tm_core, "REPO_ROOT", tmp_path)
    page_dir = tmp_path / "wiki" / "systems"
    page_dir.mkdir(parents=True)
    page = page_dir / "missing-summary.md"
    page.write_text(
        "---\n"
        "owner: codex\n"
        "status: draft\n"
        "updated: 2026-05-24\n"
        "---\n\n"
        "# Missing summary\n\n"
        "## 来源\n\n"
        "- test fixture\n",
        encoding="utf-8",
    )

    class Args:
        path = "wiki/systems/missing-summary.md"

    with pytest.raises(SystemExit) as exc:
        tm_io.cmd_lint_page(Args())

    assert exc.value.code == 1
    assert "missing '## 摘要' section" in capsys.readouterr().err
