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


def _use_hybrid_profile(monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", tm_core.TIGERMEMORY_PROFILE_HYBRID)


def test_render_inbox_body_adds_summary_cn_from_agent_chinese_line():
    body = "这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。\nOriginal English details."

    rendered = tm_core.render_inbox_body("codex", "Routed memory 50", body, date="2026-05-21")

    assert "title_cn: 这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。" in rendered
    assert "preview_cn: 这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。" in rendered
    assert "review_cn_source: body_chinese_lines" in rendered
    assert "summary_cn: 这条待审记忆说明：日报审批 UI 需要直接显示中文摘要。" in rendered
    assert "summary_cn_source: title_cn" in rendered


def test_render_inbox_body_marks_missing_chinese_summary():
    rendered = tm_core.render_inbox_body("codex", "Routed memory 50", "English only details.", date="2026-05-21")

    assert "title_cn: 未提供中文摘要" in rendered
    assert "preview_cn: 未提供中文摘要" in rendered
    assert "review_cn_source: missing" in rendered
    assert "summary_cn: 未提供中文摘要" in rendered
    assert "summary_cn_source: missing" in rendered


def test_render_inbox_body_skips_generic_routed_memory_headings():
    body = "\n".join([
        "# Routed memory 35",
        "",
        "# 标题",
        "中转API配置说明：Claude Opus 4.5 保真满血版，客户端与 Claude Code 接入",
        "",
        "# 元数据",
        "- 文档时间：未标注（API配置教程）",
        "",
        "# 摘要",
        "该文档是中转 API 配置说明，主打 Claude Opus 4.5 保真满血版。",
    ])

    rendered = tm_core.render_inbox_body("codex", "Routed memory 35", body, date="2026-05-21")

    assert "title_cn: 中转API配置说明：Claude Opus 4.5 保真满血版" in rendered
    assert "title_cn: 标题" not in rendered
    assert "preview_cn: 该文档是中转 API 配置说明" in rendered


def test_render_inbox_body_overrides_bad_frontmatter_title():
    body = "# 标题\n中转API配置说明：Claude Opus 4.5 保真满血版\n\n# 摘要\n该文档是中转 API 配置说明。"

    rendered = tm_core.render_inbox_body(
        "codex",
        "Routed memory 35",
        body,
        date="2026-05-21",
        frontmatter_extra={"title_cn": "标题", "preview_cn": "标题", "summary_cn": "标题"},
    )

    assert "title_cn: 中转API配置说明：Claude Opus 4.5 保真满血版" in rendered
    assert "title_cn: 标题" not in rendered
    assert "preview_cn: 该文档是中转 API 配置说明。" in rendered


def test_mem0_request_bypasses_default_proxy_opener(monkeypatch):
    _use_hybrid_profile(monkeypatch)
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
    _use_hybrid_profile(monkeypatch)
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
    _use_hybrid_profile(monkeypatch)
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
    _use_hybrid_profile(monkeypatch)
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
    _use_hybrid_profile(monkeypatch)
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
    _use_hybrid_profile(monkeypatch)
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 HTTP 404: nope")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "not_found"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 unreachable: refused")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: "{not-json")
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"


def test_mem0_update_content_puts_content_only(monkeypatch):
    _use_hybrid_profile(monkeypatch)
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


