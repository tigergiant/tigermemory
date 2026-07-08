from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3
import subprocess
import sys
import urllib.error

import pytest

_PKG_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

import tigermemory_core as tm_core
from tigermemory_core import runtime_events as tm_runtime_events


class _JsonResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _SequenceOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _JsonResponse(outcome)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test", code, "boom", hdrs=None, fp=None)


def _force_hybrid_profile(monkeypatch):
    monkeypatch.delenv("TIGERMEMORY_PROFILE", raising=False)
    monkeypatch.setattr(tm_core, "_runtime_profile_file_value", lambda: None)


def test_mem0_write_delete_update_build_expected_requests(monkeypatch):
    calls = []
    _force_hybrid_profile(monkeypatch)
    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_user_id", lambda: "unit-user")

    def fake_request(url, data=None, *, timeout, method=None):
        payload = json.loads(data.decode("utf-8")) if data else None
        calls.append({"url": url, "payload": payload, "timeout": timeout, "method": method})
        return '{"ok": true}'

    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    tm_core.mem0_write(
        "codex",
        "systems",
        "2026-05-24 package extraction test memory",
        metadata_extra={"extra": "yes"},
        route_decision="mem0",
        route_score=88,
        route_topic_inferred="systems",
        infer=True,
    )
    tm_core.mem0_delete(["fd65b298-05bd-493c-83ce-e37d84447362"])
    tm_core.mem0_update_content("fd65b298-05bd-493c-83ce-e37d84447362", "replacement")

    write_payload = calls[0]["payload"]
    assert write_payload["user_id"] == "unit-user"
    assert write_payload["metadata"]["source"] == "codex"
    assert write_payload["metadata"]["route_score"] == 88
    assert write_payload["metadata"]["extra"] == "yes"
    assert write_payload["infer"] is True
    assert calls[1]["method"] == "DELETE"
    assert calls[1]["payload"]["memory_ids"] == ["fd65b298-05bd-493c-83ce-e37d84447362"]
    assert calls[2]["method"] == "PUT"
    assert calls[2]["payload"]["memory_content"] == "replacement"


def test_mem0_wrappers_validate_bad_input():
    with pytest.raises(ValueError, match="invalid agent"):
        tm_core.mem0_write("unknown", "systems", "text")
    with pytest.raises(ValueError, match="text required"):
        tm_core.mem0_write("codex", "systems", "  ")
    with pytest.raises(ValueError, match="memory_ids required"):
        tm_core.mem0_delete([])
    with pytest.raises(ValueError, match="invalid memory UUID"):
        tm_core.mem0_delete(["bad-id"])
    with pytest.raises(ValueError, match="memory_id must be a full UUID"):
        tm_core.mem0_get("bad-id")
    with pytest.raises(ValueError, match="memory_content required"):
        tm_core.mem0_update_content("fd65b298-05bd-493c-83ce-e37d84447362", "")


def test_guard_commit_reports_sources_meta_inbox_and_updated_errors(monkeypatch, tmp_path):
    today = tm_core.now("%Y-%m-%d")
    (tmp_path / "sources" / "bad").mkdir(parents=True)
    (tmp_path / "sources" / "bad" / "doc.md").write_text("---\nsource_url: \n---\nbody", encoding="utf-8")
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("[codex] update: test guard\n", encoding="utf-8")
    staged = [
        ("A", "sources/bad/doc.md"),
        ("M", "AGENTS.md"),
        ("M", "log.md"),
        ("M", "wiki/person/profile.md"),
        ("M", "wiki/operations/lint-dashboard.md"),
        ("A", "inbox/not-a-valid-name.md"),
        ("A", "inbox/2026-05-24-1200-codex-badtopic.md"),
        ("A", "wiki/systems/page.md"),
    ]

    def fake_blob(path):
        if path == "wiki/systems/page.md":
            return f"---\nowner: codex\nstatus: active\nupdated: 2000-01-01\n---\n"
        return ""

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "staged_files", lambda: staged)
    monkeypatch.setattr(tm_core, "staged_blob", fake_blob)

    errors = tm_core.guard_commit(msg)

    assert any("frontmatter missing or empty" in e for e in errors)
    assert any("meta-rule file" in e for e in errors)
    assert any("log.md is append-only" in e for e in errors)
    assert any("wiki/person" in e for e in errors)
    assert any("lint-dashboard.md" in e for e in errors)
    assert any("violates inbox" in e for e in errors)
    assert any("invalid topic" in e for e in errors)
    assert any(f"!= today '{today}'" in e for e in errors)


