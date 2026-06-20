from __future__ import annotations

import io
import pathlib
import json
import os
import subprocess
import sys
import types


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import tigermemory_cli


def _cli_subprocess_env(root: pathlib.Path) -> dict[str, str]:
    package_paths = [
        str(REPO_ROOT),
        *[str(src) for src in sorted((REPO_ROOT / "packages").glob("*/src")) if src.is_dir()],
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(package_paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"
    env["TIGERMEMORY_INSTANCE_ROOT"] = str(root)
    env["TIGERMEMORY_ROOT"] = str(root)
    return env


def _snapshot_subprocess_env(root: pathlib.Path) -> dict[str, str]:
    package_paths = [
        str(root),
        *[str(src) for src in sorted((root / "packages").glob("*/src")) if src.is_dir()],
    ]
    env = dict(os.environ)
    env.pop("TIGERMEMORY_ROOT", None)
    env.pop("TIGERMEMORY_PROFILE", None)
    env.pop("TIGERMEMORY_LOCAL_DB", None)
    env["PYTHONPATH"] = os.pathsep.join(package_paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_detect_repo_root_honors_explicit_env_for_empty_local_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TIGERMEMORY_ROOT", str(tmp_path))

    assert tigermemory_cli._detect_repo_root() == tmp_path.resolve()


def test_detect_repo_root_prefers_cli_checkout_over_parent_config_root(tmp_path, monkeypatch) -> None:
    parent = tmp_path / "parent"
    snapshot = parent / "snapshot"
    (parent / ".git").mkdir(parents=True)
    (parent / "tools").mkdir()
    (parent / "wiki").mkdir()
    (snapshot / "tools").mkdir(parents=True)
    (snapshot / "wiki").mkdir()
    cli_path = snapshot / "tigermemory_cli.py"
    cli_path.write_text("# snapshot cli marker\n", encoding="utf-8")

    fake_config = types.ModuleType("tigermemory_config")
    fake_config._detect_repo_root = lambda: parent
    monkeypatch.setitem(sys.modules, "tigermemory_config", fake_config)
    monkeypatch.setattr(tigermemory_cli, "__file__", str(cli_path))

    assert tigermemory_cli._detect_repo_root() == snapshot.resolve()


def test_detect_repo_root_prefers_cwd_snapshot_over_parent_config_root_for_wheel_install(
    tmp_path, monkeypatch
) -> None:
    parent = tmp_path / "parent"
    snapshot = parent / "snapshot"
    site_packages = parent / ".tmp" / "venv" / "Lib" / "site-packages"
    (parent / ".git").mkdir(parents=True)
    (parent / "tools").mkdir()
    (parent / "wiki").mkdir()
    (parent / "tigermemory_cli.py").write_text("# private parent cli marker\n", encoding="utf-8")
    (snapshot / "tools").mkdir(parents=True)
    (snapshot / "wiki").mkdir()
    site_packages.mkdir(parents=True)
    cli_path = site_packages / "tigermemory_cli.py"
    cli_path.write_text("# installed cli marker\n", encoding="utf-8")

    fake_config = types.ModuleType("tigermemory_config")
    fake_config._detect_repo_root = lambda: parent
    monkeypatch.setitem(sys.modules, "tigermemory_config", fake_config)
    monkeypatch.setattr(tigermemory_cli, "__file__", str(cli_path))
    monkeypatch.chdir(snapshot)

    assert tigermemory_cli._detect_repo_root() == snapshot.resolve()


def test_subprocess_env_pins_detected_repo_root_for_child_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tigermemory_cli, "resolve_instance_root", None)
    monkeypatch.setattr(tigermemory_cli, "subprocess_root_env", None)
    monkeypatch.delenv("TIGERMEMORY_ROOT", raising=False)

    env = tigermemory_cli._subprocess_env()

    assert env["TIGERMEMORY_ROOT"] == str(tmp_path)
    assert env["TIGERMEMORY_INSTANCE_ROOT"] == str(tmp_path)
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_cli_init_uses_explicit_instance_root_even_when_app_root_differs(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    app_root = tmp_path / "public-core"
    instance_root = tmp_path / "private-instance"
    app_root.mkdir()
    instance_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))
    monkeypatch.setenv("TIGERMEMORY_INSTANCE_ROOT", str(instance_root))
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", instance_root)

    assert tigermemory_cli.main(["init", "--profile", "local"]) == 0

    out = capsys.readouterr().out
    assert f"root={instance_root.resolve()}" in out
    assert (instance_root / "runtime" / "tigermemory" / "profile.env").is_file()
    assert not (app_root / "runtime" / "tigermemory" / "profile.env").exists()


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


def test_profile_guide_local_explains_no_advanced_dependencies(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)

    assert tigermemory_cli.main(["profile", "guide", "local"]) == 0

    out = capsys.readouterr().out
    assert "mode=basic" in out
    assert "does_not_require=WSL,Docker,OpenMemory,Qdrant,Caddy,npm" in out


def test_llm_status_reports_provider_presence_without_secret(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    assert tigermemory_cli.main(["llm", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "tigermemory-llm-status-v1"
    assert payload["recommended_provider"] == "deepseek"
    assert payload["llm_configured"] is True
    deepseek = payload["providers"][0]
    assert deepseek["id"] == "deepseek"
    assert deepseek["configured"] is True
    assert deepseek["api_key"] == {"name": "DEEPSEEK_API_KEY", "configured": True}
    assert "sk-test-secret-value" not in json.dumps(payload)


def test_llm_guide_points_to_deepseek(capsys) -> None:
    assert tigermemory_cli.main(["llm", "guide"]) == 0

    out = capsys.readouterr().out
    assert "recommended_provider=deepseek" in out
    assert "set=DEEPSEEK_API_KEY" in out
    assert "fallback=tm ask --offline returns local evidence only" in out


def test_admin_guide_explains_proposal_first_flow(capsys) -> None:
    assert tigermemory_cli.main(["admin", "guide"]) == 0

    out = capsys.readouterr().out
    assert "propose=" in out
    assert "approve=" in out
    assert "propose only writes runtime proposals" in out


def test_admin_propose_and_approve_roundtrip(tmp_path, monkeypatch, capsys) -> None:
    fake_core = types.ModuleType("tigermemory_core")

    def fake_propose_wiki_admin_page(text, *, partition, title, source, timeout):
        assert "source material" in text
        assert partition == "systems"
        assert title == "Starter Admin"
        return {
            "schema": "tigermemory-admin-proposal-v1",
            "should_write": True,
            "partition": partition,
            "title": title,
            "slug": "starter-admin",
            "target_path": "wiki/systems/starter-admin.md",
            "action": "create",
            "summary": "Admin summary",
            "rationale": "Useful durable note.",
            "confidence": 91,
            "evidence_refs": [source],
            "wiki_markdown": "---\nowner: human\nstatus: active\nupdated: 2026-06-20\n---\n\n# Starter Admin\n\n## 摘要\n\nAdmin summary.\n\n## 来源\n\n- stdin\n",
            "user_review_required": True,
        }

    fake_core.propose_wiki_admin_page = fake_propose_wiki_admin_page
    monkeypatch.setitem(sys.modules, "tigermemory_core", fake_core)
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("source material worth preserving in a wiki proposal"))

    assert tigermemory_cli.main(["admin", "propose", "--partition", "systems", "--title", "Starter Admin", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    proposal_id = payload["id"]
    assert payload["status"] == "pending"
    assert payload["user_review_required"] is True
    assert (tmp_path / "runtime" / "tigermemory" / "admin-proposals" / f"{proposal_id}.json").is_file()
    assert not (tmp_path / "wiki" / "systems" / "starter-admin.md").exists()

    assert tigermemory_cli.main(["admin", "approve", proposal_id, "--json"]) == 0

    approved = json.loads(capsys.readouterr().out)
    assert approved["status"] == "approved"
    page = tmp_path / "wiki" / "systems" / "starter-admin.md"
    assert page.is_file()
    assert "Admin summary" in page.read_text(encoding="utf-8")
    stored = json.loads((tmp_path / "runtime" / "tigermemory" / "admin-proposals" / f"{proposal_id}.json").read_text(encoding="utf-8"))
    assert stored["status"] == "approved"


def test_admin_approve_refuses_existing_target_without_force(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", tmp_path)
    target = tmp_path / "wiki" / "systems" / "existing.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Existing\n", encoding="utf-8")
    proposal = {
        "schema": "tigermemory-admin-proposal-v1",
        "id": "unit-existing",
        "status": "pending",
        "should_write": True,
        "title": "Existing",
        "target_path": "wiki/systems/existing.md",
        "wiki_markdown": "---\nowner: human\nstatus: active\nupdated: 2026-06-20\n---\n\n# Existing\n\n## 摘要\n\nNew.\n\n## 来源\n\n- test\n",
    }
    tigermemory_cli._admin_write_proposal(proposal)

    assert tigermemory_cli.main(["admin", "approve", "unit-existing"]) == 2

    err = capsys.readouterr().err
    assert "target exists" in err
    assert target.read_text(encoding="utf-8") == "# Existing\n"


def test_dashboard_defaults_to_public_quickstart_port(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(rel_path: str, args: list[str], cwd: pathlib.Path | None = None) -> int:
        calls.append((rel_path, args))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main(["dashboard"]) == 0

    assert calls == [(str(REPO_ROOT / "tools" / "tm_review_ui.py"), ["--port", "9777"])]


def test_dashboard_accepts_explicit_private_service_port(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(rel_path: str, args: list[str], cwd: pathlib.Path | None = None) -> int:
        calls.append((rel_path, args))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main(["dashboard", "--host", "127.0.0.1", "--port", "1998"]) == 0

    assert calls == [(str(REPO_ROOT / "tools" / "tm_review_ui.py"), ["--host", "127.0.0.1", "--port", "1998"])]


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


def test_dashboard_help_mentions_public_default_port() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "dashboard", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0
    assert "default: 9777" in result.stdout


def test_update_status_uses_app_root(monkeypatch, tmp_path, capsys) -> None:
    app_root = tmp_path / "app-source"
    instance_root = tmp_path / "private-instance"
    app_root.mkdir()
    instance_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))
    monkeypatch.setenv("TIGERMEMORY_INSTANCE_ROOT", str(instance_root))

    calls: list[tuple[pathlib.Path, bool]] = []
    fake_update = types.ModuleType("tigermemory_update")

    def fake_status(root, refresh_remote=False):
        calls.append((pathlib.Path(root), refresh_remote))
        return {
            "ok": True,
            "source_mode": "git_source",
            "app_root": str(root),
            "branch": "master",
            "head": "abc",
            "upstream": "origin/master",
            "behind": 0,
            "ahead": 0,
            "dirty": False,
            "update_available": False,
            "safe_to_apply": False,
            "recommended_action": "Already up to date.",
            "warnings": [],
        }

    fake_update.get_update_status = fake_status
    fake_update.apply_update = lambda *_args, **_kwargs: {}
    monkeypatch.setitem(sys.modules, "tigermemory_update", fake_update)

    assert tigermemory_cli.main(["update", "status"]) == 0

    out = capsys.readouterr().out
    assert "source_mode=git_source" in out
    assert calls == [(app_root.resolve(), False)]


def test_update_check_refreshes_remote(monkeypatch, tmp_path, capsys) -> None:
    app_root = tmp_path / "app-source"
    app_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))

    calls: list[bool] = []
    fake_update = types.ModuleType("tigermemory_update")

    def fake_status(_root, refresh_remote=False):
        calls.append(refresh_remote)
        return {"ok": True, "source_mode": "git_source", "recommended_action": "ok"}

    fake_update.get_update_status = fake_status
    fake_update.apply_update = lambda *_args, **_kwargs: {}
    monkeypatch.setitem(sys.modules, "tigermemory_update", fake_update)

    assert tigermemory_cli.main(["update", "check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_mode"] == "git_source"
    assert calls == [True]


def test_update_apply_dry_run_returns_nonzero_when_not_safe(monkeypatch, tmp_path, capsys) -> None:
    app_root = tmp_path / "app-source"
    app_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))

    calls: list[tuple[pathlib.Path, str, bool]] = []
    fake_update = types.ModuleType("tigermemory_update")
    fake_update.get_update_status = lambda *_args, **_kwargs: {}

    def fake_apply(root, strategy="ff-only", dry_run=False):
        calls.append((pathlib.Path(root), strategy, dry_run))
        return {
            "ok": False,
            "applied": False,
            "reason": "dirty_worktree",
            "status": {"recommended_action": "Commit local edits first."},
        }

    fake_update.apply_update = fake_apply
    monkeypatch.setitem(sys.modules, "tigermemory_update", fake_update)

    assert tigermemory_cli.main(["update", "apply", "--dry-run"]) == 4

    out = capsys.readouterr().out
    assert "reason=dirty_worktree" in out
    assert calls == [(app_root.resolve(), "ff-only", True)]


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


def test_installed_style_local_cli_does_not_require_tools_dir(tmp_path) -> None:
    env = _cli_subprocess_env(tmp_path)

    init = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "init"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    assert f"root={tmp_path.resolve()}" in init.stdout

    write = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "write-memory", "--agent", "codex", "--topic", "systems"],
        cwd=tmp_path,
        input="installed local mode recall",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert write.returncode == 0, write.stderr
    memory_id = json.loads(write.stdout)["id"]

    search = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--query", "installed local mode"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert search.returncode == 0, search.stderr
    assert memory_id in search.stdout

    verify = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "verify", "--id", memory_id, "--terms", "installed local"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    assert '"direct_readback_ok": true' in verify.stdout


def test_installed_core_cli_can_use_external_instance_root(tmp_path) -> None:
    instance_root = tmp_path / "instance"
    instance_root.mkdir()
    env = _cli_subprocess_env(instance_root)
    env.pop("TIGERMEMORY_ROOT", None)

    init = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "init", "--profile", "local"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert init.returncode == 0, init.stderr
    assert f"root={instance_root.resolve()}" in init.stdout
    assert (instance_root / "data" / "tigermemory").is_dir()
    assert (instance_root / "runtime" / "tigermemory" / "profile.env").is_file()


def test_installed_core_cli_prefers_instance_root_over_legacy_root(tmp_path) -> None:
    instance_root = tmp_path / "instance"
    legacy_root = tmp_path / "legacy"
    instance_root.mkdir()
    legacy_root.mkdir()
    env = _cli_subprocess_env(instance_root)
    env["TIGERMEMORY_INSTANCE_ROOT"] = str(instance_root)
    env["TIGERMEMORY_ROOT"] = str(legacy_root)

    init = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "init", "--profile", "local"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert init.returncode == 0, init.stderr
    assert f"root={instance_root.resolve()}" in init.stdout
    assert (instance_root / "runtime" / "tigermemory" / "profile.env").is_file()
    assert not (legacy_root / "runtime" / "tigermemory" / "profile.env").exists()


def test_installed_style_cli_searches_wiki_without_mem0(tmp_path) -> None:
    env = _cli_subprocess_env(tmp_path)
    page = tmp_path / "wiki" / "systems" / "starter-search.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "\n".join([
            "---",
            'title: "Starter Search"',
            "updated: 2026-05-31",
            "owner: codex",
            "status: active",
            "---",
            "",
            "# Starter Search",
            "",
            "Local-first wiki recall works without Mem0 or Docker.",
        ]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "wiki", "--query", "local-first recall"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["search_backend"] == "wiki_hybrid"
    assert payload["results"][0]["path"] == "wiki/systems/starter-search.md"


