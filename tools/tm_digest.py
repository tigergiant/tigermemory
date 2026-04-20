#!/usr/bin/env python3
"""
tools/tm_digest.py — Daily Digest Generator (P6.2)

Scans Mem0 memories and inbox files for a target date, synthesizes a structured
daily digest via DeepSeek, and writes to inbox/daily/YYYY-MM-DD.md.

Usage:
    python -m tools.tm_digest --date 2026-04-19 [--dry-run]
    python -m tools.tm_digest --date yesterday

Exit codes:
    0 - Success (or skipped due to no activity)
    1 - Error (logged to stderr)
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Import tm_core - use absolute path to avoid hermes tools conflict
_tiger_root = Path(__file__).parent.parent
if str(_tiger_root) not in sys.path:
    sys.path.insert(0, str(_tiger_root))
import tm_core  # noqa: E402

# ---------- Configuration ----------

DIGEST_DEEPSEEK_TIMEOUT = 60  # seconds (longer than refine, for synthesis)
DIGEST_MAX_RETRIES = 3
DIGEST_RETRY_DELAY_BASE = 2  # seconds

# Asia/Shanghai timezone (consistent with tm_core)
TZ_CN = tm_core.TZ_CN
REPO_ROOT = tm_core.REPO_ROOT

DIGEST_PROMPT_TEMPLATE = """你是 Tiger 的记忆日报助手。根据下面提供的 Mem0 记忆和 inbox 文件清单，生成一份结构化的每日日报。

【任务】
1. 用 3-5 句话总结当天的关键动作与结论（TL;DR）
2. 按 topic 分类列出事实清单（systems, brand, operations, investment, person, production）
3. 标记每个事实的来源（Mem0 ID 或 inbox 文件名）
4. 列出疑似重复、低价值或需要人工审核的条目（待审核建议）

【输出格式】
严格返回 JSON 对象：
{{
  "tldr": "3-5句话总结",
  "facts_by_topic": {{
    "systems": [{{"text": "...", "source": "mem0-id-xxx" or "inbox-filename"}}],
    ...
  }},
  "audit_suggestions": ["建议1", "建议2"]
}}

【输入数据】
目标日期：{date}
Mem0 记忆数量：{memories_count}
Inbox 文件数量：{inbox_count}

Mem0 记忆：
{memories_json}

Inbox 文件：
{inbox_json}