def test_guard_commit_accepts_human_sources_and_linter_dashboard(monkeypatch, tmp_path):
    human_msg = tmp_path / "HUMAN_MSG"
    human_msg.write_text("[human] update: import source\n", encoding="utf-8")
    linter_msg = tmp_path / "LINTER_MSG"
    linter_msg.write_text("[linter] lint: refresh dashboard\n", encoding="utf-8")

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "staged_blob", lambda _path: "")
    monkeypatch.setattr(tm_core, "staged_files", lambda: [("A", "sources/raw/doc.md")])
    assert tm_core.guard_commit(human_msg) == []

    monkeypatch.setattr(tm_core, "staged_files", lambda: [("M", "wiki/operations/lint-dashboard.md")])
    assert tm_core.guard_commit(linter_msg) == []


def test_lint_repo_scan_finds_all_four_buckets(monkeypatch, tmp_path):
    wiki = tmp_path / "wiki"
    systems = wiki / "systems"
    brand = wiki / "brand"
    systems.mkdir(parents=True)
    brand.mkdir(parents=True)
    (systems / "index.md").write_text("listed-page", encoding="utf-8")
    (systems / "orphan.md").write_text("---\nowner: codex\nstatus: active\nupdated: 2026-05-24\n---\n\nbody", encoding="utf-8")
    (systems / "listed-page.md").write_text("---\nowner: unknown\nstatus: active\nupdated: 2026-05-24\n---\n\n## \u6765\u6e90\nok", encoding="utf-8")
    (brand / "index.md").write_text("", encoding="utf-8")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    stale = inbox / "2026-05-01-1200-codex-systems.md"
    stale.write_text("old", encoding="utf-8")
    old_time = (datetime.datetime.now().timestamp() - 10 * 24 * 3600)
    import os

    os.utime(stale, (old_time, old_time))

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "PARTITION_OWNERS", {"systems": {"codex"}, "brand": {"codex"}})

    result = tm_core.lint_repo_scan()

    assert "wiki/systems/orphan.md" in result["orphan_pages"]
    assert "inbox/2026-05-01-1200-codex-systems.md" in result["stale_drafts"]
    assert "wiki/systems/orphan.md" in result["missing_sources"]
    assert result["partition_mismatches"] == ["wiki/systems/listed-page.md (owner: unknown)"]


def test_search_wiki_hybrid_fuses_lexical_and_embedding(monkeypatch, tmp_path):
    page = tmp_path / "wiki" / "systems" / "alpha.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Alpha\n\nhybrid unique needle", encoding="utf-8")
    emb_only = tmp_path / "wiki" / "systems" / "beta.md"
    emb_only.write_text("# Beta\n\nvector-only context body", encoding="utf-8")

    class _EmbedIndex:
        @staticmethod
        def search(_query, scope="wiki", k=12):
            return [
                {"path": "wiki/systems/beta.md", "title": "Beta", "score": 0.95},
                {"path": "wiki/systems/alpha.md", "title": "Alpha", "score": 0.75},
            ]

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(sys.modules, "tm_embed_index", _EmbedIndex)

    hits = tm_core.search_wiki_hybrid("hybrid needle", size=2, explain=True)

    assert {hit["path"] for hit in hits} == {"wiki/systems/alpha.md", "wiki/systems/beta.md"}
    assert all("score_breakdown" in hit for hit in hits)
    assert any(hit["score_breakdown"]["vector_rank"] == 1 for hit in hits)


