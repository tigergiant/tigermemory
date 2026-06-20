from __future__ import annotations

import json
import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_supervisor_pack_context as pack_context


def test_tm_supervisor_pack_context_cli_json(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for rel in (
        "wiki/operations/project-canvas.md",
        "wiki/systems/tigermemory-development-supervisor.md",
        "wiki/operations/development-supervisor-ledger.md",
        "wiki/systems/tigermemory-project-map-for-claude.md",
        "tools/tm_dev_supervisor_context_pack.py",
        "tools/tm_dev_supervisor_review.py",
        "tools/tm_stage_accept.py",
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")

    monkeypatch.setattr(pack_context, "REPO_ROOT", repo)
    rc = pack_context.main(
        [
            "--stage",
            "p0",
            "--objective",
            "review development supervisor pack",
            "--profile",
            "dev-supervisor",
            "--output-dir",
            str(tmp_path / "packs"),
            "--print-json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["included_files"] == 7
    assert payload["excluded_sensitive_files"] == 0
    assert payload["bundle"].endswith("bundle.md")
