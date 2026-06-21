from __future__ import annotations

import json
import pathlib

from tigermemory_config import agent_connect


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    _write(repo / "tigermemory_cli.py", "# cli\n")
    _write(repo / "wiki" / "systems" / "agent-behavior-rules.md", "# Rules\n")
    _write(repo / "AGENTS.md", "# User AGENTS\n\nKeep this line.\n")
    return repo


def test_plan_reports_project_targets_without_writing(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)

    result = agent_connect.plan_agent_connect(["codex", "claude-code"], repo_root=repo)

    assert result["ok"] is True
    rows = {row["target"]: row for row in result["targets"]}  # type: ignore[index]
    assert rows["codex"]["status"] == "missing_block"
    assert rows["claude-code"]["status"] == "missing_block"
    assert not (repo / "CLAUDE.md").exists()


def test_apply_requires_yes_before_writing(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)

    result = agent_connect.apply_agent_connect(["claude-code"], repo_root=repo)

    assert result["ok"] is False
    assert "apply requires --yes" in result["errors"][0]  # type: ignore[index]
    assert not (repo / "CLAUDE.md").exists()


def test_apply_inserts_managed_block_and_preserves_user_content(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)

    result = agent_connect.apply_agent_connect(["codex"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "Keep this line." in text
    assert "<!-- tigermemory-agent-connect:start target=codex" in text
    assert "tm ask --query" in text


def test_private_source_agents_md_is_protected_from_apply(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    source_text = '---\ntitle: "TigerMemory Agent 入口规则"\n---\n\n# AGENTS.md\n'
    (repo / "AGENTS.md").write_text(source_text, encoding="utf-8")

    result = agent_connect.apply_agent_connect(["codex", "claude-code"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    assert result["targets"][0]["status"] == "protected"  # type: ignore[index]
    assert result["targets"][0]["changed"] is False  # type: ignore[index]
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == source_text
    assert not (repo / "CLAUDE.md").exists()


def test_repeated_apply_replaces_existing_block_without_duplication(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    backup_root = tmp_path / "backups"

    first = agent_connect.apply_agent_connect(["codex"], yes=True, repo_root=repo, backup_root=backup_root)
    second = agent_connect.apply_agent_connect(["codex"], yes=True, repo_root=repo, backup_root=backup_root)

    assert first["ok"] is True
    assert second["ok"] is True
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert text.count("<!-- tigermemory-agent-connect:start target=codex") == 1
    assert text.count("<!-- tigermemory-agent-connect:end -->") == 1


def test_apply_prepares_hooks_templates_without_enabling_them(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)

    result = agent_connect.apply_agent_connect(["hooks"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    readme = (repo / ".tigermemory" / "agent-hooks" / "README.md").read_text(encoding="utf-8")
    example = (repo / ".tigermemory" / "agent-hooks" / "pre_tool_use.example.ps1").read_text(encoding="utf-8")
    assert "not active" in readme
    assert "tm admin approve is human-only" in example
    assert not (repo / ".git" / "hooks").exists()


def test_rollback_restores_original_file(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    before = (repo / "AGENTS.md").read_text(encoding="utf-8")
    applied = agent_connect.apply_agent_connect(["codex"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    rolled_back = agent_connect.rollback_agent_connect(str(applied["snapshot_id"]), yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert rolled_back["ok"] is True
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == before


def test_rollback_removes_new_template_files(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    hook_dir = repo / ".tigermemory" / "agent-hooks"
    applied = agent_connect.apply_agent_connect(["hooks"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")
    assert (hook_dir / "README.md").exists()
    assert (hook_dir / "pre_tool_use.example.ps1").exists()

    rolled_back = agent_connect.rollback_agent_connect(str(applied["snapshot_id"]), yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert rolled_back["ok"] is True
    assert not (hook_dir / "README.md").exists()
    assert not (hook_dir / "pre_tool_use.example.ps1").exists()
    assert not (repo / ".tigermemory").exists()


def test_missing_mcp_command_is_blocked_not_silent(monkeypatch, tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(agent_connect.shutil, "which", lambda _name: None)

    status = agent_connect.status_agent_connect(["mcp"], repo_root=repo)
    config = agent_connect.mcp_config_payload("codex")

    assert status["blocked_count"] == 1
    assert status["targets"][0]["status"] == "blocked"  # type: ignore[index]
    assert config["ok"] is False
    assert "tm-mcp" in config["text"]


def test_agent_connect_cli_status_outputs_json(tmp_path: pathlib.Path, capsys) -> None:
    repo = _repo(tmp_path)

    rc = agent_connect.main(["status", "--target", "codex", "--repo-root", str(repo), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert result["action"] == "status"
    assert result["missing_count"] == 1
