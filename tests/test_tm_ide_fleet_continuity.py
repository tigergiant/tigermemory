"""Tests for tm_ide_fleet F2: multi-IDE switching continuity.

All tests inject a mock fetcher — no real network calls, no real API key
needed. Covers: frontmatter/section parsing, channel fallback (internal fails
-> public succeeds), open-blocker detection, and CLI JSON/text output.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_ide_fleet  # type: ignore[import-not-found]

SAMPLE_CARD = """---
memory_type: session-handoff
session_id: codex-20260614-1145
ide: cursor
agent: codex
persona_primary: executor
confidence: high
source: agent
---

## Task
改检索代码卡在单元测试

## Decisions
用 outbox 异步算 embedding

## Blockers
无

## Handoff
下一位继续跑 pytest

## Evidence Refs
- commit: abc123
"""

SAMPLE_CARD_WITH_BLOCKER = """---
memory_type: session-handoff
session_id: windsurf-20260701-0900
ide: windsurf
agent: cascade
confidence: medium
source: hook_auto
---

## Task
调试 WSL 端口转发

## Blockers
portproxy 规则在重启后丢失，需要宿主机重新创建

## Handoff
下一位检查防火墙规则是否持久化
"""


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("TM_MCP_API_KEY", "test-key-not-real")


def test_parse_handoff_card_frontmatter_and_sections():
    parsed = tm_ide_fleet._parse_handoff_card(SAMPLE_CARD)
    assert parsed["frontmatter"]["ide"] == "cursor"
    assert parsed["frontmatter"]["agent"] == "codex"
    assert parsed["frontmatter"]["confidence"] == "high"
    assert "改检索代码" in parsed["sections"]["Task"]
    assert parsed["sections"]["Blockers"] == "无"


def test_has_open_blocker():
    assert tm_ide_fleet._has_open_blocker("无") is False
    assert tm_ide_fleet._has_open_blocker("") is False
    assert tm_ide_fleet._has_open_blocker("none") is False
    assert tm_ide_fleet._has_open_blocker("portproxy 规则丢失") is True


def test_card_record_shape():
    rec = tm_ide_fleet._card_record({"id": "m1", "created_at": "2026-06-14T11:45:00", "content": SAMPLE_CARD})
    assert rec["ide"] == "cursor"
    assert rec["agent"] == "codex"
    assert rec["blockers"] == "无"
    assert "跑 pytest" in rec["handoff"]


def test_gather_continuity_no_api_key(monkeypatch):
    monkeypatch.delenv("TM_MCP_API_KEY", raising=False)
    monkeypatch.setattr(tm_ide_fleet, "_api_key", lambda: None)
    result = tm_ide_fleet.gather_continuity()
    assert result["ok"] is False
    assert "TM_MCP_API_KEY" in result["error"]


def test_gather_continuity_internal_success():
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        assert base_url == "http://172.20.160.1:8790"
        return {"results": [{"id": "m1", "created_at": "2026-06-14T11:45:00", "content": SAMPLE_CARD}]}

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    assert result["ok"] is True
    assert result["source"] == "internal"
    assert len(result["cards"]) == 1
    assert result["any_open_blocker"] is False


def test_gather_continuity_falls_back_to_public_on_internal_failure():
    calls = []

    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        calls.append(base_url)
        if "172.20.160.1" in base_url:
            raise ConnectionError("outbound firewall blocked (WinError 10013)")
        return {
            "results": [
                {"id": "m1", "created_at": "2026-07-01T09:00:00", "content": SAMPLE_CARD_WITH_BLOCKER}
            ]
        }

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    assert calls == ["http://172.20.160.1:8790", "https://tm-api.doodiu.cloud"]
    assert result["ok"] is True
    assert result["source"] == "public"
    assert result["any_open_blocker"] is True


def test_gather_continuity_all_channels_fail():
    def fetcher(*args, **kwargs):
        raise TimeoutError("no route")

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    assert result["ok"] is False
    assert "all channels failed" in result["error"]
    assert result["cards"] == []


def test_cards_sorted_newest_first():
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        return {
            "results": [
                {"id": "old", "created_at": "2026-05-01T00:00:00", "content": SAMPLE_CARD},
                {"id": "new", "created_at": "2026-07-01T00:00:00", "content": SAMPLE_CARD_WITH_BLOCKER},
            ]
        }

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    assert [c["id"] for c in result["cards"]] == ["new", "old"]


def test_limit_respected():
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        rows = [
            {"id": f"m{i}", "created_at": f"2026-07-0{i}T00:00:00", "content": SAMPLE_CARD}
            for i in range(1, 5)
        ]
        return {"results": rows}

    result = tm_ide_fleet.gather_continuity(limit=2, fetcher=fetcher)
    assert len(result["cards"]) == 2


def test_render_continuity_text_marks_blocker():
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        return {
            "results": [
                {"id": "m1", "created_at": "2026-07-01T09:00:00", "content": SAMPLE_CARD_WITH_BLOCKER}
            ]
        }

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    text = tm_ide_fleet.render_continuity_text(result)
    assert "🔴" in text
    assert "未决 blocker" in text
    assert "portproxy" in text


def test_render_continuity_text_no_blocker():
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        return {"results": [{"id": "m1", "created_at": "2026-06-14T11:45:00", "content": SAMPLE_CARD}]}

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    text = tm_ide_fleet.render_continuity_text(result)
    assert "🟢" in text
    assert "🔴" not in text


def test_render_continuity_text_error():
    result = {"schema": "tm-ide-continuity-v1", "ok": False, "error": "all channels failed", "cards": []}
    text = tm_ide_fleet.render_continuity_text(result)
    assert "读不到" in text


def test_render_continuity_text_empty_cards():
    result = {"schema": "tm-ide-continuity-v1", "ok": True, "source": "public", "cards": []}
    text = tm_ide_fleet.render_continuity_text(result)
    assert "暂无" in text


def test_cli_continuity_json(monkeypatch, capsys):
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        return {"results": [{"id": "m1", "created_at": "2026-06-14T11:45:00", "content": SAMPLE_CARD}]}

    monkeypatch.setattr(tm_ide_fleet, "_search_memories_via_http", fetcher)
    rc = tm_ide_fleet.main(["continuity", "--json", "--limit", "3"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "tm-ide-continuity-v1"
    assert payload["ok"] is True


def test_cli_continuity_text(monkeypatch, capsys):
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        return {"results": []}

    monkeypatch.setattr(tm_ide_fleet, "_search_memories_via_http", fetcher)
    rc = tm_ide_fleet.main(["continuity"])
    assert rc == 0
    assert "最近的 session handoff" in capsys.readouterr().out


def test_cli_continuity_returns_nonzero_when_unreachable(monkeypatch, capsys):
    def fetcher(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(tm_ide_fleet, "_search_memories_via_http", fetcher)
    rc = tm_ide_fleet.main(["continuity"])
    assert rc == 1


def test_api_key_from_env_takes_priority(monkeypatch):
    monkeypatch.setenv("TM_MCP_API_KEY", "from-env")
    assert tm_ide_fleet._api_key() == "from-env"


def test_api_key_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("TM_MCP_API_KEY", raising=False)
    # Point at a repo root copy with no runtime/openmemory/.env by monkeypatching
    # the module's file location resolution indirectly via a missing-file repo.
    import tm_ide_fleet as mod

    original = mod.__file__
    try:
        fake_tools_dir = tmp_path / "tools"
        fake_tools_dir.mkdir()
        mod.__file__ = str(fake_tools_dir / "tm_ide_fleet.py")
        assert mod._api_key() is None
    finally:
        mod.__file__ = original


def test_card_record_returns_none_for_non_handoff_memory():
    non_handoff = "just a regular memory about something else, no frontmatter at all"
    assert tm_ide_fleet._card_record({"id": "x", "content": non_handoff}) is None


def test_gather_continuity_filters_out_non_handoff_hits():
    def fetcher(base_url, query, limit, api_key, timeout, bypass_proxy):
        return {
            "results": [
                {"id": "noise", "created_at": 1779772973, "content": "unrelated fuzzy search hit"},
                {"id": "real", "created_at": 1779772088, "content": SAMPLE_CARD},
            ]
        }

    result = tm_ide_fleet.gather_continuity(fetcher=fetcher)
    assert [c["id"] for c in result["cards"]] == ["real"]


def test_format_created_at_epoch_and_passthrough():
    assert "2026" in tm_ide_fleet._format_created_at(1779772973)
    assert tm_ide_fleet._format_created_at("not-a-number") == "not-a-number"
    assert tm_ide_fleet._format_created_at(None) == "?"
