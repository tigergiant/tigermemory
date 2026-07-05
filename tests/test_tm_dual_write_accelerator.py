from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import types

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


def test_timer_entrypoint_audit_classifies_bound_services(tmp_path, monkeypatch):
    monkeypatch.setattr(accel, "REPO_ROOT", tmp_path)
    deploy = tmp_path / "deploy" / "mcp"
    deploy.mkdir(parents=True)
    (deploy / "direct.timer").write_text("[Timer]\nUnit=direct.service\n", encoding="utf-8")
    (deploy / "direct.service").write_text(
        "[Service]\nExecStart=/usr/bin/python tools/session-fallback-generator.py --write\n",
        encoding="utf-8",
    )
    (deploy / "watch.timer").write_text("[Timer]\nUnit=watch.service\n", encoding="utf-8")
    (deploy / "watch.service").write_text(
        "[Service]\nExecStart=/opt/tigermemory/tools/tm_runtime_events.py record\n",
        encoding="utf-8",
    )
    (deploy / "digest.timer").write_text("[Timer]\nUnit=digest.service\n", encoding="utf-8")
    (deploy / "digest.service").write_text(
        "[Service]\nExecStart=/usr/bin/python3 tools/tm_digest.py --date yesterday\n",
        encoding="utf-8",
    )
    (deploy / "tm-dashboard.timer").write_text("[Timer]\nUnit=tm-dashboard.service\n", encoding="utf-8")
    (deploy / "tm-dashboard.service").write_text("[Service]\nExecStart=tools/tm_review_ui.py\n", encoding="utf-8")

    rows = {pathlib.Path(row["timer"]).name: row for row in accel.timer_entrypoint_audit()}

    assert rows["direct.timer"]["classification"] == "direct_memory_write"
    assert rows["direct.timer"]["needs_canary"] is True
    assert rows["watch.timer"]["classification"] == "runtime_event_only"
    assert rows["watch.timer"]["needs_canary"] is False
    assert rows["digest.timer"]["classification"] == "report_or_digest_only"
    assert rows["tm-dashboard.timer"]["classification"] == "service_warm_only"


def test_live_canary_requires_hybrid_profile(monkeypatch):
    monkeypatch.setattr(accel.tm_core, "tigermemory_profile", lambda: accel.tm_core.TIGERMEMORY_PROFILE_LOCAL)

    with pytest.raises(RuntimeError, match="requires TIGERMEMORY_PROFILE=hybrid"):
        accel.run_live_canary("http://127.0.0.1:8790")


def test_parse_json_requires_object_response():
    assert accel._parse_json('{"id": "x"}') == {"id": "x"}
    with pytest.raises(RuntimeError, match="expected object response"):
        accel._parse_json('["x"]')
    with pytest.raises(RuntimeError, match="expected JSON response"):
        accel._parse_json("not-json")


def test_shadow_matches_checks_origin_state_and_shadow_state():
    row = {"backend_origin": "local-shadow", "state": "deleted", "shadow_state": "mem0_deleted"}

    assert accel._shadow_matches(row)
    assert accel._shadow_matches(row, state="deleted", shadow_state="mem0_deleted")
    assert not accel._shadow_matches(row, state="active")
    assert not accel._shadow_matches(row, shadow_state="pending")
    assert not accel._shadow_matches({"backend_origin": "openmemory"})


def test_run_tm_io_parses_json_and_reports_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(accel, "REPO_ROOT", tmp_path)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs["input"]
        return types.SimpleNamespace(returncode=0, stdout='{"id": "abc"}\n', stderr="")

    monkeypatch.setattr(accel.subprocess, "run", fake_run)

    assert accel._run_tm_io(["mem0-write"], "body") == {"id": "abc"}
    assert seen["input"] == "body"
    assert pathlib.Path(seen["cmd"][1]).name == "tm_io.py"

    def fake_fail(_cmd, **_kwargs):
        return types.SimpleNamespace(returncode=2, stdout="", stderr="bad input")

    monkeypatch.setattr(accel.subprocess, "run", fake_fail)
    with pytest.raises(RuntimeError, match="bad input"):
        accel._run_tm_io(["mem0-write"], "body")


def test_mcp_tool_result_payload_parses_text_content():
    payload = accel._mcp_tool_result_payload(
        types.SimpleNamespace(content=[types.SimpleNamespace(text='{"id": "abc"}')])
    )

    assert payload == {"id": "abc"}

    with pytest.raises(RuntimeError, match="no content"):
        accel._mcp_tool_result_payload(types.SimpleNamespace(content=[]))

    with pytest.raises(RuntimeError, match="no text"):
        accel._mcp_tool_result_payload(types.SimpleNamespace(content=[object()]))
