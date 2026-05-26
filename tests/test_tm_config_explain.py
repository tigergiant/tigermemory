from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_config_explain  # type: ignore[import-not-found]


def test_tm_config_explain_shim_json(tmp_path, capsys) -> None:
    (tmp_path / "AGENTS.md").write_text("git pull and git status", encoding="utf-8")

    rc = tm_config_explain.main(["--root", str(tmp_path), "--json"])
    result = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert result["items"][0]["path"] == "AGENTS.md"
    assert result["items"][0]["support"] == "partial"
    assert result["items"][0]["support_label_cn"].startswith("partial")


def test_tm_config_explain_text_includes_readable_controls(tmp_path, capsys) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Run git pull and git status before write_memory.",
        encoding="utf-8",
    )

    rc = tm_config_explain.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "AGENTS.md [generic-agent / partial]" in out
    assert "已识别控制点" in out
    assert "开工前同步代码" in out
