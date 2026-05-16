#!/usr/bin/env python3
"""Shared write_memory orchestration for MCP and HTTP adapters."""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable

import tm_core
import tm_route


DIGEST_DEBOUNCE_SECONDS = int(os.environ.get("TM_DIGEST_DEBOUNCE_SECONDS", "180"))
EMBED_REFRESH_DEBOUNCE_SECONDS = int(os.environ.get("TM_EMBED_REFRESH_DEBOUNCE_SECONDS", "180"))
EMBED_REFRESH_TIMEOUT_SECONDS = int(os.environ.get("TM_EMBED_REFRESH_TIMEOUT_SECONDS", "300"))
_digest_timer: threading.Timer | None = None
_digest_lock = threading.Lock()
_embed_timers: dict[str, threading.Timer] = {}
_embed_lock = threading.Lock()


def _refresh_digest_today() -> None:
    from tm_digest import generate_daily_digest

    today = datetime.datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d")
    generate_daily_digest(target_date=today, dry_run=False)


def schedule_digest_refresh() -> None:
    """Debounced digest refresh used by every write path."""
    global _digest_timer
    with _digest_lock:
        if _digest_timer is not None:
            _digest_timer.cancel()
        timer = threading.Timer(DIGEST_DEBOUNCE_SECONDS, _refresh_digest_today)
        timer.daemon = True
        _digest_timer = timer
        timer.start()