def test_ipfb_context_reads_sources_history_and_filters_mem0(monkeypatch, tmp_path):
    files = {
        "skill": "wiki/brand/skill.md",
        "guide": "wiki/brand/guide.md",
        "brand_guide": "wiki/brand/brand.md",
        "design_plan": "wiki/brand/design.md",
        "product_plan": "wiki/brand/product.md",
        "history": "sources/documents/brand/history.txt",
    }
    for rel in files.values():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"IPFB sample line for {rel}\nsecond line\nthird line", encoding="utf-8")

    search_payload = {
        "items": [
            {"id": "1", "content": "useful recent feedback", "metadata": {"topic": "brand", "source": "codex"}},
            {"id": "2", "content": "ignored topic", "metadata": {"topic": "systems"}},
            {"id": "3", "content": "\u5df2\u56fa\u5316\u4e8e guide", "metadata": {"topic": "brand"}},
        ]
    }

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "IPFB_COPYWRITING_FILES", files)
    monkeypatch.setattr(tm_core, "mem0_search", lambda *_args, **_kwargs: json.dumps(search_payload))

    ctx = tm_core.ipfb_copywriting_context(task_type="series_campaign", history_query="IPFB", history_limit=2, excerpt_chars=80)

    assert ctx["ok"] is True
    assert ctx["sources"]["guide"]["exists"] is True
    assert len(ctx["history_examples"]) >= 1
    assert ctx["recent_feedback"] == [
        {"id": "1", "text": "useful recent feedback", "topic": "brand", "created_at": "", "source": "codex"}
    ]


