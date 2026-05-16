from __future__ import annotations

import pathlib
import socket
import sys
import urllib.error

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_mcp_smoke  # type: ignore[import-not-found]


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return b'{"ok": true}'

    def getcode(self):
        return self.status


def test_probe_url_success(monkeypatch, capsys):
    monkeypatch.setattr(tm_mcp_smoke.urllib.request, "urlopen", lambda *_args, **_kwargs: _FakeResponse())

    assert tm_mcp_smoke._probe_url("http://127.0.0.1:9766/healthz", 1.0) == 0

    out = capsys.readouterr().out
    assert "PASS:" in out
    assert "127.0.0.1:9766" in out


def test_probe_url_timeout_reports_target(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr(tm_mcp_smoke.urllib.request, "urlopen", fail)

    assert tm_mcp_smoke._probe_url("http://127.0.0.1:9766/healthz", 1.0) == 1

    out = capsys.readouterr().out
    assert "phase=http_read" in out
    assert "127.0.0.1:9766" in out


def test_probe_url_urlerror_reports_connect_phase(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(tm_mcp_smoke.urllib.request, "urlopen", fail)

    assert tm_mcp_smoke._probe_url("http://127.0.0.1:9766/healthz", 1.0) == 1

    out = capsys.readouterr().out
    assert "phase=connect" in out
    assert "connection refused" in out


def test_spawn_reports_child_stderr_when_import_missing(monkeypatch, capsys):
    class FakeProc:
        returncode = 1

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            return b"", b"ModuleNotFoundError: No module named 'mcp'"

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(tm_mcp_smoke.subprocess, "Popen", lambda *_args, **_kwargs: FakeProc())
    monkeypatch.setattr(tm_mcp_smoke, "_free_port", lambda: 19999)

    assert tm_mcp_smoke._spawn_and_probe(0.1) == 1

    out = capsys.readouterr().out
    assert "spawned tm_mcp.py exited" in out
    assert "ModuleNotFoundError" in out
