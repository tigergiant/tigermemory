"""Tests for tm_ide_fleet F3: one-click config fix (diff preview + explicit apply).

All tests operate on a temp home (TM_IDE_FLEET_HOME) so nothing touches real IDE
configs. The central safety property under test: --apply NEVER writes a real
Bearer token, only the placeholder <TM_MCP_API_KEY>.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_ide_fleet  # type: ignore[import-not-found]


@pytest.fixture()
def fake_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TM_IDE_FLEET_HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    return tmp_path


def test_unknown_ide_id_not_writable():
    plan = tm_ide_fleet.build_fix_plan("nonexistent-ide")
    assert plan["writable"] is False


def test_toml_ide_not_writable_shows_hint(fake_home):
    plan = tm_ide_fleet.build_fix_plan("codex")
    assert plan["writable"] is False
    assert "hint" in plan
    assert "config.toml" in plan["hint"]


def test_remote_ide_not_writable(fake_home):
    plan = tm_ide_fleet.build_fix_plan("chatgpt")
    assert plan["writable"] is False


def test_plan_for_missing_config_creates_new(fake_home):
    plan = tm_ide_fleet.build_fix_plan("cursor")
    assert plan["writable"] is True
    assert plan["exists_before"] is False
    assert plan["already_configured"] is False
    after = json.loads(plan["after_text"])
    assert after["mcpServers"]["tigermemory"]["url"] == "https://tm.doodiu.cloud/mcp"
    # THE core safety property: never a real token, always the placeholder.
    assert (
        after["mcpServers"]["tigermemory"]["headers"]["Authorization"]
        == f"Bearer {tm_ide_fleet.TOKEN_PLACEHOLDER}"
    )


def test_plan_preserves_existing_unrelated_config(fake_home):
    cursor_config = fake_home / ".cursor" / "mcp.json"
    cursor_config.parent.mkdir(parents=True)
    cursor_config.write_text(
        json.dumps({"mcpServers": {"some-other-server": {"url": "https://example.com"}}}),
        encoding="utf-8",
    )
    plan = tm_ide_fleet.build_fix_plan("cursor")
    assert plan["writable"] is True
    after = json.loads(plan["after_text"])
    assert "some-other-server" in after["mcpServers"]
    assert "tigermemory" in after["mcpServers"]


def test_already_configured_is_noop_plan(fake_home):
    cursor_config = fake_home / ".cursor" / "mcp.json"
    cursor_config.parent.mkdir(parents=True)
    cursor_config.write_text(
        json.dumps({"mcpServers": {"tigermemory": {"url": "https://tm.doodiu.cloud/mcp"}}}),
        encoding="utf-8",
    )
    plan = tm_ide_fleet.build_fix_plan("cursor")
    assert plan["writable"] is True
    assert plan["already_configured"] is True


def test_malformed_existing_json_not_writable(fake_home):
    cursor_config = fake_home / ".cursor" / "mcp.json"
    cursor_config.parent.mkdir(parents=True)
    cursor_config.write_text("{ not valid json", encoding="utf-8")
    plan = tm_ide_fleet.build_fix_plan("cursor")
    assert plan["writable"] is False


def test_dry_run_never_writes_file(fake_home):
    plan = tm_ide_fleet.build_fix_plan("cursor")
    tm_ide_fleet.render_fix_plan_text(plan)  # preview only, no apply call
    assert not (fake_home / ".cursor" / "mcp.json").exists()


def test_apply_writes_file_and_backs_up_existing(fake_home):
    cursor_config = fake_home / ".cursor" / "mcp.json"
    cursor_config.parent.mkdir(parents=True)
    cursor_config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    plan = tm_ide_fleet.build_fix_plan("cursor")
    result = tm_ide_fleet.apply_fix_plan(plan)
    assert result["ok"] is True
    assert result["action"] == "written"
    assert result["backup_path"] is not None
    assert pathlib.Path(result["backup_path"]).exists()

    written = json.loads(cursor_config.read_text(encoding="utf-8"))
    assert "tigermemory" in written["mcpServers"]
    assert tm_ide_fleet.TOKEN_PLACEHOLDER in json.dumps(written)


def test_apply_creates_new_file_when_none_existed(fake_home):
    plan = tm_ide_fleet.build_fix_plan("cursor")
    result = tm_ide_fleet.apply_fix_plan(plan)
    assert result["ok"] is True
    assert result["backup_path"] is None  # nothing to back up
    assert (fake_home / ".cursor" / "mcp.json").exists()


def test_apply_on_already_configured_is_noop_and_does_not_touch_file(fake_home):
    cursor_config = fake_home / ".cursor" / "mcp.json"
    cursor_config.parent.mkdir(parents=True)
    original = json.dumps({"mcpServers": {"tigermemory": {"url": "https://tm.doodiu.cloud/mcp"}}})
    cursor_config.write_text(original, encoding="utf-8")
    mtime_before = cursor_config.stat().st_mtime_ns

    plan = tm_ide_fleet.build_fix_plan("cursor")
    result = tm_ide_fleet.apply_fix_plan(plan)
    assert result["ok"] is True
    assert result["action"] == "noop"
    assert cursor_config.stat().st_mtime_ns == mtime_before


def test_apply_refused_when_not_writable():
    plan = tm_ide_fleet.build_fix_plan("codex")
    result = tm_ide_fleet.apply_fix_plan(plan)
    assert result["ok"] is False


def test_never_writes_real_looking_token_even_with_env_key_set(fake_home, monkeypatch):
    # Even if a real key is present in the environment, apply must not embed it.
    monkeypatch.setenv("TM_MCP_API_KEY", "sk-real-secret-should-never-appear-in-file")
    plan = tm_ide_fleet.build_fix_plan("windsurf")
    result = tm_ide_fleet.apply_fix_plan(plan)
    assert result["ok"] is True
    written_path = pathlib.Path(result["config_path"])
    content = written_path.read_text(encoding="utf-8")
    assert "sk-real-secret-should-never-appear-in-file" not in content
    assert tm_ide_fleet.TOKEN_PLACEHOLDER in content


def test_render_fix_plan_text_dry_run_mentions_apply_flag(fake_home):
    plan = tm_ide_fleet.build_fix_plan("gemini")
    text = tm_ide_fleet.render_fix_plan_text(plan)
    assert "--apply" in text
    assert "尚未写入" in text


def test_render_fix_plan_text_after_apply(fake_home):
    plan = tm_ide_fleet.build_fix_plan("gemini")
    applied = tm_ide_fleet.apply_fix_plan(plan)
    text = tm_ide_fleet.render_fix_plan_text(plan, applied)
    assert "已写入" in text


def test_render_fix_plan_text_not_writable_shows_reason():
    plan = tm_ide_fleet.build_fix_plan("codex")
    text = tm_ide_fleet.render_fix_plan_text(plan)
    assert "不能自动改" in text
    assert "config.toml" in text


def test_cli_fix_dry_run_default(fake_home, capsys):
    rc = tm_ide_fleet.main(["fix", "cursor"])
    assert rc == 0
    assert not (fake_home / ".cursor" / "mcp.json").exists()
    out = capsys.readouterr().out
    assert "尚未写入" in out


def test_cli_fix_apply_writes(fake_home, capsys):
    rc = tm_ide_fleet.main(["fix", "cursor", "--apply"])
    assert rc == 0
    assert (fake_home / ".cursor" / "mcp.json").exists()


def test_cli_fix_json_shape(fake_home, capsys):
    rc = tm_ide_fleet.main(["fix", "cursor", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "plan" in payload
    assert payload["applied"] is None


def test_cli_fix_unwritable_returns_nonzero(fake_home, capsys):
    rc = tm_ide_fleet.main(["fix", "codex"])
    assert rc == 1
