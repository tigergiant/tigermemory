from __future__ import annotations

import datetime
import json
import pathlib
import sys
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return b'{"ok": true}'


class _FakeOpener:
    def __init__(self):
        self.open_calls = []

    def open(self, request, *, timeout):
        self.open_calls.append((request, timeout))
        return _FakeResponse()


def test_mem0_request_bypasses_default_proxy_opener(monkeypatch):
    fake_opener = _FakeOpener()

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("mem0_request must not use default urlopen")

    monkeypatch.setattr(tm_core, "mem0_key", lambda: "test-key")
    monkeypatch.setattr(tm_core.urllib.request, "urlopen", fail_urlopen)
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_handlers: fake_opener)

    raw = tm_core.mem0_request("http://localhost:8765/api/v1/memories/?user_id=tiger")

    assert raw == '{"ok": true}'
    assert len(fake_opener.open_calls) == 1
    request, timeout = fake_opener.open_calls[0]
    assert request.get_header("Authorization") == "Bearer test-key"
    assert timeout == tm_core.MEM0_READ_TIMEOUT


def test_mem0_search_uses_openmemory_search_query_param(monkeypatch):
    captured = {}

    def fake_request(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return '{"items": []}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    raw = tm_core.mem0_search("Inbox routing", size=7)

    assert raw == '{"items": []}'
    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["search_query"] == ["Inbox routing"]
    assert "query" not in qs
    assert qs["size"] == ["7"]
    assert qs["match_mode"] == ["id_first"]
    assert captured["timeout"] == tm_core.MEM0_READ_TIMEOUT


def test_mem0_search_allows_explicit_substring_match_mode(monkeypatch):
    captured = {}

    def fake_request(url, *, timeout):
        captured["url"] = url
        return '{"items": []}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    tm_core.mem0_search("legacy contiguous text", size=3, match_mode="substring")

    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["match_mode"] == ["substring"]


def test_verify_memory_id_active_hit_with_digest(monkeypatch, tmp_path):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    created = int(datetime.datetime(2026, 5, 16, 3, 23, 5, tzinfo=tm_core.TZ_CN).timestamp())
    text = "2026-05-16 T-X3.5 000001.DAT 242 rows"
    digest = tmp_path / "inbox" / "daily" / "2026-05-16.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(f"memory_ids:\n  - {mem_id}\n", encoding="utf-8")

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: json.dumps({
        "id": mem_id,
        "text": text,
        "created_at": created,
        "state": "active",
        "metadata_": {"source": "codex", "topic": "systems"},
    }))

    def fake_search(query, size=5, match_mode="id_first"):
        assert match_mode == "id_first"
        return json.dumps({"items": [{"id": mem_id}]})

    monkeypatch.setattr(tm_core, "mem0_search", fake_search)

    result = tm_core.verify_memory_id(mem_id, key_terms="T-X3.5 000001.DAT 242 rows")

    assert result["status"] == "exists_active"
    assert result["direct_readback_ok"] is True
    assert result["search_by_id_self_hit"] is True
    assert result["search_by_terms_self_hit"] is True
    assert result["digest_date"] == "2026-05-16"
    assert result["digest_contains"] is True
    assert result["metadata"]["source"] == "codex"
    assert result["text_len"] == len(text)
    assert result["text_sha256_12"]


def test_verify_memory_id_explains_outside_digest_window(monkeypatch, tmp_path):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    created = int(datetime.datetime(2026, 5, 16, 3, 23, 5, tzinfo=tm_core.TZ_CN).timestamp())

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: json.dumps({
        "id": mem_id,
        "text": "T-X3.5",
        "created_at": created,
        "state": "active",
    }))
    monkeypatch.setattr(tm_core, "mem0_search", lambda *_args, **_kwargs: json.dumps({"items": []}))

    result = tm_core.verify_memory_id(mem_id, digest_date="2026-05-15")

    assert result["status"] == "exists_active"
    assert result["digest_contains"] is False
    assert "outside digest window 2026-05-15" in result["digest_inclusion_reason"]