【输出】"""

AUDIT_LOG_PATH = Path(REPO_ROOT) / ".dreams" / "digest-audit.log"


# ---------- Public API ----------


def generate_daily_digest(target_date: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Generate daily digest for target_date (YYYY-MM-DD). Returns result dict.
    
    Result format:
    {
        "ok": bool,
        "path": str | None,      # relative path like "inbox/daily/2026-04-20.md"
        "fact_count": int,       # total facts included
        "skipped": bool,         # True if no activity
        "reason": str,           # "no_activity" or error message
        "dry_run": bool,
    }
    """
    # 1. Resolve target date
    if target_date is None or target_date == "yesterday":
        # Default to yesterday because we run at 3:15am processing previous day
        dt = datetime.datetime.now(TZ_CN) - datetime.timedelta(days=1)
        target_date = dt.strftime("%Y-%m-%d")
    
    result_base = {
        "ok": False,
        "path": None,
        "fact_count": 0,
        "skipped": False,
        "reason": "",
        "dry_run": dry_run,
    }
    
    try:
        # 2. Fetch data
        memories = _fetch_memories_for_date(target_date)
        inbox_files = _list_inbox_for_date(target_date)
        
        mem_count = len(memories)
        inbox_count = len(inbox_files)
        
        print(f"[digest] Date {target_date}: {mem_count} memories, {inbox_count} inbox files", file=sys.stderr)
        
        # 3. Empty day detection
        if mem_count == 0 and inbox_count == 0:
            result_base.update({
                "ok": True,
                "skipped": True,
                "reason": "no_activity",
            })
            print(f"[digest] Skipped: no activity for {target_date}", file=sys.stderr)
            return result_base
        
        # 4. Synthesize digest via DeepSeek
        digest_md = _synthesize_digest(target_date, memories, inbox_files)
        
        if not digest_md or not digest_md.strip():
            result_base.update({
                "ok": False,
                "reason": "deepseek_returned_empty",
            })
            print(f"[digest] Error: DeepSeek returned empty", file=sys.stderr)
            return result_base
        
        # 5. Write digest file
        rel_path = f"inbox/daily/{target_date}.md"
        full_path = REPO_ROOT / rel_path
        
        if dry_run:
            print(f"[digest] DRY RUN: would write {full_path}", file=sys.stderr)
            print(f"[digest] Content preview (first 500 chars):\n{digest_md[:500]}...", file=sys.stderr)
            result_base.update({
                "ok": True,
                "path": rel_path,
                "fact_count": mem_count + inbox_count,
                "skipped": False,
                "reason": "dry_run",
            })
        else:
            _write_digest_file(full_path, digest_md)
            result_base.update({
                "ok": True,
                "path": rel_path,
                "fact_count": mem_count + inbox_count,
                "skipped": False,
                "reason": "written",
            })
            print(f"[digest] Written: {full_path}", file=sys.stderr)
        
        # 6. Audit log
        _append_audit_log(target_date, mem_count, inbox_count, result_base["ok"], dry_run)
        
        return result_base
        
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        result_base.update({
            "ok": False,
            "reason": f"exception: {e}",
        })
        print(f"[digest] Error: {e}", file=sys.stderr)
        print(f"[digest] Traceback:\n{tb_str}", file=sys.stderr)
        return result_base


# ---------- Data Fetching ----------


def _fetch_memories_for_date(date_str: str) -> list[dict[str, Any]]:
    """Fetch Mem0 memories and filter by created_at date (client-side).
    
    Mem0 REST API lacks native time filtering, so we fetch recent memories
    and filter client-side. Returns list of memory dicts with keys:
    - id, content, created_at, metadata_
    """
    # Parse target date boundaries in Asia/Shanghai
    date_fmt = "%Y-%m-%d"
    target_date = datetime.datetime.strptime(date_str, date_fmt).date()
    
    # Get range: start of target day to end of target day (in TZ_CN)
    start_dt = datetime.datetime.combine(target_date, datetime.time.min)
    end_dt = datetime.datetime.combine(target_date, datetime.time.max)
    
    # Convert to timestamps for comparison (Mem0 created_at is unix timestamp)
    start_ts = int(start_dt.replace(tzinfo=TZ_CN).timestamp())
    end_ts = int(end_dt.replace(tzinfo=TZ_CN).timestamp())
    
    # Fetch all memories (Mem0 API returns up to 100 by default, paginated)
    all_memories: list[dict] = []
    page = 1
    page_size = 100
    max_pages = 10  # Safety limit: 1000 memories max
    
    while page <= max_pages:
        try:
            # Mem0 API: GET /api/v1/memories/?user_id=tiger&page_size=N
            url = f"{tm_core.mem0_base()}/api/v1/memories/?user_id=tiger&page_size={page_size}"
            if page > 1:
                url += f"&page={page}"
            
            headers = {
                "Authorization": f"Token {tm_core._env_value('MEM0_API_KEY')}",
                "Accept": "application/json",
            }
            
            import urllib.request
            req = urllib.request.Request(url, headers=headers, method="GET")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            
            with opener.open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            results = data.get("items", [])
            if not results:
                break
                
            all_memories.extend(results)
            
            # If fewer than page_size returned, we've reached the end
            if len(results) < page_size:
                break
                
            page += 1
            
        except Exception as e:
            print(f"[digest] Warning: Mem0 fetch failed on page {page}: {e}", file=sys.stderr)
            break
    
    # Client-side filter by created_at timestamp
    filtered = []
    for m in all_memories:
        created_at = m.get("created_at")
        if created_at and isinstance(created_at, (int, float)):
            if start_ts <= created_at <= end_ts:
                filtered.append(m)
    
    return filtered


