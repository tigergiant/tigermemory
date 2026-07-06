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

Design: detection is a raw text-contains check for tigermemory markers, so it
stays robust across config formats and never depends on tomllib. JSON configs
are additionally parsed to report transport (http/stdio) when possible.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.request

MARKERS = ("tigermemory", "tm.doodiu.cloud", "tm_mcp", "tigermemory-wsl")
PUBLIC_HEALTH_URL = "https://tm.doodiu.cloud/healthz"


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command")
    st = sub.add_parser("status", help="show multi-IDE fleet status (read-only)")
    st.add_argument("--json", action="store_true", help="emit JSON instead of text")
    st.add_argument("--check-health", action="store_true", help="also probe tigermemory /healthz")
    args = ap.parse_args(argv)

    if args.command in (None, "status"):
        check_health = getattr(args, "check_health", False)
        as_json = getattr(args, "json", False)
        fleet = gather_fleet(check_health=check_health)
        if as_json:
            print(json.dumps(fleet, ensure_ascii=False, indent=2))
        else:
            print(render_text(fleet))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
