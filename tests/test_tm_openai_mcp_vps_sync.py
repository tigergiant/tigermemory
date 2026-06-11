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


def _copy_script(root: pathlib.Path) -> pathlib.Path:
    script = root / "deploy/mcp/tm_openai_mcp_vps_sync.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        (REPO_ROOT / "deploy/mcp/tm_openai_mcp_vps_sync.sh")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n"),
        encoding="utf-8",
        newline="\n",
    )
    return script


def test_vps_sync_lock_busy_skips_git(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash is required for VPS sync smoke")

    root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    script = _copy_script(root)
    git_called = root / "git-called.txt"
    (root / ".tmp/vps-sync.lock").mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'git-called\\n' > {_sh_quote(_bash_path(git_called))}\n"
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(fake_git, 0o755)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f"cd {_sh_quote(_bash_path(root))} && "
                f"export PATH={_sh_quote(_bash_path(bin_dir))}:$PATH && "
                f"export TM_OPENAI_MCP_ROOT={_sh_quote(_bash_path(root))} && "
                "export TM_OPENAI_MCP_SYNC_LOCK_WAIT_SEC=0 && "
                f"exec {_sh_quote(_bash_path(script))}"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=4,
    )

    assert result.returncode == 0
    assert "VPS sync lock busy" in result.stderr
    assert not git_called.exists()


def test_vps_sync_uses_fetch_merge_and_restarts_on_code_change(tmp_path):
    if not shutil.which("bash") or not shutil.which("timeout"):
        pytest.skip("bash and timeout are required for VPS sync smoke")

    root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    script = _copy_script(root)
    git_log = root / "git-log.txt"
    systemctl_log = root / "systemctl-log.txt"
    merged_marker = root / "merged"
    (root / "runtime/mcp-venv/bin").mkdir(parents=True)
    (root / "tools").mkdir(parents=True)
    bin_dir.mkdir(parents=True)

    fake_python = root / "runtime/mcp-venv/bin/python"
    fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    os.chmod(fake_python, 0o755)

    fake_gh = bin_dir / "gh"
    fake_gh.write_text("#!/usr/bin/env bash\nexit 127\n", encoding="utf-8", newline="\n")
    os.chmod(fake_gh, 0o755)

    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        f"log={_sh_quote(_bash_path(git_log))}\n"
        f"merged={_sh_quote(_bash_path(merged_marker))}\n"
        "printf '%s\\n' \"$*\" >> \"$log\"\n"
        "if [ \"$1\" = \"status\" ]; then exit 0; fi\n"
        "if [ \"$1\" = \"rev-parse\" ]; then\n"
        "  if [ -f \"$merged\" ]; then echo newhead; else echo oldhead; fi\n"
        "  exit 0\n"
        "fi\n"
        "while [ \"$1\" = \"-c\" ]; do shift 2; done\n"
        "if [ \"$1\" = \"fetch\" ]; then exit 0; fi\n"
        "if [ \"$1\" = \"merge\" ]; then touch \"$merged\"; exit 0; fi\n"
        "if [ \"$1\" = \"diff\" ]; then echo tools/tm_mcp_openai.py; exit 0; fi\n"
        "if [ \"$1\" = \"pull\" ]; then exit 9; fi\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(fake_git, 0o755)

    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" > {_sh_quote(_bash_path(systemctl_log))}\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(fake_systemctl, 0o755)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f"cd {_sh_quote(_bash_path(root))} && "
                f"export PATH={_sh_quote(_bash_path(bin_dir))}:$PATH && "
                f"export TM_OPENAI_MCP_ROOT={_sh_quote(_bash_path(root))} && "
                "export TM_OPENAI_MCP_SERVICE=tm-openai-mcp.service && "
                f"exec {_sh_quote(_bash_path(script))}"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=8,
    )

    assert result.returncode == 0
    log = git_log.read_text(encoding="utf-8")
    assert "fetch origin master" in log
    assert "merge --ff-only FETCH_HEAD" in log
    assert "pull --ff-only" not in log
    assert systemctl_log.read_text(encoding="utf-8").strip() == "restart tm-openai-mcp.service"