def _list_inbox_for_date(date_str: str) -> list[dict[str, Any]]:
    """List inbox files created on target date.
    
    Inbox filename format: YYYY-MM-DD-HHMM-<agent>-<topic>.md
    We parse the date part from filename.
    """
    inbox_dir = REPO_ROOT / "inbox"
    if not inbox_dir.exists():
        return []
    
    target_prefix = date_str  # "2026-04-20"
    files: list[dict[str, Any]] = []
    
    for f in inbox_dir.glob("*.md"):
        if f.name == ".gitkeep":
            continue
        # Parse filename: 2026-04-20-1205-claude-code-systems.md
        parts = f.stem.split("-")
        if len(parts) >= 3:
            # First 3 parts are date
            file_date = "-".join(parts[:3])
            if file_date == target_prefix:
                # Extract agent and topic from filename
                agent = parts[3] if len(parts) > 3 else "unknown"
                topic = parts[4] if len(parts) > 4 else "unknown"
                files.append({
                    "filename": f.name,
                    "path": str(f.relative_to(REPO_ROOT)),
                    "date": file_date,
                    "agent": agent,
                    "topic": topic,
                    "size": f.stat().st_size,
                })
    
    # Sort by filename (chronological within day due to HHMM)
    files.sort(key=lambda x: x["filename"])
    return files


# ---------- DeepSeek Synthesis ----------


def _synthesize_digest(date_str: str, memories: list[dict], inbox_files: list[dict]) -> str:
    """Call DeepSeek to synthesize digest markdown."""
    # Prepare condensed data for prompt (to avoid token explosion)
    memories_condensed = []
    for m in memories[:50]:  # Limit to avoid prompt too long
        memories_condensed.append({
            "id": m.get("id"),
            "content": m.get("content", "")[:300],  # Truncate long content
            "created_at": m.get("created_at"),
            "metadata": m.get("metadata_", {}),
        })
    
    inbox_condensed = []
    for f in inbox_files[:20]:  # Limit inbox files
        inbox_condensed.append({
            "filename": f["filename"],
            "topic": f["topic"],
            "agent": f["agent"],
        })
    
    # Build prompt
    system_prompt = DIGEST_PROMPT_TEMPLATE.format(
        date=date_str,
        memories_count=len(memories),
        inbox_count=len(inbox_files),
        memories_json=json.dumps(memories_condensed, ensure_ascii=False, indent=2),
        inbox_json=json.dumps(inbox_condensed, ensure_ascii=False, indent=2),
    )
    
    # Call DeepSeek with retries
    for attempt in range(DIGEST_MAX_RETRIES):
        try:
            ok, parsed = tm_core._call_deepseek_json(
                system_prompt=system_prompt,
                user_msg="请根据以上数据生成日报 JSON。",
                timeout=DIGEST_DEEPSEEK_TIMEOUT,
                temperature=0.3,
                max_tokens=2048,
            )
            
            if not ok:
                # parsed is error reason string
                print(f"[digest] DeepSeek attempt {attempt+1} failed: {parsed}", file=sys.stderr)
                if attempt < DIGEST_MAX_RETRIES - 1:
                    time.sleep(DIGEST_RETRY_DELAY_BASE * (attempt + 1))
                    continue
                else:
                    return ""
            
            # Convert JSON result to Markdown
            return _render_digest_markdown(date_str, parsed, memories, inbox_files)
            
        except Exception as e:
            print(f"[digest] DeepSeek attempt {attempt+1} exception: {e}", file=sys.stderr)
            if attempt < DIGEST_MAX_RETRIES - 1:
                time.sleep(DIGEST_RETRY_DELAY_BASE * (attempt + 1))
            else:
                return ""
    
    return ""


