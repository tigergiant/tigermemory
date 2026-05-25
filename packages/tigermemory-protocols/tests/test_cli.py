from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tigermemory_protocols.cli", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def test_cli_list_schemas_prints_four_protocol_names() -> None:
    result = run_cli("list-schemas")
    assert result.returncode == 0
    assert "wiki_page:" in result.stdout
    assert "memory_item:" in result.stdout
    assert "agent_policy:" in result.stdout
    assert "context_pack:" in result.stdout


def test_cli_validate_valid_yaml_returns_ok(tmp_path: Path) -> None:
    instance = tmp_path / "wiki_page.yaml"
    instance.write_text(
        "\n".join(
            [
                "owner: codex",
                "status: active",
                "updated: 2026-05-25",
                "partition: systems",
                "title: CLI Validate Fixture",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_cli("validate", "wiki_page", str(instance))
    assert result.returncode == 0
    assert result.stdout.strip() == "OK"


def test_cli_scan_wiki_dry_run_does_not_write_fixture_pages(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki" / "systems"
    wiki_dir.mkdir(parents=True)
    page = wiki_dir / "protocol-test.md"
    page.write_text(
        "\n".join(
            [
                "---",
                "owner: codex",
                "status: active",
                "updated: 2026-05-25",
                "partition: systems",
                "title: Protocol Test Page",
                "---",
                "",
                "# Protocol Test Page",
                "",
            ]
        ),
        encoding="utf-8",
    )
    before = (page.stat().st_mtime_ns, page.stat().st_size)

    result = run_cli("scan-wiki", "--root", str(tmp_path), "--json")

    after = (page.stat().st_mtime_ns, page.stat().st_size)
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert before == after
    assert payload["summary"] == {"ok": 1, "fail": 0}
    assert payload["pages"][0]["path"] in {
        "wiki\\systems\\protocol-test.md",
        "wiki/systems/protocol-test.md",
    }
