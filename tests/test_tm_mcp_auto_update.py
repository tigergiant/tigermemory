from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _bash_path(path: pathlib.Path) -> str:
    resolved = path.resolve()
    posix = resolved.as_posix()
    if len(posix) >= 3 and posix[1:3] == ":/":
        probe = subprocess.run(["bash", "-lc", "test -d /mnt/c"], check=False)
        prefix = f"/mnt/{posix[0].lower()}" if probe.returncode == 0 else f"/{posix[0].lower()}"
        return f"{prefix}{posix[2:]}"
    return posix


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def test_auto_update_wrapper_starts_mcp_when_untracked_files_exist(tmp_path):
    if not shutil.which("bash") or not shutil.which("git"):
        pytest.skip("bash and git are required for wrapper smoke")

    root = tmp_path / "repo"
    script = root / "deploy/mcp/tm_mcp_auto_update.sh"
    fake_python = root / "runtime/mcp-venv/bin/python"
    marker = root / "wrapper-invoked.txt"
    (root / "tools").mkdir(parents=True)
    script.parent.mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    (root / "tools/tm_mcp.py").write_text("unused\n", encoding="utf-8")
    with script.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            (REPO_ROOT / "deploy/mcp/tm_mcp_auto_update.sh")
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
        )
    with fake_python.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            f"printf 'fake-python %s\\n' \"$*\" > {_sh_quote(_bash_path(marker))}\n"
        )
    os.chmod(fake_python, 0o755)

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", _bash_path(script), "--stdio"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "auto-update failed; continuing with local checkout" in result.stderr
    assert marker.read_text(encoding="utf-8").startswith("fake-python ")


def test_auto_update_wrapper_times_out_blocked_git_pull(tmp_path):
    if not shutil.which("bash") or not shutil.which("timeout"):
        pytest.skip("bash and timeout are required for wrapper timeout smoke")

    root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    script = root / "deploy/mcp/tm_mcp_auto_update.sh"
    fake_python = root / "runtime/mcp-venv/bin/python"
    fake_git = bin_dir / "git"
    marker = root / "wrapper-invoked.txt"
    (root / "tools").mkdir(parents=True)
    script.parent.mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (root / "tools/tm_mcp.py").write_text("unused\n", encoding="utf-8")
    with script.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            (REPO_ROOT / "deploy/mcp/tm_mcp_auto_update.sh")
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
        )
    with fake_python.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            f"printf 'fake-python %s\\n' \"$*\" > {_sh_quote(_bash_path(marker))}\n"
        )
    with fake_git.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"diff\" ]; then exit 0; fi\n"
            "if [ \"$1\" = \"-c\" ]; then shift 2; fi\n"
            "if [ \"$1\" = \"pull\" ]; then sleep 5; exit 0; fi\n"
            "exit 0\n"
        )
    os.chmod(fake_python, 0o755)
    os.chmod(fake_git, 0o755)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    env = os.environ.copy()
    env["PATH"] = f"{_bash_path(bin_dir)}:{env['PATH']}"
    env["TM_MCP_AUTO_UPDATE_TIMEOUT_SEC"] = "1"
    result = subprocess.run(
        ["bash", _bash_path(script), "--stdio"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=4,
    )

    assert result.returncode == 0
    assert "auto-update failed; continuing with local checkout" in result.stderr
    assert marker.read_text(encoding="utf-8").startswith("fake-python ")


def test_openai_auto_update_wrapper_starts_when_untracked_files_exist(tmp_path):
    if not shutil.which("bash") or not shutil.which("git"):
        pytest.skip("bash and git are required for wrapper smoke")

    root = tmp_path / "repo"
    script = root / "deploy/mcp/tm_openai_mcp_auto_update.sh"
    fake_python = root / "runtime/mcp-venv/bin/python"
    marker = root / "wrapper-invoked.txt"
    (root / "tools").mkdir(parents=True)
    script.parent.mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    (root / "tools/tm_mcp_openai.py").write_text("unused\n", encoding="utf-8")
    with script.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            (REPO_ROOT / "deploy/mcp/tm_openai_mcp_auto_update.sh")
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
        )
    with fake_python.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            f"printf 'fake-python %s\\n' \"$*\" > {_sh_quote(_bash_path(marker))}\n"
        )
    os.chmod(fake_python, 0o755)

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", _bash_path(script), "--http", "--port", "9776"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "OpenAI MCP auto-update failed; continuing with local checkout" in result.stderr
    assert marker.read_text(encoding="utf-8").startswith("fake-python ")


def test_openai_auto_update_wrapper_times_out_blocked_git_pull(tmp_path):
    if not shutil.which("bash") or not shutil.which("timeout"):
        pytest.skip("bash and timeout are required for wrapper timeout smoke")

    root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    script = root / "deploy/mcp/tm_openai_mcp_auto_update.sh"
    fake_python = root / "runtime/mcp-venv/bin/python"
    fake_git = bin_dir / "git"
    marker = root / "wrapper-invoked.txt"
    (root / "tools").mkdir(parents=True)
    script.parent.mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (root / "tools/tm_mcp_openai.py").write_text("unused\n", encoding="utf-8")
    with script.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            (REPO_ROOT / "deploy/mcp/tm_openai_mcp_auto_update.sh")
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
        )
    with fake_python.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            f"printf 'fake-python %s\\n' \"$*\" > {_sh_quote(_bash_path(marker))}\n"
        )
    with fake_git.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            "if [ \"$1\" = \"diff\" ]; then exit 0; fi\n"
            "if [ \"$1\" = \"-c\" ]; then shift 2; fi\n"
            "if [ \"$1\" = \"pull\" ]; then sleep 5; exit 0; fi\n"
            "exit 0\n"
        )
    os.chmod(fake_python, 0o755)
    os.chmod(fake_git, 0o755)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    env = os.environ.copy()
    env["PATH"] = f"{_bash_path(bin_dir)}:{env['PATH']}"
    env["TM_OPENAI_MCP_AUTO_UPDATE_TIMEOUT_SEC"] = "1"
    result = subprocess.run(
        ["bash", _bash_path(script), "--http", "--port", "9776"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=4,
    )

    assert result.returncode == 0
    assert "OpenAI MCP auto-update failed; continuing with local checkout" in result.stderr
    assert marker.read_text(encoding="utf-8").startswith("fake-python ")