def test_verify_memory_id_distinguishes_not_found_and_unreachable(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 HTTP 404: nope")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "not_found"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 unreachable: refused")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: "{not-json")
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"


def test_mem0_update_content_puts_content_only(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    captured = {}

    def fake_request(url, data=None, *, timeout, method=None):
        captured.update({"url": url, "data": data, "timeout": timeout, "method": method})
        return '{"id": "fd65b298-05bd-493c-83ce-e37d84447362"}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    raw = tm_core.mem0_update_content(mem_id, "replacement content")

    assert raw.startswith('{"id"')
    assert captured["url"].endswith(f"/api/v1/memories/{mem_id}")
    assert captured["timeout"] == tm_core.MEM0_WRITE_TIMEOUT
    assert captured["method"] == "PUT"
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload == {"user_id": "tiger", "memory_content": "replacement content"}
    assert "metadata" not in payload


def test_mem0_update_content_rejects_invalid_uuid_and_empty_content():
    with pytest.raises(ValueError):
        tm_core.mem0_update_content("fd65", "replacement content")
    with pytest.raises(ValueError):
        tm_core.mem0_update_content("fd65b298-05bd-493c-83ce-e37d84447362", "   ")


def test_search_wiki_ranks_alias_match_above_repeated_body_terms(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "investment").mkdir(parents=True)
    (wiki / "systems").mkdir(parents=True)
    (wiki / "investment" / "portfolio-overview.md").write_text(
        """---
owner: codex
status: active
updated: 2026-05-16
aliases: ["portfolio holdings", "family investment"]
title: "投资组合总览"
---
# 投资组合总览

## 摘要

组合入口页。

## 来源

- local
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "investment-ai-hub-upgrade-plan.md").write_text(
        """---
owner: codex
status: active
updated: 2026-05-16
aliases: ["投资 AI 中枢升级计划"]
title: "投资 AI 中枢升级计划"
---
# 投资 AI 中枢升级计划

## 摘要

investment portfolio family holdings investment portfolio family holdings investment portfolio family holdings

## 来源

- local
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("portfolio holdings family investment", size=2, include_sources=False)

    assert results[0]["path"] == "wiki/investment/portfolio-overview.md"


# ---------------------------------------------------------------------------
# git_session_status — phantom detection (added 2026-05-16)
# Background: stat cache drift on cross-fs (WSL 9P, Windows mount, CRLF/LF)
# can make `git status --porcelain=v1` report ' M' entries whose actual content
# matches HEAD byte-for-byte. close_session must not block on these phantoms.
# See lessons/2026-05-16-close-session-stat-cache-phantom.md.
# ---------------------------------------------------------------------------

import types  # noqa: E402  -- kept local to phantom tests for clarity


def _make_fake_run(status_lines: list[str], real_dirty_paths: set[str]):
    """Build a fake `tm_core.run` for phantom tests.

    `status_lines` is what `git status --porcelain=v1` returns (one line per
    entry, including the XY prefix and space). `real_dirty_paths` is the set
    of paths for which `git diff --quiet HEAD -- <path>` should report a real
    diff (return code 1). Any other ' M' / 'M ' / 'MM' path will be treated
    as phantom (return code 0).
    """
    calls: list[list[str]] = []

    def _proc(rc: int, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    def fake_run(cmd: list[str], check: bool = True) -> types.SimpleNamespace:
        calls.append(cmd)
        head = cmd[:2]
        if head == ["git", "update-index"]:
            return _proc(0)
        if head == ["git", "status"]:
            return _proc(0, "\n".join(status_lines) + ("\n" if status_lines else ""))
        if head == ["git", "diff"] and "--quiet" in cmd:
            path = cmd[-1]
            return _proc(1 if path in real_dirty_paths else 0)
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _proc(0)
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return _proc(0, "master\n")
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return _proc(0, "abc1234\n")
        if cmd[:2] == ["git", "rev-parse"]:
            return _proc(0, "origin/master\n")
        if head == ["git", "rev-list"]:
            return _proc(0, "0\t0\n")
        if head == ["git", "config"]:
            return _proc(0, ".githooks\n")
        return _proc(0)

    return fake_run, calls


def _install_hooks(tmp_path: pathlib.Path) -> None:
    githooks = tmp_path / ".githooks"
    githooks.mkdir()
    for hook in ("pre-commit", "commit-msg", "post-commit"):
        (githooks / hook).write_text("#!/bin/sh\n", encoding="utf-8")


def test_git_session_status_excludes_pure_phantom_dirty(monkeypatch, tmp_path):
    """ ' M' entries with no real content diff should be reclassified as phantom."""
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(
        status_lines=[" M .gitignore", " M deploy/openmemory/scripts/install-backup-task.ps1"],
        real_dirty_paths=set(),  # both are phantom (no real diff)
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 0
    assert result["paths"] == []
    assert result["phantom_count"] == 2
    assert sorted(result["phantom_paths"]) == [
        " M .gitignore",
        " M deploy/openmemory/scripts/install-backup-task.ps1",
    ]
    # No dirty-worktree blocker should remain when only phantoms exist.
    assert not any(b.startswith("dirty worktree:") for b in result["blockers"])
    assert result["ok"] is True


def test_git_session_status_keeps_real_dirty_when_mixed_with_phantom(monkeypatch, tmp_path):
    """Mixed phantom + real should yield dirty_count=1, phantom_count=1."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=[" M phantom.md", " M real.md", "?? new.md"],
        real_dirty_paths={"real.md"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 2  # 1 real-modified + 1 untracked
    assert result["paths"] == [" M real.md", "?? new.md"]
    assert result["phantom_count"] == 1
    assert result["phantom_paths"] == [" M phantom.md"]
    assert result["unstaged_count"] == 1
    assert result["untracked_count"] == 1
    assert any(b == "dirty worktree: 2" for b in result["blockers"])
    assert result["ok"] is False


def test_git_session_status_real_only_baseline_unaffected(monkeypatch, tmp_path):
    """Pre-existing behaviour preserved when no entries are phantoms."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=["MM both.md", " M working.md", "M  staged.md"],
        real_dirty_paths={"both.md", "working.md", "staged.md"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 3
    assert result["phantom_count"] == 0
    assert result["phantom_paths"] == []
    # Sanity: staged + unstaged accounting unchanged.
    assert result["staged_count"] == 2  # MM and M_
    assert result["unstaged_count"] == 2  # MM and _M


def test_git_session_status_runs_update_index_refresh_first(monkeypatch, tmp_path):
    """The kernel must invoke `git update-index --refresh` before reading status,
    so git CLI itself can reset stat cache where possible (cheap fast path)."""
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(status_lines=[], real_dirty_paths=set())
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    tm_core.git_session_status()

    git_cmds = [c for c in calls if c[:1] == ["git"]]
    assert git_cmds[0][:3] == ["git", "update-index", "--refresh"]
    # And status comes after refresh.
    status_idx = next(i for i, c in enumerate(git_cmds) if c[:2] == ["git", "status"])
    refresh_idx = next(i for i, c in enumerate(git_cmds) if c[:2] == ["git", "update-index"])
    assert refresh_idx < status_idx


def test_git_session_status_does_not_phantom_check_untracked(monkeypatch, tmp_path):
    """ '??' entries are real (untracked, by definition); they must never be
    submitted to the phantom diff check (which would be both wrong and slow)."""
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(
        status_lines=["?? new1.md", "?? new2.md"],
        real_dirty_paths=set(),
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 2
    assert result["untracked_count"] == 2
    assert result["phantom_count"] == 0
    # Verify no `git diff --quiet` was issued for untracked paths.
    diff_quiet_calls = [c for c in calls if c[:2] == ["git", "diff"] and "--quiet" in c]
    assert diff_quiet_calls == []
