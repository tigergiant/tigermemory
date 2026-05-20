#!/usr/bin/env python3
"""Shared write_memory orchestration for MCP and HTTP adapters."""
from __future__ import annotations

import datetime
import json
import os
import re
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

PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
CN_ID_RE = re.compile(
    r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9Xx])"
)
BANK_KEYWORD = (
    r"(?:银行卡号?|卡号|银行账号|银行账户|借记卡|储蓄卡|信用卡|银联卡|"
    r"visa|master(?:card)?|amex|jcb|bank\s*card|"
    r"card\s*(?:no\.?|number)|account\s*(?:no\.?|number))"
)
BANK_KEYWORD_RE = re.compile(BANK_KEYWORD, re.IGNORECASE)
BANK_DIGITS = r"(?<![\dA-Fa-f])(?:\d[ -]?){13,19}(?![\dA-Fa-f])"
BANK_DIGITS_RE = re.compile(BANK_DIGITS)
BANK_CARD_CONTEXT_RE = re.compile(
    rf"(?is)(?:{BANK_KEYWORD}.{{0,24}}{BANK_DIGITS}|{BANK_DIGITS}.{{0,24}}{BANK_KEYWORD})"
)
CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer_token", re.compile(r"(?i)\b(?:Authorization\s*:\s*)?Bearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    (
        "credential",
        re.compile(
            r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|pwd|access[_-]?token|"
            r"refresh[_-]?token|private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s]{8,}"
        ),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    ),
]


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


def _light_sensitive_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if PHONE_RE.search(text):
        hits.append({"kind": "phone", "pattern": "PHONE_RE"})
    if CN_ID_RE.search(text):
        hits.append({"kind": "cn_id", "pattern": "CN_ID_RE"})
    hits.extend(_bank_card_hits(text))
    hits.extend(_credential_hits(text))
    return hits


def _luhn_valid(digits: str) -> bool:
    if not re.fullmatch(r"\d{13,19}", digits):
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _bank_card_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for match in BANK_DIGITS_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if not _luhn_valid(digits):
            continue
        context = text[max(0, match.start() - 24): min(len(text), match.end() + 24)]
        if BANK_KEYWORD_RE.search(context):
            hits.append({"kind": "bank_card", "pattern": "BANK_CARD_CONTEXT_RE"})
            break
    return hits


def _credential_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for kind, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            hits.append({"kind": kind, "pattern": pattern.pattern})
    return hits


