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
