"""Tests for tm_mcp allowed-hosts computation (DNS-rebinding allowlist).

TM_MCP_ALLOWED_HOSTS fully replaces the defaults; TM_MCP_EXTRA_ALLOWED_HOSTS
appends to whatever base is in effect (so adding a direct-access peer cannot
drop localhost / tigermemory-wsl / Cloudflare from the allowlist).
"""
from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

pytest.importorskip(
    "mcp.server.fastmcp",
    reason="mcp package not installed; run: pip install -r deploy/mcp/requirements.txt",
)


def _reload_with_env(monkeypatch, **env) -> object:
    for key in ("TM_MCP_ALLOWED_HOSTS", "TM_MCP_EXTRA_ALLOWED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    import tm_mcp  # type: ignore[import-not-found]

    return importlib.reload(tm_mcp)


def test_defaults_present_when_no_env(monkeypatch):
    mod = _reload_with_env(monkeypatch)
    ah = mod._allowed_hosts
    assert "localhost" in ah
    assert "tigermemory-wsl" in ah
    assert "tm.doodiu.cloud" in ah


def test_extra_appends_and_keeps_defaults(monkeypatch):
    mod = _reload_with_env(
        monkeypatch, TM_MCP_EXTRA_ALLOWED_HOSTS="172.20.160.1,172.20.160.1:*"
    )
    ah = mod._allowed_hosts
    assert "172.20.160.1" in ah and "172.20.160.1:*" in ah
    # Defaults must survive the append.
    assert "localhost" in ah
    assert "tigermemory-wsl" in ah
    assert "tm.doodiu.cloud" in ah
    assert len(ah) == len(set(ah))  # no duplicates


def test_full_override_replaces_defaults(monkeypatch):
    mod = _reload_with_env(monkeypatch, TM_MCP_ALLOWED_HOSTS="only.example")
    ah = mod._allowed_hosts
    assert ah == ["only.example"]
    assert "localhost" not in ah


def test_override_plus_extra_compose(monkeypatch):
    mod = _reload_with_env(
        monkeypatch,
        TM_MCP_ALLOWED_HOSTS="base.example",
        TM_MCP_EXTRA_ALLOWED_HOSTS="added.example",
    )
    ah = mod._allowed_hosts
    assert ah == ["base.example", "added.example"]


def test_extra_dedupes_against_base(monkeypatch):
    mod = _reload_with_env(
        monkeypatch, TM_MCP_EXTRA_ALLOWED_HOSTS="localhost,newhost"
    )
    ah = mod._allowed_hosts
    assert ah.count("localhost") == 1
    assert "newhost" in ah


def _restore(monkeypatch):
    # Reload once more with a clean env so other test modules see defaults.
    _reload_with_env(monkeypatch)


def test_cleanup_reload(monkeypatch):
    _restore(monkeypatch)
    import tm_mcp  # type: ignore[import-not-found]

    assert "localhost" in tm_mcp._allowed_hosts
