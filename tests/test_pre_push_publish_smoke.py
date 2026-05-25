from __future__ import annotations

import pathlib


def test_pre_push_hook_runs_tigermemory_publish_dry_run_smoke() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    hook = root / ".githooks" / "pre-push"

    text = hook.read_text(encoding="utf-8")

    assert "tigermemory_publish --dry-run --json" in text
    assert ">/dev/null" in text
    assert "packages/tigermemory-publish/src" in text
