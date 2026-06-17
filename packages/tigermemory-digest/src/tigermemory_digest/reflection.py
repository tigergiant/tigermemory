#!/usr/bin/env python3
"""Daily and weekly report rendering for memory route reflection.

The renderer is intentionally read-only for routing policy. It may write the
daily/weekly markdown report, but code, prompt, policy, and test changes stay
in ask-confirm proposal material until a human runs cron-apply.
Inputs: CLI/API payloads, inbox or digest markdown, route decisions, proposal metadata, or Mem0 write requests.
Outputs: Rendered markdown, JSON status payloads, routed memory writes, proposal decisions, or review actions.
Depends-on (must-have): tm_core, tm_route/tm_memory_ops helpers, local git-managed files, and configured Mem0/OpenMemory endpoints.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable

import tigermemory_core as tm_core

REPO_ROOT = tm_core.REPO_ROOT
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import tm_route_audit

try:
    import tm_retention_audit
except Exception:  # pragma: no cover - degraded local runtime
    tm_retention_audit = None  # type: ignore[assignment]

try:
    import tm_mem0_audit
except Exception:  # pragma: no cover - degraded local runtime
    tm_mem0_audit = None  # type: ignore[assignment]

try:
    import tm_spec_capsule
except Exception:  # pragma: no cover - degraded local runtime
    tm_spec_capsule = None  # type: ignore[assignment]

INBOX_DIR = REPO_ROOT / "inbox"
OPERATIONS_DIR = REPO_ROOT / "wiki" / "operations"
PROPOSAL_ROOT = REPO_ROOT / ".tmp" / "cron-proposals"
DISCARD_ROOT = tm_route_audit.DEFAULT_AUDIT_ROOT
WSL_DISCARD_ROOT = pathlib.Path(
    os.environ.get("TM_WSL_DISCARD_ROOT")
    or str(
        pathlib.Path("\\\\" + "wsl.localhost")
        / "Ubuntu"
        / "home"
        / (os.environ.get("TM_WSL_USER") or pathlib.Path.home().name.lower())
        / "tigermemory"
        / ".tmp"
        / "memory-discard-quarantine"
    )
)
MEM0_AUDIT_ROOT = REPO_ROOT / ".tmp" / "mem0-audit"
INBOX_REVIEW_CACHE = REPO_ROOT / ".tmp" / "inbox-review-metadata-cache.json"
MAX_PREVIEW_CHARS = 160
STALE_INBOX_DAYS = 14
MISSING_SUMMARY_PREFIX = "未提供中文摘要"
SELF_EVOLUTION_HEADING = "🧭 Self-Evolution 候选"

INBOX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d{4}-([^-]+)-([^.]+)\.md$")
INBOX_REVIEW_PROMPT = """你是 tigermemory 的 inbox 审批摘要助手。
任务：把一个待审 inbox 文件提炼成给虎哥看的中文标题和中文预览。

