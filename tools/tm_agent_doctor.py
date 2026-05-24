#!/usr/bin/env python3
"""Read-only agent connect / doctor checks for tigermemory clients.
Inputs: Local repo state, service health endpoints, lessons/wiki pages, Mem0 records, or dashboard preference files.
Outputs: Doctor/audit/onboarding/metrics reports, local UI helper effects, or JSON diagnostics.
Depends-on (must-have): tm_core helpers, local filesystem/git state, and configured local services when the command asks for live checks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.request
from typing import Any

import _bootstrap_paths  # noqa: F401

import tigermemory_core as tm_core
import tigermemory_lessons as tm_lessons
import tm_review

REPO_ROOT = tm_core.REPO_ROOT
DEFAULT_QUERY = "retention dry-run agent doctor connect mem0 audit"


def _status(ok: bool) -> str:
    return "ok" if ok else "fail"


def _warn_status(ok: bool) -> str:
    return "ok" if ok else "warn"


def _http_health_url(base_url: str | None = None) -> str:
    if base_url:
        return base_url.rstrip("/") + "/health"
    import os

    explicit = os.environ.get("TM_HTTP_URL")
    if explicit:
        return explicit.rstrip("/") + "/health"
    host = os.environ.get("TM_HTTP_HOST", "127.0.0.1")
    port = os.environ.get("TM_HTTP_PORT", "8790")
    return f"http://{host}:{port}/health"


def check_worktree() -> dict[str, Any]:
    try:
        status = tm_core.git_session_status()
    except Exception as exc:
        return {
            "name": "worktree",
            "status": "fail",
            "ok": False,
            "error": str(exc),
        }
    return {
        "name": "worktree",
        "status": _status(bool(status.get("ok"))),
        "ok": bool(status.get("ok")),
        "head": status.get("head"),
        "branch": status.get("branch"),
        "upstream": status.get("upstream"),
        "ahead": status.get("ahead"),
        "behind": status.get("behind"),
        "dirty_count": status.get("dirty_count"),
        "blockers": status.get("blockers", []),
        "paths": status.get("paths", []),
    }


def check_tm_http(base_url: str | None = None, *, timeout: int = 3) -> dict[str, Any]:
    url = _http_health_url(base_url)
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {
            "name": "tm_http",
            "status": "warn",
            "ok": False,
            "url": url,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error": str(exc)[:200],
        }
    return {
        "name": "tm_http",
        "status": _warn_status(bool(data.get("ok"))),
        "ok": bool(data.get("ok")),
        "url": url,
        "latency_ms": round((time.time() - start) * 1000, 1),
        "health": data,
    }


def check_mem0(timeout: int = 3) -> dict[str, Any]:
    start = time.time()
    try:
        tm_core.mem0_request(
            f"{tm_core.mem0_base().rstrip('/')}/api/v1/memories/categories?user_id=tiger",
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "name": "mem0_api",
            "status": "warn",
            "ok": False,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error": str(exc)[:200],
        }
    return {
        "name": "mem0_api",
        "status": "ok",
        "ok": True,
        "latency_ms": round((time.time() - start) * 1000, 1),
    }


def check_l2_review(timeout: int = 5) -> dict[str, Any]:
    start = time.time()
    sample = "Agent doctor reachability probe. This is a read-only technical status check."
    review = tm_review.review_draft(sample, timeout=timeout)
    skipped = bool(review.get("review_skipped"))
    ok = not skipped and review.get("score") is not None
    return {
        "name": "l2_review",
        "status": "ok" if ok else "warn",
        "ok": ok,
        "latency_ms": round((time.time() - start) * 1000, 1),
        "score": review.get("score"),
        "review_skipped": skipped,
        "reason": review.get("reason"),
    }


def search_lessons(query: str = DEFAULT_QUERY, top: int = 3) -> dict[str, Any]:
    tokens = [t for t in query.split() if t]
    hits: list[dict[str, Any]] = []
    if not tm_lessons.LESSONS_DIR.exists():
        return {
            "name": "lessons",
            "status": "fail",
            "ok": False,
            "query": query,
            "hits": [],
            "error": f"lessons dir not found: {tm_lessons.LESSONS_DIR}",
        }
    for path in sorted(tm_lessons.LESSONS_DIR.glob("*.md")):
        if path.name == "index.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        score, title, _aliases, breakdown = tm_lessons._score_lesson(text, tokens, explain=True)
        if score > 0:
            hits.append({
                "score": score,
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "title": title,
                "breakdown": breakdown,
            })
    hits.sort(key=lambda row: (-row["score"], row["path"]))
    return {
        "name": "lessons",
        "status": _status(bool(hits)),
        "ok": bool(hits),
        "query": query,
        "hit_count": len(hits),
        "top": hits[:top],
    }


def recent_lessons_log(path: pathlib.Path = tm_lessons.LOG_FILE, *, max_rows: int = 20) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": "lessons_log",
            "status": "warn",
            "ok": False,
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "recent_count": 0,
        }
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_rows:]:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return {
        "name": "lessons_log",
        "status": "ok" if rows else "warn",
        "ok": bool(rows),
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "recent_count": len(rows),
        "last": rows[-1] if rows else None,
    }


def check_retention() -> dict[str, Any]:
    start = time.time()
    try:
        import tm_retention_audit
        # 1. 运行 sample 模式，验证工具完全可用
        report = tm_retention_audit.run_retention_audit(source="sample", max_items=3)
        if not report.get("ok"):
            raise ValueError(report.get("error") or "run_retention_audit failed")

        # 2. 静态检查 tools/tm_retention_audit.py 确实为纯离线，且不含在线 choices
        audit_file = pathlib.Path(tm_retention_audit.__file__)
        code = audit_file.read_text(encoding="utf-8")
        offline_violations = [
            kw for kw in ("mem0_request", "fetch_mem0", "urllib")
            if kw in code
        ]

        # 确认 argparse choices 里没有 "api" Choice
        has_api_source = "choices=[" in code and "api" in code.split("choices=[")[1].split("]")[0]

        if offline_violations:
            raise ValueError(f"Retention audit violated offline boundaries: found {offline_violations}")
        if has_api_source:
            raise ValueError("Retention audit contains forbidden 'api' source choice")

        return {
            "name": "retention_audit",
            "status": "ok",
            "ok": True,
            "dry_run": True,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "item_count": report.get("item_count"),
            "action_counts": report.get("action_counts"),
            "offline_only": True,
        }
    except Exception as exc:
        return {
            "name": "retention_audit",
            "status": "fail",
            "ok": False,
            "latency_ms": round((time.time() - start) * 1000, 1),
            "error": str(exc),
            "offline_only": True,
        }


def run_agent_doctor(
    *,
    query: str = DEFAULT_QUERY,
    include_l2: bool = True,
    http_url: str | None = None,
) -> dict[str, Any]:
    checks = [
        check_worktree(),
        check_tm_http(http_url),
        check_mem0(),
        search_lessons(query),
        recent_lessons_log(),
        check_retention(),
    ]
    if include_l2:
        checks.append(check_l2_review())
    hard_fail = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    return {
        "schema_version": "tm-agent-doctor-v1",
        "generated_at": dt.datetime.now(tm_core.TZ_CN).isoformat(timespec="seconds"),
        "ok": not hard_fail,
        "status": "fail" if hard_fail else ("warn" if warnings else "ok"),
        "checks": checks,
        "summary": {
            "fail_count": len(hard_fail),
            "warn_count": len(warnings),
            "ok_count": sum(1 for check in checks if check["status"] == "ok"),
        },
        "recommended_action": (
            "Resolve failing checks before editing or relying on tigermemory."
            if hard_fail
            else "Usable with warnings; inspect warn checks before high-stakes writes."
            if warnings
            else "Ready for agent work."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Tigermemory Agent Doctor",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- status: `{report['status']}`",
        f"- recommended_action: {report['recommended_action']}",
        "",
        "| check | status | evidence |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        evidence_bits = []
        for key in ("head", "branch", "dirty_count", "ahead", "behind", "latency_ms", "hit_count", "score", "error", "reason", "item_count", "action_counts", "offline_only"):
            if check.get(key) not in (None, "", []):
                evidence_bits.append(f"{key}={check[key]}")
        evidence = "; ".join(str(bit) for bit in evidence_bits).replace("|", "\\|")
        lines.append(f"| {check['name']} | {check['status']} | {evidence} |")
    return "\n".join(lines) + "\n"


def cmd_doctor(args: argparse.Namespace) -> int:
    report = run_agent_doctor(
        query=args.query,
        include_l2=not args.skip_l2,
        http_url=args.http_url,
    )
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_markdown(report))
    return 0 if report["status"] != "fail" else 6


def main() -> None:
    tm_core.configure_stdio()
    parser = argparse.ArgumentParser(prog="tm_agent_doctor.py", description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--http-url", default=None)
    parser.add_argument("--skip-l2", action="store_true", help="skip live DeepSeek/L2 probe")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    sys.exit(cmd_doctor(args))


if __name__ == "__main__":
    main()
