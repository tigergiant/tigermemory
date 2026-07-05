from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
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


def test_deferred_entrypoints_archive_openai_mcp_gate():
    rows = {row["entrypoint"]: row for row in accel.deferred_entrypoints()}

    assert rows["tm-openai-mcp write_memory"]["status"] == "archived"
    assert rows["tm-openai-mcp write_memory"]["phase1_gate"] is False


def test_summarize_reconcile_payload_requires_zero_diff():
    payload = {
        "ok": True,
        "counts": {"source": 2, "db": 2},
        "direct_readback": {"missing": 0},
        "conservation": {"balanced": True},
        "sha_diff": {"symmetric_diff_count": 0},
        "semantic": {"status": "match"},
    }

    assert accel.summarize_reconcile_payload(payload)["status"] == "pass"

    payload["sha_diff"]["symmetric_diff_count"] = 1
    blocked = accel.summarize_reconcile_payload(payload)
    assert blocked["status"] == "blocked"
    assert "sha_symmetric_diff" in blocked["reasons"]


def test_summarize_shadow_search_logs_flags_latency_and_empty_local(tmp_path):
    today = dt.datetime.now(accel.TZ_CN).date().isoformat()
    log = tmp_path / f"{today}.jsonl"
    log.write_text(
        "\n".join([
            json.dumps({
                "old_count": 1,
                "local_count": 1,
                "intersection_count": 1,
                "local_latency_ms": 20.0,
                "warnings": [],
            }),
            json.dumps({
                "old_count": 1,
                "local_count": 0,
                "intersection_count": 0,
                "local_latency_ms": 900.0,
                "warnings": [],
            }),
        ]),
        encoding="utf-8",
    )

    summary = accel.summarize_shadow_search_logs(tmp_path, days=1, max_local_p95_ms=500)

    assert summary["status"] == "blocked"
    assert "local_search_p95_too_high" in summary["reasons"]
    assert "local_empty_for_old_hits" in summary["reasons"]


def test_summarize_shadow_reconcile_checks_route_ids_against_local_shadow(tmp_path):
    today = dt.datetime.now(accel.TZ_CN).date().isoformat()
    event_dir = tmp_path / "events" / today
    event_dir.mkdir(parents=True)
    remote_id = "11111111-2222-4333-8444-555555555555"
    (event_dir / "events.jsonl").write_text(
        json.dumps({"outcome": "mem0", "target_ref": {"id": remote_id}}) + "\n",
        encoding="utf-8",
    )
    db = tmp_path / "memory.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE memories(
                legacy_mem0_id TEXT,
                state TEXT,
                backend_origin TEXT,
                shadow_state TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO memories VALUES (?, 'active', 'local-shadow', 'pending')",
            (remote_id,),
        )
        conn.commit()
    finally:
        conn.close()

    passed = accel.summarize_shadow_reconcile(route_event_root=tmp_path / "events", db_path=db, days=1)
    assert passed["status"] == "pass"
    assert passed["checked_ids"] == 1

    missing = accel.summarize_shadow_reconcile(route_event_root=tmp_path / "events", db_path=tmp_path / "missing.sqlite", days=1)
    assert missing["status"] == "blocked"
    assert missing["reason"] == "local_db_missing"

    old_schema_db = tmp_path / "old-schema.sqlite"
    conn = sqlite3.connect(old_schema_db)
    try:
        conn.execute("CREATE TABLE memories(id TEXT, content TEXT)")
        conn.commit()
    finally:
        conn.close()
    old_schema = accel.summarize_shadow_reconcile(
        route_event_root=tmp_path / "events",
        db_path=old_schema_db,
        days=1,
    )
    assert old_schema["status"] == "blocked"
    assert old_schema["reason"] == "local_db_schema_missing_columns"
    assert "legacy_mem0_id" in old_schema["missing_columns"]


def test_summarize_shadow_reconcile_can_start_from_dual_write_enable_time(tmp_path, monkeypatch):
    today = dt.datetime.now(accel.TZ_CN).date().isoformat()
    event_dir = tmp_path / "events" / today
    event_dir.mkdir(parents=True)
    before_id = "aaaaaaaa-2222-4333-8444-555555555555"
    after_id = "bbbbbbbb-2222-4333-8444-555555555555"
    rows = [
        {"outcome": "mem0", "target_ref": {"id": before_id}, "ts": f"{today}T09:00:00+08:00"},
        {"outcome": "mem0", "target_ref": {"id": after_id}, "ts": f"{today}T13:00:00+08:00"},
    ]
    (event_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    db = tmp_path / "memory.sqlite"
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(db))
    accel.tm_core._local_write_memory_record(
        "codex",
        "systems",
        "existing shadow memory text",
        {"source": "codex", "topic": "systems"},
        backend_origin="local-shadow",
        legacy_mem0_id=after_id,
        shadow_state="pending",
    )

    full_window = accel.summarize_shadow_reconcile(route_event_root=tmp_path / "events", db_path=db, days=1)
    assert full_window["status"] == "blocked"
    assert full_window["missing_count"] == 1
    assert full_window["missing_agents"] == {"": 1}
    assert full_window["missing_sources"] == {"": 1}
    assert full_window["missing_topics"] == {"": 1}

    since_window = accel.summarize_shadow_reconcile(
        route_event_root=tmp_path / "events",
        db_path=db,
        days=1,
        since=f"{today}T12:00:00+08:00",
    )
    assert since_window["status"] == "pass"
    assert since_window["checked_ids"] == 1

    monkeypatch.setattr(
        accel.tm_core,
        "mem0_get",
        lambda memory_id: json.dumps({
            "id": memory_id,
            "memory": "backfilled shadow memory text",
            "metadata_": {"source": "codex", "topic": "systems"},
        }),
    )
    repaired = accel.repair_shadow_reconcile(route_event_root=tmp_path / "events", db_path=db, days=1)
    assert repaired["status"] == "pass"
    assert repaired["repaired_count"] == 1
    after_repair = accel.summarize_shadow_reconcile(route_event_root=tmp_path / "events", db_path=db, days=1)
    assert after_repair["status"] == "pass"


