"""Tests for tm_ide_fleet: read-only multi-IDE fleet status.

Uses TM_IDE_FLEET_HOME to point detection at a temp fixture dir; never reads
the real user config and never performs network calls (health probe untested
here by design — it is opt-in and side-effect-free).
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
    # The default (no-subcommand) command also gathers F2 continuity, which
    # would otherwise read the real runtime/openmemory/.env key and attempt
    # real network calls. Force the "not configured" fast path instead.
    monkeypatch.delenv("TM_MCP_API_KEY", raising=False)
    monkeypatch.setattr(tm_ide_fleet, "_api_key", lambda: None)
    return tmp_path


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_empty_home_all_no_config_or_remote(fake_home):
    fleet = tm_ide_fleet.gather_fleet()
    statuses = {r["id"]: r["status"] for r in fleet["ides"]}
    assert statuses["claude-code"] == "no_config"
    assert statuses["cursor"] == "no_config"
    assert statuses["chatgpt"] == "remote"
    assert fleet["summary"]["configured_count"] == 0
    assert "health" not in fleet


def test_claude_code_configured_detected(fake_home):
    _write(
        fake_home / ".claude.json",
        json.dumps(
            {
                "mcpServers": {
                    "tigermemory": {
                        "type": "http",
                        "url": "https://tm.doodiu.cloud/mcp",
                        "headers": {"Authorization": "Bearer x"},
                    }
                }
            }
        ),
    )
    fleet = tm_ide_fleet.gather_fleet()
    cc = next(r for r in fleet["ides"] if r["id"] == "claude-code")
    assert cc["status"] == "configured"
    assert cc["transport"] == "http"
    assert cc["server_names"] == ["tigermemory"]
    assert fleet["summary"]["configured_count"] == 1


def test_config_present_but_no_tigermemory_is_not_configured(fake_home):
    _write(
        fake_home / ".cursor" / "mcp.json",
        json.dumps({"mcpServers": {"some-other": {"url": "https://example.com/mcp"}}}),
    )
    fleet = tm_ide_fleet.gather_fleet()
    cur = next(r for r in fleet["ides"] if r["id"] == "cursor")
    assert cur["status"] == "not_configured"
    assert cur["config_path"] is not None


def test_codex_toml_detected_by_text_scan(fake_home):
    _write(
        fake_home / ".codex" / "config.toml",
        '[mcp_servers.tigermemory]\ncommand = "python3"\nargs = ["tm_mcp_bridge.py"]\n',
    )
    fleet = tm_ide_fleet.gather_fleet()
    cx = next(r for r in fleet["ides"] if r["id"] == "codex")
    assert cx["status"] == "configured"
    # TOML is detected by marker scan, transport is best-effort None.
    assert cx["transport"] is None


def test_claude_desktop_msix_path_detected(monkeypatch, tmp_path):
    monkeypatch.setenv("TM_IDE_FLEET_HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    msix = (
        tmp_path
        / "AppData"
        / "Local"
        / "Packages"
        / "Claude_pzs8sxrjxfjjc"
        / "LocalCache"
        / "Roaming"
        / "Claude"
        / "claude_desktop_config.json"
    )
    _write(msix, json.dumps({"mcpServers": {"tigermemory": {"url": "https://tm.doodiu.cloud/mcp"}}}))
    fleet = tm_ide_fleet.gather_fleet()
    cd = next(r for r in fleet["ides"] if r["id"] == "claude-desktop")
    assert cd["status"] == "configured"
    assert "Packages" in cd["config_path"]


def test_appdata_standard_path_detected(monkeypatch, tmp_path):
    monkeypatch.setenv("TM_IDE_FLEET_HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    standard = tmp_path / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    _write(standard, json.dumps({"mcpServers": {"tigermemory": {"url": "https://tm.doodiu.cloud/mcp"}}}))
    fleet = tm_ide_fleet.gather_fleet()
    cd = next(r for r in fleet["ides"] if r["id"] == "claude-desktop")
    assert cd["status"] == "configured"


def test_malformed_json_still_detects_marker(fake_home):
    # A truncated/invalid JSON must not crash; marker scan still flags it.
    _write(fake_home / ".gemini" / "settings.json", '{ "mcpServers": { "tigermemory": ')
    fleet = tm_ide_fleet.gather_fleet()
    g = next(r for r in fleet["ides"] if r["id"] == "gemini")
    assert g["status"] == "configured"  # marker present
    assert g["transport"] is None  # parse failed, transport unknown


def test_render_text_lists_fix_for_unconfigured(fake_home):
    text = tm_ide_fleet.render_text(tm_ide_fleet.gather_fleet())
    assert "IDE 舰队状态" in text
    assert "修复：" in text  # unconfigured IDEs surface a remediation line


def test_json_output_shape(fake_home, capsys):
    rc = tm_ide_fleet.main(["status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "tm-ide-fleet-v1"
    assert "summary" in payload and "ides" in payload
    assert payload["summary"]["total"] == len(payload["ides"])


def test_default_command_is_status(fake_home, capsys):
    rc = tm_ide_fleet.main([])
    assert rc == 0
    assert "舰队状态" in capsys.readouterr().out


def test_default_command_combines_fleet_and_continuity(fake_home, capsys):
    # fake_home forces continuity into the "not configured" fast path (no
    # network), so this only proves the combined view wires both sections in.
    rc = tm_ide_fleet.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "IDE 舰队状态" in out
    assert "session handoff" in out or "读不到" in out


def test_default_command_json_has_both_sections(fake_home, capsys):
    rc = tm_ide_fleet.main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "tm-ide-fleet-full-v1"
    assert "fleet" in payload and "continuity" in payload


def test_status_subcommand_still_works_standalone(fake_home, capsys):
    rc = tm_ide_fleet.main(["status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "tm-ide-fleet-v1"
