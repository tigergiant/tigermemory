"""Unit tests for the commit-msg guard in tm_core.guard_commit.

Covers the 2026-05-24 guard refactor (虎哥 directive):
- COMMIT_AGENTS / DATA_SOURCE_AGENTS split (mem0/tigermemory-ce/dsa-cron
  no longer accepted as commit prefix)
- ACTIONS now includes "fix"
- META_RULE_OWNERS now includes cascade
- Cross-partition atomicity check removed; per-partition ownership still
  enforced for every touched partition (human bypass preserved)
- Regression: log.md compile-only, partition ownership, frontmatter updated
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_commit_msg(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(text, encoding="utf-8")
    return msg


def _stub_guard(
    monkeypatch,
    *,
    staged_paths: list[str],
    today: str = "2026-05-24",
    staged_blob_fn=None,
) -> None:
    """Stub the guard's external dependencies: staged_files, staged_blob, now."""
    monkeypatch.setattr(tm_core, "staged_files", lambda: [("M", p) for p in staged_paths])
    if staged_blob_fn is None:
        # Default: no frontmatter, so the §8 updated check is skipped.
        monkeypatch.setattr(tm_core, "staged_blob", lambda _p: None)
    else:
        monkeypatch.setattr(tm_core, "staged_blob", staged_blob_fn)
    monkeypatch.setattr(tm_core, "now", lambda fmt: today if "%Y-%m-%d" in fmt else "")


# ---------------------------------------------------------------------------
# Enum / constants
# ---------------------------------------------------------------------------

def test_commit_agents_excludes_data_source_identities() -> None:
    """Regular agents may author commits; data-source identities may not."""
    assert "cascade" in tm_core.COMMIT_AGENTS
    assert "claude-code" in tm_core.COMMIT_AGENTS
    assert "codex" in tm_core.COMMIT_AGENTS
    assert "linter" in tm_core.COMMIT_AGENTS
    assert "human" in tm_core.COMMIT_AGENTS

    for special in ("mem0", "tigermemory-ce", "dsa-cron"):
        assert special not in tm_core.COMMIT_AGENTS, special
        assert special in tm_core.DATA_SOURCE_AGENTS, special


def test_agents_union_matches_both_sets() -> None:
    """AGENTS is the union of COMMIT_AGENTS and DATA_SOURCE_AGENTS, no overlap."""
    assert tm_core.AGENTS == tm_core.COMMIT_AGENTS | tm_core.DATA_SOURCE_AGENTS
    assert tm_core.COMMIT_AGENTS.isdisjoint(tm_core.DATA_SOURCE_AGENTS)


def test_actions_includes_fix() -> None:
    """ACTIONS gained 'fix' on 2026-05-24 so bug-fix commits don't need 'update'."""
    assert "fix" in tm_core.ACTIONS
    assert tm_core.ACTIONS == {
        "create", "update", "archive", "lint", "ingest", "compile", "fix",
    }


def test_meta_rule_owners_includes_cascade() -> None:
    """cascade joined META_RULE_OWNERS on 2026-05-24 (虎哥 directive)."""
    assert tm_core.META_RULE_OWNERS == {"claude-code", "cascade", "human"}
    # Codex / kimi / gemini still cannot author meta-rule changes.
    assert "codex" not in tm_core.META_RULE_OWNERS
    assert "kimi" not in tm_core.META_RULE_OWNERS


# ---------------------------------------------------------------------------
# guard_commit — commit prefix
# ---------------------------------------------------------------------------

def test_guard_rejects_data_source_identity_as_commit_prefix(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["wiki/systems/note.md"])
    msg = _make_commit_msg(tmp_path, "[mem0] update: routed event\n")
    errors = tm_core.guard_commit(msg)
    assert any("not in commit-author set" in e and "mem0" in e for e in errors), errors


def test_guard_accepts_cascade_as_commit_prefix(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["tools/tm_core.py"])
    msg = _make_commit_msg(tmp_path, "[cascade] update: refactor helper\n")
    errors = tm_core.guard_commit(msg)
    assert not any("not in commit-author set" in e for e in errors), errors


def test_guard_accepts_fix_action(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["tools/tm_core.py"])
    msg = _make_commit_msg(tmp_path, "[cascade] fix: pid_alive Windows handling\n")
    errors = tm_core.guard_commit(msg)
    assert not any("not in allowed set" in e for e in errors), errors


def test_guard_rejects_unknown_action(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["tools/tm_core.py"])
    msg = _make_commit_msg(tmp_path, "[cascade] yolo: random action\n")
    errors = tm_core.guard_commit(msg)
    assert any("not in allowed set" in e and "yolo" in e for e in errors), errors


# ---------------------------------------------------------------------------
# guard_commit — meta-rule ownership
# ---------------------------------------------------------------------------

