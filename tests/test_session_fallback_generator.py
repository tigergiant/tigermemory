from __future__ import annotations

import importlib.util
import json
import pathlib
import urllib.request


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "session_fallback_generator",
        REPO_ROOT / "tools" / "session-fallback-generator.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_to_mem0_uses_tigermemory_http_router(monkeypatch):
    generator = load_generator()
    monkeypatch.delenv("TM_HTTP_URL", raising=False)
    monkeypatch.setattr(generator.tm_core, "mcp_api_key", lambda: "test-key")
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["auth"] = req.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert generator.write_to_mem0("fallback card", agent="codex") is True
    assert captured["url"] == "http://localhost:8790/write_memory"
    assert captured["method"] == "POST"
    assert captured["payload"] == {
        "agent": "codex",
        "topic": "systems",
        "text": "fallback card",
        "light": True,
    }
    assert captured["auth"] == "Bearer test-key"
    assert captured["timeout"] == 10


def test_write_to_mem0_respects_tm_http_url_override(monkeypatch):
    generator = load_generator()
    monkeypatch.setenv("TM_HTTP_URL", "http://127.0.0.1:9999/")
    monkeypatch.setattr(generator.tm_core, "mcp_api_key", lambda: "test-key")
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert generator.write_to_mem0("fallback card", agent="codex") is True
    assert captured["url"] == "http://127.0.0.1:9999/write_memory"


def test_write_headers_omits_auth_when_key_missing(monkeypatch):
    generator = load_generator()

    def missing_key():
        raise RuntimeError("missing test key")

    monkeypatch.setattr(generator.tm_core, "mcp_api_key", missing_key)

    assert generator.write_headers() == {"Content-Type": "application/json"}


def test_write_agent_from_pending_prefers_allowed_agent():
    generator = load_generator()

    assert generator.write_agent_from_pending({"agent": "hermes", "ide": "windsurf"}) == "hermes"
    assert generator.write_agent_from_pending({"ide": "codex"}) == "codex"
    assert generator.write_agent_from_pending({"agent": "cascade", "ide": "windsurf"}) == "codex"
    assert generator.write_agent_from_pending({"ide": "windsurf"}) == "codex"


def test_generate_card_includes_structured_repo_field():
    generator = load_generator()

    card = generator.generate_card([], {"session_id": "codex-20260608-0900", "ide": "codex"})

    assert "memory_type: session-handoff" in card
    assert f"repo: {generator.tm_core.REPO_ROOT}" in card


def test_generate_card_maps_windsurf_ide_to_cascade_agent():
    generator = load_generator()

    card = generator.generate_card(
        [],
        {"session_id": "windsurf-20260608-0900", "ide": "windsurf", "agent": "cascade"},
    )

    assert "ide: windsurf" in card
    assert "agent: cascade" in card
    assert "agent: windsurf" not in card
