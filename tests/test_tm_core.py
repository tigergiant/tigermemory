from __future__ import annotations

import datetime
import json
import pathlib
import sys
from urllib.parse import parse_qs, urlparse

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_core  # type: ignore[import-not-found]


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return b'{"ok": true}'


class _FakeOpener:
    def __init__(self):
        self.open_calls = []

    def open(self, request, *, timeout):
        self.open_calls.append((request, timeout))
        return _FakeResponse()


def test_mem0_request_bypasses_default_proxy_opener(monkeypatch):
    fake_opener = _FakeOpener()

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("mem0_request must not use default urlopen")

    monkeypatch.setattr(tm_core, "mem0_key", lambda: "test-key")
    monkeypatch.setattr(tm_core.urllib.request, "urlopen", fail_urlopen)
    monkeypatch.setattr(tm_core.urllib.request, "build_opener", lambda *_handlers: fake_opener)

    raw = tm_core.mem0_request("http://localhost:8765/api/v1/memories/?user_id=tiger")

    assert raw == '{"ok": true}'
    assert len(fake_opener.open_calls) == 1
    request, timeout = fake_opener.open_calls[0]
    assert request.get_header("Authorization") == "Bearer test-key"
    assert timeout == tm_core.MEM0_READ_TIMEOUT


def test_mem0_search_uses_openmemory_search_query_param(monkeypatch):
    captured = {}

    def fake_request(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return '{"items": []}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    raw = tm_core.mem0_search("Inbox routing", size=7)

    assert raw == '{"items": []}'
    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["search_query"] == ["Inbox routing"]
    assert "query" not in qs
    assert qs["size"] == ["7"]
    assert qs["match_mode"] == ["id_first"]
    assert captured["timeout"] == tm_core.MEM0_READ_TIMEOUT


def test_mem0_search_allows_explicit_substring_match_mode(monkeypatch):
    captured = {}

    def fake_request(url, *, timeout):
        captured["url"] = url
        return '{"items": []}'

    monkeypatch.setattr(tm_core, "mem0_base", lambda: "http://localhost:8765")
    monkeypatch.setattr(tm_core, "mem0_request", fake_request)

    tm_core.mem0_search("legacy contiguous text", size=3, match_mode="substring")

    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs["match_mode"] == ["substring"]


def test_verify_memory_id_active_hit_with_digest(monkeypatch, tmp_path):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    created = int(datetime.datetime(2026, 5, 16, 3, 23, 5, tzinfo=tm_core.TZ_CN).timestamp())
    text = "2026-05-16 T-X3.5 000001.DAT 242 rows"
    digest = tmp_path / "inbox" / "daily" / "2026-05-16.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(f"memory_ids:\n  - {mem_id}\n", encoding="utf-8")

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: json.dumps({
        "id": mem_id,
        "text": text,
        "created_at": created,
        "state": "active",
        "metadata_": {"source": "codex", "topic": "systems"},
    }))

    def fake_search(query, size=5, match_mode="id_first"):
        assert match_mode == "id_first"
        return json.dumps({"items": [{"id": mem_id}]})

    monkeypatch.setattr(tm_core, "mem0_search", fake_search)

    result = tm_core.verify_memory_id(mem_id, key_terms="T-X3.5 000001.DAT 242 rows")

    assert result["status"] == "exists_active"
    assert result["direct_readback_ok"] is True
    assert result["search_by_id_self_hit"] is True
    assert result["search_by_terms_self_hit"] is True
    assert result["digest_date"] == "2026-05-16"
    assert result["digest_contains"] is True
    assert result["metadata"]["source"] == "codex"
    assert result["text_len"] == len(text)
    assert result["text_sha256_12"]


def test_verify_memory_id_explains_outside_digest_window(monkeypatch, tmp_path):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"
    created = int(datetime.datetime(2026, 5, 16, 3, 23, 5, tzinfo=tm_core.TZ_CN).timestamp())

    monkeypatch.setattr(tm_core, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: json.dumps({
        "id": mem_id,
        "text": "T-X3.5",
        "created_at": created,
        "state": "active",
    }))
    monkeypatch.setattr(tm_core, "mem0_search", lambda *_args, **_kwargs: json.dumps({"items": []}))

    result = tm_core.verify_memory_id(mem_id, digest_date="2026-05-15")

    assert result["status"] == "exists_active"
    assert result["digest_contains"] is False
    assert "outside digest window 2026-05-15" in result["digest_inclusion_reason"]


def test_verify_memory_id_distinguishes_not_found_and_unreachable(monkeypatch):
    mem_id = "fd65b298-05bd-493c-83ce-e37d84447362"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 HTTP 404: nope")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "not_found"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: (_ for _ in ()).throw(RuntimeError("Mem0 unreachable: refused")))
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"

    monkeypatch.setattr(tm_core, "mem0_get", lambda _id: "{not-json")
    assert tm_core.verify_memory_id(mem_id)["status"] == "mem0_unreachable"
