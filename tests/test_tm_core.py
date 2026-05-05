from __future__ import annotations

import pathlib
import sys

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
