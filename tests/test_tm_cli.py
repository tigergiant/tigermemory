from __future__ import annotations

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tigermemory_cli


def test_profile_set_and_show_uses_runtime_profile_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)

    assert tigermemory_cli.main(["profile", "set", "local"]) == 0
    profile_path = tmp_path / "runtime" / "tigermemory" / "profile.env"
    assert profile_path.read_text(encoding="utf-8").strip().endswith("TIGERMEMORY_PROFILE=local")

    assert tigermemory_cli.main(["profile", "show"]) == 0
    out = capsys.readouterr().out
    assert "effective=local" in out


def test_init_creates_local_runtime_dirs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)

    assert tigermemory_cli.main(["init", "--profile", "local"]) == 0

    assert (tmp_path / "data" / "tigermemory").is_dir()
    assert (tmp_path / "runtime" / "tigermemory" / "profile.env").is_file()


def test_publish_passthrough_accepts_tool_options(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(rel_path: str, args: list[str]) -> int:
        calls.append((rel_path, args))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main(["publish", "--dest", "out", "--dry-run", "--audit-pii"]) == 0
    assert calls == [("tools/tm_io.py", ["publish", "--dest", "out", "--dry-run", "--audit-pii"])]
