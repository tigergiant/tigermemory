from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tm_self_evolution  # type: ignore[import-not-found]


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(payload + "\n", encoding="utf-8")


def _tmp_file(path: pathlib.Path, rel: str) -> pathlib.Path:
    file = path / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    return file


def test_collect_infers_three_event_types_for_date(tmp_path, capsys):
    root = tmp_path
    _write_jsonl(
        _tmp_file(root, ".tmp/guard-rejects.jsonl"),
        [
            {
                "ts": "2026-06-01T08:00:00+08:00",
                "agent": "codex",
                "session_id": "codex-20260601-0900",
                "guard": "routed_by",
                "file": "wiki/test.md",
                "line": 14,
                "msg": "routing failed",
            },
            {
                "ts": "2026-05-31T23:59:59+08:00",
                "agent": "codex",
                "session_id": "codex-20260531-2300",
                "guard": "owner",
            },
        ],
    )
    _write_jsonl(
        _tmp_file(root, ".tmp/preflight-lessons.log"),
        [
            {
                "ts": "2026-06-01T09:05:00+08:00",
                "agent": "cascade",
                "session_id": "cascade-20260601-0822",
                "query": "git commit",
                "hits": 3,
                "top": ["a", "b"],
            },
        ],
    )
    _tmp_file(root, ".tmp/pending-handoff.json").write_text(
        json.dumps(
            {
                "ts": "2026-06-01T10:10:10+08:00",
                "agent": "hermes",
                "session_id": "hermes-20260601-0922",
                "status": "pending",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = tm_self_evolution.main(
        ["collect", "--date", "2026-06-01", "--root", str(root), "--json"]
    )
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["event_count"] == 3
    types = Counter(item["event_type"] for item in data["events"])
    assert types["hook_blocked"] == 1
    assert types["lesson_searched"] == 1
    assert types["handoff_missing"] == 1
    assert all(item["outcome"] is None for item in data["events"])
    for item in data["events"]:
        assert set(item.keys()) == {
            "ts",
            "session_id",
            "agent",
            "event_type",
            "rule_id",
            "outcome",
            "evidence_ref",
            "redacted_summary",
        }


def test_dream_alias_collects_same_events_as_collect(tmp_path, capsys):
    root = tmp_path
    _write_jsonl(
        _tmp_file(root, ".tmp/guard-rejects.jsonl"),
        [
            {
                "ts": "2026-06-01T12:00:00+08:00",
                "agent": "codex",
                "session_id": "s1",
                "guard": "owner",
            },
        ],
    )
    collect_code = tm_self_evolution.main(
        ["collect", "--date", "2026-06-01", "--root", str(root), "--json"]
    )
    collect_data = json.loads(capsys.readouterr().out)

    dream_code = tm_self_evolution.main(
        ["dream", "--date", "2026-06-01", "--root", str(root), "--json"]
    )
    dream_data = json.loads(capsys.readouterr().out)

    assert collect_code == 0
    assert dream_code == 0
    assert collect_data["events"] == dream_data["events"]


def test_collect_summary_returns_counts_and_limited_samples(tmp_path):
    root = tmp_path
    _write_jsonl(
        _tmp_file(root, ".tmp/guard-rejects.jsonl"),
        [
            {
                "ts": "2026-06-01T12:00:00+08:00",
                "agent": "codex",
                "session_id": "s1",
                "guard": "owner",
                "msg": "first",
            },
            {
                "ts": "2026-06-01T13:00:00+08:00",
                "agent": "cascade",
                "session_id": "s2",
                "guard": "routed_by",
                "msg": "second",
            },
        ],
    )

    summary = tm_self_evolution.collect_summary_for_date(
        "2026-06-01",
        root=root,
        max_samples=1,
    )

    assert summary["event_count"] == 2
    assert summary["counts"] == {"hook_blocked": 2}
    assert summary["outcome_pending"] == 2
    assert len(summary["samples"]) == 1
    assert summary["samples"][0]["event_type"] == "hook_blocked"
    assert summary["inbox_route"] == "AGENTS.md section 9.3 topic=selfevolution"


def test_write_events_only_writes_to_self_evolution_dir(tmp_path, capsys):
    root = tmp_path
    _write_jsonl(
        _tmp_file(root, ".tmp/guard-rejects.jsonl"),
        [
            {
                "ts": "2026-06-01T12:00:00+08:00",
                "agent": "codex",
                "session_id": "s1",
                "guard": "owner",
            },
        ],
    )

    code = tm_self_evolution.main(
        [
            "collect",
            "--date",
            "2026-06-01",
            "--root",
            str(root),
            "--json",
            "--write-events",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    out_file = root / ".tmp" / "self-evolution" / "events" / "2026-06-01.jsonl"

    assert code == 0
    assert data["event_count"] == 1
    assert pathlib.Path(data["events_file"]).as_posix() == ".tmp/self-evolution/events/2026-06-01.jsonl"
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event_type"] == "hook_blocked"


def test_date_filter_returns_no_events_when_no_match(tmp_path, capsys):
    root = tmp_path
    _write_jsonl(
        _tmp_file(root, ".tmp/preflight-lessons.log"),
        [{"ts": "2026-05-31T23:59:59+08:00", "agent": "codex", "session_id": "s1", "query": "rules"}],
    )

    code = tm_self_evolution.main(
        ["collect", "--date", "2026-06-01", "--root", str(root), "--json"]
    )
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["event_count"] == 0
    assert data["events"] == []


def test_collect_uses_record_timestamp_not_log_file_mtime(tmp_path, capsys):
    root = tmp_path
    _write_jsonl(
        _tmp_file(root, ".tmp/guard-rejects.jsonl"),
        [
            {
                "ts": "2026-05-30T12:00:00+08:00",
                "agent": "codex",
                "session_id": "s1",
                "guard": "owner",
            },
            {
                "ts": "2026-06-01T12:00:00+08:00",
                "agent": "codex",
                "session_id": "s2",
                "guard": "routed_by",
            },
        ],
    )
    _write_jsonl(
        _tmp_file(root, ".tmp/preflight-lessons.log"),
        [
            {
                "ts": "2026-05-30T12:30:00+08:00",
                "agent": "codex",
                "session_id": "s1",
                "query": "self evolution",
            },
        ],
    )

    code = tm_self_evolution.main(
        ["collect", "--date", "2026-05-30", "--root", str(root), "--json"]
    )
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert [item["session_id"] for item in data["events"]] == ["s1", "s1"]
    assert [item["event_type"] for item in data["events"]] == [
        "hook_blocked",
        "lesson_searched",
    ]
