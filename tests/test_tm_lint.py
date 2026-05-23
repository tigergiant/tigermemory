from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_lint  # type: ignore[import-not-found]


def test_orphan_excluded_decision_log():
    assert tm_lint._is_orphan_excluded(
        "wiki/investment/decision-log/000001.SZ-2026-05.md"
    )
    assert tm_lint._is_orphan_excluded(
        "wiki/investment/decision-log/000001.SZ/2026-05-21/final_decision.md"
    )


def test_orphan_excluded_inbox_archive():
    assert tm_lint._is_orphan_excluded(
        "wiki/operations/inbox-archive/2026-04-17.md"
    )


def test_orphan_not_excluded_lessons():
    """Lessons should NOT be auto-excluded; they are expected to be linked."""
    assert not tm_lint._is_orphan_excluded(
        "wiki/self-evolution/lessons/2026-05-07-post-ask-user-stale-preflight.md"
    )


def test_orphan_not_excluded_regular_page():
    assert not tm_lint._is_orphan_excluded("wiki/systems/agent-onboarding.md")
    assert not tm_lint._is_orphan_excluded("wiki/brand/ipfb-brand-guide.md")
    assert not tm_lint._is_orphan_excluded("wiki/operations/dashboard-design.md")


def test_check_E_orphan_pages_drops_auto_generated():
    """Integration: real repo run must not include any auto-generated paths."""
    orphans = tm_lint.check_E_orphan_pages()
    for path in orphans:
        assert not path.startswith("wiki/investment/decision-log/"), path
        assert not path.startswith("wiki/operations/inbox-archive/"), path


def test_whitelist_prefixes_have_trailing_slash():
    """Prefix matching must use trailing slash to avoid matching sibling dirs."""
    for prefix in tm_lint.ORPHAN_AUTO_GENERATED_PREFIXES:
        assert prefix.endswith("/"), prefix
