#!/usr/bin/env python3
"""tigermemory stdio-to-HTTP MCP bridge.

Compatibility shim for MCP clients that only support stdio transport
(e.g., Huawei Xiaoyi Claw / OpenClaw as of 2026-05 — the embedded
client log explicitly says "stdio only today"). The tigermemory server
uses FastMCP's Streamable HTTP transport (SSE + session headers), so
such clients cannot talk to it directly.

This bridge runs as a stdio child process next to the MCP client, reads
line-delimited JSON-RPC from stdin, forwards each message to a remote
Streamable HTTP MCP server, parses the SSE response, and writes each
JSON-RPC response back to stdout on its own line.

Zero external dependencies — stdlib only, Python 3.8+.

Typical MCP client config::

    {
      "mcpServers": {
        "tigermemory": {
          "command": "python3",
          "args": ["/path/to/tm_mcp_bridge.py"],
          "env": {
            "TM_MCP_URL":     "https://tm.doodiu.cloud/mcp",
            "TM_MCP_API_KEY": "<bearer token>"
          }
        }
      }
    }

Diagnostics:
    TM_MCP_BRIDGE_LOG=1       Write request/response summaries to stderr.
    TM_MCP_BRIDGE_TIMEOUT=60  Per-request timeout in seconds (default 60).

Protocol notes (Streamable HTTP, 2025 MCP spec):
    - First request must be `initialize`; server returns a
      `Mcp-Session-Id` response header the bridge captures and replays
      on every subsequent request.
    - `Accept: application/json, text/event-stream` is mandatory.
    - Notifications (no `id`) are POSTed; server returns HTTP 202 with
      no body — bridge stays silent.
    - Server may stream multiple `data:` events per response; each one
      is forwarded to stdout as its own line.
Inputs: MCP stdio/HTTP requests, REST JSON payloads, or local facade smoke-test arguments.
Outputs: MCP tool responses, HTTP JSON responses, health checks, or smoke-test diagnostics.
Depends-on (must-have): tm_core shared APIs, FastAPI/uvicorn or MCP runtime libraries, and local tigermemory services.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any, Iterable

URL = os.environ.get("TM_MCP_URL", "").rstrip("/")
API_KEY = os.environ.get("TM_MCP_API_KEY", "")
TIMEOUT = float(os.environ.get("TM_MCP_BRIDGE_TIMEOUT", "60"))
DEBUG = bool(os.environ.get("TM_MCP_BRIDGE_LOG"))

SESSION_ID: str | None = None


def log(msg: str) -> None:
    if DEBUG:
        sys.stderr.write(f"[tm-bridge] {msg}\n")
        sys.stderr.flush()


def fail(msg: str) -> None:
    sys.stderr.write(f"[tm-bridge] fatal: {msg}\n")
    sys.exit(1)


_DEFAULT_UA = "tigermemory-mcp-bridge/1.0"

# Optional: skip TLS cert verification. Useful for direct-IP fallback when
# the Cloudflare-fronted path fails (common in CN networks) and the origin
# is fronted by a self-signed cert (e.g. Caddy `tls internal`).
# Only set when you trust the network path AND the destination IP.
INSECURE = bool(os.environ.get("TM_MCP_BRIDGE_INSECURE"))
SSL_CTX: ssl.SSLContext | None = None
if INSECURE:
    SSL_CTX = ssl.create_default_context()
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE


def build_headers() -> dict[str, str]:
    # Cloudflare-fronted endpoints reject the default `Python-urllib/X.Y`
    # UA with Error 1010 (browser_signature_banned). Set an explicit UA.
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {API_KEY}",
        "User-Agent": os.environ.get("TM_MCP_BRIDGE_UA", _DEFAULT_UA),
    }
    if SESSION_ID:
        headers["Mcp-Session-Id"] = SESSION_ID
    return headers


def parse_sse(body: bytes) -> list[dict[str, Any]]:
    """Extract JSON payloads from `data:` lines in an SSE response body.

    SSE events are separated by blank lines; each event may span multiple
    lines, but the JSON-RPC payload is conventionally a single `data:` line.
    """
    out: list[dict[str, Any]] = []
    text = body.decode("utf-8", errors="replace")
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data:"):
                continue
            raw = line[5:].lstrip()
            if not raw or raw == "[DONE]":
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                log(f"bad sse data line: {raw[:120]!r}")
    return out


def _session_id_from(resp_headers: Any) -> str | None:
    # email.message.Message is case-insensitive but some proxies lowercase.
    for key in ("Mcp-Session-Id", "mcp-session-id", "MCP-Session-Id"):
        val = resp_headers.get(key)
        if val:
            return val
    return None


def forward(rpc: dict[str, Any]) -> list[dict[str, Any]]:
    """POST one JSON-RPC message to the remote MCP server; return parsed replies."""
    global SESSION_ID
    data = json.dumps(rpc).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers=build_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
            sid = _session_id_from(resp.headers)
            if sid and sid != SESSION_ID:
                SESSION_ID = sid
                log(f"session id set: {SESSION_ID}")
            body = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            status = resp.status
    except urllib.error.HTTPError as e:
        err_body = b""
        try:
            err_body = e.read()
        except Exception:
            pass
        log(f"http {e.code}: {err_body[:200]!r}")
        if "id" in rpc:
            return [{
                "jsonrpc": "2.0",
                "id": rpc["id"],
                "error": {
                    "code": -32000,
                    "message": f"bridge http {e.code}",
                    "data": err_body.decode("utf-8", errors="replace")[:400],
                },
            }]
        return []
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"network error: {e}")
        if "id" in rpc:
            return [{
                "jsonrpc": "2.0",
                "id": rpc["id"],
                "error": {"code": -32001, "message": f"bridge network error: {e}"},
            }]
        return []

    log(f"← status={status} ctype={ctype} bytes={len(body)}")

    # 202 Accepted = notification ack, no body expected.
    if status == 202 or not body:
        return []

    if "text/event-stream" in ctype:
        return parse_sse(body)

    # Fallback: single-shot JSON (some spec-compliant servers can do this).
    try:
        obj = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
    except json.JSONDecodeError:
        log(f"non-json body: {body[:200]!r}")
    return []


def emit(stdout: Any, items: Iterable[dict[str, Any]]) -> None:
    for obj in items:
        line = json.dumps(obj, ensure_ascii=False)
        stdout.write(line.encode("utf-8"))
        stdout.write(b"\n")
        stdout.flush()
        log(f"→ id={obj.get('id')} has_error={bool(obj.get('error'))}")


def main() -> None:
    if not URL:
        fail("TM_MCP_URL env var is required (e.g. https://tm.doodiu.cloud/mcp)")
    if not API_KEY:
        fail("TM_MCP_API_KEY env var is required")

    log(f"bridge up: url={URL} timeout={TIMEOUT} insecure={INSECURE}")
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        line = stdin.readline()
        if not line:
            log("stdin closed, exiting")
            return
        line = line.strip()
        if not line:
            continue
        try:
            rpc = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"bad stdin json: {e}; raw={line[:120]!r}")
            continue
        if not isinstance(rpc, dict):
            log(f"stdin not a JSON object: {type(rpc).__name__}")
            continue
        log(f"⇢ method={rpc.get('method')} id={rpc.get('id')}")
        emit(stdout, forward(rpc))


if __name__ == "__main__":
    main()