def _refresh_embed_index(scope: str, reason: str, paths: list[str]) -> None:
    script = tm_core.REPO_ROOT / "tools" / "tm_embed_index.py"
    cmd = [sys.executable, str(script), "refresh", "--scope", scope]
    try:
        proc = subprocess.run(
            cmd,
            cwd=tm_core.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=EMBED_REFRESH_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception as exc:
        print(
            f"[tm_memory_ops] WARN embed refresh failed before completion "
            f"scope={scope} reason={reason!r} paths={paths!r}: {exc}",
            file=sys.stderr,
        )
        return
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().replace("\n", " | ")[:500]
        print(
            f"[tm_memory_ops] WARN embed refresh exited {proc.returncode} "
            f"scope={scope} reason={reason!r} paths={paths!r}: {stderr}",
            file=sys.stderr,
        )


def schedule_embed_refresh(
    *,
    scope: str = "wiki",
    reason: str = "",
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Debounced embedding index refresh for long-lived write services."""
    if scope not in {"wiki", "wiki_only", "sources_only"}:
        raise ValueError("scope must be one of: wiki, wiki_only, sources_only")
    path_list = list(paths or [])
    with _embed_lock:
        existing = _embed_timers.get(scope)
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(
            EMBED_REFRESH_DEBOUNCE_SECONDS,
            _refresh_embed_index,
            args=(scope, reason, path_list),
        )
        timer.daemon = True
        _embed_timers[scope] = timer
        timer.start()
    return {
        "embed_refresh_scheduled": True,
        "embed_refresh_scope": scope,
        "embed_refresh_debounce_seconds": EMBED_REFRESH_DEBOUNCE_SECONDS,
    }


def extract_mem0_id(data: dict[str, Any]) -> str:
    """Return the created Mem0 id or raise if OpenMemory declined/failed the write."""
    direct_id = data.get("id")
    if isinstance(direct_id, str) and tm_core.MEM0_UUID_RE.fullmatch(direct_id):
        return direct_id

    if data.get("error"):
        raise RuntimeError(f"Mem0 write returned error: {str(data['error'])[:200]}")

    results = data.get("results")
    if isinstance(results, list):
        if not results:
            raise RuntimeError("Mem0 write returned results=[]; no memory id was created")
        for item in results:
            if not isinstance(item, dict):
                continue
            candidate = item.get("id")
            if isinstance(candidate, str) and tm_core.MEM0_UUID_RE.fullmatch(candidate):
                return candidate

    raise RuntimeError("Mem0 write response did not include a memory id")


def _verified_summary(memory_id: str, *, include_readback: bool) -> dict[str, Any]:
    if not include_readback:
        return {"direct_readback_ok": None, "reason": "not checked"}
    verify = tm_core.verify_memory_id(memory_id)
    return {
        "direct_readback_ok": bool(verify.get("direct_readback_ok")),
        "status": verify.get("status"),
        "state": verify.get("state"),
        "created_at_local": verify.get("created_at_local"),
        "text_len": verify.get("text_len"),
        "text_sha256_12": verify.get("text_sha256_12"),
        "search_by_id_self_hit": verify.get("search_by_id_self_hit"),
        "digest_date": verify.get("digest_date"),
        "digest_contains": verify.get("digest_contains"),
        "digest_inclusion_reason": verify.get("digest_inclusion_reason"),
        "warnings": verify.get("warnings", []),
    }


def _to_inbox(
    decision: tm_route.RouteDecision,
    agent: str,
    text: str,
) -> dict[str, Any]:
    fm_extra = decision.as_metadata()
    fm_extra["routed_by"] = "tigermemory"
    fm_extra["route_decision_reason"] = decision.reasons
    rel, sha = tm_core.write_and_commit_inbox(
        agent,
        decision.topic_inferred,
        f"Routed memory {decision.score}",
        text,
        frontmatter_extra=fm_extra,
    )
    schedule_digest_refresh()
    return {
        "route": "inbox",
        "path": rel,
        "commit_sha": sha,
        "url": tm_core.git_remote_blob_url(rel),
        "score": decision.score,
        "topic_inferred": decision.topic_inferred,
        "reasons": decision.reasons,
        "unreviewed": decision.unreviewed,
    }


def write_memory_with_review(
    agent: str,
    topic: str,
    text: str,
    *,
    force_inbox: bool = False,
    total_budget_s: int | None = None,
    mem0_min_reserve_s: int = 5,
    include_readback: bool = True,
    warn: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Route a memory write to discard/mem0/inbox with consistent fallback semantics."""
    t0 = time.monotonic()
    decision = tm_route.route_memory(text, topic, agent)
    if force_inbox:
        decision = tm_route.RouteDecision(
            route="inbox",
            score=decision.score,
            topic_inferred=decision.topic_inferred,
            issues=decision.issues,
            reasons=f"force_inbox override: {decision.reasons}",
            is_transient=decision.is_transient,
            is_sensitive=decision.is_sensitive,
            needs_human_review=decision.needs_human_review,
            unreviewed=decision.unreviewed,
        )

    if decision.route == "discard":
        return {
            "route": "discard",
            "score": decision.score,
            "issues": decision.issues,
            "reasons": decision.reasons,
        }

    if decision.route == "mem0":
        remaining: float | None = None
        if total_budget_s is not None:
            elapsed = time.monotonic() - t0
            remaining = total_budget_s - elapsed
            if remaining < mem0_min_reserve_s:
                if warn:
                    warn("budget_exhausted", {
                        "agent": agent,
                        "topic": decision.topic_inferred,
                        "elapsed": elapsed,
                        "total_budget_s": total_budget_s,
                    })
                decision = tm_route.RouteDecision(
                    route="inbox",
                    score=decision.score,
                    topic_inferred=decision.topic_inferred,
                    issues=decision.issues,
                    reasons=(
                        f"budget exhausted by route ({elapsed:.1f}s/{total_budget_s}s); "
                        f"fallback to inbox | original: {decision.reasons}"
                    ),
                    is_transient=decision.is_transient,
                    is_sensitive=decision.is_sensitive,
                    needs_human_review=decision.needs_human_review,
                    unreviewed=decision.unreviewed,
                )
        if decision.route == "mem0":
            timeout = tm_core.MEM0_WRITE_TIMEOUT
            if remaining is not None:
                timeout = max(mem0_min_reserve_s, int(min(tm_core.MEM0_WRITE_TIMEOUT, remaining)))
            try:
                data = json.loads(tm_core.mem0_write(
                    agent,
                    decision.topic_inferred,
                    text,
                    metadata_extra=decision.as_metadata(),
                    timeout=timeout,
                ))
                memory_id = extract_mem0_id(data)
                data["id"] = memory_id
                data["route"] = "mem0"
                data["score"] = decision.score
                data["topic_inferred"] = decision.topic_inferred
                data["reasons"] = decision.reasons
                try:
                    data["verified"] = _verified_summary(memory_id, include_readback=include_readback)
                except Exception as exc:
                    data["verified"] = {"direct_readback_ok": False, "error": str(exc)}
                    data.setdefault("warnings", []).append(f"direct readback failed: {exc}")
                schedule_digest_refresh()
                return data
            except Exception as exc:
                err = str(exc)
                if warn:
                    warn("mem0_fallback", {
                        "agent": agent,
                        "topic": decision.topic_inferred,
                        "text_len": len(text),
                        "error": err,
                    })
                decision = tm_route.RouteDecision(
                    route="inbox",
                    score=decision.score,
                    topic_inferred=decision.topic_inferred,
                    issues=decision.issues,
                    reasons=f"mem0 write failed, fallback to inbox: {err[:120]} | original: {decision.reasons}",
                    is_transient=decision.is_transient,
                    is_sensitive=decision.is_sensitive,
                    needs_human_review=decision.needs_human_review,
                    unreviewed=decision.unreviewed,
                )

    return _to_inbox(decision, agent, text)
