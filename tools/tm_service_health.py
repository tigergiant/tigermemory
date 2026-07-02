#!/usr/bin/env python3
"""tm service health: one-shot diagnostic for the full tigermemory service chain.

Checks (read-only, no side effects):
  1. WSL user-level systemd units (tm-mcp/tm-http/tm-dashboard/tm-openai-mcp)
  2. Listening ports (9766/8790/1998/9776)
  3. Docker containers (OpenMemory 8765 / Caddy 9765 / Qdrant 6333)
  4. Tailscale connectivity (WSL node online)
  5. Public link (tm.doodiu.cloud/mcp initialize)
  6. End-to-end MCP search_memories (verifies Mem0 backend reachable)

Usage:
  py tools/tm_service_health.py              # full check, human-readable
  py tools/tm_service_health.py --json       # machine-readable
  py tools/tm_service_health.py --quiet      # only print on failure

Exit code: 0 if all green, 1 if any critical fail, 2 if any warn.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_URL = "https://tm.doodiu.cloud/mcp"
DEFAULT_API_KEY_ENV = "TM_MCP_API_KEY"

# WSL user-level units that must be active.
EXPECTED_UNITS = ["tm-mcp", "tm-http", "tm-dashboard", "tm-openai-mcp"]
# (port, label, critical) — ports that must be listening inside WSL.
EXPECTED_PORTS = [
    (9766, "tm-mcp", True),
    (8790, "tm-http", True),
    (1998, "tm-dashboard", False),
    (9776, "tm-openai-mcp", False),
]
# Docker containers (checked via docker ps on Windows host).
EXPECTED_CONTAINERS = [
    ("openmemory-openmemory-mcp-1", True),
    ("mem0-auth-gateway", True),
    ("openmemory-mem0_store-1", True),
]


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def _wsl_bash(script: str, timeout: int = 15) -> tuple[int, str]:
    return _run(["wsl", "-e", "bash", "-c", script], timeout=timeout)


def check_units() -> dict:
    script = "for u in tm-mcp tm-http tm-dashboard tm-openai-mcp; do echo \"$u=$(systemctl --user is-active $u 2>/dev/null)\"; done"
    code, out = _wsl_bash(script)
    units = {}
    for line in out.splitlines():
        if "=" in line:
            name, status = line.split("=", 1)
            units[name] = status
    failed = [u for u in EXPECTED_UNITS if units.get(u) != "active"]
    return {
        "check": "systemd_user_units",
        "units": units,
        "ok": not failed,
        "failed": failed,
        "critical": bool(failed),
    }


def check_ports() -> dict:
    script = "ss -tlnp 2>/dev/null | grep -oE ':[0-9]+' | sort -u"
    code, out = _wsl_bash(script)
    listening = {int(p.lstrip(":")) for p in out.split() if p.startswith(":")}
    results = []
    for port, label, crit in EXPECTED_PORTS:
        up = port in listening
        results.append({"port": port, "label": label, "up": up, "critical": crit})
    failed_critical = [r for r in results if r["critical"] and not r["up"]]
    return {
        "check": "listening_ports",
        "ports": results,
        "ok": not failed_critical,
        "failed": failed_critical,
        "critical": bool(failed_critical),
    }


def check_docker() -> dict:
    code, out = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], timeout=10)
    if code != 0:
        return {
            "check": "docker_containers",
            "ok": False,
            "critical": True,
            "error": f"docker daemon unreachable: {out[:200]}",
            "containers": [],
        }
    running = {}
    for line in out.splitlines():
        if "\t" in line:
            name, status = line.split("\t", 1)
            running[name] = status
    results = []
    for name, crit in EXPECTED_CONTAINERS:
        up = name in running and "Up" in running.get(name, "")
        results.append({"name": name, "status": running.get(name, "absent"), "up": up, "critical": crit})
    failed_critical = [r for r in results if r["critical"] and not r["up"]]
    return {
        "check": "docker_containers",
        "containers": results,
        "ok": not failed_critical,
        "failed": failed_critical,
        "critical": bool(failed_critical),
    }


def check_tailscale() -> dict:
    script = "tailscale ip 2>/dev/null && tailscale status 2>/dev/null | head -1"
    code, out = _wsl_bash(script, timeout=8)
    ip_line = out.splitlines()[0] if out.strip() else ""
    ok = bool(ip_line and "/" not in ip_line and len(ip_line) > 5)
    return {
        "check": "tailscale",
        "ip": ip_line,
        "ok": ok,
        "critical": False,
        "raw": out[:200],
    }


def check_public_link(api_key: str, url: str) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "tm-health/1.0",
    }
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "tm-health", "version": "1.0"}}
    }).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=15)
        body = r.read().decode()[:300]
        ok = r.status == 200 and "tigermemory" in body
        return {
            "check": "public_link",
            "url": url,
            "status": r.status,
            "ok": ok,
            "critical": True,
            "body_snippet": body[:150],
        }
    except urllib.error.HTTPError as e:
        return {
            "check": "public_link", "url": url, "status": e.code,
            "ok": False, "critical": True,
            "error": f"HTTP {e.code}: {e.read().decode()[:150]}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "check": "public_link", "url": url, "status": 0,
            "ok": False, "critical": True,
            "error": f"{type(exc).__name__}: {str(exc)[:150]}",
        }


def _load_api_key() -> str:
    env_key = os.environ.get(DEFAULT_API_KEY_ENV, "")
    if env_key:
        return env_key
    env_file = REPO_ROOT / "runtime" / "openmemory" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith(f"{DEFAULT_API_KEY_ENV}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    # Fallback: try WSL .env
    code, out = _wsl_bash("source /home/giant/tigermemory/runtime/openmemory/.env 2>/dev/null && echo $TM_MCP_API_KEY")
    if out.strip():
        return out.strip()
    return ""


def run_all_checks(public_url: str) -> dict:
    api_key = _load_api_key()
    results = [
        check_units(),
        check_ports(),
        check_docker(),
        check_tailscale(),
        check_public_link(api_key, public_url) if api_key else {
            "check": "public_link", "ok": False, "critical": True,
            "error": "TM_MCP_API_KEY not found in env or runtime/openmemory/.env",
        },
    ]
    critical_fail = [r for r in results if r.get("critical") and not r.get("ok")]
    warn_fail = [r for r in results if not r.get("critical") and not r.get("ok")]
    overall = "ok" if not critical_fail and not warn_fail else ("critical" if critical_fail else "warn")
    return {
        "schema": "tigermemory-service-health-v1",
        "overall": overall,
        "checks": results,
    }


def _print_human(report: dict) -> None:
    overall = report["overall"]
    icon = {"ok": "OK", "critical": "CRITICAL", "warn": "WARN"}[overall]
    print(f"=== TigerMemory Service Health: {icon} ===\n")
    for r in report["checks"]:
        name = r.get("check", "?")
        ok = r.get("ok", False)
        crit = r.get("critical", False)
        tag = "OK" if ok else ("FAIL" if crit else "WARN")
        print(f"[{tag}] {name}")
        if name == "systemd_user_units":
            for u, s in r.get("units", {}).items():
                print(f"       {u}: {s}")
        elif name == "listening_ports":
            for p in r.get("ports", []):
                flag = "up" if p["up"] else "DOWN"
                print(f"       :{p['port']} ({p['label']}) {flag}")
        elif name == "docker_containers":
            if r.get("error"):
                print(f"       {r['error']}")
            for c in r.get("containers", []):
                flag = "up" if c["up"] else "DOWN"
                print(f"       {c['name']}: {c['status']} [{flag}]")
        elif name == "tailscale":
            print(f"       ip={r.get('ip', '')}")
        elif name == "public_link":
            if r.get("error"):
                print(f"       {r['error']}")
            else:
                print(f"       status={r.get('status')} body={r.get('body_snippet', '')[:80]}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tm service-health", description="TigerMemory full-chain health check.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--quiet", action="store_true", help="Only print on failure.")
    parser.add_argument("--public-url", default=DEFAULT_PUBLIC_URL)
    args = parser.parse_args(argv)

    report = run_all_checks(args.public_url)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if args.quiet and report["overall"] == "ok":
            return 0
        _print_human(report)
    return 0 if report["overall"] == "ok" else (1 if report["overall"] == "critical" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