要求：
- 只基于输入内容，不编造不存在的事实。
- title_cn：8-42 个中文字符左右，一句话说明这条 inbox 是什么。
- preview_cn：80-220 个中文字符，说明核心事实、为什么需要审批、可能动作。
- 不要输出“标题”“摘要”“元数据”“Routed memory”等空泛词。
- 不要包含 API key、token、私钥、密码、身份证、银行卡等敏感信息。
- 输出严格 JSON：{"title_cn":"...","preview_cn":"..."}。
"""


@dataclass(frozen=True)
class InboxAuditRow:
    path: str
    created_date: str
    age_days: int
    agent: str
    topic: str
    title_cn: str
    preview_cn: str
    summary_cn: str
    summary: str
    action: str
    reason: str
    codex_recommended_action: str
    codex_recommended_reason: str
    route_target: str = "inbox"
    route_label: str = "转人工 inbox"
    route_confidence: int = 72
    route_reason: str = ""
    route_flags: tuple[str, ...] = ()
    route_hard_rule: bool = False
    stale_archive: bool = False
    already_applied: bool = False
    knowledge_target: str = ""
    proposal_kind: str = ""
    wiki_partition: str = ""
    wiki_slug_hint: str = ""
    route_score: int | None = None
    l2_review_score: int | None = None
    target_confidence: int | None = None
    wiki_action: str = ""


_INBOX_REVIEW_LABEL_PREFIX = (
    "routed memory",
    "session-handoff",
)


def _extract_session_task_title(body: str) -> tuple[str, str]:
    lines = body.splitlines()
    task_title = ""
    task_previews: list[str] = []
    inside_task = False
    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if re.match(r"^\s*#{1,3}\s*Task\b", text, flags=re.I):
            inside_task = True
            continue
        if inside_task:
            if re.match(r"^\s*#{1,6}\s+\S+", text):
                break
            clean = tm_core._clean_inbox_summary(tm_core._strip_inbox_review_label(text), limit=88)
            if not clean or tm_core.inbox_review_cn_is_low_quality(clean):
                continue
            if not task_title:
                task_title = clean
                continue
            if len(task_previews) < 3:
                task_previews.append(clean)
            continue
    return task_title, " ".join(task_previews)


def today_local() -> str:
    return dt.datetime.now(tm_core.TZ_CN).strftime("%Y-%m-%d")


def _yesterday_local() -> str:
    return (dt.datetime.now(tm_core.TZ_CN).date() - dt.timedelta(days=1)).isoformat()


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _parse_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return dt.datetime.fromtimestamp(int(text), tz=dt.timezone.utc).astimezone(tm_core.TZ_CN)
        except (OverflowError, ValueError):
            return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tm_core.TZ_CN)
    return parsed.astimezone(tm_core.TZ_CN)


def _relpath(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        fm[key.strip()] = value.strip().strip('"')
    return fm, text[end + 5 :]


def _preview(text: str, limit: int = MAX_PREVIEW_CHARS) -> str:
    compact = re.sub(r"\s+", " ", tm_route_audit._redact(text)).strip()
    return compact[:limit].rstrip()


def _item_text(item: dict[str, Any]) -> str:
    return str(item.get("content") or item.get("memory") or item.get("text") or "")


def _item_meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata_") or item.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _date_of(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def _week_dates(anchor: dt.date) -> list[str]:
    monday = anchor - dt.timedelta(days=anchor.weekday())
    return [(monday + dt.timedelta(days=i)).isoformat() for i in range(7)]


def _previous_week_dates(anchor: dt.date) -> list[str]:
    return _week_dates(anchor - dt.timedelta(days=7))


def fetch_mem0_items(max_items: int = 500) -> list[dict[str, Any]]:
    if tm_retention_audit is None:
        return []
    try:
        return tm_retention_audit.fetch_mem0_items(max_items=max_items)
    except Exception:
        return []


def mem0_records_for_dates(dates: set[str], *, items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items if items is not None else fetch_mem0_items():
        created = _date_of(item.get("created_at") or item.get("createdAt"))
        if created not in dates:
            continue
        meta = _item_meta(item)
        rows.append({
            "id": item.get("id") or item.get("memory_id") or item.get("uuid"),
            "created_date": created,
            "topic": meta.get("topic") or meta.get("route_topic_inferred") or "unknown",
            "agent": meta.get("source") or meta.get("agent") or "unknown",
            "summary": _preview(_item_text(item)),
        })
    return rows


def _review_cache_key(path: pathlib.Path, text: str) -> str:
    raw = f"{_relpath(path)}\n{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _load_review_cache(cache_path: pathlib.Path | None = None) -> dict[str, Any]:
    cache_path = cache_path or INBOX_REVIEW_CACHE
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_review_cache(cache: dict[str, Any], cache_path: pathlib.Path | None = None) -> None:
    cache_path = cache_path or INBOX_REVIEW_CACHE
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return


def _needs_live_review_llm(title_cn: str, preview_cn: str, body: str) -> bool:
    if not _valid_review_cn(title_cn, preview_cn):
        return True
    title = str(title_cn or "").strip()
    preview = str(preview_cn or "").strip()
    surface = f"{title}\n{preview}".lower()
    noisy_markers = (
        "routed memory",
        "post-response closeout summary",
        "sanitized cascade response",
        "rules used for this response",
        "file://",
        "](file",
    )
    if any(marker in surface for marker in noisy_markers):
        return True
    if re.search(r"^\s*(以下是|先做|好的|我会|我先|这里是|收到|数据出来了)", title):
        return True
    body_low = body[:800].lower()
    if (
        ("post-response closeout summary" in body_low or "sanitized cascade response" in body_low)
        and ("…" in title or "元数据" in preview or title == preview or len(preview) < 100)
    ):
        return True
    if ("[" in title and "](" in title) or "`" in title:
        return True
    if title == preview and len(preview) < 80:
        return True
    if len(preview) < 70 and len(body.strip()) > 300:
        return True
    return False


def _cached_deepseek_review_cn(
    path: pathlib.Path,
    title: str,
    body: str,
    text: str,
    cache: dict[str, Any],
) -> tuple[str, str, str] | None:
    key = _review_cache_key(path, text)
    cached = cache.get(key)
    if isinstance(cached, dict):
        title_cn = str(cached.get("title_cn") or "")
        preview_cn = str(cached.get("preview_cn") or "")
        if _valid_review_cn(title_cn, preview_cn):
            return title_cn, preview_cn, str(cached.get("source") or "deepseek_cache")
    try:
        title_cn, preview_cn, source = _deepseek_review_cn(path, title, body)
    except Exception:
        return None
    cache[key] = {
        "path": _relpath(path),
        "title_cn": title_cn,
        "preview_cn": preview_cn,
        "source": source,
        "updated": today_local(),
    }
    return title_cn, preview_cn, f"{source}_runtime"


def inbox_records(*, inbox_dir: pathlib.Path = INBOX_DIR, use_llm: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not inbox_dir.exists():
        return rows
    cache = _load_review_cache() if use_llm else {}
    cache_changed = False
    for path in sorted(inbox_dir.glob("*.md")):
        match = INBOX_RE.match(path.name)
        if not match:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _frontmatter(text)
        title_cn = fm.get("title_cn") or fm.get("summary_cn")
        preview_cn = fm.get("preview_cn") or fm.get("summary_cn")
        task_title, task_preview = _extract_session_task_title(body)
        if title_cn:
            title_cn = tm_core._strip_inbox_review_label(str(title_cn))
        if preview_cn:
            preview_clean = tm_core._clean_inbox_preview(preview_cn)
            preview_stripped = tm_core._strip_inbox_review_label(preview_clean)
            # A preview copied from the generic "标题 ..." line is usually not
            # a real summary. Re-derive it from the body, preferably the 摘要
            # section, instead of showing the heading line twice in the UI.
            preview_cn = "" if preview_clean != preview_stripped and preview_clean.startswith("标题") else preview_stripped
        if tm_core.inbox_review_cn_is_low_quality(title_cn):
            title_cn = ""
        if tm_core.inbox_review_cn_is_low_quality(preview_cn):
            preview_cn = ""
        if not title_cn or not preview_cn:
            derived_title, derived_preview, _source = tm_core.derive_inbox_review_cn(fm.get("title") or path.stem, body)
            if not title_cn:
                title_cn = derived_title
            if not preview_cn:
                preview_cn = derived_preview
        if not title_cn and task_title:
            title_cn = task_title
        if not preview_cn and task_preview:
            preview_cn = task_preview
        if any(prefix in str(title_cn).lower() for prefix in _INBOX_REVIEW_LABEL_PREFIX) and task_title:
            title_cn = task_title
        summary_cn = fm.get("summary_cn") or title_cn
        if tm_core.inbox_review_cn_is_low_quality(summary_cn):
            summary_cn = title_cn
        if tm_core.inbox_review_cn_is_low_quality(title_cn):
            title_cn = _preview(body) or fm.get("title") or path.stem
        if tm_core.inbox_review_cn_is_low_quality(preview_cn):
            preview_cn = _preview(body)
        review_source = str(fm.get("review_cn_source") or "")
        if use_llm and _needs_live_review_llm(str(title_cn), str(preview_cn), body):
            llm_review = _cached_deepseek_review_cn(path, fm.get("title") or path.stem, body, text, cache)
            if llm_review:
                title_cn, preview_cn, review_source = llm_review
                summary_cn = title_cn
                cache_changed = True
        rows.append({
            "path": _relpath(path),
            "created_date": match.group(1),
            "agent": fm.get("agent") or fm.get("owner") or match.group(2),
            "topic": fm.get("topic") or match.group(3),
            "title_cn": title_cn,
            "preview_cn": preview_cn,
            "summary_cn": summary_cn,
            "summary": _preview(body),
            "review_cn_source": review_source,
            "route_score": fm.get("route_score"),
            "l2_review_score": fm.get("l2_review_score"),
            "target_confidence": fm.get("target_confidence"),
            "route_decision_reason": fm.get("route_decision_reason"),
            "knowledge_target": fm.get("knowledge_target"),
            "proposal_kind": fm.get("proposal_kind"),
            "wiki_partition": fm.get("wiki_partition"),
            "wiki_slug_hint": fm.get("wiki_slug_hint"),
            "wiki_action": fm.get("wiki_action"),
        })
    if use_llm and cache_changed:
        _save_review_cache(cache)
    return rows


def _frontmatter_lines(fm: dict[str, str], keys_order: list[str] | None = None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for key in keys_order or []:
        if key in fm:
            ordered.append(f"{key}: {fm[key]}")
            seen.add(key)
    for key, value in fm.items():
        if key not in seen:
            ordered.append(f"{key}: {value}")
    return ordered


def _write_frontmatter(path: pathlib.Path, fm: dict[str, str], body: str, keys_order: list[str] | None = None) -> None:
    text = "\n".join(["---", *_frontmatter_lines(fm, keys_order), "---", body.lstrip("\n")])
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def _valid_review_cn(title_cn: str, preview_cn: str) -> bool:
    if tm_core.inbox_review_cn_is_low_quality(title_cn):
        return False
    if tm_core.inbox_review_cn_is_low_quality(preview_cn):
        return False
    if not re.search(r"[\u4e00-\u9fff]", title_cn + preview_cn):
        return False
    if len(title_cn.strip()) < 4 or len(preview_cn.strip()) < 30:
        return False
    return True


def _deepseek_review_cn(path: pathlib.Path, title: str, body: str, *, timeout: int = 12) -> tuple[str, str, str]:
    user_msg = (
        "请输出 JSON 对象，结构为 {\"title_cn\":\"...\",\"preview_cn\":\"...\"}。\n\n"
        f"文件：{_relpath(path)}\n"
        f"原始标题：{title}\n\n"
        f"正文：\n{tm_route_audit._redact(body)[:7000]}"
    )
    ok, parsed = tm_core._call_deepseek_json(
        INBOX_REVIEW_PROMPT,
        user_msg,
        timeout=timeout,
        temperature=0.1,
        max_tokens=900,
        purpose="inbox_review_summary",
    )
    if not ok or not isinstance(parsed, dict):
        raise RuntimeError(str(parsed))
    title_cn = tm_core._clean_inbox_summary(str(parsed.get("title_cn") or ""), limit=42)
    preview_cn = tm_core._clean_inbox_preview(str(parsed.get("preview_cn") or ""), limit=220)
    if not _valid_review_cn(title_cn, preview_cn):
        raise RuntimeError("DeepSeek returned low quality inbox review metadata")
    return title_cn, preview_cn, "deepseek"


def repair_inbox_review_metadata(
    *,
    inbox_dir: pathlib.Path = INBOX_DIR,
    limit: int = 20,
    use_llm: bool = True,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for path in sorted(inbox_dir.glob("*.md")):
        if len(changed) >= limit:
            break
        if not INBOX_RE.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        fm, body = _frontmatter(text)
        current_title = str(fm.get("title_cn") or fm.get("summary_cn") or "")
        current_preview = str(fm.get("preview_cn") or fm.get("summary_cn") or "")
        derived_title, derived_preview, source = tm_core.derive_inbox_review_cn(fm.get("title") or path.stem, body)
        new_title = current_title
        new_preview = current_preview
        new_source = str(fm.get("review_cn_source") or "")

        if not _valid_review_cn(current_title, current_preview):
            new_title, new_preview, new_source = derived_title, derived_preview, source
        if use_llm and not _valid_review_cn(new_title, new_preview):
            try:
                new_title, new_preview, new_source = _deepseek_review_cn(path, fm.get("title") or path.stem, body)
            except Exception as exc:
                changed.append({
                    "path": _relpath(path),
                    "changed": False,
                    "error": str(exc),
                    "source": "deepseek_failed",
                })
                continue
        if not _valid_review_cn(new_title, new_preview):
            continue
        if current_title == new_title and current_preview == new_preview:
            continue

        fm["title_cn"] = new_title
        fm["preview_cn"] = new_preview
        fm["review_cn_source"] = new_source
        fm["summary_cn"] = new_title
        fm["summary_cn_source"] = "title_cn"
        if not dry_run:
            _write_frontmatter(path, fm, body, keys_order=[
                "owner", "status", "updated", "route_decision", "route_score",
                "route_topic_inferred", "route_requested_topic", "stored_topic",
                "routed_by", "route_decision_reason", "title_cn", "preview_cn",
                "review_cn_source", "summary_cn", "summary_cn_source",
            ])
        changed.append({
            "path": _relpath(path),
            "changed": not dry_run,
            "dry_run": dry_run,
            "source": new_source,
            "title_cn": new_title,
            "preview_chars": len(new_preview),
        })
    return changed


def applied_inbox_paths(*, proposal_root: pathlib.Path = PROPOSAL_ROOT) -> set[str]:
    applied: set[str] = set()
    if not proposal_root.exists():
        return applied
    for path in proposal_root.glob("*/*/applied.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidates: list[Any] = []
        for key in ("paths", "targets", "inbox_paths"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        for key in ("path", "target", "inbox_path"):
            if data.get(key):
                candidates.append(data.get(key))
        for raw in candidates:
            text = str(raw).replace("\\", "/")
            if text.startswith("inbox/") and text.endswith(".md"):
                applied.add(text)
    return applied


def audit_inbox(
    *,
    date: str,
    inbox_dir: pathlib.Path = INBOX_DIR,
    proposal_root: pathlib.Path = PROPOSAL_ROOT,
    use_llm: bool = False,
) -> list[InboxAuditRow]:
    today = _parse_date(date)
    applied = applied_inbox_paths(proposal_root=proposal_root)
    rows: list[InboxAuditRow] = []
    for record in inbox_records(inbox_dir=inbox_dir, use_llm=use_llm):
        created = _parse_date(str(record["created_date"]))
        age = max(0, (today - created).days)
        record_path = str(record["path"]).replace("\\", "/")
        repo_style_path = record_path if record_path.startswith("inbox/") else f"inbox/{pathlib.PurePath(record_path).name}"
        already_applied = record_path in applied or repo_style_path in applied
        if already_applied:
            action = "keep_in_inbox"
            reason = "history shows an applied action already touched this inbox file"
            stale = False
        elif age >= STALE_INBOX_DAYS:
            action = "archive"
            reason = f"{age} days old and no applied action found; 14-day fallback"
            stale = True
        else:
            action = "keep_in_inbox"
            reason = "not older than 14 days; keep for daily review"
            stale = False
        (
            route_target,
            route_label,
            route_confidence,
            route_flags,
            route_reason,
            route_hard_rule,
        ) = _codex_route_recommendation(
            record,
            action=action,
            reason=reason,
            age_days=age,
            stale=stale,
        )
        recommended_action = route_label
        recommended_reason = route_reason
        rows.append(InboxAuditRow(
            path=str(record["path"]),
            created_date=str(record["created_date"]),
            age_days=age,
            agent=str(record["agent"]),
            topic=str(record["topic"]),
            title_cn=str(record["title_cn"]),
            preview_cn=str(record["preview_cn"]),
            summary_cn=str(record["summary_cn"]),
            summary=str(record["summary"]),
            action=action,
            reason=reason,
            codex_recommended_action=recommended_action,
            codex_recommended_reason=recommended_reason,
            route_target=route_target,
            route_label=route_label,
            route_confidence=route_confidence,
            route_reason=route_reason,
            route_flags=route_flags,
            route_hard_rule=route_hard_rule,
            stale_archive=stale,
            already_applied=already_applied,
            knowledge_target=str(record.get("knowledge_target") or ""),
            proposal_kind=str(record.get("proposal_kind") or ""),
            wiki_partition=str(record.get("wiki_partition") or ""),
            wiki_slug_hint=str(record.get("wiki_slug_hint") or ""),
            route_score=_int(record.get("route_score")),
            l2_review_score=_int(record.get("l2_review_score")),
            target_confidence=_int(record.get("target_confidence")),
            wiki_action=str(record.get("wiki_action") or ""),
        ))
    return rows


def discard_events_for_dates(
    dates: Iterable[str],
    *,
    audit_root: pathlib.Path = DISCARD_ROOT,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for root in _candidate_discard_roots(audit_root):
        for date in dates:
            for row in tm_route_audit.load_discard_events(date=date, audit_root=root):
                key = _discard_event_key(row)
                if key in seen:
                    continue
                seen.add(key)
                events.append(row)
    return events


def _candidate_discard_roots(primary: pathlib.Path) -> list[pathlib.Path]:
    roots = [primary]
    if WSL_DISCARD_ROOT != primary and WSL_DISCARD_ROOT.exists():
        roots.append(WSL_DISCARD_ROOT)
    return roots


def _discard_event_key(row: dict[str, Any]) -> tuple[str, str]:
    event_id = str(row.get("event_id") or "").strip()
    if event_id:
        return ("event_id", event_id)
    text_sha = str(row.get("text_sha256_12") or "").strip()
    if text_sha:
        return ("text_sha256_12", text_sha)
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return ("row_sha256", hashlib.sha256(payload.encode("utf-8")).hexdigest())


def discard_review_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in events:
        score = row.get("score")
        high_score = isinstance(score, int) and score >= 70
        non_transient = not bool(row.get("is_transient")) and not bool(row.get("is_sensitive"))
        if not high_score and not non_transient:
            continue
        reason = "high_score_discard" if high_score else "non_transient_discard"
        candidates.append({
            "event_id": row.get("event_id"),
            "score": score,
            "reason": reason,
            "agent": row.get("agent") or "unknown",
            "requested_topic": row.get("requested_topic") or "unknown",
            "topic_inferred": row.get("topic_inferred") or "unknown",
            "original_reason": row.get("reasons") or "",
            "excerpt": _preview(str(row.get("text_excerpt") or "")),
        })
    return candidates


def proposal_dirs(date: str, *, proposal_root: pathlib.Path = PROPOSAL_ROOT) -> list[pathlib.Path]:
    root = proposal_root / date
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and path.name.startswith("proposal-"))


def load_proposals(date: str, *, proposal_root: pathlib.Path = PROPOSAL_ROOT) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for pdir in proposal_dirs(date, proposal_root=proposal_root):
        meta_path = pdir / "proposal.json"
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            except json.JSONDecodeError:
                meta = {"warning": "proposal.json is invalid JSON"}
        patch_path = pdir / "patch"
        patch_text = ""
        patch_preview = ""
        patch_lines = 0
        if patch_path.exists():
            try:
                patch_text = patch_path.read_text(encoding="utf-8")
                lines = patch_text.splitlines()
            except OSError:
                lines = []
            patch_lines = len([line for line in lines if line.strip()])
            patch_preview = "\n".join(lines[:15])
        replay_path = pdir / "replay-result.json"
        replay: dict[str, Any] | None = None
        if replay_path.exists():
            try:
                loaded = json.loads(replay_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    replay = loaded
            except json.JSONDecodeError:
                replay = {"error": "invalid replay-result.json"}
        applied = (pdir / "applied.json").exists()
        rejected = (pdir / "rejected.json").exists()
        proposal_type = meta.get("type") or meta.get("proposal_type") or "other"
        if tm_spec_capsule is not None:
            capsule = tm_spec_capsule.load_proposal_capsule(pdir, meta=meta)
            capsule_required = tm_spec_capsule.capsule_required_for(
                str(proposal_type),
                patch_text=patch_text,
                meta=meta,
            )
        else:
            capsule = {"present": False, "ok": False, "missing_sections": []}
            capsule_required = False
        proposals.append({
            "id": pdir.name,
            "type": proposal_type,
            "trigger": meta.get("trigger") or meta.get("evidence") or "not provided",
            "impact": meta.get("impact") or "not provided",
            "summary": meta.get("summary") or meta.get("diff_summary") or "",
            "patch_preview": patch_preview,
            "patch_lines": patch_lines,
            "replay": replay,
            "spec_capsule": capsule,
            "spec_capsule_required": capsule_required,
            "applied": applied,
            "rejected": rejected,
        })
    return proposals


def _append_proposal_spec_capsule(lines: list[str], proposal: dict[str, Any]) -> None:
    capsule = proposal.get("spec_capsule") or {}
    required = bool(proposal.get("spec_capsule_required"))
    present = bool(capsule.get("present"))
    ok = bool(capsule.get("ok"))
    missing = capsule.get("missing_sections") or []
    summary = capsule.get("summary") if isinstance(capsule.get("summary"), dict) else {}
    status = "complete" if ok else ("missing" if required and not present else "incomplete" if present else "not_required")
    lines.extend([
        "**Spec Capsule**："
        + status
        + ("（高风险提案建议先补齐卡片）" if required and not ok else ""),
        "",
    ])
    if ok or present:
        for key, label in (
            ("problem", "问题"),
            ("evidence", "证据"),
            ("constraints", "约束"),
            ("solution", "方案"),
            ("acceptance", "验收"),
            ("rollback", "回滚"),
            ("needs_tiger_confirmation", "是否需要虎哥确认"),
        ):
            value = str(summary.get(key) or "").strip()
            if value:
                lines.append(f"- {label}：{value}")
        if missing:
            lines.append(f"- 缺失：{', '.join(str(item) for item in missing)}")
        lines.append("")


def _applied_rows(*, proposal_root: pathlib.Path = PROPOSAL_ROOT, limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not proposal_root.exists():
        return rows
    for path in proposal_root.glob("*/*/applied.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_path"] = _relpath(path)
        rows.append(data)
    rows.sort(key=lambda row: str(row.get("applied_at") or ""), reverse=True)
    return rows[:limit]


def _score_quality(mem0_count: int, inbox_count: int, discard_count: int, candidate_count: int, stale_count: int) -> int:
    total = mem0_count + inbox_count + discard_count
    score = 100
    if total:
        score -= min(30, int((discard_count / total) * 30))
    score -= min(30, candidate_count * 8)
    score -= min(20, stale_count * 5)
    return max(0, min(100, score))


def _codex_route_recommendation(
    record: dict[str, Any],
    *,
    action: str,
    reason: str,
    age_days: int,
    stale: bool,
) -> tuple[str, str, int, tuple[str, ...], str, bool]:
    title = str(record.get("title_cn") or "")
    preview = str(record.get("preview_cn") or "")
    raw = str(record.get("summary") or "")
    route_reason = str(record.get("route_decision_reason") or "")
    reason_text = str(reason or "")
    topic = str(record.get("topic") or "")
    path = str(record.get("path") or "")
    route_score = str(record.get("route_score") or "").strip()
    try:
        route_score_value = int(route_score)
    except (TypeError, ValueError):
        route_score_value = None

    text = " ".join([title, preview, raw, route_reason, reason_text, topic, path]).lower()
    knowledge_target = str(record.get("knowledge_target") or "")
    proposal_kind = str(record.get("proposal_kind") or "")
    wiki_partition = str(record.get("wiki_partition") or "")
    wiki_slug_hint = str(record.get("wiki_slug_hint") or "")
    generated_investment_markers = (
        "wiki/investment/decision-log",
        "decision-log",
        "portfolio-fast-scan",
        "tools/tm_adapter.py",
        "generated by `tools/tm_adapter.py`",
    )

    if action == "archive" or stale:
        return (
            "discard",
            "归档",
            96,
            ("stale-archive",),
            f"已停留 {age_days} 天且没有 apply 记录，超过 14 天兜底线；建议先归档。",
            True,
        )

    route_flags: list[str] = []

    is_session_handoff = any(
        marker in text
        for marker in (
            "memory_type: session-handoff",
            "memory_type=session-handoff",
            "session-handoff",
            "session handoff",
        )
    )
    if is_session_handoff:
        route_flags.append("legacy_session_handoff")
        return (
            "mem0",
            "旧交接卡",
            94,
            tuple(route_flags),
            "历史 Session Handoff Card 已由新 fast path 接管；旧 inbox 项默认折叠，不再进入每日人工决策主队列。",
            True,
        )

    if (
        topic == "investment"
        and knowledge_target == "wiki_proposal"
        and proposal_kind == "wiki"
        and (wiki_partition == "investment" or "investment" in path)
        and (any(marker in text for marker in generated_investment_markers) or "decision-log" in wiki_slug_hint)
    ):
        return (
            "wiki",
            "自动投研流水",
            96,
            ("auto-generated-investment-log",),
            "TradingAgents/decision-log 自动生成流水已在 Wiki 投研区落盘，默认隐藏出人工审批主队列。",
            True,
        )

    # Low-score raw capture and low-value routed memory should not进入 mem0/wiki
    if route_score_value is not None and route_score_value <= 30:
        return (
            "discard",
            "归档",
            93,
            ("route_score_low",),
            f"判定 route_score={route_score_value}，疑似 low-score capture，优先归档。",
            True,
        )
    if any(
        marker in text
        for marker in (
            "openclaw-turn-capture-low-score",
            "routed memory 0",
            "openclaw turn capture",
            "turn capture",
            "low-score",
        )
    ):
        route_flags.append("low-quality-capture")
        return (
            "discard",
            "归档",
            92,
            tuple(route_flags),
            "低分/raw capture 型样本，建议 discard（避免污染 mem0/wiki）。",
            True,
        )

    # 投研长文 / 研究纪要 / 标的 / 证券代码：不进 Mem0，默认沉到 wiki
    is_investment_context = topic == "investment" or "-investment" in path or "/investment" in path
    has_investment_signal = any(
        keyword in text
        for keyword in ("投研", "研究纪要", "标的", "证券代码", "交易代码", "ticker", "investment longform", "投资长文", "组合复核", "研报")
    )
    if is_investment_context and has_investment_signal:
        route_flags.append("investment_longform")
        return (
            "wiki",
            "写入 Wiki",
            88,
            tuple(route_flags),
            "investment_longform 风险说明：这类内容偏长期研究资料，不建议写入 Mem0；倾向沉淀到 Wiki。",
            True,
        )

    # 临时故障、告警、前置条件未满足等：人工介入（inbox）
    has_fault_context = any(
        keyword in text
        for keyword in ("临时故障", "告警", "前置条件未满足", "blocked", "paused", "connect failed", "连接失败", "失败", "未满足")
    )
    has_fault_followup = any(keyword in text for keyword in ("跳过", "未恢复", "未创建", "未发通知"))
    has_fault_anchor = any(
        keyword in text
        for keyword in ("告警", "失败", "blocked", "前置", "connect", "通知", "恢复", "cron", "qmt", "xtquant")
    )
    if has_fault_context or (has_fault_followup and has_fault_anchor):
        route_flags.append("needs_manual_inbox")
        return (
            "inbox",
            "转人工 inbox",
            90,
            tuple(route_flags),
            "涉及故障/告警/预检未通过项，建议先转人工 inbox 判断，不直接写入 Mem0/Wiki。",
            True,
        )

    # 稳定规则/流程/runbook/policy/边界：wiki
    if any(
        keyword in text
        for keyword in ("规则", "契约", "长期", "policy", "runbook", "prompt", "边界", "流程", "标准")
    ):
        route_flags.append("policy_or_stable")
        return (
            "wiki",
            "写入 Wiki",
            86,
            tuple(route_flags),
            "内容像稳定规则/边界描述，适合沉淀到 wiki 长期事实库。",
            False,
        )

    # 近期反馈 / 会话收尾 / commit/push / 测试通过：mem0
    if any(
        keyword in text
        for keyword in (
            "偏好",
            "反馈",
            "近期",
            "本次",
            "会话",
            "session-handoff",
            "session handoff",
            "closeout",
            "commit",
            "push",
            "测试通过",
            "完成",
        )
    ):
        route_flags.append("short_term_feedback")
        return (
            "mem0",
            "写入 Mem0",
            85,
            tuple(route_flags),
            "近期反馈/会话收尾/变更收尾类内容，适合进入短期记忆。",
            False,
        )

    return (
        "inbox",
        "转人工 inbox",
        76,
        ("unresolved",),
        "未命中高置信关键模式，先保留人工复核。",
        False,
    )


def _codex_recommendation(record: dict[str, Any], *, action: str, reason: str, age_days: int, stale: bool) -> tuple[str, str]:
    route_target, route_label, _route_confidence, _route_flags, route_reason, _route_hard_rule = _codex_route_recommendation(
        record,
        action=action,
        reason=reason,
        age_days=age_days,
        stale=stale,
    )
    return route_label, route_reason


def _inbox_action_groups(rows: list[InboxAuditRow]) -> tuple[list[InboxAuditRow], list[InboxAuditRow], list[InboxAuditRow]]:
    archive_rows = [row for row in rows if row.action == "archive"]
    promote_rows = [row for row in rows if row.action in {"promote_to_mem0", "promote_to_wiki"}]
    keep_rows = [row for row in rows if row.action not in {"archive", "promote_to_mem0", "promote_to_wiki"}]
    return archive_rows, promote_rows, keep_rows


def _is_low_priority_inbox_noise(row: InboxAuditRow) -> bool:
    return any(
        flag in {"legacy_session_handoff", "auto-generated-investment-log"}
        for flag in row.route_flags
    )


def _is_inbox_wiki_proposal(row: InboxAuditRow) -> bool:
    return row.knowledge_target == "wiki_proposal" or row.proposal_kind == "wiki"


def _wiki_proposal_target(row: InboxAuditRow) -> str:
    partition = row.wiki_partition or row.topic
    slug = row.wiki_slug_hint.strip().strip("/")
    if partition and slug:
        return f"wiki/{partition}/{slug.removesuffix('.md')}.md"
    if partition:
        return f"wiki/{partition}/(needs-slug)"
    return "(unknown target)"


def _wiki_proposal_target_parts(row: InboxAuditRow) -> tuple[str, str]:
    partition = (row.wiki_partition or row.topic or "").strip().strip("/")
    slug = row.wiki_slug_hint.strip().strip("/").removesuffix(".md")
    return partition, slug


_INVESTMENT_DOC_TYPE_LABELS = {
    "rule": "长期投资规则",
    "workflow": "投研系统规则",
    "stock_research": "单股长期研究",
    "decision": "决策记录",
    "scan": "组合/自动扫描",
    "private_signal": "私域线索",
    "trade_log": "交易/账户流水",
    "report": "PDF/长报告",
    "raw_evidence": "原始研究证据",
    "discard": "低价值/占位",
}


_INVESTMENT_REVIEW_LABELS = {
    "auto_archive": "只归档证据",
    "wiki_candidate": "可写入 Wiki",
    "proposal": "投资线程审核",
    "human_review": "人工复核",
}


_A_SHARE_SYMBOL_RE = re.compile(r"\b(?P<code>[036]\d{5})(?:\.(?P<suffix>SH|SZ))?\b", re.IGNORECASE)
_DECISION_MONTH_RE = re.compile(r"\b(20\d{2}-\d{2})\b")
_NEW_PROJECT_PATH_RE = re.compile(
    re.escape("C:")
    + r"\\Users\\Giant\\Documents\\New project\\(?:reports|data|logs|config)\\[^\n\r`\"']+",
    re.IGNORECASE,
)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _investment_symbol(text: str) -> str | None:
    match = _A_SHARE_SYMBOL_RE.search(text)
    if not match:
        return None
    code = match.group("code")
    suffix = (match.group("suffix") or "").upper()
    if suffix:
        return f"{code}.{suffix}"
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    return code


def _investment_decision_month(text: str, fallback_date: str) -> str | None:
    match = _DECISION_MONTH_RE.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", fallback_date or ""):
        return fallback_date[:7]
    return None


def _investment_original_paths(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in _NEW_PROJECT_PATH_RE.finditer(text):
        value = match.group(0).rstrip(" .,;，。；)")
        if value not in seen:
            seen.add(value)
            paths.append(value)
    return paths


def _investment_proposal_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("target") or ""),
        str(item.get("target_slug") or ""),
        str(item.get("wiki_actions") or ""),
    ]
    for sample in item.get("sample_items") or []:
        if isinstance(sample, dict):
            parts.extend([
                str(sample.get("path") or ""),
                str(sample.get("title") or ""),
                str(sample.get("preview") or ""),
                str(sample.get("summary") or ""),
            ])
    return "\n".join(parts)


def _investment_triage_for_item(item: dict[str, Any]) -> dict[str, Any]:
    raw_text = _investment_proposal_text(item)
    text = raw_text.lower()
    target = str(item.get("target") or "")
    slug = str(item.get("target_slug") or "").strip("/")
    symbol = _investment_symbol(raw_text)
    decision_month = _investment_decision_month(raw_text, str(item.get("newest_date") or ""))
    original_paths = _investment_original_paths(raw_text)

    contains_account_data = _contains_any(
        text,
        ("账户", "现金", "净值", "持仓明细", "成交", "委托", "order", "account", "cash", "position detail"),
    )
    contains_trade_action = _contains_any(
        text,
        ("买入", "卖出", "加仓", "减仓", "清仓", "调仓", "持有", "watch", "buy", "sell", "reduce", "add", "hold"),
    )
    contains_private_signal = _contains_any(text, ("飞书", "群聊", "私域", "private-signal", "private_signal", "feishu"))
    system_knowledge = _contains_any(
        text,
        (
            "架构",
            "设计",
            "系统规则",
            "稳定规则",
            "运行模型",
            "运行经验",
            "工作流",
            "workflow",
            "trading node",
            "miniqmt",
            "b_qmt",
            "qmt",
            "dashboard",
            "adapter",
            "kill switch",
            "审批",
            "边界",
            "诊断",
            "通知链路",
            "投票账本",
            "outcome learning",
            "资料索引",
            "research agent",
            "数据源",
        ),
    )

    doc_type = "stock_research"
    target_path = target if target.startswith("wiki/investment/") else "wiki/investment/research/(needs-symbol).md"
    storage_path = ""
    review_level = "proposal"
    reason = "投资类 Wiki proposal 需要投资线程确认后再写入长期 Wiki。"
    evidence_level = "medium" if (symbol or original_paths or (item.get("route_score_min") or 0) >= 70) else "weak"

    if _contains_any(text, ("暂无数据", "占位", "placeholder", "empty")):
        doc_type = "discard"
        target_path = ""
        review_level = "auto_archive"
        evidence_level = "none"
        reason = "内容像占位或低信息量流水，投资线程可直接归档，不应进 Wiki。"
    elif system_knowledge:
        doc_type = "workflow"
        if target.startswith("wiki/investment/"):
            target_path = target
        elif "miniqmt" in text or "qmt" in text or "trading node" in text:
            target_path = "wiki/investment/miniqmt-integration-status.md"
        elif "data-source" in text or "数据源" in text:
            target_path = "wiki/investment/data-source-capability-registry.md"
        elif "dashboard" in text or "cio" in text or "投票账本" in text:
            target_path = "wiki/investment/investment-dashboard-hub.md"
        else:
            target_path = "wiki/investment/research-workflow.md"
        review_level = "wiki_candidate"
        reason = "这是投研系统规则、架构边界或运行经验；信息价值优先，可写入 Wiki，并在正文标注来源和待确认点。"
    elif contains_private_signal:
        doc_type = "private_signal"
        target_path = ""
        storage_path = f"sources/investment/private-signals/feishu/{decision_month or 'YYYY-MM'}.jsonl"
        review_level = "auto_archive"
        reason = "私域/飞书线索先存原始证据，不直接进入长期 Wiki。"
    elif contains_account_data or _contains_any(text, ("b_qmt", "交易流水", "委托", "成交", "订单")):
        doc_type = "trade_log"
        target_path = ""
        storage_path = f"sources/investment/trading/{item.get('newest_date') or 'YYYY-MM-DD'}/"
        review_level = "human_review"
        reason = "含交易、账户或执行数据，只能由投资线程人工复核后摘要，不自动写 Wiki。"
    elif _contains_any(text, ("portfolio-fast-scan", "快扫", "扫描", "dsa", "ta scan", "持仓扫描")):
        doc_type = "scan"
        target_path = "wiki/investment/decision-log/portfolio-YYYY-MM.md"
        storage_path = f"sources/investment/scans/{item.get('newest_date') or 'YYYY-MM-DD'}/<run_id>.md"
        review_level = "auto_archive"
        reason = "组合快扫默认归档为证据；只有高价值摘要才进入 decision-log 提案。"
    elif _contains_any(text, ("final_decision", "final decision", "decision-log", "决策", "评级", "动作")) or contains_trade_action:
        doc_type = "decision"
        if symbol and decision_month:
            target_path = f"wiki/investment/decision-log/{symbol}-{decision_month}.md"
        elif symbol:
            target_path = f"wiki/investment/decision-log/{symbol}-YYYY-MM.md"
        else:
            target_path = "wiki/investment/decision-log/(needs-symbol)-YYYY-MM.md"
        storage_path = "sources/investment/research-runs/<SYMBOL>/<YYYY-MM-DD>/<run_id>/"
        review_level = "proposal" if not contains_account_data else "human_review"
        reason = "决策类内容只能 append 到 decision-log 提案，不能覆盖旧记录。"
    elif _contains_any(text, ("portfolio-rules", "组合规则", "风控", "阈值", "买卖纪律", "审批边界", "交易权限")):
        doc_type = "rule"
        target_path = "wiki/investment/portfolio-rules.md" if "portfolio-rules" in text else f"wiki/investment/rules/{slug or '(needs-slug)'}.md"
        review_level = "wiki_candidate"
        reason = "长期投资规则可直接形成 Wiki 草稿；缺少证据链时在正文标注待确认，不应只进归档。"
    elif _contains_any(text, (".pdf", "pdf", "长报告", "正式报告", "deerflow report", "reports\\")):
        doc_type = "report"
        target_path = f"wiki/investment/research/{symbol}.md" if symbol else "wiki/investment/research/(needs-symbol).md"
        storage_path = "sources/investment/reports/<YYYY-MM-DD>/<run_id>/"
        review_level = "wiki_candidate"
        reason = "PDF/长报告可进入 Wiki 摘要页；原件路径作为来源保存，缺少细节不阻止沉淀。"
    elif _contains_any(text, ("research-workflow", "data-source", "capability-registry", "miniqmt", "openclaw", "hermes", "deerflow", "tushare", "数据源")):
        doc_type = "workflow"
        if "miniqmt" in text or "qmt" in text:
            target_path = "wiki/investment/miniqmt-integration-status.md"
        elif "data-source" in text or "数据源" in text:
            target_path = "wiki/investment/data-source-capability-registry.md"
        else:
            target_path = "wiki/investment/research-workflow.md"
        review_level = "wiki_candidate"
        reason = "投研系统规则归到稳定入口页；证据链只影响置信度标注，不应阻止写入。"
    elif _contains_any(text, ("fundamentals", "news", "market", "sentiment", "risk_debate", "investment_debate", "research-runs")):
        doc_type = "raw_evidence"
        target_path = ""
        storage_path = "sources/investment/research-runs/<SYMBOL>/<YYYY-MM-DD>/<run_id>/"
        review_level = "auto_archive"
        reason = "详细研究产物先作为原始证据归档，不直接进长期 Wiki。"
    elif symbol:
        doc_type = "stock_research"
        target_path = f"wiki/investment/research/{symbol}.md"
        reason = "单股稳定研究结论可形成 research 页提案，但需要投资线程审核证据边界。"

    return {
        "investment_doc_type": doc_type,
        "investment_doc_type_label": _INVESTMENT_DOC_TYPE_LABELS[doc_type],
        "investment_target_path": target_path,
        "investment_storage_path": storage_path,
        "investment_review_level": review_level,
        "investment_review_label": _INVESTMENT_REVIEW_LABELS[review_level],
        "reason": reason,
        "original_paths": original_paths,
        "preserve_original": True,
        "copy_only": True,
        "symbol": symbol,
        "decision_month": decision_month,
        "evidence_level": evidence_level,
        "contains_trade_action": contains_trade_action,
        "contains_private_signal": contains_private_signal,
        "contains_account_data": contains_account_data,
    }


def investment_triage_for_wiki_proposal_row(row: InboxAuditRow) -> dict[str, Any]:
    """Return conservative investment archive guidance for a single wiki proposal row."""
    target = _wiki_proposal_target(row)
    target_partition, target_slug = _wiki_proposal_target_parts(row)
    item: dict[str, Any] = {
        "target": target,
        "target_partition": target_partition,
        "target_slug": target_slug,
        "count": 1,
        "first_date": row.created_date,
        "newest_date": row.created_date,
        "topics": row.topic,
        "agents": row.agent,
        "paths": [row.path],
        "sample_items": [{
            "path": row.path,
            "title": row.title_cn,
            "preview": row.preview_cn,
            "summary": row.summary,
            "route_score": row.route_score,
            "l2_review_score": row.l2_review_score,
            "target_confidence": row.target_confidence,
            "wiki_action": row.wiki_action,
        }],
        "route_score_min": row.route_score,
        "route_score_max": row.route_score,
        "l2_review_score_min": row.l2_review_score,
        "l2_review_score_max": row.l2_review_score,
        "target_confidence_min": row.target_confidence,
        "target_confidence_max": row.target_confidence,
        "wiki_actions": row.wiki_action,
        "status": "investment-thread",
    }
    return _investment_triage_for_item(item)


def _inbox_wiki_proposal_ledger(rows: list[InboxAuditRow]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = _wiki_proposal_target(row)
        target_partition, target_slug = _wiki_proposal_target_parts(row)
        item = grouped.setdefault(
            target,
            {
                "target": target,
                "target_partition": target_partition,
                "target_slug": target_slug,
                "count": 0,
                "first_date": row.created_date,
                "newest_date": row.created_date,
                "topics": set(),
                "agents": set(),
                "paths": [],
                "sample_items": [],
                "route_scores": [],
                "l2_review_scores": [],
                "target_confidences": [],
                "wiki_actions": set(),
                "status": "pending",
            },
        )
        item["count"] += 1
        item["first_date"] = min(str(item["first_date"]), row.created_date)
        item["newest_date"] = max(str(item["newest_date"]), row.created_date)
        item["topics"].add(row.topic)
        item["agents"].add(row.agent)
        item["paths"].append(row.path)
        if row.route_score is not None:
            item["route_scores"].append(row.route_score)
        if row.l2_review_score is not None:
            item["l2_review_scores"].append(row.l2_review_score)
        if row.target_confidence is not None:
            item["target_confidences"].append(row.target_confidence)
        if row.wiki_action:
            item["wiki_actions"].add(row.wiki_action)
        if len(item["sample_items"]) < 3:
            item["sample_items"].append({
                "path": row.path,
                "title": row.title_cn,
                "preview": row.preview_cn,
                "summary": row.summary,
                "route_score": row.route_score,
                "l2_review_score": row.l2_review_score,
                "target_confidence": row.target_confidence,
                "wiki_action": row.wiki_action,
            })
        if row.already_applied:
            item["status"] = "applied"
        elif row.topic == "investment" or row.wiki_partition == "investment":
            item["status"] = "investment-thread"
    ledger = list(grouped.values())
    for item in ledger:
        item["topics"] = ",".join(sorted(item["topics"]))
        item["agents"] = ",".join(sorted(item["agents"]))
        item["wiki_actions"] = ",".join(sorted(item["wiki_actions"])) if item["wiki_actions"] else ""
        route_scores = item.pop("route_scores")
        l2_scores = item.pop("l2_review_scores")
        target_confidences = item.pop("target_confidences")
        item["route_score_min"] = min(route_scores) if route_scores else None
        item["route_score_max"] = max(route_scores) if route_scores else None
        item["l2_review_score_min"] = min(l2_scores) if l2_scores else None
        item["l2_review_score_max"] = max(l2_scores) if l2_scores else None
        item["target_confidence_min"] = min(target_confidences) if target_confidences else None
        item["target_confidence_max"] = max(target_confidences) if target_confidences else None
        if item["status"] == "investment-thread":
            triage = _investment_triage_for_item(item)
            item["investment_triage"] = triage
            if triage.get("investment_review_level") == "wiki_candidate":
                item["status"] = "investment-wiki"
                item["review_level"] = "high" if (item["route_score_min"] or 0) >= 80 and (item["l2_review_score_min"] or 0) >= 80 else "medium"
                item["review_label"] = "可写入投研 Wiki"
            else:
                item["review_level"] = "handoff"
                item["review_label"] = "投资提案归档"
        if item["status"] not in {"investment-thread", "investment-wiki"} and (item["route_score_min"] or 0) >= 80 and (item["l2_review_score_min"] or 0) >= 80:
            item["review_level"] = "high"
            item["review_label"] = "高可信"
        elif item["status"] not in {"investment-thread", "investment-wiki"} and (item["route_score_min"] or 0) >= 80 and (item["l2_review_score_min"] or 0) >= 70:
            item["review_level"] = "medium"
            item["review_label"] = "中等可信"
        elif item["status"] not in {"investment-thread", "investment-wiki"}:
            item["review_level"] = "review"
            item["review_label"] = "需复核"
    ledger.sort(key=lambda item: (-int(item["count"]), str(item["target"])))
    return ledger


def inbox_wiki_proposal_ledger(rows: Iterable[InboxAuditRow]) -> list[dict[str, Any]]:
    """Build the public review ledger for inbox rows that already route to wiki proposals."""
    wiki_rows = [
        row
        for row in rows
        if _is_inbox_wiki_proposal(row) and not _is_low_priority_inbox_noise(row)
    ]
    return _inbox_wiki_proposal_ledger(wiki_rows)


def _append_inbox_wiki_proposal_ledger(lines: list[str], ledger: list[dict[str, Any]], *, limit: int = 20) -> None:
    total = sum(int(row["count"]) for row in ledger)
    lines.extend([
        "",
        "## 🧾 Inbox Wiki Proposal 台账",
        "",
        f"- 待追踪 inbox/wiki_proposal：{total} 条，聚合为 {len(ledger)} 个目标页。",
    ])
    if not ledger:
        lines.append("- none")
        return
    table: list[str] = [
        "| 目标页 | 状态 | 数量 | 首次/最新 | 样例 inbox |",
        "|---|---|---:|---|---|",
    ]
    for row in ledger[:limit]:
        paths = "<br>".join(f"`{path}`" for path in row["paths"])
        table.append(
            f"| `{row['target']}` | {row['status']} | {row['count']} | "
            f"{row['first_date']} / {row['newest_date']} | {paths} |"
        )
    if len(ledger) > limit:
        table.append(f"| ... | truncated | {len(ledger) - limit} 组未展示 | - | - |")
    lines.extend(_details_block(f"展开 {len(ledger)} 个 wiki proposal 目标页", table))


def _append_inbox_row(lines: list[str], row: InboxAuditRow) -> None:
    flag = " **高亮：14 天兜底 archive**" if row.stale_archive else ""
    lines.extend([
        f"- `{row.path}`{flag}",
        f"  - 入库时间：{row.created_date}，已停留 {row.age_days} 天",
        f"  - 中文标题：{row.title_cn}",
        f"  - 中文预览：{row.preview_cn}",
        f"  - 原文预览：{row.summary}",
        f"  - Codex 推荐操作：{row.codex_recommended_action}",
        f"  - Codex 推荐理由：{row.codex_recommended_reason}",
        f"  - 路由建议：{row.route_label}",
        f"  - 路由置信度：{row.route_confidence}",
        f"  - 路由标记：{','.join(row.route_flags) if row.route_flags else '无'}",
        f"  - 路由解释：{row.route_reason or row.codex_recommended_reason}",
        f"  - 硬规则触发：{'是' if row.route_hard_rule else '否'}",
        f"  - cron 建议动作：{row.action}",
        f"  - 建议理由：{row.reason}",
        "  - 虎哥裁决：[ ] apply  [ ] reject",
    ])


def _load_mem0_dedup_candidates(date: str, *, audit_root: pathlib.Path = MEM0_AUDIT_ROOT) -> list[dict[str, Any]]:
    if tm_mem0_audit is not None:
        return tm_mem0_audit.load_dedup_candidates(date, audit_root=audit_root)
    path = audit_root / date / "dedup_candidates.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _empty_self_evolution_summary(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "event_count": 0,
        "counts": {},
        "outcome_pending": 0,
        "samples": [],
        "inbox_route": "AGENTS.md section 9.3 topic=selfevolution",
    }


def _collect_self_evolution_summary_for_date(
    date: str,
    *,
    repo_root: pathlib.Path = REPO_ROOT,
) -> dict[str, Any]:
    """Collect optional self-evolution summary for a daily report section.

    Return an empty summary when collector module/JSON is unavailable.
    """
    try:
        from tm_self_evolution import collect_summary_for_date
    except Exception:
        return _empty_self_evolution_summary(date)

    try:
        payload = collect_summary_for_date(date, root=repo_root)
    except Exception:
        return _empty_self_evolution_summary(date)

    if not isinstance(payload, dict):
        return _empty_self_evolution_summary(date)
    return payload


def _append_mem0_dedup_row(lines: list[str], row: dict[str, Any]) -> None:
    lines.extend([
        f"- `{row.get('candidate_id')}` :: agent={row.get('agent')} topic={row.get('topic')} dist={row.get('signature_distance')}",
        f"  - canonical: `{row.get('canonical_id')}`",
        f"  - 内容预览：{row.get('preview') or ''}",
        "  - 建议动作：dedup_to_canonical",
        "  - 虎哥裁决：[ ] confirm  [ ] reject",
    ])


def _details_block(summary: str, body: list[str]) -> list[str]:
    return ["<details>", f"<summary>{summary}</summary>", "", *body, "", "</details>"]


def _append_self_evolution_section(lines: list[str], summary: dict[str, Any]) -> None:
    event_count = int(summary.get("event_count") or 0)
    if event_count <= 0:
        return
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    samples = summary.get("samples") if isinstance(summary.get("samples"), list) else []
    lines.extend([
        "",
        f"## {SELF_EVOLUTION_HEADING}",
        "",
        f"- 只读证据事件：{event_count} 条",
        f"- 类型分布：`{json.dumps(counts, ensure_ascii=False, sort_keys=True)}`",
        f"- outcome 待回填：{int(summary.get('outcome_pending') or 0)} 条",
        "- 治理路径：规则 / lesson 提案统一走 AGENTS.md §9.3 的 selfevolution inbox，不直接写 lessons。",
    ])
    sample_body: list[str] = []
    for row in samples:
        if not isinstance(row, dict):
            continue
        sample_body.append(
            "- "
            f"`{row.get('event_type')}` agent={row.get('agent')} "
            f"rule={row.get('rule_id')} evidence={row.get('evidence_ref')} :: "
            f"{row.get('summary') or ''}"
        )
    if not sample_body:
        sample_body.append("- none")
    lines.extend([""])
    lines.extend(_details_block(f"self-evolution 样本（{len(sample_body)} 条）", sample_body))


def _learning_card_lines(
    *,
    date: str,
    mem0_count: int,
    inbox_today_count: int,
    discard_count: int,
    candidate_count: int,
    stale_count: int,
    proposal_count: int,
    mem0_audit_count: int,
    self_evolution_count: int,
) -> list[str]:
    issues: list[str] = []
    if proposal_count:
        issues.append(f"有 {proposal_count} 个 Proposed Change 需要裁决")
    if candidate_count:
        issues.append(f"有 {candidate_count} 条 discard 可能误判")
    if stale_count:
        issues.append(f"有 {stale_count} 条 inbox 达到 14 天兜底")
    if mem0_audit_count:
        issues.append(f"有 {mem0_audit_count} 条 Mem0 重复 / 误判候选")

    if issues:
        conclusion = f"{date} 的重点不是新增记忆数量，而是处理：" + "；".join(issues) + "。"
    elif mem0_count == 0 and inbox_today_count == 0 and discard_count == 0:
        conclusion = f"{date} 没有新增三源记忆，日报主要承担连续性检查和历史 inbox 复审。"
    else:
        conclusion = f"{date} 路由总体平稳，未发现需要立即升级的误判信号。"

    new_issue = "无明确新增问题。"
    if candidate_count:
        new_issue = "discard 候选需要复核，确认是否有重要内容被误判为瞬态。"
    elif mem0_audit_count:
        new_issue = "Mem0 audit 出现候选，需要判断是否重复、低密度或 topic 错分。"
    elif proposal_count:
        new_issue = "存在未裁决 proposal，需要 apply / reject，避免长期悬空。"

    repeated_issue = "无重复问题。"
    if stale_count:
        repeated_issue = "inbox 继续积压，14 天兜底项应优先归档或明确保留理由。"
    elif self_evolution_count:
        repeated_issue = "self-evolution 事件持续产生，应在周报中只抽取模式，不重复阅读原始事件。"

    action = "继续观察。"
    if proposal_count:
        action = "优先裁决 Proposed Changes。"
    elif stale_count:
        action = "优先处理 14 天兜底 archive。"
    elif candidate_count or mem0_audit_count:
        action = "优先复核误判 / 重复候选。"

    sediment = "无新沉淀。"
    if proposal_count or candidate_count:
        sediment = "将误判模式沉淀为 prompt / test / policy 提案。"
    elif self_evolution_count:
        sediment = "将重复 self-evolution 模式沉淀为 lesson 或治理提案。"

    return [
        "## 🧩 今日沉淀卡",
        "",
        f"- 结论：{conclusion}",
        f"- 新问题：{new_issue}",
        f"- 重复问题：{repeated_issue}",
        f"- 建议行动：{action}",
        f"- 应沉淀内容：{sediment}",
        "",
    ]


def render_daily_report(
    *,
    date: str,
    now_iso: str | None = None,
    mem0_items: list[dict[str, Any]] | None = None,
    inbox_dir: pathlib.Path = INBOX_DIR,
    audit_root: pathlib.Path = DISCARD_ROOT,
    mem0_audit_root: pathlib.Path = MEM0_AUDIT_ROOT,
    proposal_root: pathlib.Path = PROPOSAL_ROOT,
) -> str:
    now_iso = now_iso or dt.datetime.now(tm_core.TZ_CN).isoformat()
    mem0_rows = mem0_records_for_dates({date}, items=mem0_items)
    inbox_all = audit_inbox(date=date, inbox_dir=inbox_dir, proposal_root=proposal_root)
    inbox_today = [row for row in inbox_all if row.created_date == date]
    discard_events = discard_events_for_dates([date], audit_root=audit_root)
    candidates = discard_review_candidates(discard_events)
    mem0_dedup_candidates = _load_mem0_dedup_candidates(date, audit_root=mem0_audit_root)
    proposals = load_proposals(date, proposal_root=proposal_root)
    applied = [row for row in _applied_rows(proposal_root=proposal_root) if str(row.get("applied_at") or "").startswith(date)]
    self_evolution_summary = _collect_self_evolution_summary_for_date(date, repo_root=REPO_ROOT)
    self_evolution_count = int(self_evolution_summary.get("event_count") or 0)
    archive_rows, promote_rows, keep_rows = _inbox_action_groups(inbox_all)
    low_priority_rows = [row for row in keep_rows if _is_low_priority_inbox_noise(row)]
    keep_rows = [row for row in keep_rows if not _is_low_priority_inbox_noise(row)]
    wiki_proposal_rows = [row for row in keep_rows if _is_inbox_wiki_proposal(row)]
    keep_rows = [row for row in keep_rows if not _is_inbox_wiki_proposal(row)]
    wiki_proposal_ledger = _inbox_wiki_proposal_ledger(wiki_proposal_rows)
    stale_count = sum(1 for row in archive_rows if row.stale_archive)
    promote_count = len(promote_rows)
    quality_score = _score_quality(len(mem0_rows), len(inbox_today), len(discard_events), len(candidates), stale_count)

    lines: list[str] = [
        "---",
        "owner: codex",
        "status: active",
        f"updated: {date}",
        f"aliases: [\"每日记忆日报\", \"memory digest {date}\"]",
        f"title: \"Memory Digest {date}\"",
        f"last_run_at: {now_iso}",
        f"mem0_count: {len(mem0_rows)}",
        f"inbox_count: {len(inbox_today)}",
        f"discard_count: {len(discard_events)}",
        f"proposal_count: {len(proposals)}",
        f"applied_count: {len(applied)}",
        f"stale_archive_count: {stale_count}",
        f"promote_candidate_count: {promote_count}",
        f"low_priority_inbox_count: {len(low_priority_rows)}",
        f"wiki_proposal_inbox_count: {len(wiki_proposal_rows)}",
        f"mem0_audit_candidate_count: {len(mem0_dedup_candidates)}",
        f"self_evolution_count: {self_evolution_count}",
        "---",
        "",
        f"# Memory Digest {date}",
        "",
        "## ⚡ 今日要决策",
        "",
        f"- 🔴 14 天兜底 archive 候选：{stale_count} 条 → 见下方 §inbox 决策区",
        f"- 🟡 promote_to_mem0 / promote_to_wiki 候选：{promote_count} 条 → 见下方 §inbox 决策区",
        f"- 🔵 Proposed Changes：{len(proposals)} 条 → 见下方 §Proposed Changes",
        f"- 🧾 Inbox Wiki Proposal 台账：{len(wiki_proposal_rows)} 条 / {len(wiki_proposal_ledger)} 组 → 见下方 §Inbox Wiki Proposal 台账",
        f"- 🟢 Mem0 重复 / 误判候选：{len(mem0_dedup_candidates)} 条 → 见下方 §Mem0 重复 / 误判候选",
        f"- ⚪ discard 误判候选：{len(candidates)} 条 → 见下方 §discard 误判候选",
        "",
        "## 摘要",
        "",
        (
            f"{date} 记忆路由日报：Mem0 正式写入 {len(mem0_rows)} 条，"
            f"inbox 当日新增 {len(inbox_today)} 条，discard quarantine {len(discard_events)} 条；"
            f"路由质量自评分 {quality_score}/100，Proposed Changes {len(proposals)} 条；"
            f"Self-Evolution 候选 {self_evolution_count} 条。"
        ),
        "",
        *_learning_card_lines(
            date=date,
            mem0_count=len(mem0_rows),
            inbox_today_count=len(inbox_today),
            discard_count=len(discard_events),
            candidate_count=len(candidates),
            stale_count=stale_count,
            proposal_count=len(proposals),
            mem0_audit_count=len(mem0_dedup_candidates),
            self_evolution_count=self_evolution_count,
        ),
        "## 📊 当日三源汇总",
        "",
        "| 源 | count | 链接 |",
        "|---|---:|---|",
        f"| Mem0 当日正式写入 | {len(mem0_rows)} | 见 §附录 / Mem0 当日正式写入 |",
        f"| inbox 当日新增 | {len(inbox_today)} | 见 §附录 / inbox 当日新增 |",
        f"| discard quarantine | {len(discard_events)} | 见 §附录 / discard quarantine |",
        "",
        "## 🔍 discard 误判候选",
        "",
    ]
    if candidates:
        for idx, row in enumerate(candidates[:20], 1):
            lines.extend([
                f"- 候选 {idx}：event_id={row['event_id']}, score={row['score']}, "
                f"requested_topic={row['requested_topic']}, agent={row['agent']}",
                f"  - 复审信号：{row['reason']}；建议由 cron 主代理决定是否生成 §Proposed Changes",
                f"  - 原 LLM 决策：{row['original_reason']}",
                f"  - 文本预览（已 redact）：\"{row['excerpt']}\"",
            ])
    else:
        lines.append("- none")

    lines.extend(["", "## 📝 inbox 决策区", "", "### 🔴 建议 archive", ""])
    if archive_rows:
        for row in archive_rows:
            _append_inbox_row(lines, row)
    else:
        lines.append("- none")
    lines.extend(["", "### 🟡 建议 promote", ""])
    if promote_rows:
        for row in promote_rows:
            _append_inbox_row(lines, row)
    else:
        lines.append("- none")
    keep_body: list[str] = []
    if keep_rows:
        for row in keep_rows:
            _append_inbox_row(keep_body, row)
    else:
        keep_body.append("- none")
    lines.extend(["", "### ⚪ 仅观察 keep_in_inbox", ""])
    lines.extend(_details_block(f"展开 {len(keep_rows)} 条 keep_in_inbox", keep_body))
    low_priority_body: list[str] = []
    if low_priority_rows:
        for row in low_priority_rows:
            _append_inbox_row(low_priority_body, row)
    else:
        low_priority_body.append("- none")
    lines.extend(["", "### 💤 自动折叠：旧交接 / 自动流水", ""])
    lines.extend(_details_block(f"展开 {len(low_priority_rows)} 条低优先级历史项", low_priority_body))

    _append_inbox_wiki_proposal_ledger(lines, wiki_proposal_ledger)

    lines.extend([
        "",
        "## 🟢 Mem0 重复 / 误判候选",
        "",
        "### 🟢 重复候选 (dedup)",
        "",
    ])
    if mem0_dedup_candidates:
        for row in mem0_dedup_candidates:
            _append_mem0_dedup_row(lines, row)
    else:
        lines.append("- none")
    lines.extend([
        "",
        "### 🟠 主题误判候选 (topic)",
        "",
        "- none",
        "",
        "### 🟡 低密度候选 (low-density)",
        "",
        "- none",
    ])

    lines.extend(["", "## 🧠 Proposed Changes", ""])
    if proposals:
        for proposal in proposals:
            replay = proposal.get("replay") or {}
            lines.extend([
                f"#### {proposal['id']}",
                "",
                f"**类型**：{proposal['type']}",
                "",
                f"**触发证据**：{proposal['trigger']}",
                "",
                "**diff 摘要**：",
                "",
                "```diff",
                str(proposal.get("patch_preview") or "(no patch preview)")[:1200],
                "```",
                "",
                f"**影响范围**：{proposal['impact']}",
                "",
            ])
            _append_proposal_spec_capsule(lines, proposal)
            lines.extend([
                "**7 天 replay 结果**：",
                "",
                f"- recommendation: {replay.get('recommendation', 'not_run')}",
                f"- severe_count: {replay.get('severe_count', 'not_run')}",
                f"- matrix: `{json.dumps(replay.get('matrix', {}), ensure_ascii=False, sort_keys=True)}`",
                "",
                "**虎哥裁决**：",
                f"- [ ] apply（apply 命令：`py tools\\tm_io.py cron-apply {date} --proposal {proposal['id']}`）",
                f"- [ ] reject（reject 命令：`py tools\\tm_io.py cron-reject {date} --proposal {proposal['id']} --reason \"...\"`）",
                "",
                "**默认行为**：未勾选 = reject",
                "",
            ])
    else:
        lines.append("- none")

    lines.extend(["", "## ✅ 已生效改动", ""])
    if applied:
        for row in applied:
            lines.append(f"- commit `{row.get('commit')}` proposal_id={row.get('proposal_id')} paths={row.get('paths')}")
    else:
        lines.append("- none")

    _append_self_evolution_section(lines, self_evolution_summary)

    lines.extend([
        "",
        "## 📈 自评指标",
        "",
        f"- 当日自评：{quality_score}",
        "- 7 天移动平均：not_available",
        "- 上周同日：not_available",
        f"- 主要 issue：discard 候选 {len(candidates)} 条；14 天 inbox 兜底 {stale_count} 条",
        "",
        "## 📚 附录",
        "",
    ])
    mem0_body: list[str] = []
    if mem0_rows:
        for row in mem0_rows[:40]:
            mem0_body.append(f"- `{row.get('id')}` topic={row['topic']} agent={row['agent']} :: {row['summary']}")
    else:
        mem0_body.append("- none")
    lines.extend(_details_block(f"Mem0 当日正式写入（{len(mem0_rows)} 条，最多显示 40 条）", mem0_body))
    inbox_body: list[str] = []
    if inbox_today:
        for row in inbox_today[:40]:
            inbox_body.append(f"- `{row.path}` topic={row.topic} agent={row.agent} :: {row.summary}")
    else:
        inbox_body.append("- none")
    lines.extend([""])
    lines.extend(_details_block(f"inbox 当日新增（{len(inbox_today)} 条，最多显示 40 条）", inbox_body))
    discard_body: list[str] = []
    if discard_events:
        for row in discard_events[:40]:
            discard_body.append(
                f"- event_id={row.get('event_id')} score={row.get('score')} "
                f"topic={row.get('requested_topic')} agent={row.get('agent')} :: "
                f"{_preview(str(row.get('text_excerpt') or ''))}"
            )
    else:
        discard_body.append("- none")
    lines.extend([""])
    lines.extend(_details_block(f"discard quarantine（{len(discard_events)} 条，最多显示 40 条）", discard_body))
    lines.extend([
        "",
        "## 来源",
        "",
        "- `tools/tm_route_audit.py`",
        "- `tools/tm_mem0_audit.py`",
        "- `tools/tm_route_replay.py`",
        "- `tools/tm_cron_apply.py`",
        "- `tools/tm_self_evolution.py`",
        "- `wiki/operations/cron-daily-report.md`",
        "",
    ])
    return "\n".join(lines)


def write_daily_report(**kwargs: Any) -> pathlib.Path:
    date = str(kwargs["date"])
    path = OPERATIONS_DIR / f"daily-memory-digest-{date}.md"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_daily_report(**kwargs))
    return path


def _frontmatter_counts(path: pathlib.Path) -> dict[str, int]:
    try:
        fm, _ = _frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    counts: dict[str, int] = {}
    for key in (
        "mem0_count",
        "inbox_count",
        "discard_count",
        "proposal_count",
        "applied_count",
        "wiki_proposal_inbox_count",
    ):
        try:
            counts[key] = int(fm.get(key, "0"))
        except ValueError:
            counts[key] = 0
    return counts


def _section_body(text: str, heading_contains: str) -> str:
    """Return the body under the first report section containing a marker."""
    lines = text.splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        if _is_section_heading(line, heading_contains):
            start = idx + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start, len(lines)):
        if _is_section_boundary(lines[idx]):
            end = idx
            break
    return "\n".join(line.rstrip() for line in lines[start:end]).strip()


def _is_section_heading(line: str, marker: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("## ") and marker in stripped:
        return True
    if stripped.startswith("**") and stripped.endswith("**") and marker in stripped.strip("*").strip():
        return True
    return False


def _is_section_boundary(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("## "):
        return True
    if stripped.startswith("**") and stripped.endswith("**") and 2 <= len(stripped.strip("*").strip()) <= 40:
        return True
    return False


def _compact_section_lines(section: str, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line in {"<details>", "</details>"} or line.startswith("<summary>"):
            continue
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _drop_none_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        marker = str(raw).strip().lstrip("- ").strip().lower()
        if marker in {"none", "n/a", "na"}:
            continue
        out.append(str(raw))
    return out


def _report_path(rel_or_abs: pathlib.Path) -> str:
    try:
        return str(rel_or_abs.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(rel_or_abs)


def _daily_digest_intake(date: str, *, operations_dir: pathlib.Path = OPERATIONS_DIR) -> dict[str, Any]:
    path = operations_dir / f"daily-memory-digest-{date}.md"
    report: dict[str, Any] = {
        "kind": "memory_digest",
        "date": date,
        "path": _report_path(path),
        "exists": path.exists(),
        "status": "missing",
        "counts": {},
        "learning_card": [],
        "decision_items": [],
        "issues": [],
    }
    if not path.exists():
        report["issues"].append("daily-memory-digest file missing")
        return report
    text = path.read_text(encoding="utf-8")
    fm, _ = _frontmatter(text)
    counts: dict[str, int] = {}
    for key in (
        "mem0_count",
        "inbox_count",
        "discard_count",
        "proposal_count",
        "applied_count",
        "stale_archive_count",
        "promote_candidate_count",
        "low_priority_inbox_count",
        "wiki_proposal_inbox_count",
        "mem0_audit_candidate_count",
        "self_evolution_count",
    ):
        try:
            counts[key] = int(fm.get(key, "0"))
        except ValueError:
            counts[key] = 0
    learning_card = _compact_section_lines(_section_body(text, "今日沉淀卡"), limit=8)
    decision_items = _compact_section_lines(_section_body(text, "今日要决策"), limit=8)
    stale_issue = _memory_digest_freshness_issue(date, fm)
    report.update({
        "status": "ok" if learning_card and not stale_issue else "warn",
        "counts": counts,
        "learning_card": learning_card,
        "decision_items": decision_items,
    })
    if not learning_card:
        report["issues"].append("missing 今日沉淀卡")
    if stale_issue:
        report["issues"].append(stale_issue)
    if counts.get("proposal_count", 0):
        report["issues"].append(f"{counts['proposal_count']} pending proposal(s)")
    if counts.get("wiki_proposal_inbox_count", 0):
        report["issues"].append(f"{counts['wiki_proposal_inbox_count']} inbox wiki proposal candidate(s)")
    if counts.get("stale_archive_count", 0):
        report["issues"].append(f"{counts['stale_archive_count']} stale inbox archive candidate(s)")
    return report


def _memory_digest_freshness_issue(date: str, frontmatter: dict[str, str]) -> str | None:
    last_run = _parse_dt(frontmatter.get("last_run_at"))
    if last_run is None:
        return None
    anchor = _parse_date(date)
    expected_after = dt.datetime.combine(anchor, dt.time(hour=23, minute=30), tzinfo=tm_core.TZ_CN)
    if last_run < expected_after:
        return (
            "memory digest may be stale: "
            f"last_run_at={last_run.isoformat()}, "
            f"expected_after={expected_after.isoformat()}"
        )
    return None


def _daily_health_intake(date: str, *, operations_dir: pathlib.Path = OPERATIONS_DIR) -> dict[str, Any]:
    path = operations_dir / "daily-health" / f"{date}.md"
    report: dict[str, Any] = {
        "kind": "daily_health",
        "date": date,
        "path": _report_path(path),
        "exists": path.exists(),
        "status": "missing",
        "health_color": None,
        "summary": [],
        "issues": [],
    }
    if not path.exists():
        report["issues"].append("daily-health file missing")
        return report
    text = path.read_text(encoding="utf-8")
    summary = _compact_section_lines(_section_body(text, "摘要"), limit=6)
    if not summary:
        summary = _compact_section_lines(text, limit=6)
    health_color = _daily_health_color(text)
    status = "warn" if health_color in {"red", "yellow"} else "ok"
    report.update({"status": status, "health_color": health_color, "summary": summary})
    if health_color in {"red", "yellow"}:
        report["issues"].append(f"daily-health color is {health_color}")
    return report


def _daily_health_color(text: str) -> str | None:
    match = re.search(r"健康色：`?([a-z]+)`?", text, flags=re.I)
    if not match:
        match = re.search(r"巡检结论[^。\n]*\b(red|yellow|green)\b", text, flags=re.I)
    if not match:
        return None
    value = match.group(1).lower()
    if value in {"red", "yellow", "green"}:
        return value
    return None


def _weekly_review_intake(date: str, *, operations_dir: pathlib.Path = OPERATIONS_DIR) -> dict[str, Any]:
    anchor = _parse_date(date)
    iso_year, iso_week, _ = anchor.isocalendar()
    label = f"{iso_year}-{iso_week:02d}"
    path = operations_dir / f"weekly-memory-review-{label}.md"
    report: dict[str, Any] = {
        "kind": "weekly_review",
        "date": date,
        "week": label,
        "path": _report_path(path),
        "exists": path.exists(),
        "status": "missing",
        "summary": [],
        "drift_signals": [],
        "focus": [],
        "issues": [],
    }
    if not path.exists():
        report["issues"].append("weekly-memory-review file missing")
        return report
    text = path.read_text(encoding="utf-8")
    report.update({
        "status": "ok",
        "summary": _compact_section_lines(_section_body(text, "摘要"), limit=5),
        "drift_signals": _compact_section_lines(_section_body(text, "漂移信号"), limit=8),
        "focus": _compact_section_lines(_section_body(text, "下周关注重点"), limit=5),
    })
    return report


def _ai_radar_candidates(date: str, *, codex_home: pathlib.Path) -> list[pathlib.Path]:
    return [
        codex_home / "reports" / f"daily-ai-agent-radar-{date}.md",
        codex_home / "reports" / f"daily-ai-and-agent-radar-{date}.md",
        codex_home / "automations" / "daily-ai-and-agent-radar" / f"daily-ai-agent-radar-{date}.md",
    ]


def _ai_radar_intake(date: str, *, codex_home: pathlib.Path | None = None) -> dict[str, Any]:
    codex_home = codex_home or (pathlib.Path.home() / ".codex")
    candidates = _ai_radar_candidates(date, codex_home=codex_home)
    existing = next((path for path in candidates if path.exists()), None)
    report: dict[str, Any] = {
        "kind": "ai_agent_radar",
        "date": date,
        "path": str(existing or candidates[0]),
        "exists": existing is not None,
        "status": "missing",
        "friendly_closeout": [],
        "actions": [],
        "issues": [],
    }
    if existing is None:
        report["issues"].append("AI radar report is not persisted to a known local file")
        return report
    text = existing.read_text(encoding="utf-8")
    closeout = _compact_section_lines(_section_body(text, "记忆友好收尾摘要"), limit=6)
    actions = _compact_section_lines(_section_body(text, "建议动作"), limit=8)
    report.update({
        "status": "ok" if closeout else "warn",
        "friendly_closeout": closeout,
        "actions": actions,
    })
    if not closeout:
        report["issues"].append("missing 记忆友好收尾摘要")
    return report


def _is_investment_radar_action(text: str) -> bool:
    lower = text.lower()
    markers = (
        "tradingagents",
        "tradingagent",
        "bqmt",
        "mini qmt",
        "miniqmt",
        "a-share",
        "ashare",
        "a股",
        "投研",
        "投资",
        "交易",
        "下单",
    )
    return any(marker in lower for marker in markers)


INTAKE_WINDOWS = {"all", "memory-digest", "system-health", "ai-radar"}
COMPACT_REPORT_KINDS = {"ai-radar", "answer-trace", "daily-health", "memory-digest"}


def _normalize_intake_window(window: str | None) -> str:
    value = (window or "all").strip().lower().replace("_", "-")
    aliases = {
        "memory": "memory-digest",
        "daily": "memory-digest",
        "digest": "memory-digest",
        "daily-health": "system-health",
        "health": "system-health",
        "weekly": "system-health",
        "weekly-review": "system-health",
        "radar": "ai-radar",
        "ai": "ai-radar",
    }
    value = aliases.get(value, value)
    if value not in INTAKE_WINDOWS:
        raise ValueError(f"unknown cron intake window: {window}")
    return value


def _normalize_compact_report_kind(kind: str | None) -> str:
    value = (kind or "").strip().lower().replace("_", "-")
    aliases = {
        "ai": "ai-radar",
        "radar": "ai-radar",
        "trace": "answer-trace",
        "answer": "answer-trace",
        "health": "daily-health",
        "daily": "daily-health",
        "digest": "memory-digest",
        "memory": "memory-digest",
    }
    value = aliases.get(value, value)
    if value not in COMPACT_REPORT_KINDS:
        raise ValueError(f"unknown compact report kind: {kind}")
    return value


def _compact_source_paths(text: str, *, limit: int = 12) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?:[A-Za-z]:\\[^\s)`\]]+|(?:wiki|\.tmp|tools|packages)/[^\s)`\]]+)",
        text,
    ):
        value = match.group(0).rstrip(".,;:>\"'")
        if not re.search(r"\.(?:csv|html|json|jsonl|md|py|toml|txt|xml|ya?ml)\b", value, flags=re.I):
            continue
        if value in seen:
            continue
        seen.add(value)
        paths.append(value)
        if len(paths) >= limit:
            break
    return paths


def _compact_evidence_ids(text: str) -> dict[str, Any]:
    commit_matches = re.findall(r"\b[0-9a-f]{40}\b", text, flags=re.I)
    push_match = re.search(r'"push_result"\s*:\s*"([^"]+)"', text)
    if not push_match:
        push_match = re.search(r"push_result[=:]\s*`?([A-Za-z0-9_-]+)`?", text)
    proposal_ids = sorted(set(re.findall(r"\bproposal-[A-Za-z0-9_.-]+\b", text)))
    return {
        "commit_sha": commit_matches[0] if commit_matches else None,
        "push_result": push_match.group(1) if push_match else None,
        "proposal_ids": proposal_ids[:12],
    }


def _compact_required_section_issues(text: str, markers: Iterable[str]) -> list[str]:
    issues: list[str] = []
    for marker in markers:
        if not _section_body(text, marker):
            issues.append(f"missing section: {marker}")
    return issues


def _compact_report_from_ai_radar(date: str, *, codex_home: pathlib.Path | None = None) -> dict[str, Any]:
    codex_home = codex_home or (pathlib.Path.home() / ".codex")
    candidates = _ai_radar_candidates(date, codex_home=codex_home)
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    result: dict[str, Any] = {
        "kind": "ai-radar",
        "date": date,
        "path": str(path or candidates[0]),
        "exists": path is not None,
        "status": "missing",
        "conclusion": [],
        "issues": [],
        "action_required": [],
        "learning_items": [],
        "friendly_closeout": [],
        "source_paths": [],
        "evidence": {},
    }
    if path is None:
        result["issues"].append("AI radar report is not persisted to a known local file")
        return result
    text = path.read_text(encoding="utf-8")
    required_issues = _compact_required_section_issues(
        text,
        ("AI 雷达学习台账候选", "建议动作", "记忆友好收尾摘要"),
    )
    result.update({
        "status": "warn" if required_issues else "ok",
        "conclusion": _compact_section_lines(_section_body(text, "今日结论"), limit=6),
        "issues": required_issues,
        "action_required": _compact_section_lines(_section_body(text, "建议动作"), limit=8),
        "learning_items": _compact_section_lines(_section_body(text, "AI 雷达学习台账候选"), limit=8),
        "friendly_closeout": _compact_section_lines(_section_body(text, "记忆友好收尾摘要"), limit=4),
        "source_paths": _compact_source_paths(text),
        "evidence": _compact_evidence_ids(text),
    })
    return result


def _compact_report_from_daily_health(date: str, *, operations_dir: pathlib.Path = OPERATIONS_DIR) -> dict[str, Any]:
    path = operations_dir / "daily-health" / f"{date}.md"
    result: dict[str, Any] = {
        "kind": "daily-health",
        "date": date,
        "path": _report_path(path),
        "exists": path.exists(),
        "status": "missing",
        "health_color": None,
        "conclusion": [],
        "issues": [],
        "action_required": [],
        "source_paths": [],
        "evidence": {},
    }
    if not path.exists():
        result["issues"].append("daily-health file missing")
        return result
    text = path.read_text(encoding="utf-8")
    color = _daily_health_color(text)
    issues = _compact_section_lines(_section_body(text, "问题"), limit=8)
    if not issues and color in {"red", "yellow"}:
        issues = [f"daily-health color is {color}"]
    action_required = _compact_section_lines(_section_body(text, "建议"), limit=8)
    if not action_required:
        action_required = _compact_section_lines(_section_body(text, "下一步"), limit=8)
    action_required = _drop_none_lines(action_required)
    if not action_required and color in {"red", "yellow"} and issues:
        action_required = issues[:]
    result.update({
        "status": "warn" if color in {"red", "yellow"} or issues else "ok",
        "health_color": color,
        "conclusion": _compact_section_lines(_section_body(text, "摘要"), limit=6),
        "issues": issues,
        "action_required": action_required,
        "source_paths": _compact_source_paths(text),
        "evidence": _compact_evidence_ids(text),
    })
    return result


def _compact_report_from_memory_digest(date: str, *, operations_dir: pathlib.Path = OPERATIONS_DIR) -> dict[str, Any]:
    path = operations_dir / f"daily-memory-digest-{date}.md"
    result: dict[str, Any] = {
        "kind": "memory-digest",
        "date": date,
        "path": _report_path(path),
        "exists": path.exists(),
        "status": "missing",
        "counts": {},
        "decision_items": [],
        "issues": [],
        "action_required": [],
        "learning_items": [],
        "source_paths": [],
        "evidence": {},
    }
    if not path.exists():
        result["issues"].append("daily-memory-digest file missing")
        return result
    text = path.read_text(encoding="utf-8")
    fm, _ = _frontmatter(text)
    counts = {
        key: _int(fm.get(key)) or 0
        for key in (
            "mem0_count",
            "inbox_count",
            "discard_count",
            "proposal_count",
            "applied_count",
            "stale_archive_count",
            "promote_candidate_count",
            "wiki_proposal_inbox_count",
            "mem0_audit_candidate_count",
            "self_evolution_count",
        )
    }
    issues: list[str] = []
    if counts.get("proposal_count"):
        issues.append(f"{counts['proposal_count']} pending proposal(s)")
    if counts.get("stale_archive_count"):
        issues.append(f"{counts['stale_archive_count']} stale inbox archive candidate(s)")
    if counts.get("wiki_proposal_inbox_count"):
        issues.append(f"{counts['wiki_proposal_inbox_count']} wiki proposal inbox candidate(s)")
    action_required = _drop_none_lines(_compact_section_lines(_section_body(text, "Proposed Changes"), limit=8))
    if not action_required and issues:
        action_required = issues[:]
    result.update({
        "status": "warn" if issues else "ok",
        "counts": counts,
        "decision_items": _compact_section_lines(_section_body(text, "今日要决策"), limit=8),
        "issues": issues,
        "action_required": action_required,
        "learning_items": _compact_section_lines(_section_body(text, "今日沉淀卡"), limit=8),
        "source_paths": _compact_source_paths(text),
        "evidence": _compact_evidence_ids(text),
    })
    return result


def _compact_report_from_answer_trace(path: pathlib.Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "answer-trace",
        "path": str(path),
        "exists": path.exists(),
        "status": "missing",
        "summary": {},
        "issues": [],
        "action_required": [],
        "source_paths": [],
        "evidence": {},
    }
    if not path.exists():
        result["issues"].append("answer trace json missing")
        return result
    data = json.loads(path.read_text(encoding="utf-8"))
    failure_count = _int(data.get("failure_count")) or 0
    invalid_row_count = _int(data.get("invalid_row_count")) or 0
    duration = data.get("duration_ms") if isinstance(data.get("duration_ms"), dict) else {}
    recommendation = data.get("recommendation_quality")
    if not isinstance(recommendation, dict):
        recommendation = {}
    summary = {
        "row_count": _int(data.get("row_count")) or 0,
        "selected_run_id": data.get("selected_run_id"),
        "failure_count": failure_count,
        "invalid_row_count": invalid_row_count,
        "duration_p95_ms": duration.get("p95"),
        "status_counts": data.get("status_counts") if isinstance(data.get("status_counts"), dict) else {},
        "llm_counts": data.get("llm_counts") if isinstance(data.get("llm_counts"), dict) else {},
        "recommendation_shown_count": recommendation.get("recommendation_shown_count"),
        "recommendation_used_as_evidence_count": recommendation.get("recommendation_used_as_evidence_count"),
    }
    issues: list[str] = []
    if failure_count:
        issues.append(f"answer trace has {failure_count} failure(s)")
    if invalid_row_count:
        issues.append(f"answer trace has {invalid_row_count} invalid row(s)")
    result.update({
        "status": "warn" if issues else "ok",
        "summary": summary,
        "issues": issues,
        "action_required": ["inspect answer-trace failures"] if issues else [],
        "source_paths": [str(path)],
        "evidence": _compact_evidence_ids(path.read_text(encoding="utf-8")),
    })
    return result


def build_compact_report(
    *,
    kind: str,
    date: str | None = None,
    path: pathlib.Path | None = None,
    operations_dir: pathlib.Path = OPERATIONS_DIR,
    codex_home: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic read-only summary for one persisted report."""
    normalized = _normalize_compact_report_kind(kind)
    if normalized == "answer-trace":
        if path is None:
            raise ValueError("--path is required for --kind answer-trace")
        return _compact_report_from_answer_trace(path)
    report_date = date or today_local()
    if normalized == "ai-radar":
        return _compact_report_from_ai_radar(report_date, codex_home=codex_home)
    if normalized == "daily-health":
        return _compact_report_from_daily_health(report_date, operations_dir=operations_dir)
    if normalized == "memory-digest":
        return _compact_report_from_memory_digest(report_date, operations_dir=operations_dir)
    raise ValueError(f"unknown compact report kind: {kind}")