def test_call_deepseek_json_success_malformed_and_missing_key(monkeypatch):
    success_payload = {
        "choices": [{"message": {"content": json.dumps({"ok": True})}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    opener = _SequenceOpener([success_payload, {"bad": "shape"}])
    monkeypatch.setattr(tm_core, "deepseek_endpoint", lambda: "https://api.deepseek.test/chat/completions")
    monkeypatch.setattr(tm_core, "deepseek_model", lambda: "deepseek-test")
    monkeypatch.setattr(tm_core, "_env_value", lambda key: "secret" if key == "DEEPSEEK_API_KEY" else "")
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_args: opener)

    ok, parsed = tm_core._call_deepseek_json("system json", "user", purpose="unit")
    assert ok is True and parsed == {"ok": True}
    ok, reason = tm_core._call_deepseek_json("system json", "user", purpose="unit")
    assert ok is False and "malformed DeepSeek response" in reason

    def missing(_key):
        raise RuntimeError("missing")

    monkeypatch.setattr(tm_core, "_env_value", missing)
    ok, reason = tm_core._call_deepseek_json("system", "user")
    assert ok is False and "no DEEPSEEK_API_KEY" in reason


def test_call_minimax_json_retries_and_strips_artifacts(monkeypatch):
    payload = {
        "choices": [{"message": {"content": '<think>hidden</think>```json\n{"patches": []}\n```'}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    opener = _SequenceOpener([_http_error(529), payload])
    monkeypatch.setattr(tm_core, "_env_value", lambda key: {
        "MINIMAX_API_KEY": "secret",
        "MINIMAX_BASE_URL": "https://api.minimax.test",
        "MINIMAX_MODEL": "minimax-test",
    }[key])
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_args: opener)
    monkeypatch.setattr(tm_core.time, "sleep", lambda _seconds: None)

    ok, parsed = tm_core._call_minimax_json("system json", "user", purpose="unit")

    assert ok is True
    assert parsed == {"patches": []}
    assert len(opener.requests) == 2


def test_suggest_wiki_patches_validates_paths_and_falls_back(monkeypatch):
    catalog = [{"page": "wiki/systems/target.md", "summary": "target page"}]
    parsed = {
        "patches": [
            {"page": "wiki/systems/target.md", "type": "append", "section": "", "content": "new fact", "rationale": "reason"},
            {"page": "wiki/systems/missing.md", "type": "append", "section": "", "content": "bad", "rationale": ""},
            {"page": "wiki/systems/target.md", "type": "update_section", "section": "", "content": "bad", "rationale": ""},
        ]
    }
    monkeypatch.setattr(tm_core, "_call_minimax_json", lambda *_args, **_kwargs: (False, "MiniMax HTTP 529"))
    monkeypatch.setattr(tm_core, "_call_deepseek_json", lambda *_args, **_kwargs: (True, parsed))

    result = tm_core.suggest_wiki_patches("This long enough summary should update a known page.", catalog, llm="auto")

    assert result == [
        {"page": "wiki/systems/target.md", "type": "append", "section": "", "content": "new fact", "rationale": "reason"}
    ]
    assert tm_core.suggest_wiki_patches("short", catalog) == []
    assert tm_core.suggest_wiki_patches("This is long enough.", [], llm="unknown") == []


def test_refine_and_save_wiki_patches(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_core, "_call_deepseek_json", lambda *_args, **_kwargs: (True, {
        "facts": [
            {"topic": "systems", "text": "A" * 40},
            {"topic": "cross", "text": "B" * 40},
            {"topic": "systems", "text": "x"},
        ]
    }))
    facts = tm_core.refine_from_summary("This summary is definitely long enough to call the JSON extractor.", max_facts=2)
    assert facts == [{"topic": "systems", "text": "A" * 40}]

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "now", lambda fmt: "2026-05-24" if "%Y-%m-%d" in fmt else "2026-05-24-1200")
    rel = tm_core.save_wiki_patches_to_inbox([
        {"page": "wiki/systems/target.md", "type": "append", "section": "", "content": "patch body", "rationale": "because"}
    ], "codex", summary_excerpt="summary")

    written = (tmp_path / rel).read_text(encoding="utf-8")
    assert rel.startswith("inbox/")
    assert "type: wiki-patches" in written
    assert "patch body" in written


def test_git_commit_push_success_with_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path / "events"))
    calls = []
    push_count = [0]

    def fake_run(cmd, check=True, timeout=None):
        calls.append(cmd)
        if cmd[0] == "git" and "pull" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 0, "committed", "")
        if cmd[0] == "git" and "push" in cmd:
            push_count[0] += 1
            if push_count[0] == 1:
                return subprocess.CompletedProcess(cmd, 1, "", "rejected")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "abc123\n", "")
        if cmd[:2] == ["git", "rebase"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(tm_core, "run", fake_run)

    assert tm_core.git_commit_push(["inbox/x.md"], "[codex] create: x") == "abc123"
    assert any("pull" in c for c in calls)
    assert sum(1 for c in calls if "push" in c) == 2
    events = tm_runtime_events.load_events(dates=[tm_runtime_events._date_key()], event_root=tmp_path / "events")
    assert events[-1]["event_type"] == "git_commit_push"
    assert events[-1]["outcome"] == "success"
    assert events[-1]["target_ref"]["commit_sha"] == "abc123"


def test_git_commit_push_push_failure_returns_sha_not_raise(tmp_path, monkeypatch):
    """2026-07-04: push failure after retry must NOT raise. Commit is already
    in local git history, so memory is persisted. Raising would unlink the
    inbox file and lose the memory. Push self-heals on next operation.
    """
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(tmp_path / "events"))

    def fake_run(cmd, check=True, timeout=None):
        if cmd[0] == "git" and "pull" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 0, "committed", "")
        if cmd[0] == "git" and "push" in cmd:
            # Both push attempts fail (e.g. remote down, or rebase can't
            # resolve untracked-file block).
            return subprocess.CompletedProcess(cmd, 1, "", "rejected")
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "abc123\n", "")
        if cmd[:2] == ["git", "rebase"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(tm_core, "run", fake_run)

    # Must NOT raise; returns sha because commit succeeded.
    sha = tm_core.git_commit_push(["inbox/x.md"], "[codex] create: x")
    assert sha == "abc123"

    events = tm_runtime_events.load_events(dates=[tm_runtime_events._date_key()], event_root=tmp_path / "events")
    assert events[-1]["outcome"] == "commit_ok_push_failed"
    assert events[-1]["ok"] is False
    assert events[-1]["severity"] == "warn"
    assert events[-1]["target_ref"]["commit_sha"] == "abc123"


def test_git_commit_push_unstages_on_commit_failure(monkeypatch):
    calls = []

    def fake_run(cmd, check=True, timeout=None):
        calls.append(cmd)
        if (cmd[0] == "git" and "pull" in cmd) or cmd[:2] in (["git", "add"], ["git", "restore"]):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 1, "", "guard rejected")
        raise AssertionError(cmd)

    monkeypatch.setattr(tm_core, "run", fake_run)

    with pytest.raises(tm_core.GitError, match="git commit failed"):
        tm_core.git_commit_push(["inbox/x.md"], "[codex] create: x")
    assert ["git", "restore", "--staged", "--", "inbox/x.md"] in calls