def test_guard_accepts_cascade_writing_agents_md(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["AGENTS.md"])
    msg = _make_commit_msg(tmp_path, "[cascade] update: amend meta-rule owners\n")
    errors = tm_core.guard_commit(msg)
    assert not any("meta-rule file" in e for e in errors), errors


def test_guard_accepts_cascade_writing_schemas(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["schemas/PAGE_FORMATS.md"])
    msg = _make_commit_msg(tmp_path, "[cascade] update: schema clarification\n")
    errors = tm_core.guard_commit(msg)
    assert not any("meta-rule file" in e for e in errors), errors


def test_guard_rejects_codex_writing_agents_md(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["AGENTS.md"])
    msg = _make_commit_msg(tmp_path, "[codex] update: attempt meta-rule edit\n")
    errors = tm_core.guard_commit(msg)
    assert any("meta-rule file" in e and "codex" in e for e in errors), errors


# ---------------------------------------------------------------------------
# guard_commit — cross-partition + ownership
# ---------------------------------------------------------------------------

def test_guard_allows_cross_partition_commit_when_agent_owns_all(tmp_path, monkeypatch) -> None:
    """Cross-partition atomicity check was removed; cascade owns both partitions."""
    _stub_guard(monkeypatch, staged_paths=[
        "wiki/investment/page-a.md",
        "wiki/operations/page-b.md",
    ])
    msg = _make_commit_msg(tmp_path, "[cascade] update: cross-partition policy edit\n")
    errors = tm_core.guard_commit(msg)
    # The deleted check should no longer appear.
    assert not any("multiple wiki partitions" in e for e in errors), errors
    # And cascade owns both → no ownership error either.
    assert not any("not an owner" in e for e in errors), errors


def test_guard_still_enforces_per_partition_ownership_on_cross_partition_commit(
    tmp_path, monkeypatch,
) -> None:
    """A cross-partition commit must still pass ownership for EVERY partition."""
    _stub_guard(monkeypatch, staged_paths=[
        "wiki/systems/page-a.md",     # cascade owns
        "wiki/person/page-b.md",      # claude-code only
    ])
    msg = _make_commit_msg(tmp_path, "[cascade] update: includes sensitive person edit\n")
    errors = tm_core.guard_commit(msg)
    assert any("not an owner of wiki/person/" in e for e in errors), errors
    # systems/ ownership is fine for cascade.
    assert not any("not an owner of wiki/systems/" in e for e in errors), errors


def test_guard_human_bypasses_partition_ownership(tmp_path, monkeypatch) -> None:
    """human remains the manual-edit escape hatch with no ownership gate."""
    _stub_guard(monkeypatch, staged_paths=["wiki/person/page.md"])
    msg = _make_commit_msg(tmp_path, "[human] update: hand-edited person page\n")
    errors = tm_core.guard_commit(msg)
    assert not any("not an owner" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Regression — pre-existing rules must keep working
# ---------------------------------------------------------------------------

def test_guard_still_enforces_log_md_compile_only(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["log.md"])
    msg = _make_commit_msg(tmp_path, "[cascade] update: manual log append\n")
    errors = tm_core.guard_commit(msg)
    assert any("log.md is append-only" in e for e in errors), errors


def test_guard_accepts_claude_code_log_compile(tmp_path, monkeypatch) -> None:
    _stub_guard(monkeypatch, staged_paths=["log.md"])
    msg = _make_commit_msg(tmp_path, "[claude-code] compile: weekly log refresh\n")
    errors = tm_core.guard_commit(msg)
    assert not any("log.md" in e for e in errors), errors


def test_guard_still_enforces_frontmatter_updated_today(tmp_path, monkeypatch) -> None:
    stale_blob = "---\nowner: cascade\nupdated: 2026-05-20\n---\nbody\n"
    _stub_guard(
        monkeypatch,
        staged_paths=["wiki/systems/page.md"],
        staged_blob_fn=lambda _p: stale_blob,
    )
    msg = _make_commit_msg(tmp_path, "[cascade] update: stale frontmatter\n")
    errors = tm_core.guard_commit(msg)
    assert any("frontmatter updated=" in e and "2026-05-20" in e for e in errors), errors


def test_guard_accepts_frontmatter_updated_today(tmp_path, monkeypatch) -> None:
    fresh_blob = "---\nowner: cascade\nupdated: 2026-05-24\n---\nbody\n"
    _stub_guard(
        monkeypatch,
        staged_paths=["wiki/systems/page.md"],
        staged_blob_fn=lambda _p: fresh_blob,
    )
    msg = _make_commit_msg(tmp_path, "[cascade] update: fresh frontmatter\n")
    errors = tm_core.guard_commit(msg)
    assert not any("frontmatter updated=" in e for e in errors), errors
