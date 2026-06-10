from __future__ import annotations

import json
import pathlib

from tigermemory_config import manager
from tigermemory_core import runtime_events as tm_runtime_events


CANONICAL = """canonical_version: 0.1
metadata:
  source: tigermemory
preferences:
  - id: read_wiki_first
    title: 回答前先读 wiki
    description: |
      回答前先检索 wiki。
    severity: must
    natural_language: |
      Search wiki first.
  - id: confirm_before_delete
    title: 删除前确认
    description: |
      删除前先确认。
    severity: must
    natural_language: |
      Confirm before delete.
"""


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    _write(repo / "tools" / "gate3" / "canonical_v0.yaml", CANONICAL)
    return repo


def _wsl_home(tmp_path: pathlib.Path) -> pathlib.Path:
    home = tmp_path / "home" / "giant"
    for rel in [
        "workspaces/openclaw/AGENTS.md",
        "workspaces/openclaw/SOUL.md",
        ".openclaw/workspace/AGENTS.md",
        ".openclaw/workspace/TOOLS.md",
        ".hermes/profiles/tigermemory/SOUL.md",
        ".hermes/profiles/tigermemory/config.yaml",
    ]:
        _write(home / rel, f"user content for {rel}\n")
    return home


def test_windows_wsl_home_prefers_existing_unc_candidate(tmp_path: pathlib.Path, monkeypatch) -> None:
    missing = tmp_path / "missing"
    existing = tmp_path / "home" / "giant"
    existing.mkdir(parents=True)
    monkeypatch.setattr(manager, "_wsl_unc_candidates", lambda home: [missing, existing])

    assert manager._windows_wsl_home(tmp_path / "Users" / "Giant") == existing


def test_wsl_unc_candidates_use_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("TIGERMEMORY_MANAGER_WSL_DISTRO", "Ubuntu-Test")
    monkeypatch.setenv("TIGERMEMORY_MANAGER_WSL_USER", "tiger")

    candidates = [str(path) for path in manager._wsl_unc_candidates(pathlib.Path("C:/Users/Giant"))]

    assert "\\\\wsl.localhost\\Ubuntu-Test\\home\\tiger" in candidates
    assert "\\\\wsl$\\Ubuntu-Test\\home\\tiger" in candidates


def test_plan_reports_openclaw_hermes_targets_without_writing(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)

    result = manager.build_plan(["openclaw", "hermes"], repo_root=repo, wsl_home=home)

    assert result["ok"] is True
    rows = {row["runtime"]: row for row in result["runtimes"]}  # type: ignore[index]
    assert rows["openclaw"]["apply_supported"] is True
    assert len(rows["openclaw"]["targets"]) == 4
    assert len(rows["hermes"]["targets"]) == 2
    assert all(target["exists"] for target in rows["openclaw"]["targets"])


def test_runtime_capabilities_label_apply_and_preview_runtimes() -> None:
    result = manager.runtime_capabilities(["openclaw", "codex"])

    assert result["ok"] is True
    rows = {row["runtime"]: row for row in result["runtimes"]}  # type: ignore[index]
    assert rows["openclaw"]["apply_supported"] is True
    assert rows["openclaw"]["support"] == "partial"
    assert rows["openclaw"]["capability_label_cn"].startswith("partial")
    assert rows["codex"]["apply_supported"] is False
    assert rows["codex"]["mode"] == "preview_only"
    assert rows["codex"]["support"] == "unsupported_but_explained"