def test_git_remote_and_staged_helpers(monkeypatch):
    def fake_run(cmd, check=True, timeout=None):
        if cmd[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(cmd, 0, "git@github.com:tiger/tigermemory.git\n", "")
        if cmd[:4] == ["git", "diff", "--cached", "--name-status"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                "M\x00wiki/systems/a.md\x00R100\x00old.md\x00new.md\x00",
                "",
            )
        raise AssertionError(cmd)

    class _Proc:
        returncode = 0
        stdout = b"staged text"

    monkeypatch.setattr(tm_core, "run", fake_run)
    monkeypatch.setattr(tm_core.subprocess, "run", lambda *_args, **_kwargs: _Proc())

    assert tm_core.git_remote_blob_url("wiki/systems/a.md") == "https://github.com/tiger/tigermemory/blob/master/wiki/systems/a.md"
    assert tm_core.staged_files() == [("M", "wiki/systems/a.md"), ("R", "new.md")]
    assert tm_core.staged_blob("wiki/systems/a.md") == "staged text"


def test_env_and_validate_helpers(monkeypatch):
    _force_hybrid_profile(monkeypatch)
    values = {
        "MEM0_API_KEY": "key",
        "TM_MCP_API_KEY": "mcp",
        "MEM0_URL": "http://localhost:9999",
    }
    monkeypatch.setattr(tm_core, "_env_value", lambda key: values[key])
    assert tm_core.mem0_key() == "key"
    assert tm_core.mcp_api_key() == "mcp"
    assert tm_core.mem0_base() == "http://localhost:9999"

    for func, arg in [
        (tm_core.validate_topic, "bad"),
        (tm_core.validate_partition, "bad"),
        (tm_core.validate_action, "bad"),
        (tm_core.validate_title, ""),
        (tm_core.validate_slug, "Bad Slug!"),
    ]:
        with pytest.raises(ValueError):
            func(arg)
    assert tm_core.inbox_rel_path("codex", "systems", stamp="2026-05-24-1200") == "inbox/2026-05-24-1200-codex-systems.md"


def test_tigermemory_profile_defaults_to_hybrid_when_env_missing(monkeypatch):
    _force_hybrid_profile(monkeypatch)

    def missing_env(key):
        raise RuntimeError(f"{key} missing")

    monkeypatch.setattr(tm_core, "_env_value", missing_env)

    assert tm_core.tigermemory_profile() == tm_core.TIGERMEMORY_PROFILE_HYBRID


def test_tigermemory_profile_accepts_local_value_case_insensitive(monkeypatch):
    _force_hybrid_profile(monkeypatch)
    monkeypatch.setattr(tm_core, "_env_value", lambda key: " LOCAL " if key == "TIGERMEMORY_PROFILE" else "")

    assert tm_core.tigermemory_profile() == tm_core.TIGERMEMORY_PROFILE_LOCAL


def test_tigermemory_profile_prefers_process_env_over_runtime_file(monkeypatch):
    monkeypatch.setenv("TIGERMEMORY_PROFILE", "local")
    monkeypatch.setattr(tm_core, "_runtime_profile_file_value", lambda: "hybrid")
    monkeypatch.setattr(tm_core, "_env_value", lambda key: "hybrid")

    assert tm_core.tigermemory_profile() == tm_core.TIGERMEMORY_PROFILE_LOCAL


def test_tigermemory_profile_reads_runtime_profile_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TIGERMEMORY_PROFILE", raising=False)
    runtime_dir = tmp_path / "runtime" / "tigermemory"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "profile.env").write_text("TIGERMEMORY_PROFILE=local\n", encoding="utf-8")
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "_env_value", lambda key: "hybrid")

    assert tm_core.tigermemory_profile() == tm_core.TIGERMEMORY_PROFILE_LOCAL


