#!/usr/bin/env python3
"""Daily and weekly report rendering for memory route reflection.

The renderer is intentionally read-only for routing policy. It may write the
daily/weekly markdown report, but code, prompt, policy, and test changes stay
in ask-confirm proposal material until a human runs cron-apply.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

import tm_core
import tm_route_audit

try:
    import tm_retention_audit
except Exception:  # pragma: no cover - degraded local runtime
    tm_retention_audit = None  # type: ignore[assignment]

REPO_ROOT = tm_core.REPO_ROOT
INBOX_DIR = REPO_ROOT / "inbox"
OPERATIONS_DIR = REPO_ROOT / "wiki" / "operations"
PROPOSAL_ROOT = REPO_ROOT / ".tmp" / "cron-proposals"
DISCARD_ROOT = tm_route_audit.DEFAULT_AUDIT_ROOT
MAX_PREVIEW_CHARS = 160
STALE_INBOX_DAYS = 14
MISSING_SUMMARY_PREFIX = "未提供中文摘要"

INBOX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d{4}-([^-]+)-([^.]+)\.md$")


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
    stale_archive: bool = False
    already_applied: bool = False


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


def inbox_records(*, inbox_dir: pathlib.Path = INBOX_DIR) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not inbox_dir.exists():
        return rows
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
        if preview_cn:
            preview_cn = tm_core._clean_inbox_preview(preview_cn)
        if not title_cn or not preview_cn or str(title_cn).startswith(MISSING_SUMMARY_PREFIX):
            derived_title, derived_preview, _source = tm_core.derive_inbox_review_cn(fm.get("title") or path.stem, body)
            if not title_cn or str(title_cn).startswith(MISSING_SUMMARY_PREFIX):
                title_cn = derived_title
            if not preview_cn:
                preview_cn = derived_preview
        summary_cn = fm.get("summary_cn") or title_cn
        if str(summary_cn).startswith(MISSING_SUMMARY_PREFIX):
            summary_cn = title_cn
        if str(title_cn).startswith(MISSING_SUMMARY_PREFIX):
            title_cn = _preview(body) or fm.get("title") or path.stem
        if str(preview_cn).startswith(MISSING_SUMMARY_PREFIX):
            preview_cn = _preview(body)
        rows.append({
            "path": _relpath(path),
            "created_date": match.group(1),
            "agent": fm.get("agent") or fm.get("owner") or match.group(2),
            "topic": fm.get("topic") or match.group(3),
            "title_cn": title_cn,
            "preview_cn": preview_cn,
            "summary_cn": summary_cn,
            "summary": _preview(body),
            "route_score": fm.get("route_score"),
            "route_decision_reason": fm.get("route_decision_reason"),
        })
    return rows


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
) -> list[InboxAuditRow]:
    today = _parse_date(date)
    applied = applied_inbox_paths(proposal_root=proposal_root)
    rows: list[InboxAuditRow] = []
    for record in inbox_records(inbox_dir=inbox_dir):
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
    for date in dates:
        events.extend(tm_route_audit.load_discard_events(date=date, audit_root=audit_root))
    return events


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
        f"  - cron 建议动作：{row.action}",
        f"  - 建议理由：{row.reason}",
        "  - 虎哥裁决：[ ] apply  [ ] reject",
    ])


def _details_block(summary: str, body: list[str]) -> list[str]:
    return ["<details>", f"<summary>{summary}</summary>", "", *body, "", "</details>"]


def render_daily_report(
    *,
    date: str,
    now_iso: str | None = None,
    mem0_items: list[dict[str, Any]] | None = None,
    inbox_dir: pathlib.Path = INBOX_DIR,
    audit_root: pathlib.Path = DISCARD_ROOT,
    proposal_root: pathlib.Path = PROPOSAL_ROOT,
) -> str:
    now_iso = now_iso or dt.datetime.now(tm_core.TZ_CN).isoformat()
    mem0_rows = mem0_records_for_dates({date}, items=mem0_items)
    inbox_all = audit_inbox(date=date, inbox_dir=inbox_dir, proposal_root=proposal_root)
    inbox_today = [row for row in inbox_all if row.created_date == date]
    discard_events = discard_events_for_dates([date], audit_root=audit_root)
    candidates = discard_review_candidates(discard_events)
    proposals = load_proposals(date, proposal_root=proposal_root)
    applied = [row for row in _applied_rows(proposal_root=proposal_root) if str(row.get("applied_at") or "").startswith(date)]
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
        "---",
        "",
        f"# Memory Digest {date}",
        "",
        "## ⚡ 今日要决策",
        "",
        f"- 🔴 14 天兜底 archive 候选：{stale_count} 条 → 见下方 §inbox 决策区",
        f"- 🟡 promote_to_mem0 / promote_to_wiki 候选：{promote_count} 条 → 见下方 §inbox 决策区",
        f"- 🔵 Proposed Changes：{len(proposals)} 条 → 见下方 §Proposed Changes",
        f"- ⚪ discard 误判候选：{len(candidates)} 条 → 见下方 §discard 误判候选",
        "",
        "## 摘要",
        "",
        (
            f"{date} 记忆路由日报：Mem0 正式写入 {len(mem0_rows)} 条，"
            f"inbox 当日新增 {len(inbox_today)} 条，discard quarantine {len(discard_events)} 条；"
            f"路由质量自评分 {quality_score}/100，Proposed Changes {len(proposals)} 条。"
        ),
        "",
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
        "- `tools/tm_route_replay.py`",
        "- `tools/tm_cron_apply.py`",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render memory route reflection reports")
    sub = parser.add_subparsers(dest="cmd", required=True)

    daily = sub.add_parser("daily")
    daily.add_argument("--date")
    daily.set_defaults(func=cmd_daily)

    weekly = sub.add_parser("weekly")
    weekly.add_argument("--date")
    weekly.set_defaults(func=cmd_weekly)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