def test_manager_cli_capabilities_outputs_json(capsys) -> None:
    rc = manager.main(["capabilities", "--runtime", "hermes", "--runtime", "windsurf", "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["action"] == "capabilities"
    assert [row["runtime"] for row in result["runtimes"]] == ["hermes", "windsurf"]
    assert {row["support"] for row in result["runtimes"]} == {"partial", "unsupported_but_explained"}


def test_apply_requires_yes_before_writing(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)

    result = manager.apply_manager(["openclaw"], repo_root=repo, wsl_home=home)

    assert result["ok"] is False
    assert "apply requires --yes" in result["errors"]
    assert "tigermemory-policy:start" not in (home / "workspaces/openclaw/AGENTS.md").read_text(encoding="utf-8")


def test_apply_inserts_managed_block_and_preserves_user_content(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    original = (home / "workspaces/openclaw/AGENTS.md").read_text(encoding="utf-8")

    result = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    text = (home / "workspaces/openclaw/AGENTS.md").read_text(encoding="utf-8")
    assert original.strip() in text
    assert "<!-- tigermemory-policy:start" in text
    assert "read_wiki_first" in text
    assert "confirm_before_delete" in text


def test_repeated_apply_replaces_existing_block_without_duplication(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    backup_root = tmp_path / "backups"

    first = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=backup_root)
    second = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=backup_root)

    assert first["ok"] is True
    assert second["ok"] is True
    text = (home / "workspaces/openclaw/AGENTS.md").read_text(encoding="utf-8")
    assert text.count("<!-- tigermemory-policy:start") == 1
    assert text.count("<!-- tigermemory-policy:end -->") == 1


def test_hermes_config_yaml_is_backed_up_but_not_modified(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    config = home / ".hermes/profiles/tigermemory/config.yaml"
    before = config.read_text(encoding="utf-8")

    result = manager.apply_manager(["hermes"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    assert config.read_text(encoding="utf-8") == before
    target = [item for item in result["targets"] if item["target_id"] == "profile-config"][0]
    assert target["write_policy"] == "backup_only"
    assert pathlib.Path(target["backup_path"]).is_file()


def test_verify_accepts_managed_blocks_and_backup_only_config(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    applied = manager.apply_manager(["openclaw", "hermes"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    result = manager.verify_manager(str(applied["snapshot_id"]), repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    assert result["errors"] == []
    profile_config = [item for item in result["targets"] if item["target_id"] == "profile-config"][0]
    assert profile_config["write_policy"] == "backup_only"
    assert profile_config["readable"] is True


def test_verify_reports_missing_preference_when_block_is_damaged(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    applied = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")
    target = home / "workspaces/openclaw/AGENTS.md"
    target.write_text(target.read_text(encoding="utf-8").replace("confirm_before_delete", "missing_id"), encoding="utf-8")

    result = manager.verify_manager(str(applied["snapshot_id"]), repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is False
    assert result["errors"]


def test_rollback_restores_original_file_after_apply(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    target = home / "workspaces/openclaw/AGENTS.md"
    before = target.read_text(encoding="utf-8")
    applied = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    result = manager.rollback_manager(str(applied["snapshot_id"]), runtimes=["openclaw"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == before


def test_rollback_dry_run_does_not_restore_file(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    target = home / "workspaces/openclaw/AGENTS.md"
    applied = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")
    modified = target.read_text(encoding="utf-8")

    result = manager.rollback_manager(str(applied["snapshot_id"]), runtimes=["openclaw"], dry_run=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == modified


def test_rollback_rejects_tampered_backup(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    applied = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")
    manifest_path = pathlib.Path(str(applied["manifest_path"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pathlib.Path(manifest["targets"][0]["backup_path"]).write_text("tampered", encoding="utf-8")

    result = manager.rollback_manager(str(applied["snapshot_id"]), runtimes=["openclaw"], yes=True, repo_root=repo, backup_root=tmp_path / "backups")

    assert result["ok"] is False
    assert "backup sha256 mismatch" in result["errors"][0]


def test_apply_verify_rollback_record_runtime_events(tmp_path: pathlib.Path, monkeypatch) -> None:
    event_root = tmp_path / "events"
    monkeypatch.setenv("TM_RUNTIME_EVENTS_ROOT", str(event_root))
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    backup_root = tmp_path / "backups"

    applied = manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=backup_root)
    verified = manager.verify_manager(str(applied["snapshot_id"]), repo_root=repo, backup_root=backup_root)
    rolled_back = manager.rollback_manager(str(applied["snapshot_id"]), runtimes=["openclaw"], yes=True, repo_root=repo, backup_root=backup_root)

    assert applied["ok"] is True
    assert verified["ok"] is True
    assert rolled_back["ok"] is True
    events = tm_runtime_events.load_events(dates=[tm_runtime_events._date_key()], event_root=event_root)
    event_types = [event["event_type"] for event in events]
    assert "runtime_config_apply" in event_types
    assert "runtime_config_verify" in event_types
    assert "runtime_config_rollback" in event_types


def test_apply_rejects_preview_only_runtime(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)

    result = manager.apply_manager(["codex"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    assert result["ok"] is False
    assert "preview only / unsupported in v0" in result["errors"][0]


def test_manager_cli_plan_outputs_json(tmp_path: pathlib.Path, capsys) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)

    rc = manager.main(["plan", "--runtime", "openclaw", "--repo-root", str(repo), "--wsl-home", str(home), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["ok"] is True
    assert result["runtimes"][0]["runtime"] == "openclaw"


def test_status_reports_missing_block_before_apply(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)

    result = manager.status_manager(["openclaw"], repo_root=repo, wsl_home=home)

    assert result["ok"] is False
    targets = result["runtimes"][0]["targets"]  # type: ignore[index]
    assert {target["status"] for target in targets} == {"missing_block"}
    assert all(target["has_managed_block"] is False for target in targets)


def test_status_reports_ok_after_apply_with_current_canonical_sha(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    result = manager.status_manager(["openclaw"], repo_root=repo, wsl_home=home)

    assert result["ok"] is True
    targets = result["runtimes"][0]["targets"]  # type: ignore[index]
    assert {target["status"] for target in targets} == {"ok"}
    assert all(target["canonical_match"] is True for target in targets)
    assert all(target["missing_preference_ids"] == [] for target in targets)


def test_status_detects_incomplete_managed_block_after_user_damage(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")
    target = home / "workspaces/openclaw/AGENTS.md"
    target.write_text(target.read_text(encoding="utf-8").replace("confirm_before_delete", "removed_preference"), encoding="utf-8")

    result = manager.status_manager(["openclaw"], repo_root=repo, wsl_home=home)

    damaged = [item for item in result["runtimes"][0]["targets"] if item["target_id"] == "workspace-agents"][0]  # type: ignore[index]
    assert result["ok"] is False
    assert damaged["status"] == "incomplete"
    assert damaged["missing_preference_ids"] == ["confirm_before_delete"]


def test_status_treats_hermes_config_as_readable_backup_only(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    manager.apply_manager(["hermes"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    result = manager.status_manager(["hermes"], repo_root=repo, wsl_home=home)

    profile_config = [item for item in result["runtimes"][0]["targets"] if item["target_id"] == "profile-config"][0]  # type: ignore[index]
    assert result["ok"] is True
    assert profile_config["write_policy"] == "backup_only"
    assert profile_config["status"] == "backup_only_readable"
    assert profile_config["has_managed_block"] is False


def test_manager_cli_status_outputs_json_for_runtime_readback(tmp_path: pathlib.Path, capsys) -> None:
    repo = _repo(tmp_path)
    home = _wsl_home(tmp_path)
    manager.apply_manager(["openclaw"], yes=True, repo_root=repo, wsl_home=home, backup_root=tmp_path / "backups")

    rc = manager.main(["status", "--runtime", "openclaw", "--repo-root", str(repo), "--wsl-home", str(home), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["ok"] is True
    assert result["action"] == "status"
    assert result["runtimes"][0]["targets"][0]["status"] == "ok"