def _to_inbox(
    decision: tm_route.RouteDecision,
    agent: str,
    text: str,
    *,
    requested_topic: str,
    storage_topic: str,
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fm_extra = decision.as_metadata()
    if metadata_extra:
        fm_extra.update(metadata_extra)
    fm_extra["route_requested_topic"] = requested_topic
    fm_extra["stored_topic"] = storage_topic
    fm_extra["routed_by"] = "tigermemory"
    fm_extra["route_decision_reason"] = decision.reasons
    rel, sha = tm_core.write_and_commit_inbox(
        agent,
        storage_topic,
        f"Routed memory {decision.score}",
        text,
        frontmatter_extra=fm_extra,
    )
    schedule_digest_refresh()
    result = {
        "route": "inbox",
        "path": rel,
        "commit_sha": sha,
        "url": tm_core.git_remote_blob_url(rel),
        "score": decision.score,
        "topic": storage_topic,
        "topic_inferred": decision.topic_inferred,
        "reasons": decision.reasons,
        "unreviewed": decision.unreviewed,
    }
    if metadata_extra:
        result.update(metadata_extra)
    return result


def _storage_topic(
    requested_topic: str,
    decision: tm_route.RouteDecision,
    *,
    preserve_requested_topic: bool,
) -> str:
    """Choose the final storage topic while keeping LLM inference auditable."""
    if not preserve_requested_topic:
        return decision.topic_inferred
    if decision.is_sensitive or decision.topic_inferred == "person":
        return decision.topic_inferred
    return requested_topic


def _route_metadata(
    decision: tm_route.RouteDecision,
    *,
    requested_topic: str,
    storage_topic: str,
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = decision.as_metadata()
    if metadata_extra:
        meta.update(metadata_extra)
    meta["route_requested_topic"] = requested_topic
    meta["stored_topic"] = storage_topic
    return meta


def _topic_warnings(
    requested_topic: str,
    decision: tm_route.RouteDecision,
    storage_topic: str,
) -> list[str]:
    if storage_topic == decision.topic_inferred:
        return []
    return [(
        f"topic mismatch: requested_topic={requested_topic}, "
        f"topic_inferred={decision.topic_inferred}, stored_topic={storage_topic}"
    )]


def write_memory_with_review(
    agent: str,
    topic: str,
    text: str,
    *,
    force_inbox: bool = False,
    light: bool = False,
    total_budget_s: int | None = None,
    mem0_min_reserve_s: int = 5,
    include_readback: bool = True,
    preserve_requested_topic: bool = True,
    warn: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Route a memory write to discard/mem0/inbox with consistent fallback semantics."""
    t0 = time.monotonic()
    if force_inbox and light:
        raise ValueError("force_inbox and light are mutually exclusive")
    route_metadata_extra: dict[str, Any] = {}
    if light:
        sensitive_hits = _light_sensitive_hits(text)
        if sensitive_hits:
            hit_types = [hit["kind"] for hit in sensitive_hits]
            decision = tm_route.RouteDecision(
                route="inbox",
                score=0,
                topic_inferred=topic,
                issues=[f"light_sensitive_regex:{kind}" for kind in hit_types],
                reasons=(
                    "light_bypass_sensitive_guard: local sensitive regex matched; "
                    "DeepSeek skipped; routed to inbox for human review"
                ),
                is_transient=False,
                is_sensitive=True,
                needs_human_review=True,
                unreviewed=False,
            )
            route_metadata_extra = {
                "light_bypass": True,
                "route_mode": "light_bypass",
                "light_sensitive_guard": True,
                "light_sensitive_hit_types": hit_types,
                "light_deepseek_called": False,
            }
        else:
            decision = tm_route.RouteDecision(
                route="mem0",
                score=50,
                topic_inferred=topic,
                issues=[],
                reasons=(
                    "light_bypass: explicit caller opt-in; DeepSeek skipped; "
                    "score=50 placeholder"
                ),
                is_transient=False,
                is_sensitive=False,
                needs_human_review=False,
                unreviewed=False,
            )
            route_metadata_extra = {
                "light_bypass": True,
                "route_mode": "light_bypass",
                "light_sensitive_guard": False,
                "light_sensitive_hit_types": [],
                "light_deepseek_called": False,
            }
    else:
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
            "topic": _storage_topic(topic, decision, preserve_requested_topic=preserve_requested_topic),
            "topic_inferred": decision.topic_inferred,
            "issues": decision.issues,
            "reasons": decision.reasons,
        }

    storage_topic = _storage_topic(
        topic,
        decision,
        preserve_requested_topic=preserve_requested_topic,
    )

    if decision.route == "mem0":
        remaining: float | None = None
        if total_budget_s is not None:
            elapsed = time.monotonic() - t0
            remaining = total_budget_s - elapsed
            if remaining < mem0_min_reserve_s:
                if warn:
                    warn("budget_exhausted", {
                        "agent": agent,
                        "topic": storage_topic,
                        "topic_inferred": decision.topic_inferred,
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
                    storage_topic,
                    text,
                    metadata_extra=_route_metadata(
                        decision,
                        requested_topic=topic,
                        storage_topic=storage_topic,
                        metadata_extra=route_metadata_extra,
                    ),
                    timeout=timeout,
                ))
                memory_id = extract_mem0_id(data)
                data["id"] = memory_id
                data["route"] = "mem0"
                data["score"] = decision.score
                data["topic"] = storage_topic
                data["topic_inferred"] = decision.topic_inferred
                data["reasons"] = decision.reasons
                if route_metadata_extra:
                    data.update(route_metadata_extra)
                if not isinstance(data.get("warnings"), list):
                    data["warnings"] = []
                data["warnings"].extend(_topic_warnings(topic, decision, storage_topic))
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
                        "topic": storage_topic,
                        "topic_inferred": decision.topic_inferred,
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

    storage_topic = _storage_topic(
        topic,
        decision,
        preserve_requested_topic=preserve_requested_topic,
    )
    result = _to_inbox(
        decision,
        agent,
        text,
        requested_topic=topic,
        storage_topic=storage_topic,
        metadata_extra=route_metadata_extra,
    )
    result.setdefault("warnings", []).extend(_topic_warnings(topic, decision, storage_topic))
    return result
