#!/usr/bin/env python3
"""Runtime smoke for tm_mcp.py HTTP transport.

Spins up tm_mcp.py on a free localhost port with a temporary API key,
probes /healthz, then shuts down.  Exit 0 on success, 1 on failure.

Requires: pip install -r deploy/mcp/requirements.txt
Usage:    python tools/tm_mcp_smoke.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    port = _free_port()
    api_key = "smoke-test-key-12345"
    env = os.environ.copy()
    env["TM_MCP_API_KEY"] = api_key
    # Restrict to localhost so DNS-rebind protection does not block the probe.
    env["TM_MCP_ALLOWED_HOSTS"] = "127.0.0.1,127.0.0.1:*,localhost,localhost:*"

    cmd = [
        sys.executable,
        os.path.join(TOOLS_DIR, "tm_mcp.py"),
        "--http",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    url = f"http://127.0.0.1:{port}/healthz"
    start = time.monotonic()
    timeout = 15.0
    last_err = None

    try:
        while time.monotonic() - start < timeout:
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = resp.read()
                    text = data.decode()
                    if '"ok": true' in text or '"ok":true' in text:
                        print(f"PASS: {url} -> {text}")
                        return 0
                    print(f"FAIL: {url} unexpected response: {text}")
                    return 1
            except urllib.error.URLError as e:
                last_err = e
                time.sleep(0.5)
                continue
        print(f"FAIL: healthz probe timed out after {timeout}s (last error: {last_err})")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    sys.exit(main())