def test_installed_style_cli_search_all_groups_memory_and_wiki(tmp_path) -> None:
    env = _cli_subprocess_env(tmp_path)
    env["TIGERMEMORY_PROFILE"] = "local"
    page = tmp_path / "wiki" / "systems" / "starter-all-search.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Starter All Search\n\nHybrid local search can read wiki pages.", encoding="utf-8")

    write = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "write-memory", "--agent", "codex", "--topic", "systems"],
        cwd=tmp_path,
        input="hybrid local event memory",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert write.returncode == 0, write.stderr

    result = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "all", "--query", "hybrid local"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["scope"] == "all"
    assert payload["memory"]["count"] >= 1
    assert payload["wiki"]["count"] >= 1


def test_installed_style_cli_searches_local_memory_with_chinese_query(tmp_path) -> None:
    env = _cli_subprocess_env(tmp_path)
    env["TIGERMEMORY_PROFILE"] = "local"
    db = tmp_path / "memory.sqlite"

    write = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "write-memory", "--agent", "codex", "--topic", "systems", "--db", str(db)],
        cwd=tmp_path,
        input="虎哥的偏好是先看已验证事实，再看推断。",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert write.returncode == 0, write.stderr
    memory_id = json.loads(write.stdout)["id"]

    result = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "memory", "--query", "虎哥偏好", "--db", str(db)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["id"] == memory_id

    short = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "memory", "--query", "偏好", "--db", str(db)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert short.returncode == 0, short.stderr
    assert json.loads(short.stdout)["results"][0]["id"] == memory_id


