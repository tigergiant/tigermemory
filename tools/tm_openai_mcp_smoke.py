#!/usr/bin/env python3
"""Public smoke test for the ChatGPT/OpenAI-facing tigermemory MCP facade.

The script never prints OAuth tokens. It can run without a token to verify
public health and discovery endpoints, and with the local OAuth store to test
the real streamable HTTP MCP handshake.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PUBLIC_BASE = "https://tm-openai.doodiu.cloud"
DEFAULT_STORE_PATH = REPO_ROOT / "runtime" / "openmemory" / "openai-mcp-oauth.json"
EXPECTED_TOOLS = ["search", "fetch", "get_agent_onboarding", "write_memory"]


def _json_get(url: str, *, timeout: float) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read(256_000).decode("utf-8", errors="replace")
        return {"status": getattr(resp, "status", 200), "json": json.loads(body)}


def _token_scopes(raw: dict[str, Any]) -> list[str]:
    scopes = raw.get("scopes") or raw.get("scope") or []
    if isinstance(scopes, str):
        return scopes.split()
    if isinstance(scopes, list):
        return [str(item) for item in scopes]
    return []


def _pick_access_token(store_path: pathlib.Path, required_scopes: set[str]) -> str | None:
    if not store_path.exists():
        return None
    data = json.loads(store_path.read_text(encoding="utf-8"))
    now = time.time()
    for token, raw in data.get("access_tokens", {}).items():
        if raw.get("expires_at") is not None and raw["expires_at"] < now:
            continue
        if required_scopes.issubset(set(_token_scopes(raw))):
            return str(token)
    return None


async def _mcp_smoke(base_url: str, token: str, *, timeout: float, write_smoke: bool) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    started = time.monotonic()
    headers = {"Authorization": "Bearer " + token}
    async with streamablehttp_client(f"{base_url.rstrip('/')}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            tools_result = await asyncio.wait_for(session.list_tools(), timeout=timeout)
            tool_names = [tool.name for tool in tools_result.tools]
            search_result = await asyncio.wait_for(session.call_tool("search", {"query": "IPFB"}), timeout=timeout)
            search_payload = getattr(search_result, "structuredContent", None) or getattr(
                search_result, "structured_content", None
            )
            if not search_payload:
                search_payload = json.loads(search_result.content[0].text)
            first_id = (search_payload.get("results") or [{}])[0].get("id")
            fetched = False
            if first_id:
                fetch_result = await asyncio.wait_for(session.call_tool("fetch", {"id": first_id}), timeout=timeout)
                fetched = not bool(fetch_result.isError)
            write_result = None
            if write_smoke:
                write_result = await asyncio.wait_for(
                    session.call_tool(
                        "write_memory",
                        {
                            "topic": "systems",
                            "text": "2026-05-17 public ChatGPT MCP write_memory smoke.",
                        },
                    ),
                    timeout=timeout,
                )
            write_ok = True if write_result is None else not bool(write_result.isError)
    return {
        "ok": all(name in tool_names for name in EXPECTED_TOOLS) and fetched and write_ok,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "tools": tool_names,
        "first_search_id": first_id,
        "fetch_ok": fetched,
        "write_smoke": write_smoke,
        "write_is_error": bool(write_result.isError) if write_result is not None else None,
        "write_error_excerpt": (write_result.content[0].text if write_result and write_result.content else "")[:240],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--public-base", default=DEFAULT_PUBLIC_BASE)
    ap.add_argument("--oauth-store", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--write-smoke", action="store_true", help="Actually call write_memory when a write-scoped token exists")
    args = ap.parse_args()

    base = args.public_base.rstrip("/")
    store_path = pathlib.Path(args.oauth_store)
    report: dict[str, Any] = {"public_base": base, "checks": {}}

    try:
        report["checks"]["healthz"] = _json_get(f"{base}/healthz", timeout=args.timeout)
        report["checks"]["readyz"] = _json_get(f"{base}/readyz", timeout=args.timeout)
        report["checks"]["protected_resource"] = _json_get(
            f"{base}/.well-known/oauth-protected-resource",
            timeout=args.timeout,
        )
        report["checks"]["authorization_server"] = _json_get(
            f"{base}/.well-known/oauth-authorization-server",
            timeout=args.timeout,
        )
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    full_token = _pick_access_token(store_path, {"tm:read", "tm:write_memory"})
    read_token = _pick_access_token(store_path, {"tm:read"})
    if full_token:
        try:
            report["checks"]["mcp_token"] = asyncio.run(
                _mcp_smoke(base, full_token, timeout=args.timeout, write_smoke=args.write_smoke)
            )
        except Exception as exc:
            report["checks"]["mcp_token"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    elif read_token:
        report["checks"]["mcp_token"] = {
            "ok": True,
            "skipped": True,
            "reason": "only tm:read token is available; reconnect ChatGPT to obtain tm:write_memory before full MCP smoke",
        }
    else:
        report["checks"]["mcp_token"] = {"ok": True, "skipped": True, "reason": "no valid OAuth token"}

    metadata_scopes = report["checks"]["protected_resource"]["json"].get("scopes_supported") or []
    auth_scopes = report["checks"]["authorization_server"]["json"].get("scopes_supported") or []
    report["ok"] = (
        bool(report["checks"]["healthz"]["json"].get("ok"))
        and bool(report["checks"]["readyz"]["json"].get("ok"))
        and "tm:read" in metadata_scopes
        and "tm:write_memory" in metadata_scopes
        and "tm:read" in auth_scopes
        and "tm:write_memory" in auth_scopes
        and bool(report["checks"]["mcp_token"].get("ok"))
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