def test_tigermemory_profile_invalid_value_fails_safe_to_hybrid(monkeypatch):
    _force_hybrid_profile(monkeypatch)
    monkeypatch.setattr(tm_core, "_env_value", lambda key: "offline" if key == "TIGERMEMORY_PROFILE" else "")

    assert tm_core.tigermemory_profile() == tm_core.TIGERMEMORY_PROFILE_HYBRID


def test_local_profile_mem0_base_returns_disabled_sentinel(monkeypatch):
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)

    assert tm_core.mem0_base() == "local:disabled"


def test_local_profile_mem0_request_blocks_low_level_http(monkeypatch):
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)

    with pytest.raises(RuntimeError, match="mem0_request blocked"):
        tm_core.mem0_request("http://localhost:8765/api/v1/memories/")


def test_local_profile_mem0_write_persists_to_sqlite(monkeypatch, tmp_path):
    db_path = str(tmp_path / "local.sqlite")
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", db_path)

    payload = json.loads(tm_core.mem0_write("codex", "systems", "useful local-mode note for sqlite backend"))

    assert payload["ok"] is True
    assert payload["route"] == tm_core.TIGERMEMORY_PROFILE_LOCAL
    assert payload["route_info"]["backend"] == tm_core.TIGERMEMORY_PROFILE_LOCAL
    assert (tmp_path / "local.sqlite").exists()


def test_local_profile_mem0_search_uses_local_fts(monkeypatch, tmp_path):
    db_path = str(tmp_path / "local.sqlite")
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", db_path)
    monkeypatch.setenv("TM_LOCAL_VECTOR_SEARCH", "0")

    payload = json.loads(tm_core.mem0_write("codex", "systems", "dashboard local backend searchable"))
    mem_id = payload["id"]
    payload = json.loads(tm_core.mem0_search("dashboard", size=3))

    assert payload["count"] == 1
    assert payload["results"][0]["id"] == mem_id
    assert payload["results"][0]["source_agent"] == "codex"
    assert payload["search_backend"] == tm_core.TIGERMEMORY_PROFILE_LOCAL
    assert payload["results"][0]["route_info"]["vector_status"] == "fts5_only"


def test_local_profile_mem0_search_resolves_legacy_uuid(monkeypatch, tmp_path):
    db_path = str(tmp_path / "local.sqlite")
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", db_path)
    legacy_id = "22222222-2222-4222-8222-222222222222"

    raw = tm_core.mem0_write(
        "codex",
        "systems",
        "legacy uuid searchable local record",
        metadata_extra={"legacy_mem0_id": legacy_id},
    )
    local_id = json.loads(raw)["id"]

    payload = json.loads(tm_core.mem0_search(legacy_id, size=3))

    assert payload["count"] == 1
    assert payload["results"][0]["id"] == local_id
    assert payload["results"][0]["legacy_mem0_id"] == legacy_id


def test_local_profile_mem0_search_ranks_fts_relevance_before_recency(monkeypatch, tmp_path):
    db_path = tmp_path / "local.sqlite"
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db_path))

    strong = json.loads(
        tm_core.mem0_write("codex", "systems", "project canvas project canvas authoritative note")
    )["id"]
    weak = json.loads(tm_core.mem0_write("codex", "systems", "project weak note canvas"))["id"]
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE memories SET created_at=1, updated_at=1 WHERE id=?", (strong,))
        conn.execute("UPDATE memories SET created_at=9, updated_at=9 WHERE id=?", (weak,))
        conn.commit()
    finally:
        conn.close()

    payload = json.loads(tm_core.mem0_search("project canvas", size=2))

    assert [item["id"] for item in payload["results"]] == [strong, weak]


def test_local_profile_mem0_search_bridges_chinese_natural_query(monkeypatch, tmp_path):
    db_path = str(tmp_path / "local.sqlite")
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", db_path)

    payload = json.loads(tm_core.mem0_write("codex", "systems", "虎哥的偏好是先看已验证事实，再看推断。"))
    mem_id = payload["id"]
    result = json.loads(tm_core.mem0_search("虎哥偏好", size=3))
    short_result = json.loads(tm_core.mem0_search("偏好", size=3))

    assert result["results"][0]["id"] == mem_id
    assert short_result["results"][0]["id"] == mem_id