def test_installed_style_cli_searches_wiki_chinese_without_embedding(tmp_path) -> None:
    env = _cli_subprocess_env(tmp_path)
    env["EMBEDDING_BASE_URL"] = ""
    page = tmp_path / "wiki" / "systems" / "starter-cn-search.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "\n".join([
            "---",
            'title: "本地中文检索"',
            "updated: 2026-06-13",
            "owner: codex",
            "status: active",
            "---",
            "",
            "# 本地中文检索",
            "",
            "TigerMemory 本地模式可以不用 Docker 搜索中文资料。",
        ]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "wiki", "--query", "中文资料"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["path"] == "wiki/systems/starter-cn-search.md"


def test_installed_style_cli_ask_offline_returns_local_evidence(tmp_path) -> None:
    env = _cli_subprocess_env(tmp_path)
    db = tmp_path / "memory.sqlite"
    page = tmp_path / "wiki" / "systems" / "starter-offline-ask.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "# 离线问答入口\n\n离线模式只组织本地依据，不调用在线模型。",
        encoding="utf-8",
    )

    write = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "write-memory", "--agent", "codex", "--topic", "systems", "--db", str(db)],
        cwd=tmp_path,
        input="离线问答入口可以读取本地记忆证据。",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env | {"TIGERMEMORY_PROFILE": "local"},
        timeout=20,
        check=False,
    )
    assert write.returncode == 0, write.stderr

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tigermemory_cli",
            "ask",
            "--offline",
            "--scope",
            "all",
            "--query",
            "离线问答入口",
            "--db",
            str(db),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env | {"TIGERMEMORY_PROFILE": "hybrid"},
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["offline"] is True
    assert "不生成 AI 总结" in payload["answer"]
    assert payload["memory"]["count"] >= 1
    assert payload["wiki"]["count"] >= 1
    assert {item["source"] for item in payload["evidence"]} >= {"memory", "wiki"}