def test_search_wiki_ranks_exact_alias_phrase_above_related_spec(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "api-notes.md").write_text(
        """---
owner: codex
status: active
updated: 2026-06-09
aliases: ["OpenClaw CE API notes plugin"]
title: "OpenClaw CE API notes"
---
# OpenClaw CE API notes

API baseline.
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "plugin-spec.md").write_text(
        """---
owner: codex
status: active
updated: 2026-06-09
aliases: ["OpenClaw CE plugin spec"]
title: "OpenClaw CE plugin spec"
---
# OpenClaw CE plugin spec

OpenClaw CE API notes plugin OpenClaw CE plugin OpenClaw context engine plugin.
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("OpenClaw CE API notes plugin", size=2, include_sources=False)

    assert results[0]["path"] == "wiki/systems/api-notes.md"


def test_search_wiki_ranks_exact_short_title_above_repeated_body_terms(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "operations").mkdir(parents=True)
    (wiki / "systems").mkdir(parents=True)
    (wiki / "operations" / "project-canvas.md").write_text(
        """---
owner: codex
status: active
---

# Project Canvas

The project state map.
""",
        encoding="utf-8",
    )
    (wiki / "systems" / "mermaid-task-canvas-protocol.md").write_text(
        """---
owner: codex
status: active
---

# Mermaid Task Canvas Protocol

Project canvas project canvas project canvas project canvas project canvas.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("Project Canvas", size=2, include_sources=False)

    assert results[0]["path"] == "wiki/operations/project-canvas.md"


def test_search_wiki_explain_includes_lexical_breakdown(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "systems").mkdir(parents=True)
    (wiki / "systems" / "example.md").write_text(
        """---
owner: codex
status: active
updated: 2026-05-20
aliases: ["route memory"]
title: "Route Memory"
---

# Route Memory

Body mentions routing.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    results = tm_core.search_wiki("route memory", size=1, include_sources=False, explain=True)

    breakdown = results[0]["score_breakdown"]
    assert breakdown["lexical_score"] == results[0]["score"]
    assert breakdown["lexical_rank"] == 1
    assert breakdown["alias_match"] is True
    assert breakdown["vector_score"] is None
    assert breakdown["rrf_score"] is None


def test_search_wiki_hybrid_promotes_dominant_lexical_anchor(monkeypatch):
    import types

    lex_hits = [
        {"path": "wiki/systems/exact-a.md", "score": 200.0, "title": "exact a", "snippet": ""},
        {"path": "wiki/systems/exact-b.md", "score": 150.0, "title": "exact b", "snippet": ""},
        {"path": "wiki/systems/filler.md", "score": 20.0, "title": "filler", "snippet": ""},
        {"path": "wiki/systems/semantic-top.md", "score": 10.0, "title": "semantic", "snippet": ""},
    ]
    emb_hits = [
        {"path": "wiki/systems/semantic-top.md", "score": 0.9, "title": "semantic"},
        {"path": "wiki/systems/semantic-two.md", "score": 0.8, "title": "semantic two"},
        {"path": "wiki/systems/semantic-three.md", "score": 0.7, "title": "semantic three"},
    ]

    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: lex_hits)
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: emb_hits))

    results = tm_core.search_wiki_hybrid("exact semantic query", size=3, include_sources=False)

    assert [item["path"] for item in results[:2]] == [
        "wiki/systems/exact-a.md",
        "wiki/systems/exact-b.md",
    ]


def test_search_wiki_hybrid_explain_includes_branch_scores(monkeypatch):
    import types

    lex_hits = [
        {"path": "wiki/systems/exact-a.md", "score": 200.0, "title": "exact a", "snippet": ""},
        {"path": "wiki/systems/semantic-top.md", "score": 10.0, "title": "semantic", "snippet": ""},
    ]
    emb_hits = [
        {"path": "wiki/systems/semantic-top.md", "score": 0.9, "title": "semantic"},
        {"path": "wiki/systems/exact-a.md", "score": 0.5, "title": "exact a"},
    ]

    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: lex_hits)
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: emb_hits))

    results = tm_core.search_wiki_hybrid("exact semantic query", size=2, include_sources=False, explain=True)

    by_path = {item["path"]: item["score_breakdown"] for item in results}
    assert by_path["wiki/systems/exact-a.md"]["lexical_score"] == 200.0
    assert by_path["wiki/systems/exact-a.md"]["vector_score"] == 0.5
    assert by_path["wiki/systems/exact-a.md"]["rrf_score"] == results[0]["score"]
    assert by_path["wiki/systems/exact-a.md"]["degraded"] is False


def test_search_wiki_hybrid_does_not_promote_retrieval_eval_report(monkeypatch):
    import types

    lex_hits = [
        {"path": "wiki/systems/memory-retrieval-eval.md", "score": 600.0, "title": "report", "snippet": ""},
        {"path": "wiki/systems/exact-target.md", "score": 400.0, "title": "exact", "snippet": ""},
    ]
    emb_hits = [
        {"path": "wiki/systems/semantic-top.md", "score": 0.9, "title": "semantic"},
        {"path": "wiki/systems/exact-target.md", "score": 0.8, "title": "exact"},
    ]

    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: lex_hits)
    monkeypatch.setitem(sys.modules, "tm_embed_index", types.SimpleNamespace(search=lambda *_args, **_kwargs: emb_hits))

    results = tm_core.search_wiki_hybrid("exact semantic query", size=3, include_sources=False)

    assert results[0]["path"] == "wiki/systems/exact-target.md"
    assert "wiki/systems/memory-retrieval-eval.md" in [item["path"] for item in results]


