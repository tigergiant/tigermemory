from __future__ import annotations

import pathlib


def test_pre_push_hook_runs_tigermemory_publish_dry_run_smoke() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    hook = root / ".githooks" / "pre-push"

    text = hook.read_text(encoding="utf-8")

    assert "tigermemory_publish --dry-run --json --module public-publish" in text
    assert ">/dev/null" in text
    assert "git -C \"$ROOT\" worktree add --detach --quiet \"$SMOKE_ROOT\" HEAD" in text
    assert "packages/tigermemory-publish/src" in text


def test_publish_guard_checks_public_core_source_update_smoke() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    workflow = root / ".github" / "workflows" / "publish-guard.yml"

    text = workflow.read_text(encoding="utf-8")

    assert "--verify-source-update-smoke" in text
    assert "source_updateable" in text
    assert "source_update_reason" in text