def test_cli_ask_offline_forces_local_profile_before_search(monkeypatch, capsys) -> None:
    import tigermemory_core as tm_core

    monkeypatch.setenv("TIGERMEMORY_PROFILE", "hybrid")

    def fail_online_request(*_args, **_kwargs):
        raise AssertionError("offline ask must not call online Mem0")

    def fake_mem0_search(query: str, size: int = 5, *_args, **_kwargs) -> str:
        assert os.environ["TIGERMEMORY_PROFILE"] == "local"
        return json.dumps({
            "count": 1,
            "items": [{"id": "m1", "text": "虎哥本地证据", "topic": "systems", "source_agent": "codex"}],
            "results": [{"id": "m1", "text": "虎哥本地证据", "topic": "systems", "source_agent": "codex"}],
            "search_backend": "local",
        }, ensure_ascii=False)

    monkeypatch.setattr(tm_core, "mem0_request", fail_online_request)
    monkeypatch.setattr(tm_core, "mem0_search", fake_mem0_search)
    monkeypatch.setattr(tm_core, "search_wiki", lambda *_args, **_kwargs: [])

    assert tigermemory_cli.main(["ask", "--offline", "--scope", "memory", "--query", "虎哥"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["offline"] is True
    assert payload["memory"]["count"] == 1


def test_published_snapshot_cli_detects_root_without_env(tmp_path) -> None:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "tigermemory-publish" / "src"))
    import tigermemory_publish

    snapshot = tmp_path / "snapshot"
    plan = tigermemory_publish.collect_publish_plan(REPO_ROOT)
    tigermemory_publish.execute_plan(plan, REPO_ROOT, snapshot)
    env = _snapshot_subprocess_env(snapshot)

    init = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "init"],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    assert f"root={snapshot.resolve()}" in init.stdout

    write = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "write-memory", "--agent", "codex", "--topic", "systems"],
        cwd=snapshot,
        input="published snapshot local recall",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert write.returncode == 0, write.stderr
    memory_id = json.loads(write.stdout)["id"]

    memory = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--query", "snapshot local recall", "--size", "3"],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert memory.returncode == 0, memory.stderr
    assert memory_id in memory.stdout

    wiki = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "wiki", "--query", "Project Canvas", "--size", "3"],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert wiki.returncode == 0, wiki.stderr
    wiki_payload = json.loads(wiki.stdout)
    assert wiki_payload["results"][0]["path"] == "wiki/operations/project-canvas.md"

    dashboard_help = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "dashboard", "--help"],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert dashboard_help.returncode == 0, dashboard_help.stderr
    assert "default: 9777" in dashboard_help.stdout

    wiki_cn = subprocess.run(
        [sys.executable, "-m", "tigermemory_cli", "search", "--scope", "wiki", "--query", "项目画布", "--size", "3"],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert wiki_cn.returncode == 0, wiki_cn.stderr
    wiki_cn_payload = json.loads(wiki_cn.stdout)
    assert wiki_cn_payload["results"][0]["path"] == "wiki/operations/project-canvas.md"

    ask = subprocess.run(
        [
            sys.executable,
            "-m",
            "tigermemory_cli",
            "ask",
            "--offline",
            "--scope",
            "memory",
            "--query",
            "published snapshot local recall",
        ],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert ask.returncode == 0, ask.stderr
    ask_payload = json.loads(ask.stdout)
    assert ask_payload["offline"] is True
    assert ask_payload["memory"]["results"][0]["id"] == memory_id

    ask_wiki_cn = subprocess.run(
        [
            sys.executable,
            "-m",
            "tigermemory_cli",
            "ask",
            "--offline",
            "--scope",
            "wiki",
            "--query",
            "项目画布",
        ],
        cwd=snapshot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=20,
        check=False,
    )
    assert ask_wiki_cn.returncode == 0, ask_wiki_cn.stderr
    ask_wiki_cn_payload = json.loads(ask_wiki_cn.stdout)
    assert ask_wiki_cn_payload["offline"] is True
    assert ask_wiki_cn_payload["wiki"]["results"][0]["path"] == "wiki/operations/project-canvas.md"


def test_publish_is_maintainer_only_when_app_tools_are_missing(tmp_path, monkeypatch, capsys) -> None:
    instance_root = tmp_path / "private-instance"
    app_root = tmp_path / "installed-package"
    instance_root.mkdir()
    app_root.mkdir()
    monkeypatch.setenv("TIGERMEMORY_INSTANCE_ROOT", str(instance_root))
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", instance_root)

    rc = tigermemory_cli.main([
        "publish",
        "--dry-run",
        "--json",
        "--target",
        "public-core",
        "--split-report",
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "maintainer-only" in captured.err
    assert not (instance_root / "tools").exists()


def test_publish_uses_app_root_tools_not_instance_root_tools(tmp_path, monkeypatch) -> None:
    instance_root = tmp_path / "private-instance"
    app_root = tmp_path / "app-source"
    tools_dir = app_root / "tools"
    instance_root.mkdir()
    tools_dir.mkdir(parents=True)
    script = tools_dir / "tm_io.py"
    script.write_text(
        "import sys\nprint('APP_ROOT_TOOL:' + ' '.join(sys.argv[1:]))\nsys.exit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TIGERMEMORY_INSTANCE_ROOT", str(instance_root))
    monkeypatch.setenv("TIGERMEMORY_APP_ROOT", str(app_root))
    monkeypatch.setattr(tigermemory_cli, "REPO_ROOT", instance_root)
    calls: list[tuple[str, list[str], str | None]] = []

    def fake_run(rel_path: str, args: list[str], cwd: pathlib.Path | None = None) -> int:
        calls.append((rel_path, args, str(cwd) if cwd is not None else None))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main([
        "publish",
        "--dry-run",
        "--json",
        "--target",
        "public-core",
        "--split-report",
    ]) == 0
    assert calls == [(str(script), ["publish", "--dry-run", "--json", "--target", "public-core", "--split-report"], str(app_root))]
    assert not (instance_root / "tools").exists()