def render_compact_report(result: dict[str, Any]) -> str:
    lines = [
        f"# Compact Report: {result.get('kind')}",
        "",
        f"- 状态：{result.get('status')}",
        f"- 路径：`{result.get('path')}`",
    ]
    if result.get("date"):
        lines.append(f"- 日期：{result.get('date')}")
    if result.get("health_color"):
        lines.append(f"- 健康色：{result.get('health_color')}")
    counts = result.get("counts")
    if isinstance(counts, dict) and counts:
        compact_counts = ", ".join(f"{key}={value}" for key, value in counts.items())
        lines.append(f"- 计数：{compact_counts}")
    summary = result.get("summary")
    if isinstance(summary, dict) and summary:
        lines.extend(["", "## Trace 摘要", ""])
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
    for label, key in (
        ("结论", "conclusion"),
        ("问题", "issues"),
        ("需处理", "action_required"),
        ("学习项", "learning_items"),
        ("友好收尾", "friendly_closeout"),
        ("决策项", "decision_items"),
    ):
        values = result.get(key)
        if not values:
            continue
        lines.extend(["", f"## {label}", ""])
        for item in values:
            lines.append(f"- {item}")
    evidence = result.get("evidence")
    if isinstance(evidence, dict) and any(evidence.values()):
        lines.extend(["", "## 证据 ID", ""])
        for key, value in evidence.items():
            if value:
                lines.append(f"- {key}: {value}")
    source_paths = result.get("source_paths") or []
    if source_paths:
        lines.extend(["", "## 来源路径", ""])
        for item in source_paths:
            lines.append(f"- `{item}`")
    lines.append("")
    return "\n".join(lines)


