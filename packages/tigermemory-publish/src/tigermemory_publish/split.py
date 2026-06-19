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
    private_instance_required: bool
    private_surfaces: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "target": self.target,
            "public_core_independent": self.public_core_independent,
            "public_core_independence_reason": self.public_core_independence_reason,
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
) -> dict[str, object]:
    if target == "public-core":
        public_core_independent = bool(boundary_ok and smoke_ok)
        return SplitReport(
            target=target,
            public_core_independent=public_core_independent,
            public_core_independence_reason=(
                "verified" if public_core_independent else "smoke_not_run" if boundary_ok else "boundary_failed"
            ),
            private_instance_required=False,
            private_surfaces=private_surfaces(),
        ).to_json()
    return SplitReport(
        target=target,
        public_core_independent=False,
        public_core_independence_reason="snapshot_compatibility_lane",
        private_instance_required=True,
        private_surfaces=private_surfaces(),
    ).to_json()


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
