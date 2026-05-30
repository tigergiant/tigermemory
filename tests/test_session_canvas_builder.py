from __future__ import annotations

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
CMD = [PY, str(REPO_ROOT / "tools" / "session_canvas_builder.py")]


def _write_trace(path: pathlib.Path) -> None:
    rows = [
        {"ts": "2026-05-29T19:54:00+08:00", "event_type": "commit", "summary": "init", "details": {"command": "git init"}},
        {"ts": "2026-05-29T19:55:00+08:00", "event_type": "error", "summary": "blocked"},
        {"ts": "2026-05-29T19:56:00+08:00", "event_type": "mcp_write", "summary": "handoff"},
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_trace_with_problematic_summary(path: pathlib.Path) -> None:
    rows = [
        {
            "ts": "2026-05-29T19:54:00+08:00",
            "event_type": "error",
            "summary": 'Traceback...\\r\\n File "<string>"',
            "details": {"command": "tool run"},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _read_jsonl_lines(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rows.append(json.loads(raw))
    return rows


def _run_build(trace: pathlib.Path, out: pathlib.Path, session_id: str) -> tuple[int, dict]:
    cmd = CMD + ["build", "--session-id", session_id, "--trace", str(trace), "--out", str(out), "--json"]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_build_writes_nodes_and_mmd(tmp_path):
    trace = tmp_path / "trace.jsonl"
    out = tmp_path / ".tmp" / "session-canvas"
    _write_trace(trace)
    session_id = "codex-test-20260529-0001"
    _run_build(trace, out, session_id)

    nodes_path = out / f"{session_id}.nodes.jsonl"
    mmd_path = out / f"{session_id}.mmd"
    nodes = _read_jsonl_lines(nodes_path)
    assert len(nodes) == 3
    assert {"node_id", "session_id", "repo", "ide", "agent", "type", "status", "title", "refs", "confidence", "canvas_quality", "privacy_level", "created_at", "updated_at"} <= nodes[0].keys()
    assert nodes[0]["session_id"] == session_id
    assert nodes[1]["status"] == "failed"
    assert nodes[0]["refs"][0].startswith("trace:")
    assert nodes[2]["refs"][1] == f"node:{nodes[1]['node_id']}"
    assert mmd_path.exists()
    assert 'flowchart TD' in mmd_path.read_text(encoding='utf-8')


def test_mmd_label_sanitizes_backslashes_quotes_and_newlines(tmp_path):
    trace = tmp_path / "trace.jsonl"
    out = tmp_path / ".tmp" / "session-canvas"
    _write_trace_with_problematic_summary(trace)
    session_id = "codex-test-20260529-backslash"
    _run_build(trace, out, session_id)

    mmd_path = out / f"{session_id}.mmd"
    mmd_text = mmd_path.read_text(encoding="utf-8")
    assert "\\\"" not in mmd_text
    assert "\\n" not in mmd_text
    assert "\\r" not in mmd_text
    assert "\\r\\n" not in mmd_text
    assert "traceback: blocker" in mmd_text.lower() or "traceback" in mmd_text.lower()

    nodes_path = out / f"{session_id}.nodes.jsonl"
    assert nodes_path.exists()
    audit = subprocess.run(
        CMD + ["audit", "--session-id", session_id, "--out", str(out), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert audit.returncode == 0
    report = json.loads(audit.stdout)
    assert report["ok"] is True
    assert report["ref_count"] >= 1


def test_read_node_exact_lookup(tmp_path):
    trace = tmp_path / "trace.jsonl"
    out = tmp_path / ".tmp" / "session-canvas"
    _write_trace(trace)
    session_id = "codex-test-20260529-0002"
    _run_build(trace, out, session_id)

    node_id = _read_jsonl_lines(out / f"{session_id}.nodes.jsonl")[1]["node_id"]
    lookup = subprocess.run(
        CMD + ["read-node", "--node-id", node_id, "--out", str(out), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert lookup.returncode == 0
    got = json.loads(lookup.stdout)
    assert got["node_id"] == node_id
    assert got["title"] == "blocked"

    missing = subprocess.run(
        CMD + ["read-node", "--node-id", "missing-node", "--out", str(out), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert json.loads(missing.stdout)["ok"] is False


def test_audit_detects_broken_ref_and_missing_node(tmp_path):
    trace = tmp_path / "trace.jsonl"
    out = tmp_path / ".tmp" / "session-canvas"
    _write_trace(trace)
    session_id = "codex-test-20260529-0003"
    _run_build(trace, out, session_id)

    nodes_path = out / f"{session_id}.nodes.jsonl"
    nodes = _read_jsonl_lines(nodes_path)
    nodes[2]["refs"] = ["node:ghost-node"]
    nodes_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in nodes) + "\n",
        encoding="utf-8",
    )

    mmd_path = out / f"{session_id}.mmd"
    mmd = mmd_path.read_text(encoding="utf-8")
    mmd_path.write_text(mmd + "\n    orphan_123[\"orphan\"]\n", encoding="utf-8")

    audit = subprocess.run(
        CMD + ["audit", "--session-id", session_id, "--out", str(out), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert audit.returncode == 0
    report = json.loads(audit.stdout)
    assert any(item["ref"] == "node:ghost-node" for item in report["broken_refs"])
    assert "orphan_123" in report["missing_nodes"]


def test_audit_detects_broken_trace_ref(tmp_path):
    trace = tmp_path / "trace.jsonl"
    out = tmp_path / ".tmp" / "session-canvas"
    _write_trace(trace)
    session_id = "codex-test-20260529-broken-trace"
    _run_build(trace, out, session_id)

    nodes_path = out / f"{session_id}.nodes.jsonl"
    nodes = _read_jsonl_lines(nodes_path)
    nodes[0]["refs"] = ["trace:missing-trace.jsonl#L99"]
    nodes_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in nodes) + "\n",
        encoding="utf-8",
    )

    audit = subprocess.run(
        CMD + ["audit", "--session-id", session_id, "--out", str(out), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert audit.returncode == 0
    report = json.loads(audit.stdout)
    assert report["ok"] is False
    assert any(item["error"].startswith("missing_trace:") for item in report["broken_refs"])
