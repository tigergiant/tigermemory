from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient

from tigermemory_dashboard import server


HOST = {"Host": "127.0.0.1:9777"}


def test_api_update_status_is_read_only(tmp_path, monkeypatch):
    calls = []

    class FakeUpdate:
        @staticmethod
        def get_update_status(root, refresh_remote=False):
            calls.append((pathlib.Path(root), refresh_remote))
            return {
                "ok": True,
                "source_mode": "git_source",
                "app_root": str(root),
                "update_available": True,
                "safe_to_apply": True,
                "recommended_action": "Run tm update apply.",
            }

    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setattr(server, "tm_update", FakeUpdate)
    monkeypatch.setattr(server, "resolve_app_root", lambda: app_root)
    monkeypatch.setattr(server, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(server, "ensure_write_ready", lambda *_args, **_kwargs: None)
    client = TestClient(server.app)
    client.get("/", headers=HOST, follow_redirects=False)

    response = client.get("/api/update/status?refresh=true", headers=HOST)

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_mode"] == "git_source"
    assert calls == [(app_root, False)]


def test_dashboard_static_surfaces_public_ask_llm_status() -> None:
    script = pathlib.Path(
        "packages/tigermemory-dashboard/src/tigermemory_dashboard/static/dashboard-pages.js"
    ).read_text(encoding="utf-8")

    assert "public_ask_llm" in script
    assert "在线问答" in script
    assert "tm llm status --json" in script
    assert "tm ask --offline" in script


def test_dashboard_fast_agent_doctor_includes_public_ask_llm(monkeypatch) -> None:
    monkeypatch.setattr(server, "_dashboard_worktree_check", lambda: {"name": "worktree", "status": "ok", "ok": True})
    monkeypatch.setattr(server.tm_agent_doctor, "check_tm_http", lambda timeout=0.3: {"name": "tm_http", "status": "ok", "ok": True})
    monkeypatch.setattr(server.tm_agent_doctor, "check_mem0", lambda timeout=0.5: {"name": "mem0_api", "status": "ok", "ok": True})
    monkeypatch.setattr(
        server.tm_agent_doctor,
        "check_public_ask_llm",
        lambda: {
            "name": "public_ask_llm",
            "status": "ok",
            "ok": True,
            "llm_configured": True,
            "routine_model": "deepseek-v4-flash",
        },
    )

    report = server._dashboard_fast_agent_doctor()

    checks = {check["name"]: check for check in report["checks"]}
    assert report["status"] == "ok"
    assert checks["public_ask_llm"]["llm_configured"] is True
