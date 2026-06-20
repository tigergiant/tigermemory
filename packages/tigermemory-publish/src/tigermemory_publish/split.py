from __future__ import annotations

from dataclasses import dataclass
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from .modules import private_excluded_wiki_partitions


@dataclass(frozen=True)
class SplitReport:
    target: str
    public_core_independent: bool
    public_core_independence_reason: str
    source_updateable: bool
    source_update_reason: str
    private_instance_required: bool
    private_surfaces: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "target": self.target,
            "public_core_independent": self.public_core_independent,
            "public_core_independence_reason": self.public_core_independence_reason,
            "source_updateable": self.source_updateable,
            "source_update_reason": self.source_update_reason,
            "private_instance_required": self.private_instance_required,
            "private_surfaces": list(self.private_surfaces),
        }


def private_surfaces() -> tuple[str, ...]:
    wiki_surfaces = tuple(f"wiki/{partition}" for partition in private_excluded_wiki_partitions())
    return (
        *wiki_surfaces,
        "sources/internal-analysis/development-reviews",
        "runtime",
        "data/expense_import",
        ".tmp",
    )


def build_split_report(
    target: str,
    *,
    boundary_ok: bool,
    smoke_ok: bool = False,
    source_update_smoke_ok: bool = False,
) -> dict[str, object]:
    if target == "public-core":
        public_core_independent = bool(boundary_ok and smoke_ok)
        source_updateable = bool(boundary_ok and source_update_smoke_ok)
        return SplitReport(
            target=target,
            public_core_independent=public_core_independent,
            public_core_independence_reason=(
                "verified" if public_core_independent else "smoke_not_run" if boundary_ok else "boundary_failed"
            ),
            source_updateable=source_updateable,
            source_update_reason=(
                "verified" if source_updateable else "smoke_not_run" if boundary_ok else "boundary_failed"
            ),
            private_instance_required=False,
            private_surfaces=private_surfaces(),
        ).to_json()
    return SplitReport(
        target=target,
        public_core_independent=False,
        public_core_independence_reason="snapshot_compatibility_lane",
        source_updateable=False,
        source_update_reason="snapshot_compatibility_lane",
        private_instance_required=True,
        private_surfaces=private_surfaces(),
    ).to_json()


