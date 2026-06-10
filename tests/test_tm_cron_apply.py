from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_cron_apply  # type: ignore[import-not-found]


def test_parse_report_proposals_reads_apply_and_reject_checkboxes():
    report = """
## Proposed Changes

#### proposal-2026-05-21-001

**类型**：prompt-tuning

- [x] apply（apply 命令：`py tools\\tm_io.py cron-apply 2026-05-21 --proposal proposal-2026-05-21-001`）
- [ ] reject

#### proposal-2026-05-21-002

**类型**：wiki-doc

- [ ] apply
- [x] reject（reject 命令：`py tools\\tm_io.py cron-reject 2026-05-21 --proposal proposal-2026-05-21-002 --reason "no"`）
"""

    proposals = tm_cron_apply.parse_report_proposals(report)

    assert proposals["proposal-2026-05-21-001"].proposal_type == "prompt-tuning"
    assert proposals["proposal-2026-05-21-001"].apply_checked is True
    assert proposals["proposal-2026-05-21-001"].reject_checked is False
    assert proposals["proposal-2026-05-21-002"].proposal_type == "wiki-doc"
    assert proposals["proposal-2026-05-21-002"].apply_checked is False
    assert proposals["proposal-2026-05-21-002"].reject_checked is True


def test_validate_patch_blocks_meta_files():
    patch = """diff --git a/AGENTS.md b/AGENTS.md
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -1 +1 @@
-old
+new
"""

    with pytest.raises(tm_cron_apply.CronApplyError, match="meta files"):
        tm_cron_apply.validate_patch("wiki-doc", patch)


def test_validate_patch_allows_route_prompt_patch_only_on_tm_route():
    patch = """diff --git a/tools/tm_route.py b/tools/tm_route.py
--- a/tools/tm_route.py
+++ b/tools/tm_route.py
@@ -1 +1 @@
-ROUTE_PROMPT = "old"
+ROUTE_PROMPT = "new"
"""

    assert tm_cron_apply.validate_patch("prompt-tuning", patch) == ["tools/tm_route.py"]


def test_validate_patch_rejects_prompt_patch_for_other_files():
    patch = """diff --git a/tools/tm_io.py b/tools/tm_io.py
--- a/tools/tm_io.py
+++ b/tools/tm_io.py
@@ -1 +1 @@
-old
+ROUTE_PROMPT = "new"
"""

    with pytest.raises(tm_cron_apply.CronApplyError, match="protected"):
        tm_cron_apply.validate_patch("prompt-tuning", patch)


def test_validate_patch_rejects_wiki_doc_index_and_person_pages():
    index_patch = """diff --git a/wiki/systems/index.md b/wiki/systems/index.md
--- a/wiki/systems/index.md
+++ b/wiki/systems/index.md
@@ -1 +1 @@
-old
+new
"""
    person_patch = """diff --git a/wiki/person/tiger.md b/wiki/person/tiger.md
--- a/wiki/person/tiger.md
+++ b/wiki/person/tiger.md
@@ -1 +1 @@
-old
+new
"""

    with pytest.raises(tm_cron_apply.CronApplyError, match="wiki page"):
        tm_cron_apply.validate_patch("wiki-doc", index_patch)
    with pytest.raises(tm_cron_apply.CronApplyError, match="wiki page"):
        tm_cron_apply.validate_patch("wiki-doc", person_patch)


def test_validate_patch_rejects_large_patch():
    body = "\n".join(f"+line {i}" for i in range(205))
    patch = f"""diff --git a/tests/test_tm_route.py b/tests/test_tm_route.py
--- a/tests/test_tm_route.py
+++ b/tests/test_tm_route.py
@@ -1 +1 @@
{body}
"""

    with pytest.raises(tm_cron_apply.CronApplyError, match="patch too large"):
        tm_cron_apply.validate_patch("test-case", patch)


def test_ensure_clean_worktree_aborts_on_dirty(monkeypatch):
    class Proc:
        returncode = 0
        stdout = " M tools/tm_route.py\n"
        stderr = ""

    monkeypatch.setattr(tm_cron_apply, "_run", lambda *_args, **_kwargs: Proc())

    with pytest.raises(tm_cron_apply.CronApplyError, match="dirty worktree"):
        tm_cron_apply.ensure_clean_worktree()


def test_reject_one_writes_rejected_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path / "events"))
    monkeypatch.setattr(tm_cron_apply, "PROPOSAL_ROOT", tmp_path)

    result = tm_cron_apply.reject_one("2026-05-21", "proposal-2026-05-21-001", "not useful")

    path = tmp_path / "2026-05-21" / "proposal-2026-05-21-001" / "rejected.json"
    assert result["reason"] == "not useful"
    assert path.exists()
    assert "not useful" in path.read_text(encoding="utf-8")
    events = tm_cron_apply.tm_runtime_events.load_events(
        dates=[tm_cron_apply.tm_runtime_events._date_key()],
        event_root=tmp_path / "events",
    )
    assert events[-1]["event_type"] == "cron_proposal_reject"
    assert events[-1]["target_ref"]["proposal_id"] == "proposal-2026-05-21-001"


def test_rollback_rejects_non_cron_apply_commit(monkeypatch):
    monkeypatch.setattr(tm_cron_apply, "ensure_clean_worktree", lambda: None)
    monkeypatch.setattr(tm_cron_apply, "_commit_message", lambda _sha: "[codex] update: normal change")

    with pytest.raises(tm_cron_apply.CronApplyError, match="cron-apply"):
        tm_cron_apply.rollback_commit("abc123")