def default_intake_date(window: str | None = None) -> str:
    normalized = _normalize_intake_window(window)
    if normalized == "memory-digest":
        return _yesterday_local()
    return today_local()


def _include_weekly_review_for_intake(date: str, window: str) -> bool:
    if window == "all":
        return True
    if window != "system-health":
        return False
    try:
        return dt.date.fromisoformat(date).weekday() == 0
    except ValueError:
        return False


def build_cron_intake(
    *,
    date: str,
    window: str = "all",
    include_ai: bool = True,
    operations_dir: pathlib.Path = OPERATIONS_DIR,
    codex_home: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Build a compact read-only intake card for cron heartbeat follow-up."""
    window = _normalize_intake_window(window)
    reports: list[dict[str, Any]] = []
    if window in {"all", "memory-digest"}:
        reports.append(_daily_digest_intake(date, operations_dir=operations_dir))
    if window in {"all", "system-health"}:
        reports.append(_daily_health_intake(date, operations_dir=operations_dir))
        if _include_weekly_review_for_intake(date, window):
            reports.append(_weekly_review_intake(date, operations_dir=operations_dir))
    if include_ai and window in {"all", "ai-radar"}:
        reports.append(_ai_radar_intake(date, codex_home=codex_home))
    missing = [row for row in reports if not row.get("exists")]
    warnings = [f"{row['kind']}: {issue}" for row in reports for issue in row.get("issues", [])]
    action_items: list[str] = []
    digest = next((row for row in reports if row.get("kind") == "memory_digest"), {})
    counts = digest.get("counts") or {}
    if digest and not digest.get("exists"):
        action_items.append(f"补跑或检查 tigermemory-memory-route-reflection：缺少 {digest.get('path')}")
    if digest and any("memory digest may be stale" in str(issue) for issue in digest.get("issues", [])):
        action_items.append("检查 tigermemory-memory-route-reflection：日报早于预期晚间窗口，可能未按 23:40 触发")
    if counts.get("proposal_count"):
        action_items.append(f"裁决 {counts['proposal_count']} 个 memory digest proposal")
    if counts.get("wiki_proposal_inbox_count"):
        action_items.append(f"归并或转交 {counts['wiki_proposal_inbox_count']} 个 inbox/wiki_proposal 候选")
    if counts.get("stale_archive_count"):
        action_items.append(f"处理 {counts['stale_archive_count']} 个 14 天 inbox archive 候选")
    for row in reports:
        if row.get("kind") == "daily_health" and not row.get("exists"):
            action_items.append(f"补跑或检查 tigermemory-daily-health-scan：缺少 {row.get('path')}")
        if row.get("kind") == "daily_health" and row.get("health_color") in {"red", "yellow"}:
            action_items.append(f"处理 daily-health {row['health_color']}：查看 {row.get('path')} 的阻塞项和 known debt")
    if reports and reports[-1].get("kind") == "ai_agent_radar" and not reports[-1].get("exists"):
        action_items.append("让 AI 雷达落本地短报告，否则 20:30 心跳只能依赖聊天上下文")
    for row in reports:
        if row.get("kind") == "ai_agent_radar" and row.get("exists"):
            for action in row.get("actions", [])[:6]:
                action_text = str(action).strip()
                if action_text:
                    action_text = action_text.lstrip("- ").strip()
                    if _is_investment_radar_action(action_text):
                        action_items.append(f"投资专线转交：{action_text}")
                    else:
                        action_items.append(f"AI 雷达建议：{action_text}")
    if not action_items:
        action_items.append("无立即动作，继续观察")
    status = "ok"
    if missing:
        status = "partial"
    if any(row.get("status") == "warn" for row in reports):
        status = "warn"
    return {
        "status": status,
        "date": date,
        "window": window,
        "summary": f"{date} {window} cron 承接摘要：{len(reports) - len(missing)}/{len(reports)} 个产物可读取，{len(warnings)} 条警告。",
        "reports": reports,
        "warnings": warnings,
        "action_items": action_items,
    }


def render_cron_intake(result: dict[str, Any]) -> str:
    lines = [
        f"# Cron 承接卡 {result['date']} {result['window']}",
        "",
        "## 摘要",
        "",
        f"- 状态：{result['status']}",
        f"- 摘要：{result['summary']}",
        "",
        "## 建议动作",
        "",
    ]
    for item in result.get("action_items", []):
        lines.append(f"- {item}")
    lines.extend(["", "## 产物状态", ""])
    for row in result.get("reports", []):
        lines.append(f"- {row['kind']}：{row['status']}，path={row['path']}")
        for issue in row.get("issues", [])[:3]:
            lines.append(f"  - 警告：{issue}")
    lines.extend(["", "## 沉淀摘要", ""])
    digest = next((row for row in result.get("reports", []) if row.get("kind") == "memory_digest"), None)
    if digest and digest.get("learning_card"):
        lines.extend(str(x) for x in digest["learning_card"])
    daily_health = next((row for row in result.get("reports", []) if row.get("kind") == "daily_health"), None)
    if daily_health and daily_health.get("summary"):
        lines.extend(["", "### Daily Health", *[str(x) for x in daily_health["summary"]]])
    weekly = next((row for row in result.get("reports", []) if row.get("kind") == "weekly_review"), None)
    if weekly and weekly.get("summary"):
        lines.extend(["", "### Weekly Review", *[str(x) for x in weekly["summary"]]])
    radar = next((row for row in result.get("reports", []) if row.get("kind") == "ai_agent_radar"), None)
    if radar and radar.get("friendly_closeout"):
        lines.extend(["", "### AI 雷达", *[str(x) for x in radar["friendly_closeout"]]])
    if lines[-1] == "## 沉淀摘要":
        lines.extend(["", "- 无可沉淀摘要。"])
    lines.extend(["", "## 来源", ""])
    for row in result.get("reports", []):
        if row.get("path"):
            lines.append(f"- `{row['path']}`")
    return "\n".join(lines).rstrip() + "\n"


def cron_intake_card_path(
    *,
    date: str,
    window: str,
    operations_dir: pathlib.Path = OPERATIONS_DIR,
) -> pathlib.Path:
    return operations_dir / "cron-intake" / f"{date}-{_normalize_intake_window(window)}.md"


def render_cron_intake_card(result: dict[str, Any]) -> str:
    title = f"Cron 承接卡 {result['date']} {result['window']}"
    body = render_cron_intake(result)
    return "\n".join([
        "---",
        "owner: codex",
        "status: active",
        f"updated: {today_local()}",
        f'title: "{title}"',
        "aliases:",
        f'  - "cron intake {result["date"]} {result["window"]}"',
        f'cron_date: "{result["date"]}"',
        f'window: "{result["window"]}"',
        f'intake_status: "{result["status"]}"',
        "---",
        "",
        body.rstrip(),
        "",
    ])


def _preserve_cron_intake_notes(new_text: str, existing_text: str) -> str:
    notes = _section_body(existing_text, "承接处理记录")
    if not notes:
        return new_text
    existing_actions = _section_body(existing_text, "建议动作")
    if existing_actions and any(marker in existing_actions for marker in ("已处理", "已确认", "无需", "继续观察")):
        new_text = _replace_section_body(new_text, "建议动作", existing_actions)
    new_text = _merge_section_lines(new_text, existing_text, "来源")
    section = f"## 承接处理记录\n\n{notes.strip()}\n\n"
    marker = "\n## 来源\n"
    if marker in new_text:
        return new_text.replace(marker, f"\n{section}## 来源\n", 1)
    return new_text.rstrip() + "\n\n" + section


def _merge_section_lines(new_text: str, existing_text: str, heading_contains: str) -> str:
    new_body = _section_body(new_text, heading_contains)
    existing_body = _section_body(existing_text, heading_contains)
    if not existing_body:
        return new_text
    merged: list[str] = []
    seen: set[str] = set()
    for raw in [*new_body.splitlines(), *existing_body.splitlines()]:
        line = raw.rstrip()
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(line)
    if not merged:
        return new_text
    return _replace_section_body(new_text, heading_contains, "\n".join(merged))


def _replace_section_body(text: str, heading_contains: str, body: str) -> str:
    lines = text.splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        if _is_section_heading(line, heading_contains):
            start = idx + 1
            break
    if start is None:
        return text
    end = len(lines)
    for idx in range(start, len(lines)):
        if _is_section_boundary(lines[idx]):
            end = idx
            break
    replacement = ["", *body.strip().splitlines(), ""]
    return "\n".join([*lines[:start], *replacement, *lines[end:]]).rstrip() + "\n"


def write_cron_intake_card(
    result: dict[str, Any],
    *,
    operations_dir: pathlib.Path = OPERATIONS_DIR,
) -> pathlib.Path:
    path = cron_intake_card_path(date=result["date"], window=result["window"], operations_dir=operations_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_cron_intake_card(result)
    if path.exists():
        rendered = _preserve_cron_intake_notes(rendered, path.read_text(encoding="utf-8"))
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return path


def _proposal_status_counts(dates: Iterable[str], *, proposal_root: pathlib.Path = PROPOSAL_ROOT) -> dict[str, int]:
    proposals = applied = rejected = 0
    for date in dates:
        for pdir in proposal_dirs(date, proposal_root=proposal_root):
            proposals += 1
            applied += int((pdir / "applied.json").exists())
            rejected += int((pdir / "rejected.json").exists())
    return {"proposal_count": proposals, "applied_count": applied, "rejected_count": rejected}


def _daily_counts(dates: Iterable[str], *, proposal_root: pathlib.Path = PROPOSAL_ROOT) -> dict[str, int]:
    out = {"mem0_count": 0, "inbox_count": 0, "discard_count": 0, "proposal_count": 0, "applied_count": 0, "rejected_count": 0}
    for date in dates:
        path = OPERATIONS_DIR / f"daily-memory-digest-{date}.md"
        if path.exists():
            for key, value in _frontmatter_counts(path).items():
                out[key] = out.get(key, 0) + value
    proposal_counts = _proposal_status_counts(dates, proposal_root=proposal_root)
    for key, value in proposal_counts.items():
        out[key] = max(out.get(key, 0), value) if key == "proposal_count" else out.get(key, 0) + value
    return out


def detect_drift_signals(
    *,
    week_dates: list[str],
    previous_dates: list[str],
    mem0_items: list[dict[str, Any]] | None = None,
    audit_root: pathlib.Path = DISCARD_ROOT,
    proposal_root: pathlib.Path = PROPOSAL_ROOT,
) -> list[dict[str, Any]]:
    week_set = set(week_dates)
    prev_set = set(previous_dates)
    mem0_week = mem0_records_for_dates(week_set, items=mem0_items)
    mem0_prev = mem0_records_for_dates(prev_set, items=mem0_items)
    discard_week = discard_events_for_dates(week_dates, audit_root=audit_root)
    discard_prev = discard_events_for_dates(previous_dates, audit_root=audit_root)
    counts_week = _daily_counts(week_dates, proposal_root=proposal_root)
    counts_prev = _daily_counts(previous_dates, proposal_root=proposal_root)

    signals: list[dict[str, Any]] = []

    def topic_counts(rows: list[dict[str, Any]], key: str = "topic") -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            topic = str(row.get(key) or "unknown")
            out[topic] = out.get(topic, 0) + 1
        return out

    week_topics = topic_counts(mem0_week)
    prev_topics = topic_counts(mem0_prev)
    for topic, count in week_topics.items():
        prev = prev_topics.get(topic, 0)
        if count >= max(3, int(prev * 1.5) + 1):
            signals.append({
                "type": "single_class_mem0_spike",
                "threshold": "7 days vs previous week +50%",
                "observation": f"{topic}: previous={prev}, current={count}",
                "action": "observe" if count < 10 else "review prompt/topic policy",
            })

    def discard_ratio(counts: dict[str, int], discard_events: list[dict[str, Any]]) -> float:
        discard_count = counts.get("discard_count") or len(discard_events)
        total = counts.get("mem0_count", 0) + counts.get("inbox_count", 0) + discard_count
        return (discard_count / total) if total else 0.0

    ratio_week = discard_ratio(counts_week, discard_week)
    ratio_prev = discard_ratio(counts_prev, discard_prev)
    if abs(ratio_week - ratio_prev) >= 0.15:
        signals.append({
            "type": "discard_ratio_shift",
            "threshold": "+/-15 percentage points",
            "observation": f"previous={ratio_prev:.2%}, current={ratio_week:.2%}",
            "action": "review discard candidates",
        })

    candidate_agents: dict[str, int] = {}
    for row in discard_review_candidates(discard_week):
        agent = str(row.get("agent") or "unknown")
        candidate_agents[agent] = candidate_agents.get(agent, 0) + 1
    for agent, count in candidate_agents.items():
        if count >= 5:
            signals.append({
                "type": "same_agent_misclassification_cluster",
                "threshold": ">=5 candidates from same agent",
                "observation": f"{agent}: {count}",
                "action": "inspect agent prompt and closeout wording",
            })

    week_inferred = topic_counts(discard_week, key="topic_inferred")
    prev_inferred = topic_counts(discard_prev, key="topic_inferred")
    total_week = sum(week_inferred.values())
    total_prev = sum(prev_inferred.values())
    for topic, count in week_inferred.items():
        current = count / total_week if total_week else 0.0
        previous = prev_inferred.get(topic, 0) / total_prev if total_prev else 0.0
        if total_week and abs(current - previous) >= 0.30:
            signals.append({
                "type": "topic_inferred_drift",
                "threshold": "topic share shift >=30 percentage points",
                "observation": f"{topic}: previous={previous:.2%}, current={current:.2%}",
                "action": "review topic inference examples",
            })

    unreviewed = sum(1 for row in discard_week if row.get("unreviewed"))
    total_route = len(discard_week) + counts_week.get("inbox_count", 0) + counts_week.get("mem0_count", 0)
    fail_rate = (unreviewed / total_route) if total_route else 0.0
    if fail_rate > 0.05:
        signals.append({
            "type": "llm_json_failure_rate",
            "threshold": ">5%",
            "observation": f"unreviewed={unreviewed}, total={total_route}, rate={fail_rate:.2%}",
            "action": "check route LLM availability and timeout",
        })

    if not signals:
        signals.append({
            "type": "none",
            "threshold": "all monitored signals below threshold",
            "observation": "no drift signal detected",
            "action": "observe",
        })
    return signals


def render_weekly_report(
    *,
    date: str,
    now_iso: str | None = None,
    mem0_items: list[dict[str, Any]] | None = None,
    audit_root: pathlib.Path = DISCARD_ROOT,
    proposal_root: pathlib.Path = PROPOSAL_ROOT,
    inbox_dir: pathlib.Path = INBOX_DIR,
) -> str:
    anchor = _parse_date(date)
    iso_year, iso_week, _ = anchor.isocalendar()
    label = f"{iso_year}-{iso_week:02d}"
    week_dates = _week_dates(anchor)
    prev_dates = _previous_week_dates(anchor)
    current = _daily_counts(week_dates, proposal_root=proposal_root)
    previous = _daily_counts(prev_dates, proposal_root=proposal_root)
    signals = detect_drift_signals(
        week_dates=week_dates,
        previous_dates=prev_dates,
        mem0_items=mem0_items,
        audit_root=audit_root,
        proposal_root=proposal_root,
    )
    inbox_rows = audit_inbox(date=date, inbox_dir=inbox_dir, proposal_root=proposal_root)
    stale7 = sum(1 for row in inbox_rows if row.age_days >= 7)
    stale14 = sum(1 for row in inbox_rows if row.age_days >= 14 and not row.already_applied)
    now_iso = now_iso or dt.datetime.now(tm_core.TZ_CN).isoformat()

    def delta(key: str) -> int:
        return current.get(key, 0) - previous.get(key, 0)

    lines = [
        "---",
        "owner: codex",
        "status: active",
        f"updated: {date}",
        f"aliases: [\"每周记忆复盘\", \"memory route weekly review {label}\"]",
        f"title: \"Weekly Memory Review {label}\"",
        f"last_run_at: {now_iso}",
        f"week: \"{label}\"",
        "---",
        "",
        f"# Weekly Memory Review {label}",
        "",
        "## 摘要",
        "",
        f"{label} 记忆路由周报：汇总 7 天日报、proposal 状态、inbox 健康度和漂移信号；检测到 {len([s for s in signals if s['type'] != 'none'])} 个有效漂移信号。",
        "",
        "## 7 天数据汇总",
        "",
        "| 维度 | 上周 | 本周 | 增减 |",
        "|---|---:|---:|---:|",
        f"| Mem0 写入 | {previous.get('mem0_count', 0)} | {current.get('mem0_count', 0)} | {delta('mem0_count'):+d} |",
        f"| inbox 新增 | {previous.get('inbox_count', 0)} | {current.get('inbox_count', 0)} | {delta('inbox_count'):+d} |",
        f"| discard quarantine | {previous.get('discard_count', 0)} | {current.get('discard_count', 0)} | {delta('discard_count'):+d} |",
        f"| Proposed Changes 数 | {previous.get('proposal_count', 0)} | {current.get('proposal_count', 0)} | {delta('proposal_count'):+d} |",
        f"| 虎哥 apply 数 | {previous.get('applied_count', 0)} | {current.get('applied_count', 0)} | {delta('applied_count'):+d} |",
        f"| 虎哥 reject 数 | {previous.get('rejected_count', 0)} | {current.get('rejected_count', 0)} | {delta('rejected_count'):+d} |",
        "| 路由自评分（移动平均） | not_available | not_available | not_available |",
        "",
        "## ROUTE_PROMPT 改动历史",
        "",
    ]
    prompt_rows = [row for row in _applied_rows(proposal_root=proposal_root, limit=20) if "prompt" in str(row.get("proposal_type") or "")]
    if prompt_rows:
        for row in prompt_rows:
            lines.append(f"- commit `{row.get('commit')}` proposal_id={row.get('proposal_id')} applied_at={row.get('applied_at')}")
    else:
        lines.append("- none")

    lines.extend(["", "## 漂移信号", "", "| 信号类型 | 阈值 | 当周观察 | 建议动作 |", "|---|---|---|---|"])
    for signal in signals:
        lines.append(
            f"| {signal['type']} | {signal['threshold']} | {signal['observation']} | {signal['action']} |"
        )

    lines.extend(["", "## Rollback 候选", "", "- none"])
    lines.extend([
        "",
        "## inbox 健康度",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 当前 inbox 总文件数 | {len(inbox_rows)} |",
        f"| ≥7 天未处理 | {stale7} |",
        f"| ≥14 天将自动建议 archive | {stale14} |",
        "| 本周 promote_to_mem0 | 0 |",
        "| 本周 promote_to_wiki | 0 |",
        "| 本周 archive | 0 |",
        "",
        "## 下周关注重点",
        "",
    ])
    effective = [s for s in signals if s["type"] != "none"]
    if effective:
        for signal in effective[:3]:
            lines.append(f"- {signal['type']}：{signal['action']}")
    else:
        lines.append("- 继续观察 route 分布，等待真实 apply/reject 历史积累。")
    lines.extend([
        "",
        "## 来源",
        "",
        "- `tools/tm_memory_reflection.py`",
        "- `tools/tm_route_audit.py`",
        "- `wiki/operations/cron-weekly-review.md`",
        "",
    ])
    return "\n".join(lines)


def write_weekly_report(**kwargs: Any) -> pathlib.Path:
    date = str(kwargs["date"])
    anchor = _parse_date(date)
    iso_year, iso_week, _ = anchor.isocalendar()
    path = OPERATIONS_DIR / f"weekly-memory-review-{iso_year}-{iso_week:02d}.md"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_weekly_report(**kwargs))
    return path


def cmd_daily(args: argparse.Namespace) -> int:
    path = write_daily_report(date=args.date or today_local())
    print(_relpath(path))
    return 0


def cmd_weekly(args: argparse.Namespace) -> int:
    path = write_weekly_report(date=args.date or today_local())
    print(_relpath(path))
    return 0


def cmd_enrich_inbox(args: argparse.Namespace) -> int:
    rows = repair_inbox_review_metadata(
        inbox_dir=pathlib.Path(args.inbox_dir),
        limit=args.limit,
        use_llm=not args.no_llm,
        dry_run=args.dry_run,
    )
    print(json.dumps({"ok": True, "count": len(rows), "items": rows}, ensure_ascii=False, indent=2))
    return 0


def cmd_intake(args: argparse.Namespace) -> int:
    window = _normalize_intake_window(getattr(args, "window", "all"))
    result = build_cron_intake(
        date=args.date or default_intake_date(window),
        window=window,
        include_ai=not args.no_ai,
        codex_home=pathlib.Path(args.codex_home) if args.codex_home else None,
    )
    if getattr(args, "write_card", False):
        result["written_path"] = _relpath(write_cron_intake_card(result))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_cron_intake(result), end="")
        if result.get("written_path"):
            print(f"\n写入：{result['written_path']}")
    return 0


def cmd_compact_report(args: argparse.Namespace) -> int:
    result = build_compact_report(
        kind=args.kind,
        date=args.date,
        path=pathlib.Path(args.path) if args.path else None,
        codex_home=pathlib.Path(args.codex_home) if args.codex_home else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_compact_report(result), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render memory route reflection reports")
    sub = parser.add_subparsers(dest="cmd", required=True)

    daily = sub.add_parser("daily")
    daily.add_argument("--date")
    daily.set_defaults(func=cmd_daily)

    weekly = sub.add_parser("weekly")
    weekly.add_argument("--date")
    weekly.set_defaults(func=cmd_weekly)

    enrich = sub.add_parser("enrich-inbox", help="Backfill low-quality inbox title_cn/preview_cn metadata")
    enrich.add_argument("--inbox-dir", default=str(INBOX_DIR))
    enrich.add_argument("--limit", type=int, default=20)
    enrich.add_argument("--dry-run", action="store_true")
    enrich.add_argument("--no-llm", action="store_true", help="Only use deterministic extraction; skip DeepSeek fallback")
    enrich.set_defaults(func=cmd_enrich_inbox)

    intake = sub.add_parser("intake", help="Render a compact cron follow-up card from persisted reports")
    intake.add_argument("--date")
    intake.add_argument("--window", choices=sorted(INTAKE_WINDOWS), default="all")
    intake.add_argument("--json", action="store_true")
    intake.add_argument("--no-ai", action="store_true", help="Skip AI/Agent radar artifact check")
    intake.add_argument("--codex-home", help="Override Codex home for AI radar report lookup")
    intake.add_argument("--write-card", action="store_true", help="Write the intake card to wiki/operations/cron-intake/")
    intake.set_defaults(func=cmd_intake)

    compact = sub.add_parser("compact-report", help="Deterministically summarize one persisted report or trace")
    compact.add_argument("--kind", required=True, choices=sorted(COMPACT_REPORT_KINDS))
    compact.add_argument("--date")
    compact.add_argument("--path", help="Required for --kind answer-trace")
    compact.add_argument("--json", action="store_true")
    compact.add_argument("--codex-home", help="Override Codex home for AI radar report lookup")
    compact.set_defaults(func=cmd_compact_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
