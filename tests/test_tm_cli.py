from __future__ import annotations

import pathlib
import os
import subprocess
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

    assert tigermemory_cli.main(["init"]) == 0

    assert (tmp_path / "data" / "tigermemory").is_dir()
    assert (tmp_path / "runtime" / "tigermemory" / "profile.env").read_text(encoding="utf-8").strip().endswith(
        "TIGERMEMORY_PROFILE=local"
    )


def test_init_accepts_explicit_hybrid_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)

    assert tigermemory_cli.main(["init", "--profile", "hybrid"]) == 0

    assert (tmp_path / "data" / "tigermemory").is_dir()
    assert (tmp_path / "runtime" / "tigermemory" / "profile.env").read_text(encoding="utf-8").strip().endswith(
        "TIGERMEMORY_PROFILE=hybrid"
    )


def test_profile_guide_explains_hybrid_upgrade(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)

    assert tigermemory_cli.main(["profile", "guide", "hybrid"]) == 0

    out = capsys.readouterr().out
    assert "mode=advanced" in out
    assert "OpenMemory/Mem0" in out
    assert "rollback=tm profile set local" in out


def test_cli_module_help_smoke() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0
    assert "TigerMemory umbrella command" in result.stdout


def test_local_profile_cli_write_search_verify_smoke(tmp_path) -> None:
    db = tmp_path / "memory.sqlite"
    env = {
        **dict(os.environ),
        "TIGERMEMORY_PROFILE": "local",
        "TIGERMEMORY_LOCAL_DB": str(db),
        "PYTHONIOENCODING": "utf-8",
    }

    write = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "write-memory", "--agent", "codex", "--topic", "systems"],
        cwd=REPO_ROOT,
        input="\ufefflocal profile end to end recall",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert write.returncode == 0, write.stderr
    memory_id = __import__("json").loads(write.stdout)["id"]

    search = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--query", "local profile recall", "--size", "3"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert search.returncode == 0, search.stderr
    assert memory_id in search.stdout
    assert "\ufeff" not in search.stdout

    verify = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "verify", "--id", memory_id, "--terms", "local profile"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    assert '"direct_readback_ok": true' in verify.stdout


def test_publish_passthrough_accepts_tool_options(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(rel_path: str, args: list[str]) -> int:
        calls.append((rel_path, args))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main([
        "publish",
        "--dest",
        "out",
        "--dry-run",
        "--audit-pii",
        "--audit-scope",
        "repo",
    ]) == 0
    assert calls == [
        (
            "tools/tm_io.py",
            ["publish", "--dest", "out", "--dry-run", "--audit-pii", "--audit-scope", "repo"],
        )
    ]
