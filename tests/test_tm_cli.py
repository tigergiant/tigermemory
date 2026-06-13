from __future__ import annotations

import pathlib
import json
import os
import subprocess
import sys


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


def test_dashboard_defaults_to_public_quickstart_port(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(rel_path: str, args: list[str]) -> int:
        calls.append((rel_path, args))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main(["dashboard"]) == 0

    assert calls == [("tools/tm_review_ui.py", ["--port", "9777"])]


def test_dashboard_accepts_explicit_private_service_port(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run(rel_path: str, args: list[str]) -> int:
        calls.append((rel_path, args))
        return 0

    monkeypatch.setattr(tigermemory_cli, "_run_python", fake_run)

    assert tigermemory_cli.main(["dashboard", "--host", "127.0.0.1", "--port", "1998"]) == 0

    assert calls == [("tools/tm_review_ui.py", ["--host", "127.0.0.1", "--port", "1998"])]


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