def _run_checked(
    args: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        input=input_text,
        check=True,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git(repo: pathlib.Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return _run_checked(["git", "-C", str(repo), *args], timeout=timeout)


def _configure_git_identity(repo: pathlib.Path) -> None:
    _git(repo, "config", "user.email", "public-core-smoke@example.invalid")
    _git(repo, "config", "user.name", "TigerMemory Public Core Smoke")


def run_public_core_instance_smoke(repo_root, publish_func) -> bool:
    try:
        with tempfile.TemporaryDirectory(prefix="tm-true-split-smoke-") as tmpdir:
            workspace = pathlib.Path(tmpdir)
            public_core = workspace / "public-core"
            instance_root = workspace / "private-instance"
            venv = workspace / ".venv"
            instance_root.mkdir(parents=True, exist_ok=True)
            publish_func(public_core)
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                check=True,
                timeout=60,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            tm = venv / ("Scripts/tm.exe" if os.name == "nt" else "bin/tm")
            subprocess.run(
                [str(python), "-m", "pip", "install", "-q", str(public_core)],
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["TIGERMEMORY_INSTANCE_ROOT"] = str(instance_root)
            env["TIGERMEMORY_PROFILE"] = "local"
            env.pop("TIGERMEMORY_ROOT", None)
            env.pop("MEM0_API_KEY", None)
            env.pop("MEM0_BASE_URL", None)
            env.pop("MEM0_URL", None)
            env.pop("MEM0_USER_ID", None)
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run(
                [str(tm), "init", "--profile", "local"],
                cwd=workspace,
                env=env,
                check=True,
                timeout=30,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            write = subprocess.run(
                [str(tm), "write-memory", "--agent", "codex", "--topic", "systems"],
                cwd=workspace,
                env=env,
                input="true split smoke memory",
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=30,
            )
            memory_id = json.loads(write.stdout)["id"]
            subprocess.run(
                [str(tm), "verify", "--id", memory_id, "--terms", "true split smoke"],
                cwd=workspace,
                env=env,
                check=True,
                timeout=30,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return (instance_root / "runtime" / "tigermemory" / "profile.env").is_file()
    except Exception:
        return False


def run_public_core_source_update_smoke(repo_root, publish_func) -> bool:
    """Verify a public-core source checkout can fast-forward without touching instance data."""

    try:
        with tempfile.TemporaryDirectory(prefix="tm-source-update-smoke-") as tmpdir:
            workspace = pathlib.Path(tmpdir)
            seed = workspace / "seed-public-core"
            remote = workspace / "remote.git"
            checkout = workspace / "checkout"
            instance_root = workspace / "private-instance"
            venv = workspace / ".venv"
            instance_root.mkdir(parents=True, exist_ok=True)

            publish_func(seed)
            _run_checked(["git", "init", str(seed)], timeout=30)
            _configure_git_identity(seed)
            _git(seed, "add", ".")
            _git(seed, "commit", "-m", "initial public core", timeout=60)
            _git(seed, "branch", "-M", "master")
            _run_checked(["git", "init", "--bare", str(remote)], timeout=30)
            _git(seed, "remote", "add", "origin", str(remote))
            _git(seed, "push", "-u", "origin", "master", timeout=60)
            _run_checked(["git", "clone", str(remote), str(checkout)], timeout=60)

            _run_checked([sys.executable, "-m", "venv", str(venv)], timeout=60)
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            tm = venv / ("Scripts/tm.exe" if os.name == "nt" else "bin/tm")
            _run_checked([str(python), "-m", "pip", "install", "-q", "-e", str(checkout)], timeout=180)

            env = dict(os.environ)
            env["TIGERMEMORY_APP_ROOT"] = str(checkout)
            env["TIGERMEMORY_INSTANCE_ROOT"] = str(instance_root)
            env["TIGERMEMORY_PROFILE"] = "local"
            env.pop("TIGERMEMORY_ROOT", None)
            env.pop("MEM0_API_KEY", None)
            env.pop("MEM0_BASE_URL", None)
            env.pop("MEM0_URL", None)
            env.pop("MEM0_USER_ID", None)
            env.pop("PYTHONPATH", None)
            env["PYTHONIOENCODING"] = "utf-8"

            _run_checked([str(tm), "init", "--profile", "local"], cwd=checkout, env=env, timeout=60)
            marker = seed / "SOURCE_UPDATE_SMOKE.txt"
            marker.write_text("source update smoke\n", encoding="utf-8")
            _git(seed, "add", "SOURCE_UPDATE_SMOKE.txt")
            _git(seed, "commit", "-m", "add source update smoke", timeout=60)
            _git(seed, "push", timeout=60)

            check = _run_checked([str(tm), "update", "check", "--json"], cwd=checkout, env=env, timeout=60)
            check_payload = json.loads(check.stdout)
            if not (
                check_payload.get("schema") == "tigermemory-update-status-v1"
                and check_payload.get("update_available") is True
                and check_payload.get("safe_to_apply") is True
            ):
                return False

            applied = _run_checked(
                [str(tm), "update", "apply", "--strategy", "ff-only", "--json"],
                cwd=checkout,
                env=env,
                timeout=120,
            )
            applied_payload = json.loads(applied.stdout)
            if not (applied_payload.get("ok") is True and applied_payload.get("applied") is True):
                return False
            return (
                (checkout / "SOURCE_UPDATE_SMOKE.txt").is_file()
                and (instance_root / "runtime" / "tigermemory" / "profile.env").is_file()
            )
    except Exception:
        return False