def _render_digest_markdown(
    date_str: str,
    deepseek_result: Any,
    memories: list[dict],
    inbox_files: list[dict],
) -> str:
    """Render DeepSeek JSON result to digest Markdown with frontmatter."""
    # Type guard: ensure result is a dict
    if not isinstance(deepseek_result, dict):
        print(f"[digest] Warning: DeepSeek result is not dict: {type(deepseek_result)}", file=sys.stderr)
        return ""
    
    tldr = deepseek_result.get("tldr", "")
    facts_by_topic = deepseek_result.get("facts_by_topic", {})
    audit_suggestions = deepseek_result.get("audit_suggestions", [])
    
    # Build source lists
    mem_ids = [m.get("id", "unknown") for m in memories]
    inbox_names = [f["filename"] for f in inbox_files]
    
    # Count facts per topic
    topic_counts = {}
    total_facts = 0
    for topic, facts in facts_by_topic.items():
        if isinstance(facts, list):
            topic_counts[topic] = len(facts)
            total_facts += len(facts)
    
    # Render markdown
    lines = [
        "---",
        f'source: tigermemory-digest',
        f'topic: daily',
        f'date: {date_str}',
        f'generated_at: {datetime.datetime.now(TZ_CN).isoformat()}',
        f'mem0_count: {len(memories)}',
        f'inbox_count: {len(inbox_files)}',
        f'fact_count: {total_facts}',
        "---",
        "",
        f"# Daily Digest {date_str}",
        "",
        "## TL;DR",
        "",
        tldr if tldr else "(当日无显著活动记录)",
        "",
        "## 事实清单（可审核）",
        "",
    ]
    
    # Facts by topic
    valid_topics = ["systems", "brand", "operations", "investment", "person", "production"]
    for topic in valid_topics:
        facts = facts_by_topic.get(topic, [])
        if not isinstance(facts, list) or not facts:
            continue
        
        lines.append(f"### {topic}")
        lines.append("")
        for i, fact in enumerate(facts, 1):
            text = fact.get("text", "") if isinstance(fact, dict) else str(fact)
            source = fact.get("source", "unknown") if isinstance(fact, dict) else "unknown"
            lines.append(f"- [fact-{i:03d}] {text} (source: {source})")
        lines.append("")
    
    # Source references
    lines.append("## 原始来源")
    lines.append("")
    lines.append(f"- Mem0 IDs: {', '.join(mem_ids[:10])}{'...' if len(mem_ids) > 10 else ''}")
    lines.append(f"- Inbox files: {', '.join(inbox_names[:5])}{'...' if len(inbox_names) > 5 else ''}")
    lines.append("")
    
    # Audit suggestions
    if audit_suggestions:
        lines.append("## 待审核建议")
        lines.append("")
        for sug in audit_suggestions:
            lines.append(f"- {sug}")
        lines.append("")
    
    return "\n".join(lines)


# ---------- File Writing ----------


def _write_digest_file(path: Path, content: str) -> None:
    """Write digest markdown file."""
    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write with UTF-8
    path.write_text(content, encoding="utf-8")


def _append_audit_log(
    date_str: str,
    mem_count: int,
    inbox_count: int,
    success: bool,
    dry_run: bool,
) -> None:
    """Append audit log entry."""
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.datetime.now(TZ_CN).isoformat()
        entry = {
            "timestamp": timestamp,
            "date": date_str,
            "memories": mem_count,
            "inbox_files": inbox_count,
            "success": success,
            "dry_run": dry_run,
        }
        
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[digest] Warning: audit log failed: {e}", file=sys.stderr)


# ---------- CLI ----------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate daily digest from Mem0 and inbox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        type=str,
        default="yesterday",
        help='Target date (YYYY-MM-DD) or "yesterday" (default: yesterday)',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing files",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    
    result = generate_daily_digest(
        target_date=args.date if args.date != "yesterday" else None,
        dry_run=args.dry_run,
    )
    
    # Print result as JSON to stdout for scripting
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # Exit code: 0 for success/skipped, 1 for error
    if result["ok"]:
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
