from __future__ import annotations

import json
import pathlib
import sys

from fastapi.testclient import TestClient

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_cron_apply  # type: ignore[import-not-found]
import tm_review_tools  # type: ignore[import-not-found]
import tm_review_ui  # type: ignore[import-not-found]

HOST = {"Host": "127.0.0.1:9777"}


def _client(tmp_path: pathlib.Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(tm_review_ui, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(tm_review_ui, "ensure_write_ready", lambda: None)
    return TestClient(tm_review_ui.app)


def _write_digest(root: pathlib.Path, date: str = "2026-05-21") -> pathlib.Path:
    path = root / "wiki" / "operations" / f"daily-memory-digest-{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "---",
            "owner: codex",
            "status: active",
            f"updated: {date}",
            "title: \"Memory Digest\"",
            "last_run_at: 2026-05-21T23:55:00+08:00",
            "mem0_count: 2",
            "inbox_count: 1",
            "discard_count: 3",
            "proposal_count: 1",
            "applied_count: 0",
            "stale_archive_count: 1",
            "promote_candidate_count: 0",
            "---",
            "",
            "# Memory Digest 2026-05-21",
            "",
            "## ⚡ 今日要决策",
            "",
            "- 🔴 14 天兜底 archive 候选：1 条",
            "",
            "## 摘要",
            "",
            "fixture summary",
            "",
            "## 📝 inbox 决策区",
            "",
            "### 🔴 建议 archive",
            "",
            "- `inbox/2026-05-01-1200-codex-systems.md` **高亮：14 天兜底 archive**",
            "  - 入库时间：2026-05-01，已停留 20 天",
            "  - 中文摘要：审批界面需要能快速判断这条开发收尾记录是否应归档。",
            "  - 原文预览：commit pushed pytest passed",
            "  - cron 建议动作：archive",
            "  - 建议理由：14-day fallback",
            "  - 虎哥裁决：[ ] apply  [ ] reject",
            "",
            "## 🧠 Proposed Changes",
            "",
            "#### proposal-2026-05-21-001",
            "",
            "**类型**：prompt-tuning",
            "",
            "**触发证据**：fixture",
            "",
            "```diff",
            "diff --git a/tools/tm_route.py b/tools/tm_route.py",
            "+x",
            "```",
            "",
            "**影响范围**：tools/tm_route.py",
            "",
            "## 📈 自评指标",
            "",
            "- 当日自评：76",
            "",
            "## 📚 附录",
            "",
            "raw appendix",
            "",
            "## 来源",
            "",
            "- fixture",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def _write_inbox(root: pathlib.Path, name: str = "2026-05-01-1200-codex-systems.md") -> pathlib.Path:
    path = root / "inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("inbox fixture", encoding="utf-8")
    return path


def test_host_header_rejects_non_localhost(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/healthz", headers={"Host": "example.com"})

    assert response.status_code == 403


def test_session_token_cookie_flow(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/", headers=HOST, follow_redirects=False)

    assert response.status_code == 302
    assert "tm_review_session" in response.headers["set-cookie"]


def test_api_digest_requires_cookie_after_session_exists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)
    fresh = TestClient(tm_review_ui.app)

    response = fresh.get("/api/digest/2026-05-21", headers=HOST)

    assert response.status_code == 401


def test_direct_digest_sets_cookie_for_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)

    response = client.get("/digest/2026-05-21", headers=HOST)

    assert response.status_code == 200
    assert "tm_review_session" in response.headers["set-cookie"]


def test_digest_with_cookie_returns_html_and_embedded_json(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest/2026-05-21", headers=HOST)

    assert response.status_code == 200
    assert "今日要决策" in response.text
    assert "https://cdn.tailwindcss.com" in response.text
    assert "digest-data" in response.text


def test_api_digest_parses_expected_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/digest/2026-05-21", headers=HOST)

    data = response.json()
    assert data["ok"] is True
    assert data["digest"]["counts"]["mem0"] == 2
    assert data["digest"]["inbox_rows"][0]["stale_archive"] is True
    assert "快速判断" in data["digest"]["inbox_rows"][0]["cn_summary"]
    assert data["digest"]["inbox_rows"][0]["raw_summary"] == "commit pushed pytest passed"
    assert data["digest"]["proposals"][0]["id"] == "proposal-2026-05-21-001"


def test_inbox_archive_moves_file_and_returns_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{inbox.name}", "action": "archive"})

    data = response.json()
    assert data["ok"] is True
    assert data["commit_sha"] == "abc123"
    assert not inbox.exists()


def test_inbox_promote_mem0_uses_review_tool_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    inbox = _write_inbox(tmp_path)
    calls: dict[str, object] = {}

    def fake_promote(fact, topic=None):
        calls["fact"] = fact
        calls["topic"] = topic
        return {"ok": True, "memory_id": "mem-1"}

    monkeypatch.setattr(tm_review_tools, "execute_promote_mem0", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": f"inbox/{inbox.name}", "action": "promote_mem0"})

    data = response.json()
    assert data["ok"] is True
    assert calls["fact"]["topic"] == "systems"
    assert calls["topic"] is None
    assert not inbox.exists()


def test_inbox_action_invalid_path_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/inbox/action", headers=HOST, json={"path": "../x.md", "action": "archive"})

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_batch_inbox_archive_selected_commits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{first.name}", f"inbox/{second.name}"], "action": "archive"},
    )

    data = response.json()
    assert data["ok"] is True
    assert data["success_count"] == 2
    assert data["failure_count"] == 0
    assert data["commit_sha"] == "abc123"
    assert len(commits) == 1
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_promote_mem0_archives_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    promoted: list[str] = []

    def fake_promote(fact, topic=None):
        promoted.append(fact["source_id"])
        return {"ok": True, "memory_id": f"mem-{len(promoted)}", "topic": topic or fact["topic"]}

    monkeypatch.setattr(tm_review_tools, "execute_promote_mem0", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "abc123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": [f"inbox/{first.name}", f"inbox/{second.name}"], "action": "promote_mem0"},
    )

    data = response.json()
    assert data["ok"] is True
    assert data["success_count"] == 2
    assert promoted == [f"inbox/{first.name}", f"inbox/{second.name}"]
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_promote_wiki_generates_slugs_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    first = _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    second = _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    promoted: list[tuple[str, str, str]] = []

    def fake_promote(fact, partition, slug):
        promoted.append((fact["source_id"], partition, slug))
        return {"ok": True, "wiki_path": f"wiki/{partition}/{slug}.md", "commit_sha": f"wiki-{len(promoted)}"}

    monkeypatch.setattr(tm_review_tools, "execute_promote", fake_promote)
    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", lambda _paths, _message: "archive123")
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={
            "paths": [f"inbox/{first.name}", f"inbox/{second.name}"],
            "action": "promote_wiki",
            "partition": "systems",
            "slug_prefix": "daily-review-note",
        },
    )

    data = response.json()
    assert data["ok"] is True
    assert data["success_count"] == 2
    assert promoted[0][1] == "systems"
    assert promoted[0][2].startswith("daily-review-note-1-")
    assert promoted[1][2].startswith("daily-review-note-2-")
    assert not first.exists()
    assert not second.exists()


def test_batch_inbox_action_rejects_invalid_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/inbox/batch-action",
        headers=HOST,
        json={"paths": ["../x.md"], "action": "archive"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "inside repo" in data["error"]


def test_review_html_contains_batch_controls_and_status_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    _write_digest(tmp_path)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/digest/2026-05-21", headers=HOST)

    assert response.status_code == 200
    assert "批量归档" in response.text
    assert "批量写入 Mem0" in response.text
    assert "data-row-status" in response.text
    assert "展开原文预览（至少 100 字）" in response.text


def test_proposal_apply_calls_tm_cron_apply(tmp_path, monkeypatch):
    called: dict[str, str] = {}
    monkeypatch.setattr(tm_cron_apply, "load_report_proposals", lambda _date: {
        "proposal-2026-05-21-001": tm_cron_apply.Proposal("proposal-2026-05-21-001", "prompt-tuning", True)
    })

    def fake_apply(date: str, proposal: tm_cron_apply.Proposal):
        called["date"] = date
        called["id"] = proposal.proposal_id
        called["type"] = proposal.proposal_type
        return {"commit": "abc123"}

    monkeypatch.setattr(tm_cron_apply, "apply_one", fake_apply)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/proposal/apply",
        headers=HOST,
        json={"date": "2026-05-21", "proposal_id": "proposal-2026-05-21-001"},
    )

    assert response.json()["ok"] is True
    assert called == {"date": "2026-05-21", "id": "proposal-2026-05-21-001", "type": "prompt-tuning"}


def test_batch_archive_stale_commits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_review_ui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    _write_inbox(tmp_path, "2026-05-01-1200-codex-systems.md")
    _write_inbox(tmp_path, "2026-05-02-1200-codex-systems.md")
    commits: list[list[str]] = []

    def fake_commit(paths, _message):
        commits.append(paths)
        return "abc123"

    monkeypatch.setattr(tm_review_ui, "commit_and_push_paths", fake_commit)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post("/api/batch/archive-stale", headers=HOST, json={"date": "2026-05-21"})

    data = response.json()
    assert data["ok"] is True
    assert data["commit_sha"] == "abc123"
    assert len(data["archived"]) == 2
    assert len(commits) == 1


def test_healthz_returns_version(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/healthz", headers=HOST)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "version" in response.json()


def test_proposal_failure_returns_ok_false(tmp_path, monkeypatch):
    monkeypatch.setattr(tm_cron_apply, "load_report_proposals", lambda _date: {})

    def fail(_date, _proposal):
        raise tm_cron_apply.CronApplyError("bad patch")

    monkeypatch.setattr(tm_cron_apply, "apply_one", fail)
    client = _client(tmp_path, monkeypatch)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.post(
        "/api/proposal/apply",
        headers=HOST,
        json={"date": "2026-05-21", "proposal_id": "proposal-2026-05-21-001"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "bad patch" in response.json()["error"]


def test_main_rejects_non_local_bind():
    assert tm_review_ui.main(["--host", "0.0.0.0"]) == 2
