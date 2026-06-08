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
            "route_decision_reason": fm.get("route_decision_reason"),
            "knowledge_target": fm.get("knowledge_target"),
            "proposal_kind": fm.get("proposal_kind"),
            "wiki_partition": fm.get("wiki_partition"),
            "wiki_slug_hint": fm.get("wiki_slug_hint"),
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
        patch_preview = ""
        patch_lines = 0
        if patch_path.exists():
            try:
                lines = patch_path.read_text(encoding="utf-8").splitlines()
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
        proposals.append({
            "id": pdir.name,
            "type": meta.get("type") or meta.get("proposal_type") or "other",
            "trigger": meta.get("trigger") or meta.get("evidence") or "not provided",
            "impact": meta.get("impact") or "not provided",
            "summary": meta.get("summary") or meta.get("diff_summary") or "",
            "patch_preview": patch_preview,
            "patch_lines": patch_lines,
            "replay": replay,
            "applied": applied,
            "rejected": rejected,
        })
    return proposals


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
    for key in ("mem0_count", "inbox_count", "discard_count", "proposal_count", "applied_count"):
        try:
            counts[key] = int(fm.get(key, "0"))
        except ValueError:
            counts[key] = 0
    return counts


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