def test_search_wiki_lexical_demotes_retrieval_eval_report():
    results = tm_core.search_wiki("OpenMemory CE search limits", size=3, include_sources=False)

    assert results[0]["path"] == "wiki/systems/openmemory-ce-limits.md"


def test_search_wiki_lexical_expands_cjk_domain_terms():
    assert tm_core.search_wiki("记忆库备份策略", size=1, include_sources=False)[0]["path"] == "wiki/operations/mem0-backup.md"
    assert tm_core.search_wiki("虎哥个人资料", size=1, include_sources=False)[0]["path"] == "wiki/person/tiger.md"
    assert tm_core.search_wiki("变基出现冲突怎么办", size=1, include_sources=True)[0]["path"] == "AGENTS.md"


def test_primary_scope_routes_chinese_commit_push_to_lessons():
    assert tm_core.primary_search_scope("提交后忘记推送") == "lessons"


# ---------------------------------------------------------------------------
# git_session_status — phantom detection (added 2026-05-16)
# Background: stat cache drift on cross-fs (WSL 9P, Windows mount, CRLF/LF)
# can make `git status --porcelain=v1` report ' M' entries whose actual content
# matches HEAD byte-for-byte. close_session must not block on these phantoms.
# See lessons/2026-05-16-close-session-stat-cache-phantom.md.
# ---------------------------------------------------------------------------

import types  # noqa: E402  -- kept local to phantom tests for clarity


def _make_fake_run(
    status_lines: list[str],
    real_dirty_paths: set[str],
    eol_only_paths: set[str] | None = None,
):
    """Build a fake `tm_core.run` for phantom tests.

    `status_lines` is what `git status --porcelain=v1` returns (one line per
    entry, including the XY prefix and space).

    `real_dirty_paths` is the set of paths that are TRULY dirty: both passes
    (`git diff --quiet HEAD --` and `git diff --quiet --ignore-cr-at-eol HEAD --`)
    return rc=1 for these.

    `eol_only_paths` is the set of paths whose only diff is CRLF↔LF: pass 2
    (byte-equality) returns rc=1, but pass 3 (--ignore-cr-at-eol) returns rc=0.

    Anything else (` M` / `M ` / `MM` entry not in either set) is a stat-cache
    phantom: pass 2 returns rc=0 immediately.
    """
    eol_only_paths = eol_only_paths or set()
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
            if "--ignore-cr-at-eol" in cmd:
                # Pass 3: only TRULY dirty paths still differ here.
                return _proc(1 if path in real_dirty_paths else 0)
            # Pass 2: bytes differ for both real-dirty and eol-only.
            return _proc(1 if (path in real_dirty_paths or path in eol_only_paths) else 0)
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
    """Mixed phantom + real should yield dirty_count=1+untracked=1, phantom_count=1.

    Self-scope discipline (2026-05-24): dirty_count is reported but is NOT a
    default blocker. The session is OK to proceed even with real dirty paths;
    use strict_clean=True to restore the legacy behaviour.
    """
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
    # New default: foreign dirty is informational only, not a blocker.
    assert not any(b.startswith("dirty worktree:") for b in result["blockers"]), result["blockers"]
    assert result["ok"] is True


