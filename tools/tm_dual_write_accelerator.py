#!/usr/bin/env python3
"""Phase 1 acceleration checks for SQLite shadow dual-write.

Default mode is read-only. Use --live-canary to create short-lived test
memories and verify that each write path creates a local SQLite shadow row.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from typing import Any

import _bootstrap_paths  # noqa: F401

import tigermemory_core as tm_core
from tigermemory_core import TZ_CN
import tm_memory_ops
import tm_review_tools


REPO_ROOT = tm_core.REPO_ROOT
SCAN_ROOTS = ("tools", "packages", "deploy")
SCAN_PATTERNS = {
    "create": ("mem0_write(", "write_memory_with_review("),
    "update": ("mem0_update_content(", "method=\"PUT\"", 'method="PUT"'),
    "delete": ("mem0_delete(", "method=\"DELETE\"", 'method="DELETE"'),
    "raw_memories_api": ("/api/v1/memories",),
}


def _rel(path: pathlib.Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _git_head_short() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def scan_code_entrypoints() -> dict[str, list[dict[str, Any]]]:
    matches: dict[str, list[dict[str, Any]]] = {key: [] for key in SCAN_PATTERNS}
    for root_name in SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".sh", ".service"} or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                for kind, patterns in SCAN_PATTERNS.items():
                    if any(pattern in line for pattern in patterns):
                        matches[kind].append({
                            "path": _rel(path),
                            "line": line_no,
                            "text": line.strip()[:220],
                        })
    return matches


def service_env_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for unit in sorted((REPO_ROOT / "deploy" / "mcp").glob("*.service")):
        try:
            lines = unit.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        env_files = [
            line.split("=", 1)[1].strip()
            for line in lines
            if line.strip().startswith("EnvironmentFile=")
        ]
        rows.append({
            "unit": _rel(unit),
            "environment_files": env_files,
            "uses_openmemory_env": any("runtime/openmemory/.env" in value for value in env_files),
        })
    return rows


def route_event_replay(days: int) -> dict[str, Any]:
    today = dt.datetime.now(TZ_CN).date()
    root = REPO_ROOT / ".tmp" / "memory-route-events"
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        date = (today - dt.timedelta(days=offset)).isoformat()
        path = root / date / "events.jsonl"
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item["_date"] = date
                rows.append(item)

    mem0_rows = [
        row for row in rows
        if str(row.get("outcome") or row.get("route") or row.get("flow_target") or "").lower() == "mem0"
        or str(row.get("flow_target") or "").lower() == "mem0"
    ]
    by_signature: dict[str, int] = {}
    for row in mem0_rows:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        target_ref = row.get("target_ref") if isinstance(row.get("target_ref"), dict) else {}
        agent = str(row.get("agent") or extra.get("agent") or target_ref.get("agent") or "unknown")
        topic = str(row.get("route") or extra.get("topic") or target_ref.get("topic") or "unknown")
        outcome = str(row.get("outcome") or "unknown")
        component = str(row.get("component") or "unknown")
        key = f"{agent}|{topic}|{component}|{outcome}"
        by_signature[key] = by_signature.get(key, 0) + 1
    return {
        "days": days,
        "event_count": len(rows),
        "mem0_event_count": len(mem0_rows),
        "mem0_signatures": [
            {"signature": key, "count": count}
            for key, count in sorted(by_signature.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _shadow_row(remote_id: str) -> dict[str, Any] | None:
    db_path = tm_core._local_db_path()
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, legacy_mem0_id, backend_origin, shadow_state, topic, source_agent, state,
                   content, content_sha256
            FROM memories
            WHERE legacy_mem0_id=?
            """,
            (remote_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _hard_delete_shadow(remote_id: str) -> int:
    db_path = tm_core._local_db_path()
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM memories WHERE legacy_mem0_id=?", (remote_id,))
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def _cleanup_remote_and_shadow(remote_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"remote_id": remote_id}
    try:
        out["remote_delete"] = json.loads(tm_core.mem0_delete([remote_id]))
    except Exception as exc:
        out["remote_delete"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:220]}
    out["local_hard_deleted"] = _hard_delete_shadow(remote_id)
    return out


def _canary_text(name: str) -> str:
    marker = f"tm-dual-write-canary-{name}-{uuid.uuid4()}"
    return "\n".join([
        "---",
        "memory_type: session-handoff",
        "agent: codex",
        f"topic: {marker}",
        f"created_at: {dt.datetime.now(TZ_CN).strftime('%Y-%m-%d')}",
        "---",
        "",
        "## Task",
        f"Temporary dual-write canary for {name}.",
        "",
        "## Decisions",
        "This row must be deleted after shadow verification.",
        "",
        "## Blockers",
        "None.",
        "",
        "## Handoff",
        "Delete after checking local SQLite shadow.",
        "",
        "## Evidence Refs",
        marker,
    ])


