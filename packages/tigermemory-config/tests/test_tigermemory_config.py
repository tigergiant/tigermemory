from __future__ import annotations

import json
import pathlib

import tigermemory_config


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_config_surfaces_explains_known_files(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "AGENTS.md", "开工先 git pull，然后 git status。不要 --no-verify。")
    _write(tmp_path / ".cursor" / "rules" / "memory.md", "回答前先读 MCP 和 wiki。")
    _write(tmp_path / ".githooks" / "pre-commit", "#!/bin/sh\n# block secret .env\n")

    items = tigermemory_config.scan_config_surfaces(tmp_path)
    by_path = {str(item["path"]): item for item in items}

    assert set(by_path) == {"AGENTS.md", ".cursor/rules/memory.md", ".githooks/pre-commit"}
    assert by_path["AGENTS.md"]["target"] == "generic-agent"
    assert by_path["AGENTS.md"]["support"] == "partial"
    assert "开工前同步代码" in by_path["AGENTS.md"]["controls_cn"]
    assert by_path[".cursor/rules/memory.md"]["support"] == "soft_only"
    assert by_path[".githooks/pre-commit"]["support"] == "requires_external_guard"
    assert by_path[".githooks/pre-commit"]["risks_cn"]


def test_scan_ignores_unknown_files(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "README.md", "not an agent config")

    assert tigermemory_config.scan_config_surfaces(tmp_path) == []


def test_summarize_groups_by_target_and_support(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "AGENTS.md", "git status")
    _write(tmp_path / "CLAUDE.md", "write_memory")

    summary = tigermemory_config.summarize(tigermemory_config.scan_config_surfaces(tmp_path))

    assert summary["total"] == 2
    assert summary["by_target"] == {"claude-code": 1, "generic-agent": 1}
    assert summary["by_support"] == {"partial": 1, "soft_only": 1}


def test_cli_json_output(tmp_path: pathlib.Path, capsys) -> None:
    _write(tmp_path / "AGENTS.md", "get_agent_onboarding and write_memory")

    rc = tigermemory_config.main(["--root", str(tmp_path), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["ok"] is True
    assert result["summary"]["total"] == 1
    assert result["items"][0]["path"] == "AGENTS.md"


def test_detect_repo_root_honors_env(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_config._detect_repo_root() == tmp_path.resolve()


def test_support_constants_match_product_vision_5_level_rubric() -> None:
    # Canonical 5-state rubric from wiki/systems/tigermemory-product-vision.md §推断 §11
    # line 406: "输出 full / partial / soft_only / unsupported_but_explained /
    # requires_external_guard 五态，不止 Yes/Partial/No". Lock the public contract here.
    assert tigermemory_config.SUPPORT_FULL == "full"
    assert tigermemory_config.SUPPORT_PARTIAL == "partial"
    assert tigermemory_config.SUPPORT_SOFT_ONLY == "soft_only"
    assert tigermemory_config.SUPPORT_UNSUPPORTED_BUT_EXPLAINED == "unsupported_but_explained"
    assert tigermemory_config.SUPPORT_REQUIRES_EXTERNAL_GUARD == "requires_external_guard"