def test_local_profile_mem0_fallback_does_not_append_single_term_english_noise(monkeypatch, tmp_path):
    db_path = str(tmp_path / "local.sqlite")
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", db_path)

    good = json.loads(tm_core.mem0_write("codex", "systems", "alpha beta exact local fact"))["id"]
    tm_core.mem0_write("codex", "systems", "alpha only weak local fact")
    result = json.loads(tm_core.mem0_search("alpha beta", size=5))

    assert [item["id"] for item in result["results"]] == [good]


def test_local_profile_mem0_get_reports_unavailable_after_uuid_validation(monkeypatch):
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)

    with pytest.raises(ValueError, match="mem0_get unavailable"):
        tm_core.mem0_get("fd65b298-05bd-493c-83ce-e37d84447362")


def test_local_profile_mem0_delete_returns_zero_deleted_json(monkeypatch):
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)

    payload = json.loads(tm_core.mem0_delete(["fd65b298-05bd-493c-83ce-e37d84447362"]))

    assert payload == {"ok": False, "deleted": 0, "reason": "local profile"}


def test_local_profile_mem0_update_content_returns_fail_closed_json(monkeypatch):
    monkeypatch.setattr(tm_core, "tigermemory_profile", lambda: tm_core.TIGERMEMORY_PROFILE_LOCAL)

    payload = json.loads(tm_core.mem0_update_content("fd65b298-05bd-493c-83ce-e37d84447362", "replacement"))

    assert payload == {"ok": False, "reason": "local profile"}


def test_write_inbox_file_and_cleanup_on_commit_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "inbox_rel_path", lambda *_args, **_kwargs: "inbox/unit.md")
    (tmp_path / "inbox").mkdir()

    rel = tm_core.write_inbox_file("codex", "systems", "Unit title", "Useful body")
    assert rel == "inbox/unit.md"
    assert (tmp_path / rel).exists()

    with pytest.raises(FileExistsError):
        tm_core.write_inbox_file("codex", "systems", "Unit title", "Useful body")

    (tmp_path / rel).unlink()
    monkeypatch.setattr(tm_core, "git_commit_push", lambda *_args, **_kwargs: (_ for _ in ()).throw(tm_core.GitError("fail")))
    with pytest.raises(tm_core.GitError):
        tm_core.write_and_commit_inbox("codex", "systems", "Unit title", "Useful body")
    assert not (tmp_path / rel).exists()


def test_render_wiki_body_and_lint_page_errors():
    rendered = tm_core.render_wiki_body(
        "owner: codex\nstatus: active\nupdated: 2000-01-01\npublic: maybe",
        "body",
        date="2026-05-24",
    )

    assert "updated: 2000-01-01" not in rendered
    assert "updated: 2026-05-24" in rendered
    errors = tm_core.lint_page_errors(rendered)
    assert "public 'maybe' must be 'true' or 'false'" in errors
    assert "missing '##" in "\n".join(errors)


def test_deepseek_and_minimax_transport_errors(monkeypatch):
    monkeypatch.setattr(tm_core, "_env_value", lambda key: "secret")
    monkeypatch.setattr(tm_core, "deepseek_endpoint", lambda: "https://api.deepseek.test/chat/completions")
    monkeypatch.setattr(tm_core, "deepseek_model", lambda: "deepseek-test")
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_args: _SequenceOpener([_http_error(401)]))
    ok, reason = tm_core._call_deepseek_json("system", "user")
    assert ok is False and "DeepSeek HTTP 401" in reason

    monkeypatch.setattr(tm_core, "_env_value", lambda key: {
        "MINIMAX_API_KEY": "secret",
        "MINIMAX_BASE_URL": "https://api.minimax.test",
        "MINIMAX_MODEL": "minimax-test",
    }[key])
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_args: _SequenceOpener([_http_error(400)]))
    ok, reason = tm_core._call_minimax_json("system", "user")
    assert ok is False and "MiniMax HTTP 400" in reason
