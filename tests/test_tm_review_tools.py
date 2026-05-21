from __future__ import annotations

import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_review_tools  # type: ignore[import-not-found]


def _fake_run_factory(commands: list[list[str]]):
    def fake_run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        stdout = "abc123\n" if cmd[:3] == ["git", "rev-parse", "--short"] else ""
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    return fake_run


def test_execute_promote_writes_valid_codex_wiki_page(tmp_path, monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "run", _fake_run_factory(commands))
    monkeypatch.setattr(tm_review_tools.tm_core, "git_pull_rebase", lambda: None)
    monkeypatch.setattr(tm_review_tools.tm_core, "now", lambda _fmt: "2026-05-21")
    monkeypatch.setitem(sys.modules, "tm_review", types.SimpleNamespace(review_draft=lambda _text: {"score": 80, "issues": []}))

    fact = {
        "id": "inbox/2026-05-08-0006-linter-operations.md",
        "topic": "operations",
        "text": "\n".join([
            "---",
            "owner: linter",
            "status: draft",
            "title_cn: L4报表：系统页缺摘要与来源",
            "preview_cn: 这份 L4 报表说明 systems 页面缺摘要与来源。",
            "---",
            "",
            "# Lint Findings",
            "",
            "- wiki/systems/minimax-cli-integration.md: missing '## 摘要' section",
        ]),
    }

    result = tm_review_tools.execute_promote(fact, "operations", "lint-linter-systems-minimax-cli-cc19aa")

    page = tmp_path / "wiki" / "operations" / "lint-linter-systems-minimax-cli-cc19aa.md"
    text = page.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "owner: codex" in text
    assert "owner: linter" not in text
    assert text.count("---") == 2
    assert "## 摘要" in text
    assert "## 来源" in text
    commit_cmd = next(cmd for cmd in commands if cmd[:2] == ["git", "commit"])
    assert commit_cmd[3].startswith("[codex] create: promote fact 2026-05-08-0006-linter-operations.md")
    assert commit_cmd[-2:] == ["--", "wiki/operations/lint-linter-systems-minimax-cli-cc19aa.md"]


def test_execute_promote_l2_fallback_uses_allowed_codex_inbox(tmp_path, monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(tm_review_tools.tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_review_tools.tm_core, "run", _fake_run_factory(commands))
    monkeypatch.setattr(tm_review_tools.tm_core, "git_pull_rebase", lambda: None)
    monkeypatch.setattr(tm_review_tools.tm_core, "now", lambda fmt: "2026-05-21-1650" if "%H%M" in fmt else "2026-05-21")
    monkeypatch.setitem(sys.modules, "tm_review", types.SimpleNamespace(review_draft=lambda _text: {"score": 10, "issues": ["too weak"]}))

    result = tm_review_tools.execute_promote({"id": "fact-1", "text": "weak", "topic": "systems"}, "self-evolution", "weak-note")

    assert result["ok"] is True
    assert result["fallback_to_inbox"] is True
    assert result["inbox_path"] == "inbox/2026-05-21-1650-codex-selfevolution.md"
    fallback = tmp_path / result["inbox_path"]
    assert "owner: codex" in fallback.read_text(encoding="utf-8")
    commit_cmd = next(cmd for cmd in commands if cmd[:2] == ["git", "commit"])
    assert commit_cmd[3].startswith("[codex] create: L2-block promote")
    assert commit_cmd[-2:] == ["--", "inbox/2026-05-21-1650-codex-selfevolution.md"]
