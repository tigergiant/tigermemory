#!/usr/bin/env python3
"""TigerMemory multi-IDE fleet status — read-only, user-facing.

Inputs: known-IDE registry + each IDE's on-disk MCP config file (JSON/TOML).
Outputs: per-IDE connection status (configured/not-configured/no-config/remote),
         one-line remediation, optional tigermemory /health reachability.
Depends-on: stdlib only. Never edits any IDE config (read-only by design; the
            "F3 apply" stage lives in tm_agent_connect behind explicit confirm).

This answers the three questions a user has when switching IDEs:
  1) which of my IDEs are wired to tigermemory, and are they healthy?
  2) if the one I just opened isn't configured, what is the ONE fix?
  3) (with --check-health) is tigermemory itself reachable right now?

Running with no subcommand shows F1 (fleet status) + F2 (continuity) together
-- the one command to run at the start of any session. `status` / `continuity`
remain available standalone for scripting.

Design: detection is a raw text-contains check for tigermemory markers, so it
stays robust across config formats and never depends on tomllib. JSON configs
are additionally parsed to report transport (http/stdio) when possible.

F2 (continuity, `continuity` subcommand): answers a fourth question — "no
matter which IDE the last session ran in, where did it leave off, and is there
an unresolved blocker?" It reads the most recent Session Handoff Cards
(memory_type=session-handoff) via /search_memories, trying the internal direct
channel first and falling back to the public Cloudflare tunnel — because the
internal channel (VM<->host portproxy) has been observed to drop between
sessions (host-side firewall rule not surviving a restart), and a continuity
tool that hard-fails on that is useless. Read-only; never writes.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import pathlib
import shutil
import sys
import urllib.request

MARKERS = ("tigermemory", "tm.doodiu.cloud", "tm_mcp", "tigermemory-wsl")
PUBLIC_HEALTH_URL = "https://tm.doodiu.cloud/healthz"

# F2 continuity: try internal direct channel first (fast, but has been observed
# to go stale across VM/host restarts), then the public Cloudflare tunnel.
CONTINUITY_BASES = [
    {"name": "internal", "url": "http://172.20.160.1:8790", "bypass_proxy": True},
    {"name": "public", "url": "https://tm-api.doodiu.cloud", "bypass_proxy": False},
]
_EMPTY_BLOCKER_MARKERS = {"", "无", "none", "n/a", "无阻塞", "无遗留", "无。"}


def _home() -> pathlib.Path:
    override = os.environ.get("TM_IDE_FLEET_HOME")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home()


def _appdata() -> pathlib.Path | None:
    override = os.environ.get("TM_IDE_FLEET_HOME")
    if override:
        return pathlib.Path(override) / "AppData" / "Roaming"
    val = os.environ.get("APPDATA")
    return pathlib.Path(val) if val else None


def _localappdata() -> pathlib.Path | None:
    override = os.environ.get("TM_IDE_FLEET_HOME")
    if override:
        return pathlib.Path(override) / "AppData" / "Local"
    val = os.environ.get("LOCALAPPDATA")
    return pathlib.Path(val) if val else None


def _registry() -> list[dict]:
    """Known IDEs. `candidates` are tried in order; first existing wins.

    `kind`:
      - "config_file": detectable by scanning a local MCP config file.
      - "remote": no local file (e.g. ChatGPT web connector); guidance only.
    """
    home = _home()
    appdata = _appdata()
    localappdata = _localappdata()

    claude_desktop_candidates: list[pathlib.Path] = []
    if appdata:
        claude_desktop_candidates.append(appdata / "Claude" / "claude_desktop_config.json")
    if localappdata:
        # MSIX-redirected path used by the packaged Claude Desktop app.
        claude_desktop_candidates.append(
            localappdata
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
            / "claude_desktop_config.json"
        )

    return [
        {
            "id": "claude-code",
            "name": "Claude Code (CLI)",
            "kind": "config_file",
            "format": "json",
            "candidates": [home / ".claude.json"],
            "hint": "claude mcp add --transport http tigermemory https://tm.doodiu.cloud/mcp --header \"Authorization: Bearer <KEY>\" -s user",
        },
        {
            "id": "claude-desktop",
            "name": "Claude Desktop",
            "kind": "config_file",
            "format": "json",
            "candidates": claude_desktop_candidates,
            "hint": "编辑 claude_desktop_config.json，见 mcp-client-setup.md §1A（Claude Desktop 用 MSIX 重定向路径）",
        },
        {
            "id": "cursor",
            "name": "Cursor",
            "kind": "config_file",
            "format": "json",
            "candidates": [home / ".cursor" / "mcp.json"],
            "hint": "py tools/tm_agent_connect.py --print-config http，粘入 ~/.cursor/mcp.json",
        },
        {
            "id": "windsurf",
            "name": "Windsurf",
            "kind": "config_file",
            "format": "json",
            "candidates": [home / ".codeium" / "windsurf" / "mcp_config.json"],
            "hint": "见 mcp-client-setup.md §1A/1C，写入 ~/.codeium/windsurf/mcp_config.json",
        },
        {
            "id": "codex",
            "name": "Codex CLI",
            "kind": "config_file",
            "format": "toml",
            "candidates": [home / ".codex" / "config.toml"],
            "hint": "在 ~/.codex/config.toml 加 [mcp_servers.tigermemory]，见 mcp-client-setup.md",
        },
        {
            "id": "gemini",
            "name": "Gemini CLI",
            "kind": "config_file",
            "format": "json",
            "candidates": [home / ".gemini" / "settings.json"],
            "hint": "在 ~/.gemini/settings.json 的 mcpServers 加 tigermemory，见 mcp-client-setup.md",
        },
        {
            "id": "chatgpt",
            "name": "ChatGPT (web connector)",
            "kind": "remote",
            "format": None,
            "candidates": [],
            "hint": "ChatGPT 走公网 MCP 连接器 https://tm.doodiu.cloud/mcp（网页端添加，无本地配置文件），见 chatgpt-mcp-access.md",
        },
    ]


def _scan_config(path: pathlib.Path, fmt: str | None) -> dict:
    """Return {configured, transport, server_names, parse_ok}."""
    result = {"configured": False, "transport": None, "server_names": [], "parse_ok": False}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    result["configured"] = any(m in text for m in MARKERS)
    if fmt == "json":
        try:
            data = json.loads(text)
            result["parse_ok"] = True
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            if isinstance(servers, dict):
                for name, cfg in servers.items():
                    if not isinstance(cfg, dict):
                        continue
                    blob = json.dumps(cfg, ensure_ascii=False)
                    if any(m in blob for m in MARKERS) or "tigermemory" in str(name).lower():
                        result["server_names"].append(str(name))
                        t = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
                        result["transport"] = str(t)
        except (json.JSONDecodeError, ValueError):
            result["parse_ok"] = False
    return result


def _check_health(timeout: float = 8.0) -> dict:
    # Cloudflare blocks the default Python-urllib UA (Error 1010); use a
    # curl-like UA so /healthz behaves the same as a manual curl probe.
    req = urllib.request.Request(
        PUBLIC_HEALTH_URL,
        method="GET",
        headers={"User-Agent": "tigermemory-ide-fleet/1.0"},
    )
    opener = urllib.request.build_opener()
    try:
        import time

        t0 = time.monotonic()
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", errors="replace")
        latency_ms = (time.monotonic() - t0) * 1000.0
        ok = '"ok"' in body and "true" in body.lower()
        return {"reachable": ok, "latency_ms": round(latency_ms, 1), "url": PUBLIC_HEALTH_URL}
    except Exception as exc:  # noqa: BLE001 - report any failure honestly
        return {"reachable": False, "error": str(exc)[:200], "url": PUBLIC_HEALTH_URL}


def gather_fleet(check_health: bool = False) -> dict:
    ides = []
    for entry in _registry():
        row = {
            "id": entry["id"],
            "name": entry["name"],
            "kind": entry["kind"],
            "config_path": None,
            "status": None,
            "transport": None,
            "server_names": [],
            "hint": entry["hint"],
        }
        if entry["kind"] == "remote":
            row["status"] = "remote"
            ides.append(row)
            continue
        existing = [p for p in entry["candidates"] if p.exists()]
        if not existing:
            row["status"] = "no_config"
            ides.append(row)
            continue
        # Prefer a candidate that actually contains tigermemory (handles the
        # Claude Desktop AppData-vs-MSIX ambiguity: report the file that has it).
        chosen = existing[0]
        chosen_scan = _scan_config(chosen, entry.get("format"))
        for p in existing:
            scan = _scan_config(p, entry.get("format"))
            if scan["configured"]:
                chosen, chosen_scan = p, scan
                break
        row["config_path"] = str(chosen)
        row["transport"] = chosen_scan["transport"]
        row["server_names"] = chosen_scan["server_names"]
        row["status"] = "configured" if chosen_scan["configured"] else "not_configured"
        ides.append(row)

    configured = sum(1 for r in ides if r["status"] == "configured")
    summary = {
        "configured_count": configured,
        "not_configured_count": sum(1 for r in ides if r["status"] == "not_configured"),
        "no_config_count": sum(1 for r in ides if r["status"] == "no_config"),
        "remote_count": sum(1 for r in ides if r["status"] == "remote"),
        "total": len(ides),
    }
    out = {"schema": "tm-ide-fleet-v1", "summary": summary, "ides": ides}
    if check_health:
        out["health"] = _check_health()
    return out


_ICON = {
    "configured": "🟢",
    "not_configured": "🟡",
    "no_config": "⚪",
    "remote": "🌐",
}


def render_text(fleet: dict) -> str:
    lines = ["TigerMemory IDE 舰队状态", ""]
    for r in fleet["ides"]:
        icon = _ICON.get(r["status"], "?")
        label = {
            "configured": "已接入",
            "not_configured": "有配置但未接 tigermemory",
            "no_config": "未发现配置文件",
            "remote": "公网连接器（无本地文件）",
        }.get(r["status"], r["status"])
        extra = ""
        if r["status"] == "configured" and r["transport"]:
            extra = f"  [{r['transport']}]"
        lines.append(f"{icon} {r['name']:<26} {label}{extra}")
        if r["status"] in ("not_configured", "no_config"):
            lines.append(f"     ↳ 修复：{r['hint']}")
    s = fleet["summary"]
    lines.append("")
    lines.append(
        f"合计 {s['total']}：已接入 {s['configured_count']} / 待接 "
        f"{s['not_configured_count'] + s['no_config_count']} / 公网 {s['remote_count']}"
    )
    if "health" in fleet:
        h = fleet["health"]
        if h.get("reachable"):
            lines.append(f"tigermemory 服务：🟢 可达 ({h.get('latency_ms')}ms)")
        else:
            lines.append(f"tigermemory 服务：🔴 不可达 ({h.get('error', 'unknown')})")
    return "\n".join(lines)


# ---------- F2: multi-IDE switching continuity ----------


def _api_key() -> str | None:
    val = os.environ.get("TM_MCP_API_KEY", "").strip()
    if val:
        return val
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    env_path = repo_root / "runtime" / "openmemory" / ".env"
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("TM_MCP_API_KEY="):
            return line.split("=", 1)[1].strip()
    return None


def _search_memories_via_http(
    base_url: str, query: str, limit: int, api_key: str, timeout: float, bypass_proxy: bool
) -> dict:
    req = urllib.request.Request(
        f"{base_url}/search_memories",
        data=json.dumps({"query": query, "limit": limit}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "tigermemory-ide-fleet/1.0",
        },
        method="POST",
    )
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if bypass_proxy
        else urllib.request.build_opener()
    )
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_handoff_card(content: str) -> dict:
    """Split a Session Handoff Card's raw text into frontmatter dict + body sections.

    Cards are `--- key: value ... --- \\n## Task\\n...\\n## Blockers\\n...`
    (see wiki/systems/session-handoff-protocol.md). Simple line-based parsing —
    values here are flat scalars, no need for a YAML dependency.
    """
    frontmatter: dict[str, str] = {}
    body = content
    if content.lstrip().startswith("---"):
        stripped = content.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            fm_block = stripped[3:end]
            body = stripped[end + 4 :].lstrip("\n")
            for line in fm_block.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lstrip("-").strip()
                if key:
                    frontmatter[key] = value.strip()
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return {"frontmatter": frontmatter, "sections": sections}


def _format_created_at(raw_value: object) -> str:
    """Best-effort human-readable timestamp; falls back to the raw value."""
    if raw_value is None:
        return "?"
    text = str(raw_value)
    try:
        num = float(text)
    except ValueError:
        return text
    try:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(num, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return text


def _card_record(raw: dict) -> dict | None:
    """Return a normalized handoff record, or None if this isn't actually a
    session-handoff card (search is semantic/fuzzy and can surface unrelated
    memories that merely mention similar words)."""
    parsed = _parse_handoff_card(str(raw.get("content") or ""))
    fm, sections = parsed["frontmatter"], parsed["sections"]
    if fm.get("memory_type") != "session-handoff":
        return None
    return {
        "id": raw.get("id"),
        "created_at": _format_created_at(raw.get("created_at")),
        "created_at_raw": raw.get("created_at"),
        "session_id": fm.get("session_id"),
        "ide": fm.get("ide"),
        "agent": fm.get("agent"),
        "confidence": fm.get("confidence"),
        "source": fm.get("source"),
        "task": sections.get("Task", "").strip(),
        "blockers": sections.get("Blockers", "").strip(),
        "handoff": sections.get("Handoff", "").strip(),
    }


def _has_open_blocker(blockers_text: str) -> bool:
    normalized = (blockers_text or "").strip().lower()
    return bool(normalized) and normalized not in _EMPTY_BLOCKER_MARKERS


def gather_continuity(limit: int = 5, timeout: float = 8.0, fetcher=None) -> dict:
    """Read the most recent Session Handoff Cards across all IDEs.

    Tries CONTINUITY_BASES in order (internal direct channel, then the public
    Cloudflare tunnel) and returns the first that succeeds. Never raises on
    network failure — reports ok=False with the last error instead.
    """
    api_key = _api_key()
    if not api_key:
        return {
            "schema": "tm-ide-continuity-v1",
            "ok": False,
            "error": "TM_MCP_API_KEY not configured (checked env and runtime/openmemory/.env)",
            "cards": [],
        }
    fetch = fetcher or _search_memories_via_http
    last_error = "no channel attempted"
    display_limit = max(0, int(limit))
    fetch_limit = min(max(display_limit, 1) * 3, 30)
    for base in CONTINUITY_BASES:
        try:
            payload = fetch(
                base["url"],
                "memory_type session-handoff",
                fetch_limit,
                api_key,
                timeout,
                base["bypass_proxy"],
            )
        except Exception as exc:  # noqa: BLE001 - any channel may fail; try the next
            last_error = f"{base['name']}: {str(exc)[:200]}"
            continue
        cards = [
            rec for rec in (_card_record(r) for r in (payload.get("results") or [])) if rec is not None
        ]
        cards.sort(key=lambda c: str(c.get("created_at_raw") or ""), reverse=True)
        cards = cards[:display_limit]
        return {
            "schema": "tm-ide-continuity-v1",
            "ok": True,
            "source": base["name"],
            "cards": cards,
            "any_open_blocker": any(_has_open_blocker(c["blockers"]) for c in cards),
        }
    return {
        "schema": "tm-ide-continuity-v1",
        "ok": False,
        "error": f"all channels failed ({last_error})",
        "cards": [],
    }


def render_continuity_text(result: dict) -> str:
    if not result.get("ok"):
        return f"⚠️ 读不到 session handoff（{result.get('error', 'unknown error')}）"
    lines = [f"最近的 session handoff（来源：{result['source']}）", ""]
    if not result["cards"]:
        lines.append("（暂无 session-handoff 记录）")
        return "\n".join(lines)
    for c in result["cards"]:
        open_blocker = _has_open_blocker(c["blockers"])
        icon = "🔴" if open_blocker else "🟢"
        lines.append(f"{icon} {c.get('created_at', '?')}  [{c.get('ide', '?')}/{c.get('agent', '?')}]")
        if c["task"]:
            lines.append(f"   任务: {c['task'][:150]}")
        if open_blocker:
            lines.append(f"   ⚠️ 未决 blocker: {c['blockers'][:200]}")
        elif c["handoff"]:
            lines.append(f"   接力: {c['handoff'][:150]}")
        lines.append("")
    if result.get("any_open_blocker"):
        lines.append("有未决 blocker，接手前先看清楚上面标 🔴 的那条。")
    return "\n".join(lines).rstrip()


# ---------- F3: one-click config fix (diff-preview, explicit --apply) ----------
#
# Safety posture (deliberately conservative, matching tm_agent_connect.py's
# established convention in this codebase): the Authorization header written
# by --apply is ALWAYS the placeholder "<TM_MCP_API_KEY>" — this tool never
# writes a real Bearer token into a file automatically. The user swaps in the
# real value themselves (it lives in runtime/openmemory/.env). This keeps the
# blast radius of an automated multi-file writer bounded: worst case it writes
# a config with a placeholder that doesn't work yet, never a leaked secret.
#
# Only JSON mcpServers configs are auto-writable. TOML (Codex) has no safe
# stdlib round-trip writer, so `fix` refuses to touch it and returns the
# existing manual hint instead.

TOKEN_PLACEHOLDER = "<TM_MCP_API_KEY>"


def _http_server_value() -> dict:
    return {
        "type": "http",
        "url": "https://tm.doodiu.cloud/mcp",
        "headers": {"Authorization": f"Bearer {TOKEN_PLACEHOLDER}"},
    }


def build_fix_plan(ide_id: str) -> dict:
    """Compute what `fix` would change for one IDE, without writing anything."""
    entry = next((e for e in _registry() if e["id"] == ide_id), None)
    if entry is None:
        return {"ide_id": ide_id, "writable": False, "reason": f"unknown ide id: {ide_id}"}
    if entry["kind"] != "config_file" or entry.get("format") != "json":
        return {
            "ide_id": ide_id,
            "name": entry["name"],
            "writable": False,
            "reason": (
                f"F3 只自动写 JSON mcpServers 配置；{entry['name']} "
                f"用的是 {entry.get('format') or '远程连接器'}，只能手动配置"
            ),
            "hint": entry["hint"],
        }
    existing_path = next((p for p in entry["candidates"] if p.exists()), None)
    target_path = existing_path or (entry["candidates"][0] if entry["candidates"] else None)
    if target_path is None:
        return {
            "ide_id": ide_id,
            "name": entry["name"],
            "writable": False,
            "reason": "no candidate config path known for this platform",
        }
    before_text = ""
    before_data: dict = {}
    if existing_path is not None:
        before_text = existing_path.read_text(encoding="utf-8", errors="replace")
        try:
            before_data = json.loads(before_text)
            if not isinstance(before_data, dict):
                raise ValueError("config root is not a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return {
                "ide_id": ide_id,
                "name": entry["name"],
                "writable": False,
                "reason": f"{existing_path} 不是合法 JSON（{exc}），需要先手动修复",
            }
    already_configured = "tigermemory" in (before_data.get("mcpServers") or {})
    after_data = json.loads(json.dumps(before_data)) if before_data else {}
    after_data.setdefault("mcpServers", {})
    after_data["mcpServers"]["tigermemory"] = _http_server_value()
    after_text = json.dumps(after_data, ensure_ascii=False, indent=2) + "\n"
    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=str(target_path) if existing_path else "(不存在，将新建)",
            tofile=str(target_path),
        )
    )
    return {
        "ide_id": ide_id,
        "name": entry["name"],
        "config_path": str(target_path),
        "exists_before": existing_path is not None,
        "already_configured": already_configured,
        "before_text": before_text,
        "after_text": after_text,
        "diff": "".join(diff_lines),
        "writable": True,
    }


def apply_fix_plan(plan: dict) -> dict:
    """Write the planned change to disk. Call only after showing the diff.

    Backs up any existing file first. No-ops (does not touch the file) when
    already_configured is True.
    """
    if not plan.get("writable"):
        return {"ok": False, "reason": plan.get("reason", "not writable")}
    if plan.get("already_configured"):
        return {"ok": True, "action": "noop", "reason": "tigermemory already configured"}
    target_path = pathlib.Path(plan["config_path"])
    backup_path = None
    if plan.get("exists_before"):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = target_path.with_name(f"{target_path.name}.bak_{timestamp}")
        shutil.copy2(target_path, backup_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(plan["after_text"], encoding="utf-8")
    return {
        "ok": True,
        "action": "written",
        "config_path": str(target_path),
        "backup_path": str(backup_path) if backup_path else None,
        "token_is_placeholder": True,
    }


def render_fix_plan_text(plan: dict, applied: dict | None = None) -> str:
    if not plan.get("writable"):
        lines = [f"⚪ {plan.get('name', plan['ide_id'])}: 不能自动改"]
        lines.append(f"   原因: {plan.get('reason', 'unknown')}")
        if plan.get("hint"):
            lines.append(f"   手动修复: {plan['hint']}")
        return "\n".join(lines)
    if plan.get("already_configured"):
        return f"🟢 {plan['name']}: 已经配置好了，无需修复（{plan['config_path']}）"
    lines = [f"计划修改: {plan['name']}  ({plan['config_path']})", ""]
    lines.append(plan["diff"] if plan["diff"] else "(新建文件，无 diff 可比)")
    lines.append("")
    lines.append(
        f"注意：Authorization 里的 {TOKEN_PLACEHOLDER} 是占位符，写入后需要你自己"
        "换成 runtime/openmemory/.env 里的真实 TM_MCP_API_KEY 值——本工具不会自动"
        "写入真实密钥。"
    )
    if applied is None:
        lines.append("这是预览，尚未写入。加 --apply 才会真的写文件（写入前自动备份原文件）。")
    elif applied.get("ok"):
        if applied.get("action") == "written":
            lines.append(f"✅ 已写入 {applied['config_path']}")
            if applied.get("backup_path"):
                lines.append(f"   原文件已备份到 {applied['backup_path']}")
        else:
            lines.append(f"（{applied.get('reason')}，未改动文件）")
    else:
        lines.append(f"❌ 写入失败: {applied.get('reason')}")
    return "\n".join(lines)


def gather_full(check_health: bool = False, limit: int = 5) -> dict:
    """F1 + F2 combined: the one-shot "what's my situation" snapshot."""
    return {
        "schema": "tm-ide-fleet-full-v1",
        "fleet": gather_fleet(check_health=check_health),
        "continuity": gather_continuity(limit=limit),
    }


def render_full_text(full: dict) -> str:
    return render_text(full["fleet"]) + "\n\n" + render_continuity_text(full["continuity"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument(
        "--check-health", action="store_true", help="also probe tigermemory /healthz (default view only)"
    )
    ap.add_argument(
        "--limit", type=int, default=5, help="how many recent handoff cards to show (default view only)"
    )
    sub = ap.add_subparsers(dest="command")
    st = sub.add_parser("status", help="show multi-IDE fleet status only (read-only)")
    st.add_argument("--json", action="store_true", help="emit JSON instead of text")
    st.add_argument("--check-health", action="store_true", help="also probe tigermemory /healthz")
    ct = sub.add_parser(
        "continuity",
        help="show where the last session left off only, across IDEs (F2, read-only)",
    )
    ct.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ct.add_argument("--limit", type=int, default=5, help="how many recent handoff cards to show")
    fx = sub.add_parser(
        "fix",
        help="preview (and, with --apply, write) the tigermemory MCP config for one IDE (F3)",
    )
    fx.add_argument("ide_id", help="IDE id from `status` output, e.g. cursor, windsurf, gemini")
    fx.add_argument("--apply", action="store_true", help="actually write the file (default: preview only)")
    fx.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    if args.command is None:
        # Default: F1 + F2 together — the one command to run at the start of
        # any session, regardless of which IDE you just opened.
        full = gather_full(check_health=args.check_health, limit=args.limit)
        if args.json:
            print(json.dumps(full, ensure_ascii=False, indent=2))
        else:
            print(render_full_text(full))
        return 0
    if args.command == "status":
        check_health = getattr(args, "check_health", False)
        as_json = getattr(args, "json", False)
        fleet = gather_fleet(check_health=check_health)
        if as_json:
            print(json.dumps(fleet, ensure_ascii=False, indent=2))
        else:
            print(render_text(fleet))
        return 0
    if args.command == "continuity":
        as_json = getattr(args, "json", False)
        result = gather_continuity(limit=args.limit)
        if as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render_continuity_text(result))
        return 0 if result.get("ok") else 1
    if args.command == "fix":
        plan = build_fix_plan(args.ide_id)
        applied = None
        if args.apply and plan.get("writable") and not plan.get("already_configured"):
            applied = apply_fix_plan(plan)
        if args.json:
            print(json.dumps({"plan": plan, "applied": applied}, ensure_ascii=False, indent=2))
        else:
            print(render_fix_plan_text(plan, applied))
        if not plan.get("writable"):
            return 1
        if applied is not None and not applied.get("ok"):
            return 1
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