def test_git_session_status_strict_clean_blocks_on_real_dirty(monkeypatch, tmp_path):
    """strict_clean=True restores the legacy 'dirty worktree' blocker for sweep
    tasks (archive moves, release verification) that genuinely need a clean tree."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=[" M phantom.md", " M real.md", "?? new.md"],
        real_dirty_paths={"real.md"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status(strict_clean=True)

    # Same accounting as the default-mode test, but blockers now include dirty.
    assert result["dirty_count"] == 2
    assert result["phantom_count"] == 1
    assert any(b == "dirty worktree: 2" for b in result["blockers"]), result["blockers"]
    assert result["ok"] is False


def test_git_session_status_strict_clean_does_not_block_on_pure_phantom(monkeypatch, tmp_path):
    """strict_clean should still respect phantom detection: a pure-phantom
    worktree is clean for legacy callers, not dirty."""
    _install_hooks(tmp_path)
    fake_run, _calls = _make_fake_run(
        status_lines=[" M .gitignore", " M deploy/script.ps1"],
        real_dirty_paths=set(),  # both phantom
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status(strict_clean=True)

    assert result["dirty_count"] == 0
    assert result["phantom_count"] == 2
    assert not any(b.startswith("dirty worktree:") for b in result["blockers"]), result["blockers"]
    assert result["ok"] is True


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


def test_git_session_status_excludes_eol_only_phantom(monkeypatch, tmp_path):
    """CRLF↔LF only diff (Windows editor saves CRLF; WSL git autocrlf=false sees
    diff vs LF index) should be reclassified as phantom via --ignore-cr-at-eol.

    Repro from 2026-05-16 V3.1C incident: D:\\tigermemory\\.gitignore and
    deploy/openmemory/scripts/install-backup-task.ps1 showed 65-line diff but
    every hunk was '-LF +CRLF' on identical content. close_session correctly
    flagged real codex audit_replay/* as dirty but should NOT have flagged the
    EOL-only entries.
    """
    _install_hooks(tmp_path)
    fake_run, calls = _make_fake_run(
        status_lines=[
            " M .gitignore",                                                # EOL-only phantom
            " M deploy/openmemory/scripts/install-backup-task.ps1",         # EOL-only phantom
            " M data/expense_import/reports/2026/audit_replay/alipay.jsonl",  # real dirty (codex)
        ],
        real_dirty_paths={"data/expense_import/reports/2026/audit_replay/alipay.jsonl"},
        eol_only_paths={".gitignore", "deploy/openmemory/scripts/install-backup-task.ps1"},
    )
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "run", fake_run)

    result = tm_core.git_session_status()

    assert result["dirty_count"] == 1, "only the codex real-dirty entry should remain"
    assert result["paths"] == [" M data/expense_import/reports/2026/audit_replay/alipay.jsonl"]
    assert result["phantom_count"] == 2
    assert sorted(result["phantom_paths"]) == [
        " M .gitignore",
        " M deploy/openmemory/scripts/install-backup-task.ps1",
    ]
    # Sanity: the kernel issued both passes for the EOL-only entries; for the
    # real-dirty entry it stops after pass 3 with rc=1.
    diff_quiet_calls = [c for c in calls if c[:2] == ["git", "diff"] and "--quiet" in c]
    pass2_calls = [c for c in diff_quiet_calls if "--ignore-cr-at-eol" not in c]
    pass3_calls = [c for c in diff_quiet_calls if "--ignore-cr-at-eol" in c]
    assert len(pass2_calls) == 3   # one per ' M' entry
    assert len(pass3_calls) == 3   # all three need pass 3 (pass 2 returned rc=1)


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


def test_mem0_user_id_default_when_env_missing(monkeypatch, tmp_path):
    # Runtime config file omits MEM0_USER_ID; helper falls back to "tiger".
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("MEM0_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.mem0_user_id() == "tiger"


def test_mem0_user_id_reads_env_when_set(monkeypatch, tmp_path):
    # Runtime config file sets MEM0_USER_ID=alice; helper returns the override.
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("MEM0_USER_ID=alice\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.mem0_user_id() == "alice"


def test_deepseek_endpoint_default_when_env_missing(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_endpoint() == tm_core.DEFAULT_DEEPSEEK_ENDPOINT


def test_deepseek_endpoint_reads_env_when_set(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_BASE_URL=https://example.test/v1/chat/completions\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_endpoint() == "https://example.test/v1/chat/completions"


def test_deepseek_model_default_when_env_missing(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_API_KEY=stub\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_model() == tm_core.DEFAULT_DEEPSEEK_MODEL


def test_deepseek_model_reads_env_when_set(monkeypatch, tmp_path):
    env_path = tmp_path / "runtime" / "openmemory" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DEEPSEEK_MODEL=custom-chat\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)

    assert tm_core.deepseek_model() == "custom-chat"


_LINT_PAGE_BASE = (
    "---\n"
    "owner: cascade\n"
    "status: active\n"
    "updated: 2026-05-24\n"
    "title: t\n"
    "{extra}"
    "---\n"
    "\n## 摘要\n\nbody\n\n## 来源\n\n- none\n"
)


def test_lint_page_accepts_missing_public_field():
    text = _LINT_PAGE_BASE.format(extra="")
    assert tm_core.lint_page_errors(text) == []


def test_lint_page_accepts_public_true_and_false():
    text_true = _LINT_PAGE_BASE.format(extra="public: true\n")
    text_false = _LINT_PAGE_BASE.format(extra="public: false\n")
    assert tm_core.lint_page_errors(text_true) == []
    assert tm_core.lint_page_errors(text_false) == []


def test_lint_page_rejects_non_bool_public_field():
    text_str = _LINT_PAGE_BASE.format(extra="public: yes\n")
    errors = tm_core.lint_page_errors(text_str)
    assert any("public" in e and "yes" in e for e in errors), errors