def _extract_id(payload: dict[str, Any]) -> str:
    value = str(payload.get("id") or payload.get("memory_id") or "").strip()
    if not tm_core.MEM0_UUID_RE.fullmatch(value):
        raise RuntimeError(f"missing remote id in response keys={sorted(payload.keys())}")
    return value


def _parse_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"expected JSON response, got {raw[:220]!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected object response, got {type(payload).__name__}")
    return payload


def _run_tm_io(args: list[str], stdin_text: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "tm_io.py"), *args],
        cwd=REPO_ROOT,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tm_io {' '.join(args)} failed rc={proc.returncode}: {proc.stderr[:220]}")
    return _parse_json(proc.stdout.strip())


def _shadow_matches(shadow: dict[str, Any] | None, *, state: str | None = None, shadow_state: str | None = None) -> bool:
    if not shadow or shadow.get("backend_origin") != "local-shadow":
        return False
    if state is not None and shadow.get("state") != state:
        return False
    if shadow_state is not None and shadow.get("shadow_state") != shadow_state:
        return False
    return True


def run_live_canary(http_url: str) -> list[dict[str, Any]]:
    if tm_core.tigermemory_profile() != tm_core.TIGERMEMORY_PROFILE_HYBRID:
        raise RuntimeError("live canary requires TIGERMEMORY_PROFILE=hybrid")
    results: list[dict[str, Any]] = []

    def record(name: str, raw_payload: dict[str, Any]) -> str:
        remote_id = _extract_id(raw_payload)
        shadow = _shadow_row(remote_id)
        cleanup = _cleanup_remote_and_shadow(remote_id)
        results.append({
            "name": name,
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow),
            "shadow": shadow,
            "cleanup": cleanup,
        })
        return remote_id

    def record_error(name: str, exc: Exception) -> None:
        results.append({
            "name": name,
            "shadow_ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        })

    raw = tm_core.mem0_write(
        "codex",
        "systems",
        _canary_text("tm_core_mem0_write"),
        metadata_extra={"canary_entrypoint": "tm_core_mem0_write"},
    )
    record("tm_core.mem0_write", json.loads(raw))

    payload = tm_memory_ops.write_memory_with_review(
        "codex",
        "systems",
        _canary_text("tm_memory_ops_write_memory_with_review"),
        light=False,
        total_budget_s=None,
    )
    if payload.get("route") != "mem0":
        results.append({"name": "tm_memory_ops.write_memory_with_review", "shadow_ok": False, "error": payload})
    else:
        record("tm_memory_ops.write_memory_with_review", payload)

    body = json.dumps({
        "agent": "codex",
        "topic": "systems",
        "text": _canary_text("tm_http_write_memory"),
    }).encode("utf-8")
    req = urllib.request.Request(
        http_url.rstrip("/") + "/write_memory",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + tm_core.mcp_api_key(),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        record("tm-http /write_memory", json.loads(resp.read().decode("utf-8")))

    try:
        record(
            "tm_io mem0-write",
            _run_tm_io(
                ["mem0-write", "--agent", "codex", "--topic", "systems"],
                _canary_text("tm_io_mem0_write"),
            ),
        )
    except Exception as exc:
        record_error("tm_io mem0-write", exc)

    try:
        payload = _run_tm_io(
            [
                "write-inbox",
                "--agent",
                "codex",
                "--topic",
                "systems",
                "--title",
                "Dual write canary",
            ],
            _canary_text("tm_io_write_inbox_route_mem0"),
        )
        if payload.get("route") != "mem0":
            results.append({"name": "tm_io write-inbox route=mem0", "shadow_ok": False, "error": payload})
        else:
            record("tm_io write-inbox route=mem0", payload)
    except Exception as exc:
        record_error("tm_io write-inbox route=mem0", exc)

    try:
        result = tm_review_tools.execute_promote_mem0(
            {
                "id": "tm-dual-write-canary-review-promote",
                "topic": "systems",
                "text": _canary_text("tm_review_tools_execute_promote_mem0"),
                "source_type": "canary",
            },
            topic="systems",
        )
        if not result.get("ok") or not result.get("memory_id"):
            results.append({"name": "tm_review_tools.execute_promote_mem0", "shadow_ok": False, "error": result})
        else:
            record("tm_review_tools.execute_promote_mem0", {"id": result["memory_id"]})
    except Exception as exc:
        record_error("tm_review_tools.execute_promote_mem0", exc)

    try:
        create_payload = _parse_json(tm_core.mem0_write(
            "codex",
            "systems",
            _canary_text("tm_core_mem0_update_content_seed"),
            metadata_extra={"canary_entrypoint": "tm_core_mem0_update_content"},
        ))
        remote_id = _extract_id(create_payload)
        new_content = _canary_text("tm_core_mem0_update_content_replacement")
        tm_core.mem0_update_content(remote_id, new_content)
        shadow = _shadow_row(remote_id)
        cleanup = _cleanup_remote_and_shadow(remote_id)
        results.append({
            "name": "tm_core.mem0_update_content",
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow, shadow_state="mem0_updated") and shadow.get("content") == new_content,
            "shadow": shadow,
            "cleanup": cleanup,
        })
    except Exception as exc:
        record_error("tm_core.mem0_update_content", exc)

    try:
        create_payload = _run_tm_io(
            ["mem0-write", "--agent", "codex", "--topic", "systems"],
            _canary_text("tm_io_mem0_update_content_seed"),
        )
        remote_id = _extract_id(create_payload)
        new_content = _canary_text("tm_io_mem0_update_content_replacement")
        _run_tm_io(["mem0-update-content", "--id", remote_id], new_content)
        shadow = _shadow_row(remote_id)
        cleanup = _cleanup_remote_and_shadow(remote_id)
        results.append({
            "name": "tm_io mem0-update-content",
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow, shadow_state="mem0_updated") and shadow.get("content") == new_content,
            "shadow": shadow,
            "cleanup": cleanup,
        })
    except Exception as exc:
        record_error("tm_io mem0-update-content", exc)

    try:
        create_payload = _parse_json(tm_core.mem0_write(
            "codex",
            "systems",
            _canary_text("tm_core_mem0_delete_seed"),
            metadata_extra={"canary_entrypoint": "tm_core_mem0_delete"},
        ))
        remote_id = _extract_id(create_payload)
        delete_payload = _parse_json(tm_core.mem0_delete([remote_id]))
        shadow = _shadow_row(remote_id)
        results.append({
            "name": "tm_core.mem0_delete",
            "remote_id": remote_id,
            "shadow_ok": _shadow_matches(shadow, state="deleted", shadow_state="mem0_deleted"),
            "shadow": shadow,
            "delete": delete_payload,
            "cleanup": {"local_hard_deleted": _hard_delete_shadow(remote_id)},
        })
    except Exception as exc:
        record_error("tm_core.mem0_delete", exc)

    try:
        create_payload = _parse_json(tm_core.mem0_write(
            "codex",
            "systems",
            _canary_text("tm_review_tools_delete_seed"),
            metadata_extra={"canary_entrypoint": "tm_review_tools_mem0_delete_by_id"},
        ))
        remote_id = _extract_id(create_payload)
        deleted = tm_review_tools._mem0_delete_by_id(remote_id)
        shadow = _shadow_row(remote_id)
        results.append({
            "name": "tm_review_tools._mem0_delete_by_id",
            "remote_id": remote_id,
            "shadow_ok": bool(deleted) and _shadow_matches(shadow, state="deleted", shadow_state="mem0_deleted"),
            "shadow": shadow,
            "cleanup": {"local_hard_deleted": _hard_delete_shadow(remote_id)},
        })
    except Exception as exc:
        record_error("tm_review_tools._mem0_delete_by_id", exc)

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--days", type=int, default=14, help="route event replay window")
    parser.add_argument("--live-canary", action="store_true", help="create and clean live canary writes")
    parser.add_argument("--http-url", default="http://127.0.0.1:8790", help="tm-http base URL for live canary")
    args = parser.parse_args(argv)

    result: dict[str, Any] = {
        "generated_at": dt.datetime.now(TZ_CN).isoformat(),
        "repo_head": _git_head_short(),
        "profile": tm_core.tigermemory_profile(),
        "dual_write_enabled": tm_core._local_dual_write_enabled(),
        "local_db": str(tm_core._local_db_path()),
        "code_entrypoints": scan_code_entrypoints(),
        "service_env": service_env_audit(),
        "route_event_replay": route_event_replay(args.days),
    }
    if args.live_canary:
        result["live_canary"] = run_live_canary(args.http_url)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"generated_at: {result['generated_at']}")
    print(f"repo_head: {result['repo_head']} profile={result['profile']} dual_write={result['dual_write_enabled']}")
    print(f"local_db: {result['local_db']}")
    print("\nservice_env:")
    for row in result["service_env"]:
        print(f"- {row['unit']}: openmemory_env={row['uses_openmemory_env']} files={row['environment_files']}")
    print("\nroute_event_replay:")
    replay = result["route_event_replay"]
    print(f"- days={replay['days']} events={replay['event_count']} mem0_events={replay['mem0_event_count']}")
    for row in replay["mem0_signatures"][:20]:
        print(f"  - {row['count']} {row['signature']}")
    if args.live_canary:
        print("\nlive_canary:")
        for row in result["live_canary"]:
            print(f"- {row['name']}: shadow_ok={row.get('shadow_ok')} remote_id={row.get('remote_id')} cleanup={row.get('cleanup')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
