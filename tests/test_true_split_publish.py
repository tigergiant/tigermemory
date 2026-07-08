from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-publish" / "src"))

import tigermemory_publish


def _build_fake_repo(root: pathlib.Path) -> None:
    from tests.test_tm_publish import _build_fake_repo as build_snapshot_repo

    build_snapshot_repo(root)
    for checks in tigermemory_publish.module_checks().values():
        for check in checks:
            path = root / check
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# module check placeholder\n", encoding="utf-8")


def _publish_public_core(dest: pathlib.Path, repo_root: pathlib.Path) -> None:
    plan = tigermemory_publish.collect_publish_plan(repo_root)
    tigermemory_publish.execute_plan(plan, repo_root, dest)
    dest.joinpath("pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["setuptools>=68", "wheel"]',
                'build-backend = "setuptools.build_meta"',
                "",
                "[project]",
                'name = "tigermemory"',
                'version = "0.0.0"',
                'description = "TigerMemory public core test fixture"',
                'license = { text = "MIT" }',
                "",
                "[project.scripts]",
                'tm = "tigermemory_cli:main"',
                "",
                "[tool.setuptools]",
                'py-modules = ["tigermemory_cli"]',
                "include-package-data = true",
                "",
                "[tool.setuptools.packages.find]",
                "where = [",
                '  "packages/tigermemory-answer/src",',
                '  "packages/tigermemory-config/src",',
                '  "packages/tigermemory-core/src",',
                '  "packages/tigermemory-digest/src",',
                '  "packages/tigermemory-doctor/src",',
                '  "packages/tigermemory-index/src",',
                '  "packages/tigermemory-lessons/src",',
                '  "packages/tigermemory-persona/src",',
                '  "packages/tigermemory-protocols/src",',
                '  "packages/tigermemory-route/src",',
                '  "packages/tigermemory-search/src",',
                '  "packages/tigermemory-update/src",',
                "]",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_public_core_export_reports_true_split_boundary(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    monkeypatch.setattr(tigermemory_publish, "REPO_ROOT", repo)

    rc = tigermemory_publish.main([
        "--dest",
        str(tmp_path / "out"),
        "--dry-run",
        "--json",
        "--target",
        "public-core",
        "--split-report",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["target"] == "public-core"
    assert payload["split_report"]["target"] == "public-core"
    assert payload["split_report"]["public_core_independent"] is False
    assert payload["split_report"]["public_core_independence_reason"] == "smoke_not_run"
    assert payload["split_report"]["private_instance_required"] is False
    assert "wiki/investment" in payload["split_report"]["private_surfaces"]
    assert "runtime" in payload["split_report"]["private_surfaces"]


def test_tm_io_publish_forwards_true_split_flags(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_fake_repo(repo)
    env = dict(os.environ)
    env["TIGERMEMORY_ROOT"] = str(repo)
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [
            sys.executable,
            "tools/tm_io.py",
            "publish",
            "--dry-run",
            "--json",
            "--target",
            "public-core",
            "--split-report",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["target"] == "public-core"
    assert payload["split_report"]["target"] == "public-core"


def test_public_core_snapshot_can_use_external_instance_root(tmp_path: pathlib.Path) -> None:
    public_core = tmp_path / "public-core"
    instance_root = tmp_path / "instance"
    instance_root.mkdir()

    _publish_public_core(public_core, REPO_ROOT)

    venv = tmp_path / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=60)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    tm = venv / ("Scripts/tm.exe" if os.name == "nt" else "bin/tm")
    subprocess.run([str(python), "-m", "pip", "install", "-q", str(public_core)], check=True, timeout=120)

    env = dict(os.environ)
    env["TIGERMEMORY_INSTANCE_ROOT"] = str(instance_root)
    env["TIGERMEMORY_PROFILE"] = "local"
    env.pop("TIGERMEMORY_ROOT", None)
    env.pop("MEM0_API_KEY", None)
    env.pop("MEM0_BASE_URL", None)
    env.pop("MEM0_URL", None)
    env.pop("MEM0_USER_ID", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"

    init = subprocess.run(
        [str(tm), "init", "--profile", "local"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    assert (instance_root / "runtime" / "tigermemory" / "profile.env").is_file()

    update_status = subprocess.run(
        [str(tm), "update", "status", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert update_status.returncode == 0, update_status.stderr
    update_status_payload = json.loads(update_status.stdout)
    assert update_status_payload["schema"] == "tigermemory-update-status-v1"
    assert update_status_payload["source_mode"] in {"installed_package", "git_source", "editable_install"}

    update_apply = subprocess.run(
        [str(tm), "update", "apply", "--dry-run", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert update_apply.returncode in {0, 4}, update_apply.stderr
    update_apply_payload = json.loads(update_apply.stdout)
    assert update_apply_payload["applied"] is False
    assert update_apply_payload["reason"] in {"dry_run", "missing_upstream", "up_to_date"}

    write = subprocess.run(
        [str(tm), "write-memory", "--agent", "codex", "--topic", "systems"],
        cwd=tmp_path,
        env=env,
        input="true split smoke memory",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert write.returncode == 0, write.stderr
    memory_id = json.loads(write.stdout)["id"]

    verify = subprocess.run(
        [str(tm), "verify", "--id", memory_id, "--terms", "true split smoke"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr


def test_public_core_source_checkout_can_apply_fast_forward_update(tmp_path: pathlib.Path) -> None:
    from tigermemory_publish.split import run_public_core_source_update_smoke

    assert run_public_core_source_update_smoke(
        repo_root=REPO_ROOT,
        publish_func=lambda dest: _publish_public_core(dest, REPO_ROOT),
    )
