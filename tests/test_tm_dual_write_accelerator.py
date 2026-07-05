from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_dual_write_accelerator as accel  # type: ignore[import-not-found]


def test_route_event_replay_summarizes_mem0_signatures(tmp_path, monkeypatch):
    monkeypatch.setattr(accel, "REPO_ROOT", tmp_path)
    today = dt.datetime.now(accel.TZ_CN).date().isoformat()
    event_dir = tmp_path / ".tmp" / "memory-route-events" / today
    event_dir.mkdir(parents=True)
    (event_dir / "events.jsonl").write_text(
        "\n".join([
            json.dumps({"agent": "codex", "route": "mem0", "component": "write_memory", "outcome": "mem0"}),
            json.dumps({"agent": "codex", "route": "mem0", "component": "write_memory", "outcome": "mem0"}),
            json.dumps({"agent": "codex", "route": "inbox", "component": "write_memory", "outcome": "wiki_proposal"}),
        ]),
        encoding="utf-8",
    )

    result = accel.route_event_replay(14)

    assert result["event_count"] == 3
    assert result["mem0_event_count"] == 2
    assert result["mem0_signatures"][0] == {
        "signature": "codex|mem0|write_memory|mem0",
        "count": 2,
    }


def test_service_env_audit_marks_openmemory_env(tmp_path, monkeypatch):
    monkeypatch.setattr(accel, "REPO_ROOT", tmp_path)
    service_dir = tmp_path / "deploy" / "mcp"
    service_dir.mkdir(parents=True)
    (service_dir / "tm-mcp.service").write_text(
        "EnvironmentFile=/opt/tigermemory/runtime/openmemory/.env\n",
        encoding="utf-8",
    )
    (service_dir / "other.service").write_text("Environment=FOO=bar\n", encoding="utf-8")

    rows = {row["unit"]: row for row in accel.service_env_audit()}

    assert rows["deploy/mcp/tm-mcp.service"]["uses_openmemory_env"] is True
    assert rows["deploy/mcp/other.service"]["uses_openmemory_env"] is False


def test_live_canary_requires_hybrid_profile(monkeypatch):
    monkeypatch.setattr(accel.tm_core, "tigermemory_profile", lambda: accel.tm_core.TIGERMEMORY_PROFILE_LOCAL)

    with pytest.raises(RuntimeError, match="requires TIGERMEMORY_PROFILE=hybrid"):
        accel.run_live_canary("http://127.0.0.1:8790")