def test_phase_readiness_combines_reconcile_shadow_and_eval_reports(tmp_path):
    today = dt.datetime.now(accel.TZ_CN).date().isoformat()
    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    (shadow_dir / f"{today}.jsonl").write_text(
        json.dumps({
            "old_count": 1,
            "local_count": 1,
            "intersection_count": 1,
            "local_latency_ms": 10.0,
            "warnings": [],
        }) + "\n",
        encoding="utf-8",
    )
    reconcile = tmp_path / "reconcile.json"
    reconcile.write_text(json.dumps({
        "ok": True,
        "counts": {"source": 1, "db": 1},
        "direct_readback": {"missing": 0},
        "conservation": {"balanced": True},
        "sha_diff": {"symmetric_diff_count": 0},
        "semantic": {"status": "match"},
    }), encoding="utf-8")
    eval_report = tmp_path / "eval.json"
    eval_report.write_text(json.dumps({
        "case_count": 1,
        "quality_hit5_rate": 1.0,
        "runtime_unavailable_count": 0,
        "contract_failure_count": 0,
    }), encoding="utf-8")

    result = accel.phase_readiness(types.SimpleNamespace(
        reconcile_report=str(reconcile),
        reconcile_input=None,
        local_db=str(tmp_path / "memory.sqlite"),
        reconcile_out=None,
        check_shadow_reconcile=False,
        route_event_root=str(tmp_path / "events"),
        shadow_log_dir=str(shadow_dir),
        days=1,
        max_local_p95_ms=500.0,
        retrieval_eval_report=str(eval_report),
        run_retrieval_eval=False,
        retrieval_eval_cases=str(tmp_path / "cases.jsonl"),
        eval_top_k=5,
        eval_missing_path_policy="block",
        min_hit5_rate=1.0,
        max_eval_p95_ms=500.0,
    ))

    assert result["overall_status"] == "pass"
    assert result["blockers"] == []
    assert result["pending"] == []


def test_summarize_retrieval_eval_payload_uses_preserved_latency_p95():
    summary = accel.summarize_retrieval_eval_payload(
        {
            "case_count": 2,
            "quality_hit5_rate": 1.0,
            "runtime_unavailable_count": 0,
            "contract_failure_count": 0,
            "latency_p95_ms": 800.0,
        },
        min_hit5_rate=1.0,
        max_p95_ms=500.0,
    )

    assert summary["status"] == "blocked"
    assert summary["latency_p95_ms"] == 800.0
    assert "retrieval_eval_p95_too_high" in summary["reasons"]


def test_summarize_retrieval_eval_payload_flags_missing_expected_paths():
    summary = accel.summarize_retrieval_eval_payload(
        {
            "case_count": 2,
            "quality_hit5_rate": 1.0,
            "runtime_unavailable_count": 0,
            "contract_failure_count": 0,
            "expected_path_missing_count": 1,
            "expected_path_missing_samples": [{"id": "case-1", "missing_expected_paths": ["wiki/missing.md"]}],
        },
        min_hit5_rate=1.0,
        max_p95_ms=0.0,
    )

    assert summary["status"] == "blocked"
    assert "eval_expected_paths_missing" in summary["reasons"]
    assert summary["expected_path_missing_count"] == 1


def test_summarize_retrieval_eval_payload_can_exclude_missing_expected_paths():
    summary = accel.summarize_retrieval_eval_payload(
        {
            "case_count": 1,
            "evaluated_case_count": 1,
            "quality_hit5_rate": 1.0,
            "runtime_unavailable_count": 0,
            "contract_failure_count": 0,
            "expected_path_missing_count": 23,
            "excluded_missing_expected_path_count": 23,
        },
        min_hit5_rate=1.0,
        max_p95_ms=0.0,
        missing_path_policy="exclude",
    )

    assert summary["status"] == "pass"
    assert summary["expected_path_missing_policy"] == "exclude"
    assert summary["excluded_missing_expected_path_count"] == 23


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


def test_run_fault_drill_uses_temp_dbs_and_reports_expected_outcomes(monkeypatch, tmp_path):
    monkeypatch.setenv("TIGERMEMORY_LOCAL_DB", str(tmp_path / "production.sqlite"))
    results = {row["name"]: row for row in accel.run_fault_drill()}

    assert results["shadow_write_failure_non_blocking"]["ok"] is True
    assert results["shadow_write_failure_non_blocking"]["runtime_event_recorded"] is True
    assert results["remote_down_preserves_fail_closed"]["ok"] is True
    assert results["remote_down_preserves_fail_closed"]["local_db_created"] is False
    assert results["local_wal_schema_readback"]["ok"] is True
    assert results["local_wal_schema_readback"]["journal_mode"] == "wal"
    assert (tmp_path / "production.sqlite").exists() is False


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
